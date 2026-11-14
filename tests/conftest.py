"""Shared fixtures for the Astrion test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from astrion.config import Settings
from astrion.loader import LoadedTopic, load_topics
from astrion.models import Topic
from astrion.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
CONTENT_DIR = ROOT / "content"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "root": tmp_path,
            "paths": {
                "content": str(CONTENT_DIR),
                "state": str(tmp_path / "data" / "state.json"),
                "queue": str(tmp_path / "data" / "queue"),
                "archive": str(tmp_path / "data" / "archive"),
                "dist": str(tmp_path / "dist"),
            },
        }
    )


@pytest.fixture
def loaded() -> list[LoadedTopic]:
    """Load every module with the long-standing seed topics first.

    Several tests use ``topics[0]`` and ``topics[1]`` as stable examples. New
    draft modules must not change which topics those indexes refer to, so the
    seed modules keep a fixed position and everything else follows by id.
    """

    seed_order = ("math.division-by-zero", "physics.rayleigh-scattering-sky")

    def order(item: LoadedTopic) -> tuple[int, str]:
        topic_id = item.topic.id
        if topic_id in seed_order:
            return (seed_order.index(topic_id), topic_id)
        return (len(seed_order), topic_id)

    return sorted(load_topics(CONTENT_DIR), key=order)


@pytest.fixture
def topics(loaded: list[LoadedTopic]) -> list[Topic]:
    return [item.topic for item in loaded]


@pytest.fixture
def state(settings: Settings) -> StateStore:
    return StateStore(settings.state_path)


def topic_payload(topic: Topic) -> dict[str, Any]:
    """Return a validated topic payload that can be edited and re-validated."""

    return topic.model_dump(by_alias=True)
