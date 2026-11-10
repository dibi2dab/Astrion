from __future__ import annotations

from datetime import date, timedelta

from astrion.loader import LoadedTopic
from astrion.models import Topic
from astrion.validate import counts, validate_topics

from .conftest import topic_payload


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_sample_content_has_no_issues(loaded, settings):
    issues = validate_topics(loaded, settings)
    assert counts(issues) == (0, 0)
    assert issues == []


def test_duplicate_title_is_an_error(loaded, settings):
    original = loaded[0]
    payload = topic_payload(original.topic)
    payload["id"] = "math.other-topic"
    payload["slug"] = "other-topic"
    payload["primary_keyword"] = "other keyword"
    payload["aliases"] = []
    payload["related"] = []
    duplicate = LoadedTopic(Topic.model_validate(payload), original.path)
    issues = validate_topics([*loaded, duplicate], settings)
    assert "duplicate_title" in _codes(issues)
    assert counts(issues)[0] >= 1


def test_duplicate_slug_is_an_error(loaded, settings):
    original = loaded[0]
    payload = topic_payload(original.topic)
    payload["id"] = "math.other-topic"
    payload["title"] = "A Completely Different Title About Nothing"
    payload["primary_keyword"] = "other keyword"
    payload["aliases"] = []
    payload["related"] = []
    duplicate = LoadedTopic(Topic.model_validate(payload), original.path)
    issues = validate_topics([*loaded, duplicate], settings)
    assert "duplicate_slug" in _codes(issues)


def test_filename_mismatch_is_a_warning(loaded, settings):
    original = loaded[0]
    moved = LoadedTopic(original.topic, original.path.with_name("wrong-name.yaml"))
    issues = validate_topics([moved, *loaded[1:]], settings)
    assert "filename_mismatch" in _codes(issues)


def test_unapproved_active_topic_is_a_warning(loaded, settings):
    original = loaded[0]
    payload = topic_payload(original.topic)
    payload["provenance"]["review_status"] = "draft"
    changed = LoadedTopic(Topic.model_validate(payload), original.path)
    issues = validate_topics([changed, *loaded[1:]], settings)
    assert "not_approved" in _codes(issues)


def test_unknown_related_topic_is_an_error(loaded, settings):
    original = loaded[0]
    payload = topic_payload(original.topic)
    payload["related"] = ["math.does-not-exist"]
    changed = LoadedTopic(Topic.model_validate(payload), original.path)
    issues = validate_topics([changed, *loaded[1:]], settings)
    assert "unknown_related_topic" in _codes(issues)
    assert counts(issues)[0] >= 1


def test_future_access_date_is_a_warning(loaded, settings, monkeypatch):
    original = loaded[0]
    payload = topic_payload(original.topic)
    payload["sources"][0]["accessed_at"] = date(2099, 1, 1)
    changed = LoadedTopic(Topic.model_validate(payload), original.path)
    issues = validate_topics(
        [changed, *loaded[1:]], settings, today=date(2026, 11, 10)
    )
    assert "future_access_date" in _codes(issues)


def test_shared_primary_keyword_is_a_warning(loaded, settings):
    original = loaded[0]
    payload = topic_payload(original.topic)
    payload["id"] = "math.other-topic"
    payload["title"] = "A Completely Different Title About Nothing"
    payload["slug"] = "other-topic"
    payload["aliases"] = []
    payload["related"] = []
    duplicate = LoadedTopic(Topic.model_validate(payload), original.path)
    issues = validate_topics([*loaded, duplicate], settings)
    assert "shared_keyword" in _codes(issues)


def test_published_duplicate_is_an_error(loaded, settings):
    original = loaded[0]
    record = {
        "topic_id": "math.previously-published",
        "title": "Why Dividing by Zero Is Undefined",
        "signature": original.topic.signature_text,
        "primary_keyword": original.topic.primary_keyword,
        "aliases": [],
    }
    issues = validate_topics(loaded, settings, [record])
    assert "published_duplicate" in _codes(issues)
    assert counts(issues)[0] >= 1
