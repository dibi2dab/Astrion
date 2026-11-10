"""Local state: queue reservations and published-post history.

GitHub Actions commits this file between runs. It is the idempotency ledger for
the publish workflow; Blogger itself remains the source of truth for post IDs,
URLs and live status.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .text import iso_now

SCHEMA = "astrion.state/v1"


def empty_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "updated_at": iso_now(),
        "published": [],
        "pending": [],
        "failed": [],
        "builds": [],
    }


class StateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_state()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"state file is unreadable: {self.path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"state file must contain a JSON object: {self.path}")
        state = empty_state()
        state.update(payload)
        for key in ("published", "pending", "failed", "builds"):
            if not isinstance(state.get(key), list):
                state[key] = []
        return state

    def save(self) -> None:
        self.data["updated_at"] = iso_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

    def reload(self) -> None:
        self.data = self._load()

    @property
    def published(self) -> list[dict[str, Any]]:
        return self.data["published"]

    @property
    def pending(self) -> list[dict[str, Any]]:
        return self.data["pending"]

    @property
    def failed(self) -> list[dict[str, Any]]:
        return self.data["failed"]

    def published_topic_ids(self) -> set[str]:
        return {str(item.get("topic_id")) for item in self.published if item.get("topic_id")}

    def queued_topic_ids(self) -> set[str]:
        return {
            str(item.get("topic_id"))
            for item in self.pending
            if item.get("topic_id") and item.get("status") in {"queued", "in_flight"}
        }

    def pending_queue_ids(self) -> set[str]:
        return {
            str(item.get("queue_id"))
            for item in self.pending
            if item.get("queue_id") and item.get("status") in {"queued", "in_flight"}
        }

    def records_for_date(self, publish_date: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        records.extend(
            item
            for item in self.pending
            if item.get("publish_date") == publish_date
            and item.get("status") in {"queued", "in_flight"}
        )
        records.extend(
            item for item in self.published if item.get("publish_date") == publish_date
        )
        return records

    def count_for_date(self, publish_date: str) -> int:
        return len(self.records_for_date(publish_date))

    def used_slots(self, publish_date: str) -> set[int]:
        slots: set[int] = set()
        for item in self.records_for_date(publish_date):
            try:
                slots.add(int(item.get("slot")))
            except (TypeError, ValueError):
                continue
        return slots

    def categories_for_date(self, publish_date: str) -> set[str]:
        return {
            str(item.get("main_category"))
            for item in self.records_for_date(publish_date)
            if item.get("main_category")
        }

    def history(self) -> list[dict[str, Any]]:
        return [*self.published, *self.pending]

    def related_urls(self) -> dict[str, dict[str, str]]:
        items: dict[str, dict[str, str]] = {}
        for record in self.published:
            topic_id = str(record.get("topic_id") or "")
            url = str(record.get("url") or "")
            if topic_id and url and topic_id not in items:
                items[topic_id] = {"title": str(record.get("title") or topic_id), "url": url}
        return items

    def days_since_category(self, category: str, publish_date: date) -> int:
        dates: list[date] = []
        for record in self.published:
            if record.get("main_category") != category:
                continue
            value = record.get("publish_date") or str(record.get("published_at") or "")[:10]
            try:
                dates.append(date.fromisoformat(str(value)))
            except ValueError:
                continue
        if not dates:
            return 3650
        return max(0, (publish_date - max(dates)).days)

    def add_pending(self, record: dict[str, Any]) -> None:
        queue_id = str(record.get("queue_id") or "")
        self.data["pending"] = [
            item for item in self.pending if str(item.get("queue_id") or "") != queue_id
        ]
        self.data["pending"].append(record)

    def mark_published(self, record: dict[str, Any]) -> None:
        queue_id = str(record.get("queue_id") or "")
        topic_id = str(record.get("topic_id") or "")
        self.data["pending"] = [
            item
            for item in self.pending
            if str(item.get("queue_id") or "") != queue_id
            and str(item.get("topic_id") or "") != topic_id
        ]
        self.data["failed"] = [
            item
            for item in self.failed
            if str(item.get("queue_id") or "") != queue_id
        ]
        self.data["published"] = [
            item
            for item in self.published
            if str(item.get("queue_id") or "") != queue_id
            and str(item.get("topic_id") or "") != topic_id
        ]
        self.data["published"].append(record)

    def find_pending(self, queue_id: str) -> dict[str, Any] | None:
        for item in self.pending:
            if str(item.get("queue_id")) == queue_id:
                return item
        return None

    def find_published(self, *, queue_id: str = "", topic_id: str = "") -> dict[str, Any] | None:
        for item in self.published:
            if queue_id and str(item.get("queue_id")) == queue_id:
                return item
            if topic_id and str(item.get("topic_id")) == topic_id:
                return item
        return None

    def mark_inflight(self, queue_id: str, *, started_at: str) -> dict[str, Any] | None:
        item = self.find_pending(queue_id)
        if item is None:
            return None
        item["status"] = "in_flight"
        item["started_at"] = started_at
        item["last_error"] = ""
        self.save()
        return item

    def requeue(self, queue_id: str, *, error: str, next_attempt_at: str) -> None:
        item = self.find_pending(queue_id)
        if item is None:
            return
        item["status"] = "queued"
        item["last_error"] = error[:1000]
        item["next_attempt_at"] = next_attempt_at
        item["attempts"] = int(item.get("attempts") or 0) + 1
        self.save()

    def release_inflight(self, queue_id: str, *, error: str) -> None:
        item = self.find_pending(queue_id)
        if item is None:
            return
        item["status"] = "queued"
        item["last_error"] = error[:1000]
        self.save()

    def published_count_for_date(self, publish_date: str) -> int:
        return sum(
            1 for item in self.published if item.get("publish_date") == publish_date
        )

    def mark_failed(self, queue_id: str, *, error: str) -> None:
        item = self.find_pending(queue_id)
        record = dict(item or {"queue_id": queue_id})
        record["status"] = "failed"
        record["last_error"] = error[:1000]
        record["failed_at"] = iso_now()
        self.data["pending"] = [
            pending for pending in self.pending if str(pending.get("queue_id")) != queue_id
        ]
        self.data["failed"] = [
            pending for pending in self.failed if str(pending.get("queue_id")) != queue_id
        ]
        self.data["failed"].append(record)
        self.save()

    def record_build(self, record: dict[str, Any]) -> None:
        self.data["builds"].append(record)

    def summary(self) -> dict[str, Any]:
        pending = [
            item for item in self.pending if item.get("status") in {"queued", "in_flight"}
        ]
        dates = sorted(
            {
                str(item.get("publish_date"))
                for item in [*self.published, *pending]
                if item.get("publish_date")
            }
        )
        categories: dict[str, int] = {}
        for record in self.published:
            category = str(record.get("main_category") or "unknown")
            categories[category] = categories.get(category, 0) + 1
        return {
            "published": len(self.published),
            "pending": len(pending),
            "failed": len(self.failed),
            "first_date": dates[0] if dates else "",
            "last_date": dates[-1] if dates else "",
            "categories": categories,
            "pending_items": sorted(pending, key=lambda item: str(item.get("publish_after") or "")),
            "recent_published": sorted(
                self.published,
                key=lambda item: str(item.get("published_at") or ""),
                reverse=True,
            )[:5],
        }

    def merge_builds(self, builds: Iterable[dict[str, Any]]) -> None:
        known = {str(item.get("build_id")) for item in self.data["builds"]}
        for build in builds:
            if str(build.get("build_id")) in known:
                continue
            self.data["builds"].append(build)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)
