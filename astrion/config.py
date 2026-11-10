"""Project configuration and filesystem conventions."""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def default_root() -> Path:
    env = os.environ.get("ASTRION_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "config" / "astrion.yaml").exists():
        return cwd.resolve()
    return Path(__file__).resolve().parents[1]


class SiteSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "Astrion"
    language: str = "en-US"
    timezone: str = "America/New_York"
    posts_per_day: int = Field(default=2, ge=1, le=6)
    publish_slots: list[str] = Field(default_factory=lambda: ["08:30", "15:30"])

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("publish_slots")
    @classmethod
    def _valid_slots(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("publish_slots must not be empty")
        for value in values:
            try:
                time.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"invalid publish slot: {value}") from exc
        return values

    @model_validator(mode="after")
    def _enough_slots(self) -> "SiteSettings":
        if len(self.publish_slots) < self.posts_per_day:
            raise ValueError("publish_slots must provide at least posts_per_day entries")
        return self


class QualitySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    publish_threshold: float = Field(default=85, ge=0, le=100)
    revise_threshold: float = Field(default=70, ge=0, le=100)
    min_words_default: int = Field(default=600, ge=200)
    min_words: dict[str, int] = Field(
        default_factory=lambda: {
            "latest-discovery": 700,
            "scientific-breakthrough": 700,
            "unsolved-problem": 700,
            "mathematics-explained": 700,
            "science-explained": 700,
            "research-analysis": 800,
            "experiments-data-modeling": 700,
            "emerging-science": 700,
            "basic-knowledge": 600,
        }
    )
    max_keyword_density: float = Field(default=0.025, ge=0.001, le=0.10)
    require_primary_source_for: list[str] = Field(
        default_factory=lambda: [
            "latest-discovery",
            "scientific-breakthrough",
            "research-analysis",
        ]
    )

    def min_words_for(self, content_type: str) -> int:
        return int(self.min_words.get(content_type, self.min_words_default))


class DuplicateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_threshold: float = Field(default=0.70, ge=0, le=1)
    sequence_threshold: float = Field(default=0.85, ge=0, le=1)
    title_threshold: float = Field(default=0.92, ge=0, le=1)


class BloggerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_draft: bool = True
    max_secondary_labels: int = Field(default=4, ge=0, le=10)
    max_per_run: int = Field(default=1, ge=1, le=5)
    max_attempts: int = Field(default=3, ge=1, le=10)
    timeout_seconds: int = Field(default=30, ge=5, le=120)


class PathSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = "content"
    queue: str = "data/queue"
    archive: str = "data/archive"
    state: str = "data/state.json"
    dist: str = "dist"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: SiteSettings = Field(default_factory=SiteSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    duplicates: DuplicateSettings = Field(default_factory=DuplicateSettings)
    blogger: BloggerSettings = Field(default_factory=BloggerSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    root: Path = Field(default_factory=default_root, exclude=True)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.site.timezone)

    @property
    def content_dir(self) -> Path:
        return self._resolve(self.paths.content)

    @property
    def state_path(self) -> Path:
        return self._resolve(self.paths.state)

    @property
    def queue_dir(self) -> Path:
        return self._resolve(self.paths.queue)

    @property
    def archive_dir(self) -> Path:
        return self._resolve(self.paths.archive)

    @property
    def dist_dir(self) -> Path:
        return self._resolve(self.paths.dist)

    @property
    def preview_dir(self) -> Path:
        return self.dist_dir / "preview"

    @property
    def report_dir(self) -> Path:
        return self.dist_dir / "reports"

    def _resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.root / path)

    def with_paths(self, *, state: Path | None = None, dist: Path | None = None) -> "Settings":
        payload = self.model_dump(exclude={"root"})
        if state is not None:
            payload["paths"]["state"] = str(state)
        if dist is not None:
            payload["paths"]["dist"] = str(dist)
        return Settings.model_validate({**payload, "root": self.root})


def load_settings(root: Path | str | None = None, config_path: Path | str | None = None) -> Settings:
    project_root = Path(root).expanduser().resolve() if root else default_root()
    path = Path(config_path) if config_path else project_root / "config" / "astrion.yaml"
    data: dict = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"configuration must be a mapping: {path}")
        data = loaded
    data.setdefault("root", project_root)
    return Settings.model_validate(data)


def list_categories() -> list[str]:
    from .models import Category

    return [category.value for category in Category]
