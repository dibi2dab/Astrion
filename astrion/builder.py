"""Build phase: select topics, render HTML and reserve queue payloads."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .config import Settings
from .loader import LoadedTopic
from .planner import PlanResult, PlannedPost, SkippedTopic, build_plan
from .queue import build_payload, payload_path, save_payload
from .state import StateStore
from .text import iso_now


@dataclass
class BuildResult:
    publish_date: str
    plan: PlanResult
    written: list[str] = field(default_factory=list)
    dry_run: bool = False
    report_path: str = ""

    @property
    def count(self) -> int:
        return len(self.written)

    def to_dict(self) -> dict[str, Any]:
        return {
            "publish_date": self.publish_date,
            "dry_run": self.dry_run,
            "written": self.written,
            "plan": self.plan.to_dict(),
        }


def build(
    loaded: list[LoadedTopic],
    settings: Settings,
    state: StateStore,
    *,
    publish_date: str | None = None,
    limit: int | None = None,
    topic_id: str | None = None,
    dry_run: bool = False,
) -> BuildResult:
    plan = build_plan(
        loaded,
        settings,
        state,
        publish_date=publish_date,
        limit=limit,
        topic_id=topic_id,
    )
    result = BuildResult(publish_date=plan.publish_date, plan=plan, dry_run=dry_run)
    if plan.error:
        return result
    if dry_run:
        return result

    build_id = f"{plan.publish_date}-{iso_now().replace(':', '').replace('-', '')}"
    pending_records: list[dict[str, Any]] = []
    for item in plan.items:
        payload = build_payload(item, settings)
        target = payload_path(settings, item.queue_id)
        if target.exists() and state.find_pending(item.queue_id) is None:
            plan.skipped.append(SkippedTopic(item.queue_id, "queue file already exists"))
            continue
        save_payload(payload, settings)
        _write_preview(settings, item)
        result.written.append(item.queue_id)
        pending_records.append(_pending_record(item))

    if pending_records:
        for record in pending_records:
            state.add_pending(record)
        state.record_build(
            {
                "build_id": build_id,
                "created_at": iso_now(),
                "publish_date": plan.publish_date,
                "queue_ids": result.written,
            }
        )
        state.save()

    settings.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = settings.report_dir / f"build-{build_id}.json"
    report_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result.report_path = str(report_path)
    return result


def _pending_record(item: PlannedPost) -> dict[str, Any]:
    topic = item.topic
    return {
        "queue_id": item.queue_id,
        "topic_id": topic.id,
        "title": topic.title,
        "main_category": topic.main_category.value,
        "content_type": topic.content_type.value,
        "publish_date": item.publish_date,
        "publish_after": item.publish_after,
        "slot": item.slot,
        "status": "queued",
        "built_at": iso_now(),
        "quality_score": item.quality.score,
        "attempts": 0,
        "signature": topic.signature_text,
        "fingerprint": topic.fingerprint,
        "primary_keyword": topic.primary_keyword,
        "aliases": list(topic.aliases),
        "labels": [topic.main_category.value, *topic.labels],
    }


def _write_preview(settings: Settings, item: PlannedPost) -> None:
    settings.preview_dir.mkdir(parents=True, exist_ok=True)
    (settings.preview_dir / f"{item.queue_id}.html").write_text(
        item.rendered.html, encoding="utf-8"
    )
