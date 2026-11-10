"""Validated content model for one Astrion topic module."""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Annotated, Literal, Union
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .text import contains_hangul, contains_placeholder, sha256_text, slugify


class Category(str, Enum):
    MATHEMATICS = "Mathematics"
    PHYSICS = "Physics"
    CHEMISTRY = "Chemistry"
    BIOLOGY = "Biology & Life Sciences"
    SPACE = "Space & Astronomy"
    EARTH = "Earth & Natural Sciences"
    COMPUTING = "Computing & Formal Sciences"
    ENGINEERING = "Engineering & Applied Sciences"
    EMERGING = "Emerging & Interdisciplinary Science"


class ContentType(str, Enum):
    LATEST_DISCOVERY = "latest-discovery"
    BREAKTHROUGH = "scientific-breakthrough"
    UNSOLVED_PROBLEM = "unsolved-problem"
    MATHEMATICS_EXPLAINED = "mathematics-explained"
    SCIENCE_EXPLAINED = "science-explained"
    RESEARCH_ANALYSIS = "research-analysis"
    EXPERIMENTS = "experiments-data-modeling"
    EMERGING = "emerging-science"
    BASIC_KNOWLEDGE = "basic-knowledge"


class SourceKind(str, Enum):
    TEXTBOOK = "textbook"
    PEER_REVIEWED = "peer_reviewed"
    AGENCY = "agency"
    STANDARD = "standard"
    REFERENCE = "reference"
    DATA = "data"


PrimarySourceKind = {SourceKind.PEER_REVIEWED, SourceKind.AGENCY, SourceKind.STANDARD}


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    title: str = Field(min_length=3, max_length=300)
    publisher: str = Field(min_length=2, max_length=160)
    url: str = Field(min_length=10, max_length=500)
    kind: SourceKind = SourceKind.REFERENCE
    published_at: date | None = None
    accessed_at: date
    doi: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source url must be an absolute http(s) URL")
        host = parsed.netloc.lower()
        if host in {"localhost", "example.com", "www.example.com"} or host.endswith(".example.com"):
            raise ValueError("source url must not be a placeholder")
        if "." not in host:
            raise ValueError("source url must use a public host name")
        return value

    @field_validator("doi")
    @classmethod
    def _valid_doi(cls, value: str | None) -> str | None:
        if value and not re.match(r"^10\.\d{4,9}/\S+$", value):
            raise ValueError("doi must look like 10.xxxx/suffix")
        return value


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _no_hangul(cls, value: str) -> str:
        if contains_hangul(value):
            raise ValueError("post body must be English-only")
        return value


class MathBlock(BaseModel):
    type: Literal["math"] = "math"
    latex: str = Field(min_length=1)
    display: bool = True
    caption: str | None = None


class EquationVariable(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    meaning: str = Field(min_length=3, max_length=160)
    unit: str = Field(default="", max_length=40)


class EquationBlock(BaseModel):
    type: Literal["equation"] = "equation"
    latex: str = Field(min_length=1)
    variables: list[EquationVariable] = Field(min_length=1, max_length=10)
    caption: str | None = None


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(min_length=1, max_length=12)


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    caption: str = Field(min_length=3, max_length=200)
    headers: list[str] = Field(min_length=2, max_length=6)
    rows: list[list[str]] = Field(min_length=1, max_length=30)

    @field_validator("rows")
    @classmethod
    def _rectangular(cls, rows: list[list[str]]) -> list[list[str]]:
        if not rows:
            return rows
        width = len(rows[0])
        if width < 2 or width > 6:
            raise ValueError("table rows must contain 2..6 columns")
        if any(len(row) != width for row in rows):
            raise ValueError("every table row must have the same number of cells")
        return rows

    @model_validator(mode="after")
    def _width_matches_headers(self) -> "TableBlock":
        if self.rows and len(self.headers) != len(self.rows[0]):
            raise ValueError("table headers and rows must have the same number of columns")
        return self


class CalloutBlock(BaseModel):
    type: Literal["callout"] = "callout"
    tone: Literal["key", "note", "warning"] = "note"
    title: str | None = Field(default=None, max_length=120)
    text: str = Field(min_length=1)


class ExampleBlock(BaseModel):
    type: Literal["example"] = "example"
    title: str = Field(min_length=3, max_length=160)
    steps: list[str] = Field(min_length=1, max_length=12)
    conclusion: str | None = None


Block = Annotated[
    Union[
        ParagraphBlock,
        MathBlock,
        EquationBlock,
        ListBlock,
        TableBlock,
        CalloutBlock,
        ExampleBlock,
    ],
    Field(discriminator="type"),
]


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str = Field(min_length=3, max_length=140)
    blocks: list[Block] = Field(min_length=1, max_length=40)


class Misconception(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=5, max_length=240)
    correction: str = Field(min_length=20, max_length=1200)


class DistinctOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str
    note: str = Field(min_length=40, max_length=600)


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authored_by: str = Field(default="Astrion editorial", min_length=2, max_length=120)
    fact_basis: Literal["textbook", "peer_reviewed", "agency_data", "editorial_synthesis"]
    reviewed_at: date
    review_status: Literal["draft", "reviewed", "approved"] = "draft"
    notes: str = Field(default="", max_length=1000)


class Topic(BaseModel):
    """One topic module. One published Blogger post is generated from one module."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["astrion.topic/v1"] = Field(default="astrion.topic/v1", alias="schema")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+$")
    status: Literal["draft", "active", "paused"] = "draft"
    content_type: ContentType
    main_category: Category
    labels: list[str] = Field(default_factory=list, max_length=4)
    title: str = Field(min_length=20, max_length=80)
    seo_title: str | None = Field(default=None, min_length=20, max_length=80)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    meta_description: str = Field(min_length=80, max_length=160)
    primary_keyword: str = Field(min_length=3, max_length=80)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=8)
    hook: str = Field(min_length=60, max_length=1200)
    core_question: str = Field(min_length=30, max_length=400)
    explanation_target: str = Field(min_length=40, max_length=500)
    central_principle: str = Field(min_length=40, max_length=600)
    unique_value: str = Field(min_length=80, max_length=900)
    key_takeaways: list[str] = Field(min_length=3, max_length=5)
    sections: list[Section] = Field(min_length=3, max_length=10)
    misconceptions: list[Misconception] = Field(default_factory=list, max_length=8)
    sources: list[Source] = Field(default_factory=list, max_length=12)
    further_reading: list[Source] = Field(default_factory=list, max_length=8)
    related: list[str] = Field(default_factory=list, max_length=8)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    distinct_from: list[DistinctOverride] = Field(default_factory=list, max_length=8)
    provenance: Provenance
    priority: int = Field(default=3, ge=1, le=5)
    evergreen: bool = True
    notes: str = Field(default="", max_length=1000)

    @field_validator("title", "meta_description", "hook", "core_question", "explanation_target",
                     "central_principle", "unique_value", "seo_title")
    @classmethod
    def _english_only(cls, value: str | None) -> str | None:
        if value and contains_hangul(value):
            raise ValueError("Astrion posts are English-only")
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> "Topic":
        if self.seo_title == "":
            self.seo_title = None

        seen: set[str] = set()
        for label in self.labels:
            key = label.casefold().strip()
            if not key:
                raise ValueError("labels must not be empty")
            if key == self.main_category.value.casefold():
                raise ValueError("do not repeat the main category in labels")
            if key in seen:
                raise ValueError(f"duplicate label: {label}")
            seen.add(key)

        headings = [section.heading.casefold().strip() for section in self.sections]
        if len(set(headings)) != len(headings):
            raise ValueError("section headings must be unique")

        if self.id in self.related:
            raise ValueError("a topic cannot relate to itself")
        if len(set(self.related)) != len(self.related):
            raise ValueError("related topic ids must be unique")
        for related_id in self.related:
            if not re.match(r"^[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+$", related_id):
                raise ValueError(f"invalid related topic id: {related_id}")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("aliases must be unique")
        if len({item.topic_id for item in self.distinct_from}) != len(self.distinct_from):
            raise ValueError("distinct_from topic ids must be unique")
        for override in self.distinct_from:
            if override.topic_id == self.id:
                raise ValueError("distinct_from must reference another topic")

        source_ids = [source.id for source in [*self.sources, *self.further_reading]]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source ids must be unique within a topic")

        prose = self._prose_strings()
        if any(contains_hangul(value) for value in prose):
            raise ValueError("Astrion posts are English-only")
        if any(contains_placeholder(value) for value in prose):
            raise ValueError("prose contains a placeholder marker")

        if self.slug != slugify(self.slug, fallback="post"):
            raise ValueError("slug must already be lowercase kebab-case")
        return self

    def _prose_strings(self) -> list[str]:
        values: list[str] = [
            self.title,
            self.seo_title or "",
            self.meta_description,
            self.primary_keyword,
            *self.secondary_keywords,
            self.hook,
            self.core_question,
            self.explanation_target,
            self.central_principle,
            self.unique_value,
            *self.key_takeaways,
            *self.labels,
            *self.aliases,
        ]
        for section in self.sections:
            values.append(section.heading)
            for block in section.blocks:
                values.extend(_block_strings(block))
        for item in self.misconceptions:
            values.extend([item.claim, item.correction])
        for source in [*self.sources, *self.further_reading]:
            values.extend([source.title, source.publisher, source.note or ""])
        values.extend([self.provenance.authored_by, self.provenance.notes, self.notes])
        return [value for value in values if value]

    @property
    def signature_text(self) -> str:
        return " ".join(
            [
                self.core_question,
                self.explanation_target,
                self.central_principle,
            ]
        )

    @property
    def fingerprint(self) -> str:
        return sha256_text(f"{self.id}|{self.signature_text}")

    @property
    def has_math(self) -> bool:
        for section in self.sections:
            for block in section.blocks:
                if block.type in {"math", "equation"}:
                    return True
                if block.type == "paragraph" and ("\\(" in block.text or "\\[" in block.text):
                    return True
        return False

    def plain_text(self) -> str:
        parts: list[str] = [
            self.title,
            self.hook,
            *self.key_takeaways,
        ]
        for section in self.sections:
            parts.append(section.heading)
            for block in section.blocks:
                parts.extend(_block_strings(block))
        for item in self.misconceptions:
            parts.extend([item.claim, item.correction])
        return "\n\n".join(parts)


def _block_strings(block: BaseModel) -> list[str]:
    if isinstance(block, ParagraphBlock):
        return [block.text]
    if isinstance(block, MathBlock):
        return [block.latex, block.caption or ""]
    if isinstance(block, EquationBlock):
        return [block.latex, block.caption or "", *(f"{v.symbol} {v.meaning} {v.unit}" for v in block.variables)]
    if isinstance(block, ListBlock):
        return list(block.items)
    if isinstance(block, TableBlock):
        return [block.caption, *block.headers, *(cell for row in block.rows for cell in row)]
    if isinstance(block, CalloutBlock):
        return [block.title or "", block.text]
    if isinstance(block, ExampleBlock):
        return [block.title, *block.steps, block.conclusion or ""]
    return []
