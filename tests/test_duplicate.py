from __future__ import annotations

from astrion.duplicate import (
    compare,
    find_conflicts,
    record_from_state,
    record_from_topic,
    unresolved,
    validate_pairs,
)
from astrion.models import Topic

from .conftest import topic_payload


def test_sample_topics_are_distinct(topics, settings):
    assert validate_pairs(topics, settings.duplicates) == []


def test_same_topic_is_a_full_conflict(topics, settings):
    topic = topics[0]
    match = compare(topic, record_from_topic(topic), settings.duplicates)
    assert match.score == 1.0
    assert "same topic id" in match.reasons


def test_identical_signature_under_a_new_id_conflicts(topics, settings):
    original = topics[0]
    payload = topic_payload(original)
    payload["id"] = "math.rewritten-topic"
    payload["slug"] = "rewritten-topic"
    payload["title"] = "A Completely Different Title About Nothing"
    payload["aliases"] = []
    payload["related"] = []
    rewritten = Topic.model_validate(payload)

    match = compare(rewritten, record_from_topic(original), settings.duplicates)
    assert match.reasons
    assert any("signature" in reason for reason in match.reasons)
    assert unresolved([match])


def test_distinct_from_override_suppresses_conflict(topics, settings):
    original = topics[0]
    payload = topic_payload(original)
    payload["id"] = "math.rewritten-topic"
    payload["slug"] = "rewritten-topic"
    payload["title"] = "A Completely Different Title About Nothing"
    payload["aliases"] = []
    payload["related"] = []
    payload["distinct_from"] = [
        {
            "topic_id": original.id,
            "note": "This module deliberately revisits the same definition from a historical angle.",
        }
    ]
    rewritten = Topic.model_validate(payload)

    matches = find_conflicts(
        rewritten, [record_from_topic(original)], settings.duplicates
    )
    assert matches
    assert matches[0].overridden is True
    assert unresolved(matches) == []


def test_shared_alias_is_detected(topics, settings):
    original = topics[1]
    payload = topic_payload(topics[0])
    payload["aliases"] = ["blue sky"]
    candidate = Topic.model_validate(payload)
    match = compare(candidate, record_from_topic(original), settings.duplicates)
    assert any(reason.startswith("shared alias") for reason in match.reasons)


def test_same_primary_keyword_is_detected(topics, settings):
    original = topics[1]
    payload = topic_payload(topics[0])
    payload["primary_keyword"] = original.primary_keyword
    candidate = Topic.model_validate(payload)
    match = compare(candidate, record_from_topic(original), settings.duplicates)
    assert "same primary keyword" in match.reasons


def test_title_similarity_is_detected(topics, settings):
    original = topics[1]
    payload = topic_payload(topics[0])
    payload["title"] = "Why Is the Sky Blue? Rayleigh Scattering Explained!"
    candidate = Topic.model_validate(payload)
    match = compare(candidate, record_from_topic(original), settings.duplicates)
    assert any("title similarity" in reason for reason in match.reasons)


def test_state_record_requires_a_signature(topics):
    assert record_from_state({"topic_id": "x", "title": "y"}) is None
    record = record_from_state(
        {
            "topic_id": "x",
            "title": "y",
            "signature": "a signature",
            "aliases": ["one"],
        }
    )
    assert record is not None
    assert record.aliases == ("one",)


def test_override_only_applies_to_the_named_topic(topics, settings):
    original = topics[0]
    payload = topic_payload(original)
    payload["id"] = "math.rewritten-topic"
    payload["slug"] = "rewritten-topic"
    payload["title"] = "A Completely Different Title About Nothing"
    payload["aliases"] = []
    payload["related"] = []
    payload["distinct_from"] = [
        {
            "topic_id": topics[1].id,
            "note": "This note names a different topic, so it must not suppress the real conflict.",
        }
    ]
    rewritten = Topic.model_validate(payload)
    matches = find_conflicts(
        rewritten, [record_from_topic(original)], settings.duplicates
    )
    assert unresolved(matches)
