from __future__ import annotations

from astrion.render import render_post
from astrion.queue import attach_queue_marker
from astrion.text import contains_hangul, count_words, html_to_text


def test_render_returns_a_blogger_fragment(topics):
    rendered = render_post(topics[0])
    html = rendered.html
    assert html.lstrip().startswith('<div class="astrion-post"')
    assert "<html" not in html.lower()
    assert "<h1" not in html.lower()
    assert 'data-astrion-topic="math.division-by-zero"' in html
    assert "astrion-table-wrap" in html
    assert "astrion-misconceptions" in html
    assert "astrion-sources" in html
    assert rendered.word_count > 600
    assert rendered.reading_minutes >= 1


def test_render_is_deterministic(topics):
    assert render_post(topics[0]).html == render_post(topics[0]).html


def test_math_post_uses_mathjax(topics):
    rendered = render_post(topics[1])
    assert rendered.has_math is True
    assert "window.MathJax" in rendered.html
    assert "cdn.jsdelivr.net/npm/mathjax@3" in rendered.html
    assert "\\(" in rendered.html or "\\[" in rendered.html


def test_non_math_post_has_no_scripts(topics):
    from astrion.models import ParagraphBlock, Section

    topic = topics[0].model_copy(
        update={
            "sections": [
                Section(
                    heading="A plain section",
                    blocks=[ParagraphBlock(text="A plain paragraph without any mathematics.")],
                )
            ]
        }
    )
    rendered = render_post(topic)
    assert rendered.has_math is False
    assert "<script" not in rendered.html


def test_text_is_english_only(topics):
    for topic in topics:
        rendered = render_post(topic)
        assert not contains_hangul(rendered.text)
        assert not contains_hangul(rendered.html)
        assert count_words(rendered.text) > 100
        assert html_to_text(rendered.html) == rendered.text


def test_hook_is_html_escaped(topics):
    topic = topics[0].model_copy(update={"hook": "A <b>bold</b> opening & a test."})
    html = render_post(topic).html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "<b>bold</b>" not in html


def test_queue_marker_is_added_once(topics):
    html = render_post(topics[0]).html
    marked = attach_queue_marker(html, "2026-01-01-s1-test")
    assert marked.count('data-astrion-queue="2026-01-01-s1-test"') == 1
    assert marked.startswith('<div class="astrion-post"')
    assert attach_queue_marker(marked, "2026-01-01-s1-test") == marked


def test_related_links_render_only_for_published_urls(topics):
    html = render_post(
        topics[0],
        related_urls={
            "physics.rayleigh-scattering-sky": {
                "title": "Why Is the Sky Blue?",
                "url": "https://example.blogspot.com/2026/01/sky.html",
            }
        },
    ).html
    assert "Related reading" not in html

    topic = topics[0].model_copy(
        update={"related": ["physics.rayleigh-scattering-sky"]}
    )
    html = render_post(
        topic,
        related_urls={
            "physics.rayleigh-scattering-sky": {
                "title": "Why Is the Sky Blue?",
                "url": "https://example.blogspot.com/2026/01/sky.html",
            }
        },
    ).html
    assert "Related reading" in html
    assert "Why Is the Sky Blue?" in html


def test_source_links_are_hardened(topics):
    html = render_post(topics[0]).html
    assert 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html
