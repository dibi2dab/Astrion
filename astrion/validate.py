"""Cross-module validation beyond the per-topic Pydantic schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .duplicate import find_conflicts, unresolved, validate_pairs
from .loader import LoadedTopic
from .models import PrimarySourceKind, Topic


@dataclass(frozen=True)
class Issue:
    level: str
    code: str
    message: str
    topic_id: str = ""
    path: str = ""

    def format(self) -> str:
        location = self.topic_id or self.path or "project"
        return f"[{self.level.upper()}] {location}: {self.message}"


def _issue(level: str, code: str, message: str, topic: Topic | None = None, path: Path | None = None) -> Issue:
    return Issue(
        level=level,
        code=code,
        message=message,
        topic_id=topic.id if topic else "",
        path=str(path) if path else "",
    )


def validate_topics(
    loaded: Iterable[LoadedTopic],
    settings: Settings,
    published_records: Iterable[dict[str, Any]] = (),
    *,
    today: date | None = None,
) -> list[Issue]:
    items = list(loaded)
    topics = [item.topic for item in items]
    paths = {item.topic.id: item.path for item in items}
    issues: list[Issue] = []
    now = today or date.today()

    slugs: dict[str, str] = {}
    titles: dict[str, str] = {}
    aliases: dict[str, str] = {}
    keywords: dict[str, str] = {}

    for item in items:
        topic = item.topic
        path = item.path

        if topic.slug in slugs:
            issues.append(
                _issue("error", "duplicate_slug", f"slug also used by {slugs[topic.slug]}", topic, path)
            )
        slugs[topic.slug] = topic.id

        title_key = topic.title.casefold().strip()
        if title_key in titles:
            issues.append(
                _issue("error", "duplicate_title", f"title also used by {titles[title_key]}", topic, path)
            )
        titles[title_key] = topic.id

        keyword_key = topic.primary_keyword.casefold().strip()
        if keyword_key in keywords:
            issues.append(
                _issue(
                    "warning",
                    "shared_keyword",
                    f"primary keyword also used by {keywords[keyword_key]}; review duplicate risk",
                    topic,
                    path,
                )
            )
        keywords[keyword_key] = topic.id

        for alias in topic.aliases:
            key = alias.casefold().strip()
            if key in aliases and aliases[key] != topic.id:
                issues.append(
                    _issue("error", "duplicate_alias", f"alias '{alias}' also used by {aliases[key]}", topic, path)
                )
            aliases[key] = topic.id

        if path.stem != topic.slug:
            issues.append(
                _issue(
                    "warning",
                    "filename_mismatch",
                    f"filename '{path.name}' should match slug '{topic.slug}.yaml'",
                    topic,
                    path,
                )
            )

        if topic.status == "active" and topic.provenance.review_status != "approved":
            issues.append(
                _issue(
                    "warning",
                    "not_approved",
                    "active topic is not editorially approved; build will block it",
                    topic,
                    path,
                )
            )

        if topic.content_type.value in settings.quality.require_primary_source_for:
            if not any(source.kind in PrimarySourceKind for source in topic.sources):
                issues.append(
                    _issue(
                        "error",
                        "primary_source_required",
                        "this content type requires at least one peer-reviewed, agency, or standards source",
                        topic,
                        path,
                    )
                )

        if topic.provenance.fact_basis == "editorial_synthesis" and len(topic.sources) < 2:
            issues.append(
                _issue(
                    "warning",
                    "synthesis_needs_sources",
                    "editorial synthesis should cite at least two sources",
                    topic,
                    path,
                )
            )

        if topic.primary_keyword.casefold() not in topic.title.casefold():
            issues.append(
                _issue(
                    "warning",
                    "keyword_missing_from_title",
                    f"primary keyword '{topic.primary_keyword}' is not in the title",
                    topic,
                    path,
                )
            )

        for source in [*topic.sources, *topic.further_reading]:
            if source.accessed_at > now + timedelta(days=1):
                issues.append(
                    _issue(
                        "warning",
                        "future_access_date",
                        f"source '{source.id}' has a future accessed_at date",
                        topic,
                        path,
                    )
                )
            if source.published_at and source.published_at > now + timedelta(days=1):
                issues.append(
                    _issue(
                        "warning",
                        "future_publish_date",
                        f"source '{source.id}' has a future publication date",
                        topic,
                        path,
                    )
                )

        for related_id in topic.related:
            if related_id not in paths:
                issues.append(
                    _issue(
                        "error",
                        "unknown_related_topic",
                        f"related topic '{related_id}' does not exist",
                        topic,
                        path,
                    )
                )

    for pair in validate_pairs(topics, settings.duplicates):
        left = next(topic for topic in topics if topic.id == pair["left"])
        right = next(topic for topic in topics if topic.id == pair["right"])
        both_active = left.status == "active" and right.status == "active"
        level = "error" if both_active and not pair["overridden"] else "warning"
        issues.append(
            Issue(
                level=level,
                code="topic_overlap",
                message=(
                    f"possible overlap with '{pair['right']}' "
                    f"(score {pair['score']:.2f}: {', '.join(pair['reasons'])})"
                ),
                topic_id=pair["left"],
                path=str(paths.get(pair["left"], "")),
            )
        )

    for item in items:
        if item.topic.status != "active":
            continue
        conflicts = unresolved(find_conflicts(item.topic, published_records, settings.duplicates))
        for conflict in conflicts:
            issues.append(
                _issue(
                    "error",
                    "published_duplicate",
                    f"overlaps already published topic '{conflict.record.topic_id}' "
                    f"(score {conflict.score:.2f}: {', '.join(conflict.reasons)})",
                    item.topic,
                    item.path,
                )
            )

    issues.sort(key=lambda issue: (issue.level != "error", issue.topic_id, issue.code))
    return issues


def counts(issues: Iterable[Issue]) -> tuple[int, int]:
    errors = sum(1 for issue in issues if issue.level == "error")
    warnings = sum(1 for issue in issues if issue.level == "warning")
    return errors, warnings
