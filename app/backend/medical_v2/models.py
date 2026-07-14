from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SourceFile:
    absolute_path: Path
    relative_path: str
    extension: str
    sha256: str
    size_bytes: int
    exam: str
    collection_kind: str
    subject: str
    title: str
    year: int | None = None


@dataclass(frozen=True)
class RawQuestion:
    source: SourceFile
    sheet_name: str
    row_number: int
    position: int
    raw_type: str
    stem: str
    options: tuple[str, ...] = field(default_factory=tuple)
    answer: str = ""
    point: str = ""
    explanation: str = ""


@dataclass(frozen=True)
class NormalizedQuestion:
    question_id: str
    fingerprint: str
    canonical_type: str
    raw_type: str
    source_code: str
    stem: str
    normalized_stem: str
    options: tuple[str, ...]
    normalized_options: tuple[str, ...]
    answer: str
    normalized_answer: str
    point: str
    explanation: str
    quality_score: int
    version_id: str


@dataclass(frozen=True)
class QuestionGroupMembership:
    group_id: str
    question_id: str
    shared_stem: str
    normalized_shared_stem: str
    sub_stem: str
    normalized_sub_stem: str
    position: int


@dataclass
class BuildStats:
    source_files: int = 0
    question_occurrences: int = 0
    unique_questions: int = 0
    question_versions: int = 0
    conflicts: int = 0
    collections: int = 0
    question_groups: int = 0
    warnings: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source_files": self.source_files,
            "question_occurrences": self.question_occurrences,
            "unique_questions": self.unique_questions,
            "question_versions": self.question_versions,
            "conflicts": self.conflicts,
            "collections": self.collections,
            "question_groups": self.question_groups,
            "warning_count": len(self.warnings),
        }


def option_text(options: Iterable[str]) -> str:
    return "\n".join(f"{chr(65 + index)}. {value}" for index, value in enumerate(options))
