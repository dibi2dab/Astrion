"""Publish queued payloads through the Blogger API v3.

The publisher is deliberately boring: validate again, reserve, call once,
record the result, archive. Blogger itself is the source of truth for post IDs
and URLs; local state is the idempotency ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .blogger import BloggerAuthError, BloggerClient, BloggerDriftError, BloggerError
from .config import Settings
from .queue import (
    archive_failure,
    due_payloads,
    load_payload,
    move_to_archive,
    payload_path,
    validate_payload,
)
from .state import StateStore
from .text import iso_now, normalize_signature


@dataclass
class PublishOutcome:
    queue_id: str
    topic_id: str
    status: str
    post_id: str = ""
    url: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "queue_id": self.queue_id,
            "topic_id": self.topic_id,
            "status": self.status,
            "post_id": self.post_id,
            "url": self.url,
            "error": self.error,
        }


@dataclass
class PublishResult:
    outcomes: list[PublishOutcome] = field(default_factory=list)
    reconciled: list[PublishOutcome] = field(default_factory=list)
    stopped: bool = False
    error: str = ""

    @property
    def published(self) -> int:
        return sum(1 for item in self.outcomes if item.status in {"published", "draft"})

    def to_dict(self) -> dict[str, Any]:
        return {
            "published": self.published,
            "stopped": self.stopped,
            "error": self.error,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "reconciled": [item.to_dict() for item in self.reconciled],
        }


def publish_due(
    settings: Settings,
    state: StateStore,
    client: BloggerClient,
    *,
    now: datetime | None = None,
    max_posts: int | None = None,
    dry_run: bool = False,
    only_queue_id: str = "",
) -> PublishResult:
    moment = now or datetime.now(timezone.utc)
    result = PublishResult()

    try:
        _reconcile_inflight(settings, state, client, result, moment)
    except BloggerError as exc:
        result.stopped = True
        result.error = f"could not reconcile in-flight posts: {exc}"
        return result

    if only_queue_id:
        items = []
        path = payload_path(settings, only_queue_id)
        if path.exists():
            try:
                payload = load_payload(path)
                payload["__path"] = str(path)
                items.append(payload)
            except (OSError, ValueError) as exc:
                result.outcomes.append(
                    PublishOutcome(only_queue_id, "", "failed", error=f"unreadable payload: {exc}")
                )
                return result
        else:
            result.outcomes.append(
                PublishOutcome(only_queue_id, "", "failed", error="queue payload not found")
            )
            return result
    else:
        items = due_payloads(settings, moment)

    limit = max_posts or settings.blogger.max_per_run
    attempted = 0
    for payload in items:
        if attempted >= limit:
            break
        queue_id = str(payload.get("queue_id") or "")
        topic_id = str(payload.get("topic_id") or "")

        if "__error" in payload:
            result.outcomes.append(
                PublishOutcome(queue_id, topic_id, "failed", error=str(payload["__error"]))
            )
            continue

        pending = state.find_pending(queue_id)
        if pending is None:
            result.outcomes.append(
                PublishOutcome(queue_id, topic_id, "skipped", error="no matching pending record")
            )
            continue
        if pending.get("status") == "in_flight":
            result.outcomes.append(
                PublishOutcome(queue_id, topic_id, "skipped", error="still in flight")
            )
            continue
        next_attempt = _parse_timestamp(pending.get("next_attempt_at"))
        if not only_queue_id and next_attempt and next_attempt > moment:
            result.outcomes.append(
                PublishOutcome(
                    queue_id,
                    topic_id,
                    "skipped",
                    error=f"retry scheduled at {pending['next_attempt_at']}",
                )
            )
            continue

        publish_date = str(payload.get("publish_date") or "")
        if state.published_count_for_date(publish_date) >= settings.site.posts_per_day:
            result.outcomes.append(
                PublishOutcome(queue_id, topic_id, "skipped", error="daily quota is filled")
            )
            continue

        errors = validate_payload(payload, settings)
        if errors:
            message = "payload validation failed: " + "; ".join(errors)
            archive_failure(payload, settings, message)
            state.mark_failed(queue_id, error=message)
            result.outcomes.append(PublishOutcome(queue_id, topic_id, "failed", error=message))
            attempted += 1
            continue

        duplicate = _duplicate_reason(payload, state)
        if duplicate:
            archive_failure(payload, settings, duplicate)
            state.mark_failed(queue_id, error=duplicate)
            result.outcomes.append(PublishOutcome(queue_id, topic_id, "failed", error=duplicate))
            attempted += 1
            continue

        if dry_run:
            result.outcomes.append(PublishOutcome(queue_id, topic_id, "dry_run"))
            attempted += 1
            continue

        prior_attempts = int(pending.get("attempts") or 0)
        if prior_attempts:
            try:
                existing = client.find_recent_by_marker(topic_id)
            except BloggerError as exc:
                result.stopped = True
                result.error = f"could not check for an earlier partial publish: {exc}"
                break
            if existing:
                outcome = _record_existing(settings, state, payload, existing, result)
                result.outcomes.append(outcome)
                attempted += 1
                continue

        state.mark_inflight(queue_id, started_at=iso_now())
        try:
            post = client.insert_post(
                title=str(payload["post"]["title"]),
                content=str(payload["post"]["html"]),
                labels=list((payload.get("blogger") or {}).get("labels") or []),
                is_draft=settings.blogger.is_draft,
            )
        except BloggerAuthError as exc:
            state.release_inflight(queue_id, error=str(exc))
            result.stopped = True
            result.error = str(exc)
            result.outcomes.append(PublishOutcome(queue_id, topic_id, "failed", error=str(exc)))
            break
        except BloggerDriftError as exc:
            state.release_inflight(queue_id, error=str(exc))
            result.stopped = True
            result.error = str(exc)
            result.outcomes.append(PublishOutcome(queue_id, topic_id, "failed", error=str(exc)))
            break
        except BloggerError as exc:
            attempts = prior_attempts + 1
            if exc.retryable and attempts < settings.blogger.max_attempts:
                next_at = (moment + timedelta(minutes=15 * attempts)).replace(microsecond=0).isoformat()
                state.requeue(queue_id, error=str(exc), next_attempt_at=next_at)
                result.outcomes.append(
                    PublishOutcome(
                        queue_id,
                        topic_id,
                        "retry_scheduled",
                        error=f"{exc} (next attempt {next_at})",
                    )
                )
            else:
                archive_failure(payload, settings, str(exc))
                state.mark_failed(queue_id, error=str(exc))
                result.outcomes.append(
                    PublishOutcome(queue_id, topic_id, "failed", error=str(exc))
                )
            attempted += 1
            continue

        outcome = _record_success(settings, state, payload, post, result)
        result.outcomes.append(outcome)
        attempted += 1

    return result


def _record_success(
    settings: Settings,
    state: StateStore,
    payload: dict[str, Any],
    post: dict[str, Any],
    result: PublishResult,
) -> PublishOutcome:
    queue_id = str(payload["queue_id"])
    topic_id = str(payload["topic_id"])
    status = str(post.get("status") or ("draft" if settings.blogger.is_draft else "live"))
    published_at = str(post.get("published") or "") or (
        iso_now() if status == "live" else None
    )
    record = _publication_record(payload, post, status=status, published_at=published_at)
    state.mark_published(record)
    state.save()
    move_to_archive(
        payload,
        settings,
        kind="published",
        extra={"publication": record},
    )
    return PublishOutcome(
        queue_id,
        topic_id,
        "published" if status == "live" else "draft",
        post_id=str(post.get("id") or ""),
        url=str(post.get("url") or ""),
    )


def _record_existing(
    settings: Settings,
    state: StateStore,
    payload: dict[str, Any],
    post: dict[str, Any],
    result: PublishResult,
) -> PublishOutcome:
    queue_id = str(payload["queue_id"])
    topic_id = str(payload["topic_id"])
    status = str(post.get("status") or "live")
    published_at = str(post.get("published") or "") or iso_now()
    record = _publication_record(payload, post, status=status, published_at=published_at)
    state.mark_published(record)
    state.save()
    move_to_archive(payload, settings, kind="published", extra={"publication": record})
    return PublishOutcome(
        queue_id,
        topic_id,
        "published" if status == "live" else "draft",
        post_id=str(post.get("id") or ""),
        url=str(post.get("url") or ""),
        error="reconciled from an earlier partial publish",
    )


def _record_existing_from_state(
    state: StateStore,
    item: dict[str, Any],
    post: dict[str, Any],
) -> PublishOutcome:
    """Reconcile a Blogger post when its local queue payload is already gone."""

    queue_id = str(item.get("queue_id") or "")
    topic_id = str(item.get("topic_id") or "")
    status = str(post.get("status") or "live")
    record = {
        "queue_id": queue_id,
        "topic_id": topic_id,
        "title": str(item.get("title") or ""),
        "main_category": str(item.get("main_category") or ""),
        "content_type": str(item.get("content_type") or ""),
        "publish_date": str(item.get("publish_date") or ""),
        "slot": item.get("slot"),
        "blogger_post_id": str(post.get("id") or ""),
        "url": str(post.get("url") or ""),
        "blogger_status": status,
        "published_at": str(post.get("published") or "") or iso_now(),
        "quality_score": item.get("quality_score"),
        "labels": item.get("labels") or [],
        "signature": item.get("signature") or "",
        "fingerprint": item.get("fingerprint") or "",
        "primary_keyword": item.get("primary_keyword") or "",
        "aliases": item.get("aliases") or [],
    }
    state.mark_published(record)
    state.save()
    return PublishOutcome(
        queue_id,
        topic_id,
        "published" if status == "live" else "draft",
        post_id=str(post.get("id") or ""),
        url=str(post.get("url") or ""),
        error="reconciled from an earlier partial publish (payload already archived)",
    )


def _publication_record(
    payload: dict[str, Any],
    post: dict[str, Any],
    *,
    status: str,
    published_at: str | None,
) -> dict[str, Any]:
    fingerprint = payload.get("fingerprint") or {}
    return {
        "queue_id": payload["queue_id"],
        "topic_id": payload["topic_id"],
        "title": payload["post"]["title"],
        "main_category": payload["post"]["main_category"],
        "content_type": payload["post"]["content_type"],
        "publish_date": payload["publish_date"],
        "slot": payload.get("slot"),
        "blogger_post_id": str(post.get("id") or ""),
        "url": str(post.get("url") or ""),
        "blogger_status": status,
        "published_at": published_at,
        "quality_score": (payload.get("quality") or {}).get("score"),
        "labels": (payload.get("blogger") or {}).get("labels") or [],
        "signature": fingerprint.get("signature") or "",
        "fingerprint": fingerprint.get("sha256") or "",
        "primary_keyword": fingerprint.get("primary_keyword") or "",
        "aliases": fingerprint.get("aliases") or [],
    }


def _reconcile_inflight(
    settings: Settings,
    state: StateStore,
    client: BloggerClient,
    result: PublishResult,
    moment: datetime,
) -> None:
    items = [item for item in state.pending if item.get("status") == "in_flight"]
    if not items:
        return
    for item in items:
        queue_id = str(item.get("queue_id") or "")
        topic_id = str(item.get("topic_id") or "")
        existing = client.find_recent_by_marker(topic_id)
        path = payload_path(settings, queue_id)
        payload = load_payload(path) if path.exists() else None
        if existing:
            outcome = (
                _record_existing(settings, state, payload, existing, result)
                if payload
                else _record_existing_from_state(state, item, existing)
            )
            result.reconciled.append(outcome)
        else:
            state.release_inflight(
                queue_id,
                error="previous attempt did not complete and no Blogger post was found",
            )
            result.reconciled.append(
                PublishOutcome(queue_id, topic_id, "requeued", error="no partial post found")
            )
    state.save()


def _duplicate_reason(payload: dict[str, Any], state: StateStore) -> str:
    topic_id = str(payload.get("topic_id") or "")
    if state.find_published(topic_id=topic_id):
        return f"topic '{topic_id}' is already published"
    fingerprint = payload.get("fingerprint") or {}
    signature = str(fingerprint.get("signature") or "")
    keyword = normalize_signature(str(fingerprint.get("primary_keyword") or ""))
    aliases = {
        normalize_signature(item)
        for item in (fingerprint.get("aliases") or [])
        if str(item).strip()
    }
    for record in state.published:
        if record.get("signature") and record.get("signature") == signature:
            return f"identical editorial signature to '{record.get('topic_id')}'"
        if keyword and keyword == normalize_signature(str(record.get("primary_keyword") or "")):
            return f"same primary keyword as '{record.get('topic_id')}'"
        record_aliases = {
            normalize_signature(item)
            for item in (record.get("aliases") or [])
            if str(item).strip()
        }
        shared = aliases & record_aliases
        if shared:
            return f"shared alias '{sorted(shared)[0]}' with '{record.get('topic_id')}'"
    return ""


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
