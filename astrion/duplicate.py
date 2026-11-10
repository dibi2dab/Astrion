"""Topic-level duplicate detection.

Astrion treats two posts as duplicates when they answer the same core question
about the same target with the same central principle. Titles alone are not a
safe signal, so the comparison uses the editorial signature, the primary
keyword, the title, and explicit editorial overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .config import DuplicateSettings
from .models import Topic
from .text import normalize_signature, sequence_similarity, title_similarity, token_jaccard


@dataclass(frozen=True)
class FingerprintRecord:
    topic_id: str
    title: str
    signature: str
    primary_keyword: str = ""
    aliases: tuple[str, ...] = ()


@dataclass
class DuplicateMatch:
    record: FingerprintRecord
    score: float
    reasons: list[str] = field(default_factory=list)
    overridden: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.record.topic_id,
            "title": self.record.title,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "overridden": self.overridden,
            "note": self.note,
        }


def record_from_topic(topic: Topic) -> FingerprintRecord:
    return FingerprintRecord(
        topic_id=topic.id,
        title=topic.title,
        signature=topic.signature_text,
        primary_keyword=topic.primary_keyword,
        aliases=tuple(topic.aliases),
    )


def record_from_state(record: dict[str, Any]) -> FingerprintRecord | None:
    topic_id = str(record.get("topic_id") or "")
    title = str(record.get("title") or "")
    signature = str(record.get("signature") or "")
    if not topic_id or not signature:
        return None
    aliases = record.get("aliases") or []
    return FingerprintRecord(
        topic_id=topic_id,
        title=title,
        signature=signature,
        primary_keyword=str(record.get("primary_keyword") or ""),
        aliases=tuple(str(item) for item in aliases),
    )


def compare(
    topic: Topic,
    record: FingerprintRecord,
    config: DuplicateSettings,
) -> DuplicateMatch:
    reasons: list[str] = []
    scores: list[float] = []

    if topic.id.casefold() == record.topic_id.casefold():
        reasons.append("same topic id")
        scores.append(1.0)

    if record.signature:
        token = token_jaccard(topic.signature_text, record.signature)
        sequence = sequence_similarity(topic.signature_text, record.signature)
        scores.extend([token, sequence])
        if token >= config.token_threshold:
            reasons.append(f"editorial signature overlap {token:.2f}")
        if sequence >= config.sequence_threshold:
            reasons.append(f"editorial signature sequence {sequence:.2f}")

    if record.title:
        title = title_similarity(topic.title, record.title)
        scores.append(title)
        if title >= config.title_threshold:
            reasons.append(f"title similarity {title:.2f}")

    candidate_keyword = normalize_signature(topic.primary_keyword)
    record_keyword = normalize_signature(record.primary_keyword)
    if candidate_keyword and candidate_keyword == record_keyword:
        reasons.append("same primary keyword")
        scores.append(0.75)

    candidate_aliases = {normalize_signature(item) for item in topic.aliases if item.strip()}
    record_aliases = {normalize_signature(item) for item in record.aliases if item.strip()}
    shared = (candidate_aliases & record_aliases) - {""}
    if shared:
        reasons.append(f"shared alias: {sorted(shared)[0]}")
        scores.append(0.8)

    return DuplicateMatch(
        record=record,
        score=max(scores or [0.0]),
        reasons=reasons,
    )


def find_conflicts(
    topic: Topic,
    records: Iterable[FingerprintRecord | dict[str, Any]],
    config: DuplicateSettings,
) -> list[DuplicateMatch]:
    overrides = {item.topic_id: item.note for item in topic.distinct_from}
    matches: list[DuplicateMatch] = []
    seen: set[str] = set()
    for item in records:
        record = item if isinstance(item, FingerprintRecord) else record_from_state(item)
        if record is None or record.topic_id in seen:
            continue
        seen.add(record.topic_id)
        match = compare(topic, record, config)
        if not match.reasons:
            continue
        if record.topic_id in overrides:
            match.overridden = True
            match.note = overrides[record.topic_id]
        matches.append(match)
    matches.sort(key=lambda item: item.score, reverse=True)
    return matches


def unresolved(matches: Iterable[DuplicateMatch]) -> list[DuplicateMatch]:
    return [match for match in matches if not match.overridden]


def validate_pairs(
    topics: Iterable[Topic],
    config: DuplicateSettings,
) -> list[dict[str, Any]]:
    """Pairwise conflicts used by ``astrion validate`` for active modules."""

    items = sorted(topics, key=lambda topic: topic.id)
    results: list[dict[str, Any]] = []
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            match = compare(left, record_from_topic(right), config)
            if not match.reasons:
                continue
            overridden = any(
                item.topic_id == right.id for item in left.distinct_from
            ) or any(item.topic_id == left.id for item in right.distinct_from)
            results.append(
                {
                    "left": left.id,
                    "right": right.id,
                    "score": round(match.score, 3),
                    "reasons": match.reasons,
                    "overridden": overridden,
                }
            )
    results.sort(key=lambda item: item["score"], reverse=True)
    return results
