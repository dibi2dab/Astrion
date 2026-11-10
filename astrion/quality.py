"""Deterministic 100-point quality gate.

No model self-evaluation is involved. Every point comes from an inspectable
rule. A fatal failure blocks publication regardless of the numeric score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable

from .config import Settings
from .duplicate import find_conflicts, unresolved
from .models import (
    CalloutBlock,
    ContentType,
    ExampleBlock,
    ListBlock,
    MathBlock,
    EquationBlock,
    ParagraphBlock,
    PrimarySourceKind,
    TableBlock,
    Topic,
)
from .render import RenderedPost, render_post
from .text import (
    average_sentence_words,
    count_words,
    keyword_density,
    reading_minutes,
)


@dataclass
class QualityReport:
    topic_id: str
    score: float
    decision: str
    breakdown: dict[str, float]
    fatal: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.decision == "PUBLISH"

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "score": round(self.score, 1),
            "decision": self.decision,
            "breakdown": {key: round(value, 1) for key, value in self.breakdown.items()},
            "fatal": self.fatal,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "conflicts": self.conflicts,
        }


def evaluate(
    topic: Topic,
    settings: Settings,
    *,
    rendered: RenderedPost | None = None,
    published_records: Iterable[dict[str, Any]] = (),
) -> QualityReport:
    rendered = rendered or render_post(topic)
    prose = _prose_text(topic)
    words = count_words(prose)
    paragraphs = _paragraphs(topic)
    paragraph_words = [count_words(item) for item in paragraphs]
    avg_paragraph = sum(paragraph_words) / len(paragraph_words) if paragraph_words else 0.0
    avg_sentence = average_sentence_words(prose)
    min_words = settings.quality.min_words_for(topic.content_type.value)
    words_per_section = words / max(1, len(topic.sections))
    density = keyword_density(prose, topic.primary_keyword)
    html_ok, html_errors, html_stats = check_html(rendered.html)

    conflicts = find_conflicts(topic, published_records, settings.duplicates)
    unresolved_conflicts = unresolved(conflicts)
    conflict_view = [match.to_dict() for match in conflicts]

    fatal: list[str] = []
    warnings: list[str] = []

    if topic.provenance.review_status != "approved":
        fatal.append("editorial review is not approved")
    if words < min_words:
        fatal.append(f"only {words} words; {topic.content_type.value} needs at least {min_words}")
    if unresolved_conflicts:
        fatal.append(
            "duplicate topic: " + ", ".join(match.record.topic_id for match in unresolved_conflicts)
        )
    if not html_ok:
        fatal.append("HTML compatibility: " + "; ".join(html_errors))
    if rendered.size_bytes > 400_000:
        fatal.append("rendered HTML exceeds 400 KB")
    if topic.content_type.value in settings.quality.require_primary_source_for:
        if not any(source.kind in PrimarySourceKind for source in topic.sources):
            fatal.append("content type requires a peer-reviewed, agency, or standards source")
    if topic.provenance.fact_basis == "editorial_synthesis" and len(topic.sources) < 2:
        fatal.append("editorial synthesis requires at least two sources")
    if len(topic.labels) > settings.blogger.max_secondary_labels:
        fatal.append("too many secondary labels")

    if not topic.sources:
        warnings.append("no sources listed")
    if not topic.misconceptions:
        warnings.append("no misconceptions section")
    if density > settings.quality.max_keyword_density:
        warnings.append(
            f"primary keyword density {density:.2%} exceeds {settings.quality.max_keyword_density:.2%}"
        )
    if avg_paragraph > 95:
        warnings.append("average paragraph is long")
    if rendered.reading_minutes > 12:
        warnings.append("reading time above 12 minutes")

    accuracy = _accuracy(topic)
    depth = _depth(topic, words, min_words)
    distinctness = _distinctness(topic, unresolved_conflicts)
    readability = _readability(avg_paragraph, avg_sentence, words_per_section)
    notation = _notation(topic)
    html_score = 5.0 if html_ok else 0.0
    seo = _seo(topic, prose, density, settings)

    breakdown = {
        "accuracy_sources": accuracy,
        "depth_explanation": depth,
        "distinct_value": distinctness,
        "readability": readability,
        "notation": notation,
        "blogger_html": html_score,
        "seo": seo,
    }
    score = round(sum(breakdown.values()), 1)
    if fatal:
        decision = "BLOCK"
    elif score >= settings.quality.publish_threshold:
        decision = "PUBLISH"
    elif score >= settings.quality.revise_threshold:
        decision = "REVISE"
    else:
        decision = "BLOCK"

    metrics = {
        "words": words,
        "min_words": min_words,
        "sections": len(topic.sections),
        "paragraphs": len(paragraphs),
        "avg_paragraph_words": round(avg_paragraph, 1),
        "avg_sentence_words": round(avg_sentence, 1),
        "words_per_section": round(words_per_section, 1),
        "keyword_density": round(density, 4),
        "sources": len(topic.sources),
        "primary_sources": sum(1 for source in topic.sources if source.kind in PrimarySourceKind),
        "labels": [topic.main_category.value, *topic.labels],
        "reading_minutes": reading_minutes(words),
        "html_bytes": rendered.size_bytes,
        "has_math": rendered.has_math,
        "tables": html_stats["tables"],
        "scripts": html_stats["scripts"],
        "links": html_stats["links"],
    }

    return QualityReport(
        topic_id=topic.id,
        score=score,
        decision=decision,
        breakdown=breakdown,
        fatal=fatal,
        warnings=warnings,
        metrics=metrics,
        conflicts=conflict_view,
    )


def _accuracy(topic: Topic) -> float:
    basis = {
        "peer_reviewed": 15.0,
        "agency_data": 15.0,
        "textbook": 15.0,
        "editorial_synthesis": 10.0,
    }[topic.provenance.fact_basis]
    count = len(topic.sources)
    source_points = 0.0 if count == 0 else 3.0 if count == 1 else 5.0 if count == 2 else 7.0
    primary_points = 3.0 if any(source.kind in PrimarySourceKind for source in topic.sources) else (1.0 if count else 0.0)
    return basis + source_points + primary_points


def _depth(topic: Topic, words: int, min_words: int) -> float:
    length_points = 8.0 * min(1.0, words / max(1, min_words))
    section_points = 4.0 if len(topic.sections) >= 3 else 2.0
    takeaway_points = 4.0 if 3 <= len(topic.key_takeaways) <= 5 else 2.0
    has_example = any(
        isinstance(block, (ExampleBlock, TableBlock))
        for section in topic.sections
        for block in section.blocks
    )
    extra_points = 4.0 if topic.misconceptions or has_example else 2.0
    return round(length_points + section_points + takeaway_points + extra_points, 1)


def _distinctness(topic: Topic, unresolved_conflicts: list) -> float:
    length = len(topic.unique_value)
    value_points = 8.0 if length >= 120 else 6.0 if length >= 80 else 3.0
    misconception_points = 6.0 if topic.misconceptions else 3.0
    takeaway_points = 3.0 if len(topic.key_takeaways) >= 3 else 0.0
    conflict_points = 0.0 if unresolved_conflicts else 3.0
    return value_points + misconception_points + takeaway_points + conflict_points


def _readability(avg_paragraph: float, avg_sentence: float, words_per_section: float) -> float:
    paragraph_points = 5.0 if avg_paragraph <= 75 else 3.0 if avg_paragraph <= 95 else 0.0
    sentence_points = 5.0 if avg_sentence <= 25 else 3.0 if avg_sentence <= 30 else 0.0
    section_points = 5.0 if 80 <= words_per_section <= 400 else 3.0 if 60 <= words_per_section <= 500 else 0.0
    return paragraph_points + sentence_points + section_points


def _notation(topic: Topic) -> float:
    if not topic.has_math:
        return 10.0

    equations = [
        block
        for section in topic.sections
        for block in section.blocks
        if isinstance(block, EquationBlock)
    ]
    # Every equation block must define its symbols; the schema enforces that, so
    # the check documents the intent and fails loudly if the schema is relaxed.
    variable_points = 5.0 if all(block.variables for block in equations) else 0.0
    latex_parts = [
        block.latex
        for section in topic.sections
        for block in section.blocks
        if isinstance(block, (MathBlock, EquationBlock))
    ]
    latex_parts.extend(
        block.text
        for section in topic.sections
        for block in section.blocks
        if isinstance(block, ParagraphBlock)
    )
    balanced = _balanced_math(latex_parts)
    return variable_points + (5.0 if balanced else 0.0)


def _seo(topic: Topic, prose: str, density: float, settings: Settings) -> float:
    title_points = 2.0 if topic.primary_keyword.casefold() in topic.title.casefold() else 0.0
    opening = " ".join(prose.split()[:120]).casefold()
    opening_points = 1.0 if topic.primary_keyword.casefold() in opening else 0.0
    density_points = (
        1.0
        if 0.001 <= density <= settings.quality.max_keyword_density
        else 0.0
    )
    meta_points = 1.0 if 80 <= len(topic.meta_description) <= 160 else 0.0
    return title_points + opening_points + density_points + meta_points


def _balanced_math(parts: Iterable[str]) -> bool:
    text = "\n".join(parts)
    if text.count("\\(") != text.count("\\)"):
        return False
    if text.count("\\[") != text.count("\\]"):
        return False
    stripped = re.sub(r"\\[{}]", "", text)
    return stripped.count("{") == stripped.count("}")


def _paragraphs(topic: Topic) -> list[str]:
    items: list[str] = [topic.hook]
    for section in topic.sections:
        for block in section.blocks:
            if isinstance(block, ParagraphBlock):
                items.append(block.text)
            elif isinstance(block, CalloutBlock):
                items.append(block.text)
            elif isinstance(block, (ListBlock,)):
                items.extend(block.items)
    items.extend(item.correction for item in topic.misconceptions)
    return [item for item in items if item.strip()]


def _prose_text(topic: Topic) -> str:
    parts: list[str] = [topic.hook]
    for section in topic.sections:
        parts.append(section.heading)
        for block in section.blocks:
            if isinstance(block, ParagraphBlock):
                parts.append(block.text)
            elif isinstance(block, MathBlock):
                parts.append(block.caption or "")
            elif isinstance(block, EquationBlock):
                parts.append(block.caption or "")
                parts.extend(f"{v.symbol} {v.meaning}" for v in block.variables)
            elif isinstance(block, ListBlock):
                parts.extend(block.items)
            elif isinstance(block, TableBlock):
                parts.append(block.caption)
                parts.extend(block.headers)
                parts.extend(cell for row in block.rows for cell in row)
            elif isinstance(block, CalloutBlock):
                parts.extend([block.title or "", block.text])
            elif isinstance(block, ExampleBlock):
                parts.extend([block.title, *block.steps, block.conclusion or ""])
    for item in topic.misconceptions:
        parts.extend([item.claim, item.correction])
    return "\n\n".join(part for part in parts if part)


_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_ALLOWED_SCRIPT_SRC = "cdn.jsdelivr.net/npm/mathjax@3/"


class _HTMLChecker(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.scripts: list[dict[str, str]] = []
        self.stats = {"tables": 0, "links": 0, "images": 0, "h2": 0, "h3": 0, "scripts": 0}
        self._script_buffer: list[str] = []
        self._in_script = False
        self._style_attr_fixed_width = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag in {"html", "head", "body"}:
            self.errors.append(f"<{tag}> is not allowed in a Blogger post fragment")
        if tag == "h1":
            self.errors.append("<h1> is not allowed; the Blogger post title is the only H1")
        if tag == "script":
            self.stats["scripts"] += 1
            self._in_script = True
            self._script_buffer = []
            self.scripts.append({"src": attributes.get("src", ""), "inline": ""})
        if tag == "img":
            self.stats["images"] += 1
            if not attributes.get("alt", "").strip():
                self.errors.append("image is missing alt text")
        if tag == "table":
            self.stats["tables"] += 1
        if tag == "a":
            self.stats["links"] += 1
            if not attributes.get("href", "").strip():
                self.errors.append("anchor has no href")
        if tag == "h2":
            self.stats["h2"] += 1
        if tag == "h3":
            self.stats["h3"] += 1
        identifier = attributes.get("id", "")
        if identifier:
            if identifier in self.ids:
                self.errors.append(f"duplicate id '{identifier}'")
            self.ids.add(identifier)
        style = attributes.get("style", "").lower()
        if re.search(r"width\s*:\s*\d+px", style) or re.search(r"min-width\s*:\s*\d+px", style):
            self.errors.append("fixed pixel width in inline style")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False
            if self.scripts:
                self.scripts[-1]["inline"] = "".join(self._script_buffer).strip()
        if tag in _VOID:
            return
        if not self.stack:
            self.errors.append(f"unexpected closing tag </{tag}>")
            return
        if self.stack[-1] != tag:
            self.errors.append(f"mismatched closing tag </{tag}>; expected </{self.stack[-1]}>")
            if tag in self.stack:
                while self.stack and self.stack[-1] != tag:
                    self.stack.pop()
                if self.stack:
                    self.stack.pop()
            return
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_buffer.append(data)

    def close(self) -> None:
        super().close()
        if self.stack:
            self.errors.append("unclosed tags: " + ", ".join(self.stack))
        for script in self.scripts:
            src = script["src"]
            inline = script["inline"]
            if src and _ALLOWED_SCRIPT_SRC not in src:
                self.errors.append(f"external script is not allowed: {src}")
            if not src and "MathJax" not in inline:
                self.errors.append("inline script is not an allowed MathJax configuration")


def check_html(html: str) -> tuple[bool, list[str], dict[str, int]]:
    checker = _HTMLChecker()
    checker.feed(html)
    checker.close()
    return not checker.errors, checker.errors, checker.stats
