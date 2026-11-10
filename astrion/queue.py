"""Queue payload contract shared by build and publish workflows."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .models import Topic
from .planner import PlannedPost
from .quality import check_html
from .text import contains_hangul, html_to_text, iso_now, sha256_text

SCHEMA = "astrion.queue/v1"
QUEUE_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-s\d+-[a-z0-9-]+$")
TOPIC_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+$")


def attach_queue_marker(html: str, queue_id: str) -> str:
    if "data-astrion-queue=" in html:
        return html
    marker = f' data-astrion-queue="{queue_id}"'
    pattern = re.compile(r'(<div class="astrion-post"[^>]*)(>)')
    return pattern.sub(lambda match: match.group(1) + marker + match.group(2), html, count=1)


def build_payload(planned: PlannedPost, settings: Settings) -> dict[str, Any]:
    topic = planned.topic
    html = attach_queue_marker(planned.rendered.html, planned.queue_id)
    labels = [topic.main_category.value, *topic.labels]
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "queue_id": planned.queue_id,
        "topic_id": topic.id,
        "status": "queued",
        "created_at": iso_now(),
        "publish_date": planned.publish_date,
        "publish_after": planned.publish_after,
        "slot": planned.slot,
        "blogger": {
            "is_draft": settings.blogger.is_draft,
            "labels": labels,
        },
        "post": {
            "title": topic.title,
            "html": html,
            "html_sha256": sha256_text(html),
            "meta_description": topic.meta_description,
            "primary_keyword": topic.primary_keyword,
            "content_type": topic.content_type.value,
            "main_category": topic.main_category.value,
            "slug": topic.slug,
        },
        "fingerprint": {
            "signature": topic.signature_text,
            "sha256": topic.fingerprint,
            "topic_key": topic.id,
            "primary_keyword": topic.primary_keyword,
            "aliases": list(topic.aliases),
        },
        "quality": planned.quality.to_dict(),
        "review": {
            "reviewed_at": topic.provenance.reviewed_at.isoformat(),
            "review_status": topic.provenance.review_status,
            "fact_basis": topic.provenance.fact_basis,
            "authored_by": topic.provenance.authored_by,
        },
        "sources": [
            {
                "id": source.id,
                "title": source.title,
                "publisher": source.publisher,
                "url": source.url,
                "kind": source.kind.value,
                "published_at": source.published_at.isoformat() if source.published_at else None,
                "accessed_at": source.accessed_at.isoformat(),
            }
            for source in [*topic.sources, *topic.further_reading]
        ],
    }
    return payload


def payload_path(settings: Settings, queue_id: str) -> Path:
    return settings.queue_dir / f"{queue_id}.json"


def save_payload(payload: dict[str, Any], settings: Settings) -> Path:
    path = payload_path(settings, str(payload["queue_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def load_payload(path: Path | str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"queue payload must be a JSON object: {path}")
    return payload


def list_queue(settings: Settings) -> list[dict[str, Any]]:
    if not settings.queue_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(settings.queue_dir.glob("*.json")):
        try:
            payload = load_payload(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            items.append({"__path": str(path), "__error": str(exc), "queue_id": path.stem})
            continue
        payload["__path"] = str(path)
        items.append(payload)
    return items


def validate_payload(payload: dict[str, Any], settings: Settings) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        errors.append(f"unsupported schema: {payload.get('schema')!r}")
    queue_id = str(payload.get("queue_id") or "")
    if not QUEUE_ID_RE.match(queue_id):
        errors.append(f"invalid queue_id: {queue_id!r}")
    if not TOPIC_ID_RE.match(str(payload.get("topic_id") or "")):
        errors.append("invalid topic_id")
    date_value = str(payload.get("publish_date") or "")
    try:
        date.fromisoformat(date_value)
    except ValueError:
        errors.append(f"invalid publish_date: {date_value!r}")
    try:
        datetime.fromisoformat(str(payload.get("publish_after") or ""))
    except ValueError:
        errors.append("invalid publish_after timestamp")

    post = payload.get("post") if isinstance(payload.get("post"), dict) else {}
    html = str(post.get("html") or "")
    if not html:
        errors.append("post.html is empty")
    elif post.get("html_sha256") != sha256_text(html):
        errors.append("post.html checksum mismatch")
    title = str(post.get("title") or "")
    if not 20 <= len(title) <= 80:
        errors.append("post.title length is outside 20..80")
    meta = str(post.get("meta_description") or "")
    if not 80 <= len(meta) <= 160:
        errors.append("post.meta_description length is outside 80..160")
    if html:
        if contains_hangul(html_to_text(html)):
            errors.append("post contains Korean text")
        ok, html_errors, _ = check_html(html)
        if not ok:
            errors.extend(f"HTML: {error}" for error in html_errors)
        if len(html.encode("utf-8")) > 400_000:
            errors.append("post HTML exceeds 400 KB")

    quality = payload.get("quality") if isinstance(payload.get("quality"), dict) else {}
    if quality.get("decision") != "PUBLISH":
        errors.append("quality decision is not PUBLISH")
    try:
        score = float(quality.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    if score < settings.quality.publish_threshold:
        errors.append(f"quality score {score} is below threshold")

    labels = (payload.get("blogger") or {}).get("labels") or []
    if not isinstance(labels, list) or not labels:
        errors.append("blogger.labels must contain the main category")
    elif len(labels) > 1 + settings.blogger.max_secondary_labels:
        errors.append("too many labels")
    if labels and labels[0] not in _category_values():
        errors.append(f"first label is not a main category: {labels[0]!r}")

    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    if review.get("review_status") != "approved":
        errors.append("review_status is not approved")
    if payload.get("status") != "queued":
        errors.append(f"unexpected queue status: {payload.get('status')!r}")
    return errors


def move_to_archive(
    payload: dict[str, Any],
    settings: Settings,
    *,
    kind: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    queue_path = payload_path(settings, str(payload["queue_id"]))
    month = str(payload.get("publish_date") or date.today().isoformat())[:7]
    out_dir = settings.archive_dir / kind / month
    out_dir.mkdir(parents=True, exist_ok=True)
    archived = {
        key: value for key, value in payload.items() if not str(key).startswith("__")
    }
    archived["status"] = kind
    if extra:
        archived.update(extra)
    json_path = out_dir / f"{payload['queue_id']}.json"
    json_path.write_text(json.dumps(archived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html = str((payload.get("post") or {}).get("html") or "")
    if html:
        (out_dir / f"{payload['queue_id']}.html").write_text(html, encoding="utf-8")
    if queue_path.exists():
        queue_path.unlink()
    return json_path


def archive_failure(payload: dict[str, Any], settings: Settings, error: str) -> Path:
    path = move_to_archive(payload, settings, kind="failed", extra={"error": error[:2000]})
    (path.parent / f"{payload['queue_id']}.error.txt").write_text(error, encoding="utf-8")
    return path


def _category_values() -> set[str]:
    from .models import Category

    return {category.value for category in Category}


def due_payloads(settings: Settings, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    for payload in list_queue(settings):
        if "__error" in payload:
            items.append(payload)
            continue
        try:
            due = datetime.fromisoformat(str(payload.get("publish_after") or ""))
        except ValueError:
            items.append(payload)
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= moment:
            items.append(payload)
    items.sort(key=lambda item: str(item.get("publish_after") or "0000"))
    return items


def queue_summary(settings: Settings) -> dict[str, Any]:
    items = list_queue(settings)
    valid = [item for item in items if "__error" not in item]
    return {
        "queued": len(valid),
        "invalid": len(items) - len(valid),
        "next": min((str(item.get("publish_after")) for item in valid), default=""),
        "topics": [item.get("topic_id") for item in valid],
    }


def labels_for_topic(topic: Topic) -> list[str]:
    return [topic.main_category.value, *topic.labels]
