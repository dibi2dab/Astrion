from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from astrion.state import StateStore, empty_state
from astrion.text import (
    average_sentence_words,
    contains_hangul,
    contains_placeholder,
    count_words,
    html_to_text,
    iso_now,
    keyword_density,
    normalize_signature,
    reading_minutes,
    sequence_similarity,
    sha256_text,
    slugify,
    split_sentences,
    strip_math,
    title_similarity,
    token_jaccard,
)


def test_html_to_text_removes_markup_and_scripts():
    html = "<div><p>Hello <b>world</b>.</p><script>alert(1)</script></div>"
    assert html_to_text(html) == "Hello world."


def test_count_words_strips_math_and_html():
    text = "Before \\[x^2 + y^2\\] after, with \\(a\\) inline."
    assert count_words(text) == 4
    assert count_words("<p>One two three</p>") == 3


def test_strip_math_keeps_surrounding_words():
    assert "before" in strip_math("before \\(x\\) after")
    assert "x" not in strip_math("before \\(x\\) after")


def test_split_sentences_and_average_length():
    text = "First sentence here. Second one is longer!"
    sentences = split_sentences(text)
    assert len(sentences) == 2
    assert average_sentence_words(text) > 0


def test_hangul_and_placeholder_detection():
    assert contains_hangul("한국어")
    assert not contains_hangul("English only")
    assert contains_placeholder("TODO: finish")
    assert contains_placeholder("[insert name]")
    assert not contains_placeholder("A finished sentence.")


def test_keyword_density():
    text = "sky blue " * 10 + "other words " * 90
    assert keyword_density(text, "sky blue") > 0
    assert keyword_density("", "sky") == 0


def test_normalisation_and_similarity():
    assert normalize_signature("Hello, World!") == "hello world"
    assert token_jaccard("the sky is blue", "the sky is blue") == 1.0
    assert sequence_similarity("abc def", "abc def") == 1.0
    assert title_similarity("abc", "xyz") < 0.5


def test_slugify_and_reading_time():
    assert slugify("Why Is the Sky Blue?") == "why-is-the-sky-blue"
    assert slugify("!!!") == "post"
    assert reading_minutes(0) == 1
    assert reading_minutes(440) == 2


def test_sha256_and_iso_now():
    assert len(sha256_text("abc")) == 64
    moment = datetime(2030, 1, 1, 12, 34, 56, microsecond=123, tzinfo=timezone.utc)
    assert iso_now(moment) == "2030-01-01T12:34:56+00:00"


def test_empty_state_shape():
    state = empty_state()
    assert state["schema"] == "astrion.state/v1"
    assert state["published"] == []
    assert state["pending"] == []
    assert state["failed"] == []
    assert state["builds"] == []


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.add_pending({"queue_id": "q1", "topic_id": "t1", "status": "queued"})
    store.save()

    reloaded = StateStore(path)
    assert reloaded.pending == [
        {"queue_id": "q1", "topic_id": "t1", "status": "queued"}
    ]


def test_state_rejects_unreadable_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        StateStore(path)


def test_state_rejects_non_object_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        StateStore(path)


def test_add_pending_replaces_same_queue_id(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.add_pending({"queue_id": "q1", "topic_id": "t1", "status": "queued"})
    store.add_pending({"queue_id": "q1", "topic_id": "t1", "status": "queued", "x": 1})
    assert len(store.pending) == 1
    assert store.pending[0]["x"] == 1


def test_mark_published_cleans_pending_and_duplicates(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.add_pending({"queue_id": "q1", "topic_id": "t1", "status": "queued"})
    store.add_pending({"queue_id": "q2", "topic_id": "t2", "status": "queued"})
    store.mark_published(
        {"queue_id": "q1", "topic_id": "t1", "title": "one", "published_at": "x"}
    )
    store.mark_published(
        {"queue_id": "q1", "topic_id": "t1", "title": "one", "published_at": "y"}
    )
    assert [item["queue_id"] for item in store.pending] == ["q2"]
    assert len(store.published) == 1
    assert store.published[0]["published_at"] == "y"


def test_pending_and_published_queries(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.add_pending(
        {
            "queue_id": "q1",
            "topic_id": "t1",
            "status": "queued",
            "publish_date": "2030-01-01",
            "slot": 0,
            "main_category": "Physics",
        }
    )
    store.add_pending(
        {
            "queue_id": "q2",
            "topic_id": "t2",
            "status": "in_flight",
            "publish_date": "2030-01-01",
            "slot": 1,
            "main_category": "Mathematics",
        }
    )
    store.add_pending(
        {
            "queue_id": "q3",
            "topic_id": "t3",
            "status": "failed",
            "publish_date": "2030-01-01",
            "slot": 0,
        }
    )
    assert store.queued_topic_ids() == {"t1", "t2"}
    assert store.pending_queue_ids() == {"q1", "q2"}
    assert store.count_for_date("2030-01-01") == 2
    assert store.used_slots("2030-01-01") == {0, 1}
    assert store.categories_for_date("2030-01-01") == {"Physics", "Mathematics"}
    assert store.find_pending("q2")["topic_id"] == "t2"
    assert store.find_pending("missing") is None


def test_mark_inflight_requeue_and_fail_persist(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.add_pending({"queue_id": "q1", "topic_id": "t1", "status": "queued"})
    store.mark_inflight("q1", started_at="2030-01-01T00:00:00+00:00")
    assert StateStore(path).pending[0]["status"] == "in_flight"

    store.requeue("q1", error="retry", next_attempt_at="2030-01-01T00:10:00+00:00")
    reloaded = StateStore(path).pending[0]
    assert reloaded["status"] == "queued"
    assert reloaded["attempts"] == 1
    assert reloaded["next_attempt_at"] == "2030-01-01T00:10:00+00:00"

    store.mark_failed("q1", error="fatal")
    after = StateStore(path)
    assert after.pending == []
    assert after.failed[0]["status"] == "failed"
    assert after.failed[0]["last_error"] == "fatal"


def test_mark_inflight_for_unknown_queue_is_a_noop(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.mark_inflight("missing", started_at="x") is None


def test_days_since_category(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.mark_published(
        {
            "queue_id": "q1",
            "topic_id": "t1",
            "main_category": "Physics",
            "publish_date": "2030-01-01",
        }
    )
    assert store.days_since_category("Physics", date(2030, 1, 11)) == 10
    assert store.days_since_category("Physics", date(2029, 1, 1)) == 0
    assert store.days_since_category("Chemistry", date(2030, 1, 1)) == 3650


def test_related_urls_returns_the_published_url(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.mark_published(
        {"queue_id": "q1", "topic_id": "t1", "title": "One", "url": "https://a/1"}
    )
    store.mark_published(
        {"queue_id": "q2", "topic_id": "t1", "title": "One again", "url": "https://a/2"}
    )
    assert store.related_urls()["t1"]["url"] == "https://a/2"


def test_summary_counts(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.add_pending(
        {
            "queue_id": "q1",
            "topic_id": "t1",
            "status": "queued",
            "publish_date": "2030-01-02",
            "publish_after": "2030-01-02T08:30:00-05:00",
        }
    )
    store.mark_published(
        {
            "queue_id": "q0",
            "topic_id": "t0",
            "main_category": "Physics",
            "publish_date": "2030-01-01",
            "published_at": "2030-01-01T00:00:00+00:00",
        }
    )
    summary = store.summary()
    assert summary["published"] == 1
    assert summary["pending"] == 1
    assert summary["failed"] == 0
    assert summary["first_date"] == "2030-01-01"
    assert summary["last_date"] == "2030-01-02"
    assert summary["categories"] == {"Physics": 1}
    assert len(summary["pending_items"]) == 1
    assert len(summary["recent_published"]) == 1


def test_record_and_merge_builds(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.record_build({"build_id": "b1"})
    store.merge_builds([{"build_id": "b1"}, {"build_id": "b2"}])
    assert [item["build_id"] for item in store.data["builds"]] == ["b1", "b2"]
