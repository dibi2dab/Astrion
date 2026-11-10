"""Load topic modules from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from .models import Topic


@dataclass(frozen=True)
class LoadedTopic:
    topic: Topic
    path: Path


class TopicLoadError(ValueError):
    pass


def load_topics(content_dir: Path | str) -> list[LoadedTopic]:
    root = Path(content_dir)
    if not root.exists():
        return []

    loaded: list[LoadedTopic] = []
    seen: dict[str, Path] = {}
    for path in sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml")):
        if path.name.startswith("."):
            continue
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise TopicLoadError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(payload, dict):
            raise TopicLoadError(f"{path}: topic module must be a YAML mapping")
        try:
            topic = Topic.model_validate(payload)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            )
            raise TopicLoadError(f"{path}: {details}") from exc
        if topic.id in seen:
            raise TopicLoadError(
                f"duplicate topic id '{topic.id}' in {seen[topic.id]} and {path}"
            )
        seen[topic.id] = path
        loaded.append(LoadedTopic(topic=topic, path=path))
    return loaded


def topics_by_id(items: list[LoadedTopic]) -> dict[str, Topic]:
    return {item.topic.id: item.topic for item in items}
