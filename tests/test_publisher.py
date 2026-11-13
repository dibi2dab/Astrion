from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from astrion.blogger import BloggerAuthError, BloggerDriftError, BloggerError
from astrion.builder import build
from astrion.publisher import publish_due
from astrion.queue import list_queue, load_payload, payload_path

TARGET = "2030-01-01"
AFTER_SLOTS = datetime(2030, 1, 2, 0, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, *, failures=None, existing=None):
        self.failures = list(failures or [])
        self.existing = existing
        self.inserted: list[dict] = []
        self.marker_queries: list[str] = []

    def insert_post(self, *, title, content, labels, is_draft):
        if self.failures:
            raise self.failures.pop(0)
        post = {
            "id": f"post-{len(self.inserted) + 1}",
            "url": f"https://astrion.blogspot.com/2030/01/post-{len(self.inserted) + 1}.html",
            "status": "draft" if is_draft else "live",
            "published": "" if is_draft else "2030-01-02T00:00:00-05:00",
        }
        self.inserted.append(
            {
                "title": title,
                "content": content,
                "labels": labels,
                "is_draft": is_draft,
                "post": post,
            }
        )
        return post

    def find_recent_by_marker(self, topic_id, *, title="", limit=50):
        self.marker_queries.append(topic_id)
        return self.existing


def _prepare(loaded, settings, state):
    result = build(loaded, settings, state, publish_date=TARGET)
    assert result.count == 2
    return result


def test_publish_inserts_drafts_and_archives(loaded, settings, state):
    _prepare(loaded, settings, state)
    client = FakeClient()

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=2)

    assert result.stopped is False
    assert result.published == 2
    assert [item.status for item in result.outcomes] == ["draft", "draft"]
    assert len(client.inserted) == 2
    for call in client.inserted:
        assert call["is_draft"] is True
        assert call["labels"][0] in {"Mathematics", "Physics"}
        assert 'data-astrion-queue="' in call["content"]

    assert state.pending == []
    assert len(state.published) == 2
    assert len(state.failed) == 0
    assert list_queue(settings) == []
    archived = list(settings.archive_dir.glob("published/*/*.json"))
    assert len(archived) == 2
    for path in archived:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "__path" not in payload
        assert payload["status"] == "published"
        assert payload["publication"]["blogger_post_id"]


def test_publish_respects_max_per_run(loaded, settings, state):
    _prepare(loaded, settings, state)
    client = FakeClient()

    result = publish_due(settings, state, client, now=AFTER_SLOTS)

    assert result.published == settings.blogger.max_per_run == 1
    assert len(client.inserted) == 1
    assert len(list_queue(settings)) == 1
    assert len(state.published) == 1
    assert len(state.pending) == 1


def test_live_mode_records_published_at(loaded, settings, state):
    _prepare(loaded, settings, state)
    settings = settings.model_copy(deep=True)
    settings.blogger.is_draft = False
    client = FakeClient()

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert result.outcomes[0].status == "published"
    assert state.published[0]["blogger_status"] == "live"
    assert state.published[0]["published_at"]


def test_dry_run_never_touches_blogger_or_state(loaded, settings, state):
    _prepare(loaded, settings, state)
    client = FakeClient()

    result = publish_due(
        settings, state, client, now=AFTER_SLOTS, max_posts=2, dry_run=True
    )

    assert [item.status for item in result.outcomes] == ["dry_run", "dry_run"]
    assert client.inserted == []
    assert len(state.published) == 0
    assert len(state.pending) == 2
    assert len(list_queue(settings)) == 2


def test_retryable_error_schedules_a_retry(loaded, settings, state):
    _prepare(loaded, settings, state)
    client = FakeClient(
        failures=[BloggerError("temporary outage", status=503, retryable=True)]
    )

    first = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert first.outcomes[0].status == "retry_scheduled"
    assert len(state.published) == 0
    pending = state.pending[0]
    assert pending["status"] == "queued"
    assert pending["attempts"] == 1
    assert pending["next_attempt_at"] > AFTER_SLOTS.isoformat()
    assert len(list_queue(settings)) == 2

    second = publish_due(
        settings,
        state,
        client,
        now=AFTER_SLOTS + timedelta(minutes=1),
        max_posts=2,
    )
    assert second.outcomes[0].status == "skipped"
    assert "retry scheduled" in second.outcomes[0].error
    assert len(client.inserted) == 1  # the other slot is not blocked by this retry

    third = publish_due(
        settings,
        state,
        client,
        now=AFTER_SLOTS + timedelta(hours=2),
        max_posts=2,
    )
    assert any(
        item.status == "draft" and item.queue_id == pending["queue_id"]
        for item in third.outcomes
    )
    assert len(state.published) == 2


def test_auth_error_stops_without_archiving(loaded, settings, state):
    _prepare(loaded, settings, state)
    client = FakeClient(failures=[BloggerAuthError("bad token", status=401)])

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=2)

    assert result.stopped is True
    assert "bad token" in result.error
    assert result.outcomes[0].status == "failed"
    assert state.pending[0]["status"] == "queued"
    assert len(list_queue(settings)) == 2
    assert list(settings.archive_dir.glob("failed/*/*.json")) == []


def test_drift_error_stops_without_archiving(loaded, settings, state):
    _prepare(loaded, settings, state)
    client = FakeClient(failures=[BloggerDriftError("response shape changed")])

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=2)

    assert result.stopped is True
    assert result.outcomes[0].status == "failed"
    assert len(list_queue(settings)) == 2


def test_non_retryable_error_archives_the_payload(loaded, settings, state):
    _prepare(loaded, settings, state)
    client = FakeClient(failures=[BloggerError("bad request", status=400)])

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert result.outcomes[0].status == "failed"
    assert len(state.failed) == 1
    assert len(list_queue(settings)) == 1
    failures = list(settings.archive_dir.glob("failed/*/*.json"))
    assert len(failures) == 1
    assert (failures[0].parent / f"{failures[0].stem}.error.txt").exists()


def test_invalid_payload_is_archived_before_any_api_call(loaded, settings, state):
    _prepare(loaded, settings, state)
    item = list_queue(settings)[0]
    payload = load_payload(item["__path"])
    payload["quality"]["decision"] = "BLOCK"
    payload_path(settings, payload["queue_id"]).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    client = FakeClient()

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert result.outcomes[0].status == "failed"
    assert "validation failed" in result.outcomes[0].error
    assert client.inserted == []
    assert len(state.failed) == 1
    assert len(list(settings.archive_dir.glob("failed/*/*.json"))) == 1


def test_duplicate_primary_keyword_blocks_publication(loaded, settings, state):
    _prepare(loaded, settings, state)
    item = list_queue(settings)[0]
    state.data["published"].append(
        {
            "queue_id": "earlier",
            "topic_id": "math.earlier",
            "title": "An earlier article",
            "primary_keyword": item["post"]["primary_keyword"],
            "signature": "a different signature",
            "aliases": [],
            "publish_date": "2029-12-31",
        }
    )
    client = FakeClient()

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert result.outcomes[0].status == "failed"
    assert "primary keyword" in result.outcomes[0].error
    assert client.inserted == []
    assert len(list(settings.archive_dir.glob("failed/*/*.json"))) == 1


def test_daily_quota_is_enforced(loaded, settings, state):
    _prepare(loaded, settings, state)
    for index in range(settings.site.posts_per_day):
        state.data["published"].append(
            {
                "queue_id": f"filled-{index}",
                "topic_id": f"math.filled-{index}",
                "publish_date": TARGET,
            }
        )
    client = FakeClient()

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=2)

    assert all("quota" in item.error for item in result.outcomes)
    assert client.inserted == []
    assert len(list_queue(settings)) == 2


def test_inflight_reconciliation_records_existing_post(loaded, settings, state):
    _prepare(loaded, settings, state)
    item = list_queue(settings)[0]
    state.mark_inflight(item["queue_id"], started_at="2030-01-02T00:00:00+00:00")
    existing = {
        "id": "existing-post",
        "url": "https://astrion.blogspot.com/2030/01/existing.html",
        "status": "live",
        "published": "2030-01-02T00:01:00-05:00",
        "content": f'data-astrion-topic="{item["topic_id"]}"',
    }
    client = FakeClient(existing=existing)

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert len(result.reconciled) == 1
    assert result.reconciled[0].status == "published"
    assert result.reconciled[0].post_id == "existing-post"
    assert state.published[0]["blogger_post_id"] == "existing-post"
    assert not any(entry["queue_id"] == item["queue_id"] for entry in state.pending)
    assert not payload_path(settings, item["queue_id"]).exists()


def test_inflight_reconciliation_works_without_the_payload(loaded, settings, state):
    _prepare(loaded, settings, state)
    item = list_queue(settings)[0]
    state.mark_inflight(item["queue_id"], started_at="2030-01-02T00:00:00+00:00")
    payload_path(settings, item["queue_id"]).unlink()
    existing = {
        "id": "existing-post",
        "url": "https://astrion.blogspot.com/2030/01/existing.html",
        "status": "live",
        "published": "",
    }
    client = FakeClient(existing=existing)

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert len(result.reconciled) == 1
    assert state.published[0]["topic_id"] == item["topic_id"]


def test_inflight_without_a_post_is_requeued(loaded, settings, state):
    _prepare(loaded, settings, state)
    item = list_queue(settings)[0]
    state.mark_inflight(item["queue_id"], started_at="2030-01-02T00:00:00+00:00")
    client = FakeClient(existing=None)

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert result.reconciled[0].status == "requeued"
    assert state.pending[0]["status"] == "queued"


def test_publish_one_queue_id_only(loaded, settings, state):
    _prepare(loaded, settings, state)
    items = list_queue(settings)
    target = items[1]["queue_id"]
    client = FakeClient()

    result = publish_due(
        settings, state, client, now=AFTER_SLOTS, only_queue_id=target
    )

    assert len(result.outcomes) == 1
    assert result.outcomes[0].queue_id == target
    assert len(list_queue(settings)) == 1
    assert list_queue(settings)[0]["queue_id"] == items[0]["queue_id"]


def test_unknown_queue_id_fails(settings, state):
    client = FakeClient()
    result = publish_due(
        settings, state, client, now=AFTER_SLOTS, only_queue_id="2030-01-01-s1-nope"
    )
    assert result.outcomes[0].status == "failed"
    assert "not found" in result.outcomes[0].error


def test_previous_attempt_reconciles_before_inserting(loaded, settings, state):
    _prepare(loaded, settings, state)
    item = list_queue(settings)[0]
    pending = state.find_pending(item["queue_id"])
    pending["attempts"] = 1
    existing = {
        "id": "earlier-attempt",
        "url": "https://astrion.blogspot.com/2030/01/earlier.html",
        "status": "draft",
        "published": "",
    }
    client = FakeClient(existing=existing)

    result = publish_due(settings, state, client, now=AFTER_SLOTS, max_posts=1)

    assert result.outcomes[0].post_id == "earlier-attempt"
    assert client.inserted == []
    assert state.published[0]["queue_id"] == item["queue_id"]
