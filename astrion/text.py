"""Deterministic text helpers shared by validation, duplication and quality checks."""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from html.parser import HTMLParser

HANGUL_RE = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")
PLACEHOLDER_RE = re.compile(
    r"\b(?:lorem ipsum|todo|tbd|tk|xxx|fixme)\b|\[insert|placeholder",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[.'-][A-Za-z0-9]+)*")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+(?:\s|$)")
MATH_DELIMITED_RE = re.compile(r"\\\(.+?\\\)|\\\[.+?\\\]", re.DOTALL)

STOPWORDS = frozenset(
    """
    a an and are as at be because been before being between both but by can could
    did do does doing for from had has have having he her here hers him his how i
    if in into is it its itself just me more most my no nor not of off on once only
    or other our out over own same she should so some such than that the their them
    then there these they this those through to too under until up very was we were
    what when where which while who whom why will with would you your
    """.split()
)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _INLINE_SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _INLINE_SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data:
            self.parts.append(data)


_INLINE_SKIP_TAGS = {"script", "style", "noscript", "pre", "code"}
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "caption", "dd", "div",
    "dl", "dt", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hr", "li", "main", "nav", "ol", "p", "section", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


def html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def contains_hangul(value: str) -> bool:
    return bool(HANGUL_RE.search(value or ""))


def contains_placeholder(value: str) -> bool:
    return bool(PLACEHOLDER_RE.search(value or ""))


def strip_math(value: str) -> str:
    return MATH_DELIMITED_RE.sub(" ", value or "")


def count_words(value: str, *, remove_math: bool = True) -> int:
    text = html_to_text(value) if "<" in (value or "") else (value or "")
    if remove_math:
        text = strip_math(text)
    return len(WORD_RE.findall(text))


def split_sentences(value: str) -> list[str]:
    text = html_to_text(value) if "<" in (value or "") else (value or "")
    text = strip_math(text)
    return [match.group(0).strip() for match in SENTENCE_RE.finditer(text) if match.group(0).strip()]


def average_sentence_words(value: str) -> float:
    sentences = split_sentences(value)
    if not sentences:
        return 0.0
    return sum(count_words(sentence) for sentence in sentences) / len(sentences)


def keyword_density(value: str, keyword: str) -> float:
    words = count_words(value)
    if not words or not keyword.strip():
        return 0.0
    occurrences = len(re.findall(re.escape(keyword.lower()), (value or "").lower()))
    if not occurrences:
        return 0.0
    return (occurrences * len(WORD_RE.findall(keyword))) / words


def normalize_signature(value: str) -> str:
    lowered = (value or "").lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def signature_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_signature(value).split()
        if token and token not in STOPWORDS and len(token) > 2
    }


def token_jaccard(left: str, right: str) -> float:
    a = signature_tokens(left)
    b = signature_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def sequence_similarity(left: str, right: str) -> float:
    a = normalize_signature(left)
    b = normalize_signature(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def title_similarity(left: str, right: str) -> float:
    return sequence_similarity(left, right)


def reading_minutes(words: int) -> int:
    return max(1, round(words / 220))


def slugify(value: str, *, fallback: str = "post", limit: int = 80) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug[:limit].rstrip("-") or fallback)


def sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def iso_now(now=None) -> str:
    from datetime import datetime, timezone

    moment = now or datetime.now(timezone.utc)
    return moment.replace(microsecond=0).isoformat()
