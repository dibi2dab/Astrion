from __future__ import annotations

from datetime import datetime, timezone

from astrion.builder import build
from astrion.loader import LoadedTopic
from astrion.models import Topic
from astrion.planner import build_plan
from astrion.queue import list_queue, validate_payload

from .conftest import topic_payload

TARGET = "2030-01-01"
NOW = datetime(2029, 12, 31, 12, 0, tzinfo=timezone.utc)


def test_plan_fills_the_daily_quota(loaded, settings, state):
    result = build_plan(loaded, settings, state, publish_date=TARGET, now=NOW)
    assert result.error == ""
    assert result.existing == 0
    assert result.remaining == settings.site.posts_per_day
    assert len(result.items) == settings.site.posts_per_day
    assert {item.topic.main_category.value for item in result.items} == {
        "Mathematics",
        "Physics",
    }
    for item in result.items:
        assert item.quality.decision == "PUBLISH"
        assert item.queue_id.startswith(TARGET)
        assert item.publish_after.startswith(TARGET)
        assert item.publish_date == TARGET


def test_plan_respects_the_limit(loaded, settings, state):
    result = build_plan(loaded, settings, state, publish_date=TARGET, limit=1, now=NOW)
    assert len(result.items) == 1
    assert result.remaining == 1


def test_plan_uses_distinct_slots(loaded, settings, state):
    result = build_plan(loaded, settings, state, publish_date=TARGET, now=NOW)
    assert len({item.slot for item in result.items}) == len(result.items)
    assert {item.slot for item in result.items} == {0, 1}


def test_plan_skips_already_published_topics(loaded, settings, state):
    published = loaded[0].topic
    state.mark_published(
        {
            "queue_id": f"{TARGET}-s1-old",
            "topic_id": published.id,
            "title": published.title,
            "main_category": published.main_category.value,
            "publish_date": TARGET,
            "slot": 0,
            "published_at": "2029-12-31T00:00:00+00:00",
            "signature": published.signature_text,
            "primary_keyword": published.primary_keyword,
            "aliases": [],
        }
    )
    result = build_plan(loaded, settings, state, publish_date=TARGET, now=NOW)
    skipped_ids = {item.topic_id for item in result.skipped}
    assert published.id in skipped_ids
    assert len(result.items) == 1
    assert result.items[0].topic.id != published.id


def test_plan_skips_topics_that_are_not_approved(loaded, settings, state):
    payload = topic_payload(loaded[0].topic)
    payload["provenance"]["review_status"] = "draft"
    blocked = LoadedTopic(Topic.model_validate(payload), loaded[0].path)
    result = build_plan([blocked], settings, state, publish_date=TARGET, now=NOW)
    assert result.items == []
    assert result.reason
    assert "review" in result.reason or any(
        "review" in item.reason for item in result.skipped
    )


def test_plan_unknown_topic_is_an_error(loaded, settings, state):
    result = build_plan(
        loaded, settings, state, publish_date=TARGET, topic_id="math.nope", now=NOW
    )
    assert result.error
    assert result.items == []


def test_plan_when_quota_is_full(loaded, settings, state):
    for index in range(settings.site.posts_per_day):
        state.mark_published(
            {
                "queue_id": f"{TARGET}-s{index + 1}-filled",
                "topic_id": f"math.filled-{index}",
                "title": "Filled slot",
                "main_category": "Mathematics",
                "publish_date": TARGET,
                "slot": index,
                "published_at": "2029-12-31T00:00:00+00:00",
                "signature": f"filled signature {index}",
            }
        )
    result = build_plan(loaded, settings, state, publish_date=TARGET, now=NOW)
    assert result.items == []
    assert result.remaining == 0
    assert "quota" in result.reason


def test_publish_after_is_timezone_aware_and_not_in_the_past(loaded, settings, state):
    now = datetime(2030, 1, 2, 0, 0, tzinfo=timezone.utc)
    result = build_plan(loaded, settings, state, publish_date=TARGET, now=now)
    for item in result.items:
        value = datetime.fromisoformat(item.publish_after)
        assert value.tzinfo is not None
        assert value > now


def test_build_writes_queue_payloads_and_state(loaded, settings, state):
    result = build(loaded, settings, state, publish_date=TARGET)
    assert result.count == settings.site.posts_per_day
    assert len(result.written) == settings.site.posts_per_day
    assert result.report_path

    payloads = list_queue(settings)
    assert len(payloads) == settings.site.posts_per_day
    for payload in payloads:
        assert validate_payload(payload, settings) == []
        assert payload["status"] == "queued"
        assert payload["topic_id"] in {topic.id for topic in (item.topic for item in loaded)}
        assert payload["post"]["html"].startswith('<div class="astrion-post"')
        assert 'data-astrion-queue="' in payload["post"]["html"]

    assert len(state.pending) == settings.site.posts_per_day
    assert all(item["status"] == "queued" for item in state.pending)
    assert len(state.data["builds"]) == 1

    previews = list(settings.preview_dir.glob("*.html"))
    assert len(previews) == settings.site.posts_per_day


def test_second_build_for_the_same_day_is_a_noop(loaded, settings, state):
    first = build(loaded, settings, state, publish_date=TARGET)
    assert first.count == settings.site.posts_per_day

    second = build(loaded, settings, state, publish_date=TARGET)
    assert second.count == 0
    assert "quota" in second.plan.reason
    assert len(list_queue(settings)) == settings.site.posts_per_day
    assert len(state.pending) == settings.site.posts_per_day


def test_build_dry_run_writes_nothing(loaded, settings, state):
    result = build(loaded, settings, state, publish_date=TARGET, dry_run=True)
    assert result.dry_run is True
    assert result.count == 0
    assert not settings.queue_dir.exists() or list(settings.queue_dir.glob("*.json")) == []
    assert state.pending == []
    assert not settings.state_path.exists()


def test_build_one_topic(loaded, settings, state):
    topic_id = "physics.rayleigh-scattering-sky"
    result = build(loaded, settings, state, publish_date=TARGET, topic_id=topic_id)
    assert result.count == 1
    payload = list_queue(settings)[0]
    assert payload["topic_id"] == topic_id
    assert validate_payload(payload, settings) == []


def test_build_reports_plan_error(loaded, settings, state):
    result = build(
        loaded, settings, state, publish_date=TARGET, topic_id="math.does-not-exist"
    )
    assert result.plan.error
    assert result.count == 0
    assert list_queue(settings) == []
