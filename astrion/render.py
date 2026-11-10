"""Render one topic module into a Blogger-ready HTML fragment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .models import (
    CalloutBlock,
    EquationBlock,
    ExampleBlock,
    ListBlock,
    MathBlock,
    ParagraphBlock,
    TableBlock,
    Topic,
)
from .text import count_words, html_to_text, reading_minutes, slugify

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class RenderedPost:
    html: str
    text: str
    word_count: int
    reading_minutes: int
    has_math: bool
    topic_id: str

    @property
    def size_bytes(self) -> int:
        return len(self.html.encode("utf-8"))


def render_post(
    topic: Topic,
    *,
    related_urls: dict[str, dict[str, str]] | None = None,
) -> RenderedPost:
    html = _ENV.get_template("post.html.j2").render(
        topic=topic,
        lead=Markup(f"<p>{escape(topic.hook)}</p>"),
        sections_html=Markup(_render_sections(topic)),
        takeaways=[str(item) for item in topic.key_takeaways],
        misconceptions=[
            {"claim": item.claim, "correction": item.correction}
            for item in topic.misconceptions
        ],
        sources=[_source_view(source) for source in topic.sources],
        further_reading=[_source_view(source) for source in topic.further_reading],
        related=_related_view(topic, related_urls or {}),
        meta=_meta_line(topic),
        reading_minutes=reading_minutes(count_words(topic.plain_text())),
        has_math=topic.has_math,
    )
    text = html_to_text(html)
    words = count_words(topic.plain_text())
    return RenderedPost(
        html=html,
        text=text,
        word_count=words,
        reading_minutes=reading_minutes(words),
        has_math=topic.has_math,
        topic_id=topic.id,
    )


def _render_sections(topic: Topic) -> str:
    parts: list[str] = []
    for index, section in enumerate(topic.sections, start=1):
        anchor = f"astrion-section-{index}-{slugify(section.heading, limit=50)}"
        blocks = "\n".join(_render_block(block) for block in section.blocks)
        parts.append(
            f'<section class="astrion-section">\n'
            f'  <h2 id="{escape(anchor)}">{escape(section.heading)}</h2>\n'
            f"  {blocks}\n"
            f"</section>"
        )
    return "\n".join(parts)


def _render_block(block) -> str:
    if isinstance(block, ParagraphBlock):
        return f"<p>{escape(block.text)}</p>"

    if isinstance(block, MathBlock):
        body = escape(block.latex)
        caption = (
            f"\n<figcaption>{escape(block.caption)}</figcaption>" if block.caption else ""
        )
        if block.display:
            return (
                '<figure class="astrion-figure">\n'
                f'  <div class="astrion-math">\\[{body}\\]</div>{caption}\n'
                "</figure>"
            )
        return f'<p class="astrion-math-inline">\\({body}\\)</p>'

    if isinstance(block, EquationBlock):
        variables = "\n".join(
            f"      <dt>{escape(variable.symbol)}</dt>\n"
            f"      <dd>{escape(variable.meaning)}"
            f"{' (' + escape(variable.unit) + ')' if variable.unit else ''}</dd>"
            for variable in block.variables
        )
        caption = (
            f'\n  <figcaption class="astrion-equation-caption">{escape(block.caption)}</figcaption>'
            if block.caption
            else ""
        )
        return (
            '<div class="astrion-equation">\n'
            f'  <div class="astrion-math">\\[{escape(block.latex)}\\]</div>\n'
            '  <dl class="astrion-vars">\n'
            f"{variables}\n"
            "  </dl>"
            f"{caption}\n"
            "</div>"
        )

    if isinstance(block, ListBlock):
        tag = "ol" if block.ordered else "ul"
        items = "\n".join(f"  <li>{escape(item)}</li>" for item in block.items)
        return f'<{tag} class="astrion-list">\n{items}\n</{tag}>'

    if isinstance(block, TableBlock):
        headers = "".join(f'<th scope="col">{escape(cell)}</th>' for cell in block.headers)
        rows = "\n".join(
            "      <tr>"
            + "".join(f"<td>{escape(cell)}</td>" for cell in row)
            + "</tr>"
            for row in block.rows
        )
        return (
            '<div class="astrion-table-wrap" role="region" tabindex="0" '
            f'aria-label="{escape(block.caption)}">\n'
            '  <table class="astrion-table">\n'
            f"    <caption>{escape(block.caption)}</caption>\n"
            f"    <thead><tr>{headers}</tr></thead>\n"
            f"    <tbody>\n{rows}\n    </tbody>\n"
            "  </table>\n"
            "</div>"
        )

    if isinstance(block, CalloutBlock):
        title = (
            f'<p class="astrion-callout-title">{escape(block.title)}</p>\n'
            if block.title
            else ""
        )
        return (
            f'<aside class="astrion-callout astrion-callout--{escape(block.tone)}">\n'
            f"  {title}<p>{escape(block.text)}</p>\n"
            "</aside>"
        )

    if isinstance(block, ExampleBlock):
        steps = "\n".join(f"    <li>{escape(step)}</li>" for step in block.steps)
        conclusion = (
            f"\n  <p>{escape(block.conclusion)}</p>" if block.conclusion else ""
        )
        return (
            '<div class="astrion-example">\n'
            f"  <h3>{escape(block.title)}</h3>\n"
            f"  <ol>\n{steps}\n  </ol>{conclusion}\n"
            "</div>"
        )

    raise TypeError(f"unsupported block: {type(block)!r}")


def _source_view(source) -> dict[str, str]:
    year = str(source.published_at.year) if source.published_at else "n.d."
    return {
        "id": source.id,
        "title": source.title,
        "publisher": source.publisher,
        "url": source.url,
        "meta": f"{source.publisher}, {year}",
        "note": source.note or "",
    }


def _related_view(topic: Topic, related_urls: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for topic_id in topic.related:
        item = related_urls.get(topic_id)
        if not item or not item.get("url"):
            continue
        items.append({"title": item.get("title") or topic_id, "url": item["url"]})
    return items


def _meta_line(topic: Topic) -> str:
    labels = [topic.main_category.value, *topic.labels]
    reviewed = topic.provenance.reviewed_at
    return f"{' · '.join(labels)} · Reviewed {_month_year(reviewed)}"


def _month_year(value: date) -> str:
    return f"{value.strftime('%B')} {value.year}"
