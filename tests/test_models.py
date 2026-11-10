from __future__ import annotations

import pytest
from pydantic import ValidationError

from astrion.models import Topic

from .conftest import topic_payload


def test_sample_modules_load(topics):
    ids = {topic.id for topic in topics}
    assert ids == {"math.division-by-zero", "physics.rayleigh-scattering-sky"}
    assert all(topic.status == "active" for topic in topics)
    assert all(topic.provenance.review_status == "approved" for topic in topics)


def test_english_only_field_rejects_hangul(topics):
    payload = topic_payload(topics[0])
    payload["hook"] = "나눗셈은 역수를 곱하는 것이다. " * 5
    with pytest.raises(ValidationError, match="English-only"):
        Topic.model_validate(payload)


def test_english_only_block_rejects_hangul(topics):
    payload = topic_payload(topics[0])
    payload["sections"][0]["blocks"].append({"type": "paragraph", "text": "한국어 문장"})
    with pytest.raises(ValidationError, match="English-only"):
        Topic.model_validate(payload)


def test_placeholder_prose_rejected(topics):
    payload = topic_payload(topics[0])
    payload["notes"] = "TODO: finish this later"
    with pytest.raises(ValidationError, match="placeholder"):
        Topic.model_validate(payload)


def test_slug_must_be_kebab_case(topics):
    payload = topic_payload(topics[0])
    payload["slug"] = "Why Dividing By Zero"
    with pytest.raises(ValidationError, match="slug"):
        Topic.model_validate(payload)


def test_table_rows_must_be_rectangular(topics):
    payload = topic_payload(topics[0])
    table = next(
        block
        for section in payload["sections"]
        for block in section["blocks"]
        if block["type"] == "table"
    )
    table["rows"][0].append("extra column")
    with pytest.raises(ValidationError, match="same number of cells"):
        Topic.model_validate(payload)


def test_placeholder_source_url_rejected(topics):
    payload = topic_payload(topics[0])
    payload["sources"][0]["url"] = "https://example.com/source"
    with pytest.raises(ValidationError, match="placeholder"):
        Topic.model_validate(payload)


def test_labels_cannot_repeat_main_category(topics):
    payload = topic_payload(topics[0])
    payload["labels"] = ["Mathematics"]
    with pytest.raises(ValidationError, match="main category"):
        Topic.model_validate(payload)


def test_labels_must_be_unique(topics):
    payload = topic_payload(topics[0])
    payload["labels"] = ["Arithmetic", "arithmetic"]
    with pytest.raises(ValidationError, match="duplicate label"):
        Topic.model_validate(payload)


def test_related_topic_cannot_be_self(topics):
    payload = topic_payload(topics[0])
    payload["related"] = [payload["id"]]
    with pytest.raises(ValidationError, match="relate to itself"):
        Topic.model_validate(payload)


def test_equation_requires_defined_variables(topics):
    payload = topic_payload(topics[0])
    equation = next(
        block
        for section in payload["sections"]
        for block in section["blocks"]
        if block["type"] == "equation"
    )
    equation["variables"] = []
    with pytest.raises(ValidationError, match="variables"):
        Topic.model_validate(payload)
