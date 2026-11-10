"""Deterministic daily topic selection and slot assignment."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .config import Settings
from .duplicate import FingerprintRecord, find_conflicts, record_from_state, unresolved
from .loader import LoadedTopic
from .models import Topic
from .quality import QualityReport, evaluate
from .render import RenderedPost, render_post
from .state import StateStore


@dataclass
class PlannedPost:
    topic: Topic
    quality: QualityReport
    rendered: RenderedPost
    queue_id: str
    slot: int
    publish_date: str
    publish_after: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic.id,
            "title": self.topic.title,
            "main_category": self.topic.main_category.value,
            "content_type": self.topic.content_type.value,
            "queue_id": self.queue_id,
            "slot": self.slot,
            "publish_date": self.publish_date,
            "publish_after": self.publish_after,
            "quality_score": self.quality.score,
            "decision": self.quality.decision,
        }


@dataclass
class SkippedTopic:
    topic_id: str
    reason: str
    decision: str = ""
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "reason": self.reason,
            "decision": self.decision,
            "score": self.score,
        }


@dataclass
class PlanResult:
    publish_date: str
    items: list[PlannedPost] = field(default_factory=list)
    skipped: list[SkippedTopic] = field(default_factory=list)
    reason: str = ""
    existing: int = 0
    remaining: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "publish_date": self.publish_date,
            "existing": self.existing,
            "remaining": self.remaining,
            "reason": self.reason,
            "error": self.error,
            "items": [item.to_dict() for item in self.items],
            "skipped": [item.to_dict() for item in self.skipped],
        }


def build_plan(
    loaded: list[LoadedTopic],
    settings: Settings,
    state: StateStore,
    *,
    publish_date: str | None = None,
    limit: int | None = None,
    topic_id: str | None = None,
    now: datetime | None = None,
) -> PlanResult:
    tz = settings.tz
    moment = (now or datetime.now(timezone.utc)).astimezone(tz)
    try:
        target = date.fromisoformat(publish_date) if publish_date else moment.date()
    except ValueError as exc:
        raise ValueError(f"invalid --date value: {publish_date}") from exc
    target_key = target.isoformat()

    existing = state.count_for_date(target_key)
    quota = settings.site.posts_per_day
    remaining = max(0, quota - existing)
    if limit is not None:
        remaining = min(remaining, max(0, limit))

    result = PlanResult(
        publish_date=target_key,
        existing=existing,
        remaining=remaining,
    )
    if remaining == 0:
        result.reason = f"daily quota already filled ({existing}/{quota})"
        return result

    published_ids = state.published_topic_ids()
    queued_ids = state.queued_topic_ids()
    history = [record for record in (record_from_state(item) for item in state.history()) if record]

    if topic_id:
        selected = [item for item in loaded if item.topic.id == topic_id]
        if not selected:
            result.error = f"topic not found: {topic_id}"
            return result
        candidates = selected
    else:
        candidates = loaded

    evaluated: list[tuple[Topic, QualityReport, RenderedPost]] = []
    related_urls = state.related_urls()
    for item in candidates:
        topic = item.topic
        if topic_id is None and topic.status != "active":
            result.skipped.append(SkippedTopic(topic.id, f"status is {topic.status}"))
            continue
        if topic.status != "active":
            result.skipped.append(SkippedTopic(topic.id, f"status is {topic.status}"))
            continue
        if topic.id in published_ids:
            result.skipped.append(SkippedTopic(topic.id, "already published"))
            continue
        if topic.id in queued_ids:
            result.skipped.append(SkippedTopic(topic.id, "already queued"))
            continue
        if topic.provenance.review_status != "approved":
            result.skipped.append(
                SkippedTopic(topic.id, "editorial review is not approved", "BLOCK")
            )
            continue

        rendered = render_post(topic, related_urls=related_urls)
        quality = evaluate(topic, settings, rendered=rendered, published_records=history)
        if quality.decision != "PUBLISH":
            reason = "; ".join(quality.fatal or quality.warnings) or "quality gate did not pass"
            result.skipped.append(
                SkippedTopic(topic.id, reason, quality.decision, quality.score)
            )
            continue
        evaluated.append((topic, quality, rendered))

    if not evaluated:
        result.reason = result.reason or "no eligible topics passed the quality gate"
        return result

    used_categories = state.categories_for_date(target_key)
    used_slots = state.used_slots(target_key)
    chosen: list[PlannedPost] = []
    chosen_records: list[FingerprintRecord] = []

    while evaluated and len(chosen) < remaining:
        evaluated.sort(
            key=lambda entry: _rank(entry[0], used_categories, target, state)
        )
        topic, quality, rendered = evaluated.pop(0)

        slot = _next_slot(used_slots, settings.site.posts_per_day)
        if slot is None:
            result.reason = "all publication slots for the day are occupied"
            break

        conflicts = unresolved(
            find_conflicts(topic, [*history, *chosen_records], settings.duplicates)
        )
        if conflicts:
            result.skipped.append(
                SkippedTopic(
                    topic.id,
                    "duplicate of selected topic: "
                    + ", ".join(match.record.topic_id for match in conflicts),
                    quality.decision,
                    quality.score,
                )
            )
            continue

        publish_after = _publish_after(
            target,
            settings.site.publish_slots[slot],
            moment,
            tz,
            offset_minutes=2 * len(chosen),
        )
        queue_id = f"{target_key}-s{slot + 1}-{topic.slug[:50].rstrip('-')}"
        planned = PlannedPost(
            topic=topic,
            quality=quality,
            rendered=rendered,
            queue_id=queue_id,
            slot=slot,
            publish_date=target_key,
            publish_after=publish_after,
        )
        chosen.append(planned)
        chosen_records.append(
            FingerprintRecord(
                topic_id=topic.id,
                title=topic.title,
                signature=topic.signature_text,
                primary_keyword=topic.primary_keyword,
                aliases=tuple(topic.aliases),
            )
        )
        used_slots.add(slot)
        used_categories.add(topic.main_category.value)

    result.items = chosen
    if not chosen and not result.reason:
        result.reason = "nothing remained after duplicate and slot checks"
    return result


def _rank(topic: Topic, used_categories: set[str], target: date, state: StateStore) -> tuple:
    category_used = 1 if topic.main_category.value in used_categories else 0
    category_gap = state.days_since_category(topic.main_category.value, target)
    return (category_used, -topic.priority, -category_gap, topic.id)


def _next_slot(used: set[int], total: int) -> int | None:
    for index in range(total):
        if index not in used:
            return index
    return None


def _publish_after(
    target: date,
    slot_value: str,
    now: datetime,
    tz,
    *,
    offset_minutes: int = 0,
) -> str:
    slot_time = time.fromisoformat(slot_value)
    scheduled = datetime.combine(target, slot_time, tzinfo=tz)
    floor = now.replace(second=0, microsecond=0) + timedelta(minutes=3 + offset_minutes)
    chosen = max(scheduled, floor)
    return chosen.isoformat()
