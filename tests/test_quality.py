from __future__ import annotations

import pytest

from astrion.models import CalloutBlock, ParagraphBlock, TableBlock
from astrion.quality import check_html, evaluate
from astrion.render import render_post

from .conftest import topic_payload


@pytest.mark.parametrize("index", [0, 1])
def test_sample_topics_pass_the_quality_gate(topics, settings, index):
    report = evaluate(topics[index], settings)
    assert report.decision == "PUBLISH"
    assert report.score >= settings.quality.publish_threshold
    assert report.fatal == []
    assert report.metrics["words"] >= report.metrics["min_words"]
    assert report.metrics["html_bytes"] < 400_000


def test_quality_is_deterministic(topics, settings):
    first = evaluate(topics[0], settings).to_dict()
    second = evaluate(topics[0], settings).to_dict()
    assert first == second


def test_unapproved_review_blocks_regardless_of_score(topics, settings):
    payload = topic_payload(topics[0])
    payload["provenance"]["review_status"] = "reviewed"
    from astrion.models import Topic

    report = evaluate(Topic.model_validate(payload), settings)
    assert report.decision == "BLOCK"
    assert "editorial review is not approved" in report.fatal


def test_word_count_floor_is_fatal(topics, settings):
    settings.quality.min_words["mathematics-explained"] = 100_000
    report = evaluate(topics[0], settings)
    assert report.decision == "BLOCK"
    assert any("needs at least" in reason for reason in report.fatal)


def test_missing_sources_is_a_warning(topics, settings):
    payload = topic_payload(topics[0])
    payload["sources"] = []
    from astrion.models import Topic

    report = evaluate(Topic.model_validate(payload), settings)
    assert "no sources listed" in report.warnings
    assert report.decision in {"PUBLISH", "REVISE", "BLOCK"}


def test_missing_misconceptions_is_a_warning(topics, settings):
    payload = topic_payload(topics[0])
    payload["misconceptions"] = []
    from astrion.models import Topic

    report = evaluate(Topic.model_validate(payload), settings)
    assert "no misconceptions section" in report.warnings


def test_html_checker_accepts_rendered_posts(topics):
    for topic in topics:
        ok, errors, stats = check_html(render_post(topic).html)
        assert ok, errors
        assert stats["tables"] >= 1


def test_html_checker_rejects_h1():
    ok, errors, _ = check_html("<h1>Title</h1><p>Body</p>")
    assert not ok
    assert any("<h1>" in error for error in errors)


def test_html_checker_rejects_unclosed_tags():
    ok, errors, _ = check_html("<div><p>Body</div>")
    assert not ok
    assert errors


def test_html_checker_rejects_disallowed_scripts():
    ok, errors, _ = check_html(
        '<div><script src="https://evil.example/track.js"></script></div>'
    )
    assert not ok
    assert any("external script" in error for error in errors)


def test_html_checker_rejects_non_mathjax_inline_script():
    ok, errors, _ = check_html("<div><script>alert(1)</script></div>")
    assert not ok
    assert any("MathJax" in error for error in errors)


def test_html_checker_rejects_image_without_alt():
    ok, errors, _ = check_html('<div><img src="https://example.org/x.png"></div>')
    assert not ok
    assert any("alt" in error for error in errors)


def test_html_checker_rejects_fixed_inline_width():
    ok, errors, _ = check_html('<div><p style="width: 900px">Body</p></div>')
    assert not ok
    assert any("fixed pixel width" in error for error in errors)


def test_html_checker_rejects_duplicate_ids():
    ok, errors, _ = check_html('<div><p id="x">A</p><p id="x">B</p></div>')
    assert not ok
    assert any("duplicate id" in error for error in errors)


def test_html_checker_rejects_document_tags():
    ok, errors, _ = check_html("<html><body><p>Body</p></body></html>")
    assert not ok
    assert any("<html>" in error for error in errors)


def test_paragraph_block_must_be_non_empty():
    with pytest.raises(Exception):
        ParagraphBlock(text="")


def test_table_block_width_validation():
    with pytest.raises(Exception):
        TableBlock(
            caption="A table",
            headers=["A", "B"],
            rows=[["one", "two", "three"]],
        )


def test_callout_tone_is_restricted():
    with pytest.raises(Exception):
        CalloutBlock(tone="danger", text="Text")
