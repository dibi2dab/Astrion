from __future__ import annotations

import json
from datetime import datetime, timezone

from astrion.builder import build
from astrion.planner import build_plan
from astrion.queue import (
    archive_failure,
    build_payload,
    due_payloads,
    labels_for_topic,
    list_queue,
    load_payload,
    move_to_archive,
    queue_summary,
    validate_payload,
)
from astrion.text import sha256_text

TARGET = "2030-01-01"
NOW = datetime(2029, 12, 31, 12, 0, tzinfo=timezone.utc)


def _payload(loaded, settings, state):
    plan = build_plan(loaded, settings, state, publish_date=TARGET, now=NOW)
    return build_payload(plan.items[0], settings)


def test_build_payload_contract(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    assert payload["schema"] == "astrion.queue/v1"
    assert payload["status"] == "queued"
    assert payload["publish_date"] == TARGET
    assert payload["post"]["html_sha256"] == sha256_text(payload["post"]["html"])
    assert payload["blogger"]["labels"][0] == payload["post"]["main_category"]
    assert payload["fingerprint"]["topic_key"] == payload["topic_id"]
    assert payload["review"]["review_status"] == "approved"
    assert payload["sources"]
    assert validate_payload(payload, settings) == []


def test_validate_payload_detects_checksum_mismatch(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    payload["post"]["html"] = payload["post"]["html"].replace("the", "teh", 1)
    errors = validate_payload(payload, settings)
    assert any("checksum" in error for error in errors)


def test_validate_payload_detects_quality_failure(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    payload["quality"]["decision"] = "BLOCK"
    payload["quality"]["score"] = 50
    errors = validate_payload(payload, settings)
    assert any("PUBLISH" in error for error in errors)
    assert any("threshold" in error for error in errors)


def test_validate_payload_detects_review_failure(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    payload["review"]["review_status"] = "draft"
    assert any("approved" in error for error in validate_payload(payload, settings))


def test_validate_payload_detects_label_failure(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    payload["blogger"]["labels"] = ["Not A Category"]
    assert any("main category" in error for error in validate_payload(payload, settings))


def test_validate_payload_detects_bad_ids(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    payload["queue_id"] = "nope"
    payload["topic_id"] = "NOPE"
    errors = validate_payload(payload, settings)
    assert any("queue_id" in error for error in errors)
    assert any("topic_id" in error for error in errors)


def test_validate_payload_detects_unsupported_schema(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    payload["schema"] = "astrion.queue/v999"
    assert any("schema" in error for error in validate_payload(payload, settings))


def test_validate_payload_requires_title_and_meta_lengths(loaded, settings, state):
    payload = _payload(loaded, settings, state)
    payload["post"]["title"] = "Short"
    payload["post"]["meta_description"] = "Too short"
    errors = validate_payload(payload, settings)
    assert any("title length" in error for error in errors)
    assert any("meta_description" in error for error in errors)


def test_due_payloads_respects_time(loaded, settings, state):
    build(loaded, settings, state, publish_date=TARGET)
    assert due_payloads(settings, datetime(2029, 12, 31, 12, 0, tzinfo=timezone.utc)) == []
    due = due_payloads(settings, datetime(2030, 1, 2, 0, 0, tzinfo=timezone.utc))
    assert len(due) == 2
    assert [item["publish_after"] for item in due] == sorted(
        item["publish_after"] for item in due
    )


def test_list_queue_marks_unreadable_files(settings):
    settings.queue_dir.mkdir(parents=True, exist_ok=True)
    (settings.queue_dir / "broken.json").write_text("{not json", encoding="utf-8")
    items = list_queue(settings)
    assert len(items) == 1
    assert "__error" in items[0]


def test_load_payload_rejects_non_object(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")
    try:
        load_payload(path)
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_move_to_archive_strips_private_keys(loaded, settings, state):
    build(loaded, settings, state, publish_date=TARGET)
    item = list_queue(settings)[0]
    assert "__path" in item
    path = move_to_archive(item, settings, kind="published", extra={"reason": "test"})
    assert path.exists()
    archived = json.loads(path.read_text(encoding="utf-8"))
    assert "__path" not in archived
    assert archived["status"] == "published"
    assert archived["reason"] == "test"
    assert (path.parent / f"{item['queue_id']}.html").exists()
    assert not (settings.queue_dir / f"{item['queue_id']}.json").exists()


def test_archive_failure_writes_error_file(loaded, settings, state):
    build(loaded, settings, state, publish_date=TARGET)
    item = list_queue(settings)[0]
    path = archive_failure(item, settings, "synthetic failure")
    assert path.exists()
    error_file = path.parent / f"{item['queue_id']}.error.txt"
    assert error_file.read_text(encoding="utf-8") == "synthetic failure"


def test_queue_summary(loaded, settings, state):
    assert queue_summary(settings)["queued"] == 0
    build(loaded, settings, state, publish_date=TARGET)
    summary = queue_summary(settings)
    assert summary["queued"] == 2
    assert summary["invalid"] == 0
    assert len(summary["topics"]) == 2
    assert summary["next"]


def test_labels_for_topic(loaded):
    topic = loaded[0].topic
    labels = labels_for_topic(topic)
    assert labels[0] == topic.main_category.value
    assert labels[1:] == topic.labels
