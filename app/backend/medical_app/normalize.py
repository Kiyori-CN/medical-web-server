from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from .models import NormalizedQuestion, QuestionGroupMembership, RawQuestion


_WHITESPACE_RE = re.compile(r"\s+")
_OPTION_PREFIX_RE = re.compile(r"^\s*([A-H])\s*[.．、。:]\s*", re.IGNORECASE)
_SOURCE_CODE_RE = re.compile(
    r"^\s*((?:19|20)\d{2}(?:(?:[A-Z][A-Z0-9]*|\d+)(?:-[A-Z0-9]+)*))"
    r"(?:-(?:临床|基础))?(?=$|\s|[\u3400-\u4dbf\u4e00-\u9fff(（])\s*",
    re.IGNORECASE,
)
_LEADING_NUMBER_TYPE_RE = re.compile(r"^\s*\d+\s*[.．、)]\s*\[([^\]]+)]\s*")
_EXPLICIT_GROUP_RE = re.compile(
    r"^\s*共用题干\s*[：:]\s*(.+?)\s*问题\s*[：:]\s*(.+)$",
    re.IGNORECASE,
)
_GROUP_BOUNDARIES = "。！？；;，,"
_MIN_INFERRED_GROUP_PREFIX = 40

_TYPE_ALIASES = {
    "a1型题": "单选题",
    "a2型题": "单选题",
    "a3型题": "单选题",
    "a4型题": "单选题",
    "a3/a4型题": "单选题",
    "b型题": "单选题",
    "b1型题": "单选题",
    "c型题": "单选题",
    "x型题": "多选题",
    "单选题": "单选题",
    "多选题": "多选题",
    "判断题": "判断题",
    "填空题": "填空题",
    "简答题": "简答题",
    "名词解释": "名词解释",
    "论述题": "论述题",
    "知识点": "知识点",
}


def display_text(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).replace("\u00a0", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def key_text(value: object) -> str:
    return display_text(value).casefold().replace(" ", "")


def clean_option(value: object, expected_label: str | None = None) -> str:
    text = display_text(value)
    match = _OPTION_PREFIX_RE.match(text)
    if match and (expected_label is None or match.group(1).upper() == expected_label.upper()):
        text = text[match.end() :].strip()
    return text


def extract_source_code(stem: str) -> tuple[str, str, str]:
    text = display_text(stem)
    raw_type = ""
    number_type = _LEADING_NUMBER_TYPE_RE.match(text)
    if number_type:
        raw_type = display_text(number_type.group(1))
        text = text[number_type.end() :].strip()

    source_code = ""
    source_match = _SOURCE_CODE_RE.match(text)
    if source_match:
        source_code = source_match.group(1).upper()
        text = text[source_match.end() :].strip()
    return source_code, raw_type, text


def canonical_question_type(raw_type: str, answer: str, options: tuple[str, ...]) -> str:
    normalized = key_text(raw_type)
    if normalized in _TYPE_ALIASES:
        return _TYPE_ALIASES[normalized]
    compact_answer = re.sub(r"[^A-H]", "", display_text(answer).upper())
    if options:
        return "多选题" if len(compact_answer) > 1 else "单选题"
    return "未知题型"


def normalize_answer(answer: str, canonical_type: str) -> str:
    text = display_text(answer)
    if canonical_type in {"单选题", "多选题"}:
        labels = re.findall(r"[A-H]", text.upper())
        unique = list(dict.fromkeys(labels))
        return "".join(sorted(unique) if canonical_type == "多选题" else unique[:1])
    return key_text(text)


def _digest(prefix: str, payload: object, length: int = 24) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:length]}"


def normalize_question(raw: RawQuestion) -> NormalizedQuestion:
    source_code, type_from_stem, stem_without_code = extract_source_code(raw.stem)
    raw_type = display_text(raw.raw_type) or type_from_stem
    options = tuple(
        clean_option(value, chr(65 + index))
        for index, value in enumerate(raw.options)
        if display_text(value)
    )
    canonical_type = canonical_question_type(raw_type, raw.answer, options)
    normalized_stem = key_text(stem_without_code)
    normalized_options = tuple(key_text(value) for value in options)
    fingerprint_payload = {
        "type": canonical_type,
        "stem": normalized_stem,
        "options": normalized_options,
    }
    fingerprint = _digest("fp", fingerprint_payload, length=32)
    question_id = fingerprint.replace("fp_", "q_", 1)
    answer = display_text(raw.answer)
    normalized_answer = normalize_answer(answer, canonical_type)
    point = display_text(raw.point)
    explanation = display_text(raw.explanation)
    quality_score = (
        (100_000 if normalized_answer else 0)
        + min(len(explanation), 50_000)
        + min(len(point), 25_000)
        + len(options) * 100
    )
    version_payload = {
        "question_id": question_id,
        "raw_type": raw_type,
        "source_code": source_code,
        "stem": display_text(raw.stem),
        "options": options,
        "answer": answer,
        "point": point,
        "explanation": explanation,
    }
    return NormalizedQuestion(
        question_id=question_id,
        fingerprint=fingerprint,
        canonical_type=canonical_type,
        raw_type=raw_type,
        source_code=source_code,
        stem=stem_without_code,
        normalized_stem=normalized_stem,
        options=options,
        normalized_options=normalized_options,
        answer=answer,
        normalized_answer=normalized_answer,
        point=point,
        explanation=explanation,
        quality_score=quality_score,
        version_id=_digest("qv", version_payload, length=32),
    )


def infer_question_groups(
    questions: list[NormalizedQuestion],
) -> tuple[QuestionGroupMembership | None, ...]:
    """Extract A3/A4 shared stems without changing canonical question text.

    Explicit ``共用题干: ... 问题: ...`` records are authoritative. For older
    files that repeat a case stem in consecutive A3/A4 rows, a group is inferred
    only when adjacent rows share a punctuation-terminated prefix of at least 40
    normalized characters.
    """

    memberships: list[QuestionGroupMembership | None] = [None] * len(questions)
    explicit_positions: dict[str, int] = {}

    for index, question in enumerate(questions):
        if key_text(question.raw_type) != "a3/a4型题":
            continue
        match = _EXPLICIT_GROUP_RE.match(question.stem)
        if not match:
            continue
        shared_stem = display_text(match.group(1))
        sub_stem = display_text(match.group(2))
        normalized_shared = key_text(shared_stem)
        if not normalized_shared or not key_text(sub_stem):
            continue
        group_id = _digest("grp", normalized_shared, length=32)
        position = explicit_positions.get(group_id, 0) + 1
        explicit_positions[group_id] = position
        memberships[index] = QuestionGroupMembership(
            group_id=group_id,
            question_id=question.question_id,
            shared_stem=shared_stem,
            normalized_shared_stem=normalized_shared,
            sub_stem=sub_stem,
            normalized_sub_stem=key_text(sub_stem),
            position=position,
        )

    run_start: int | None = None
    for index in range(len(questions) - 1):
        left = questions[index]
        right = questions[index + 1]
        shared = ""
        if (
            memberships[index] is None
            and memberships[index + 1] is None
            and key_text(left.raw_type) == "a3/a4型题"
            and key_text(right.raw_type) == "a3/a4型题"
        ):
            shared = _shared_prefix((left.stem, right.stem))
        if shared:
            if run_start is None:
                run_start = index
            continue
        if run_start is not None:
            _assign_inferred_group(questions, memberships, run_start, index)
            run_start = None
    if run_start is not None:
        _assign_inferred_group(questions, memberships, run_start, len(questions) - 1)

    return tuple(memberships)


def _assign_inferred_group(
    questions: list[NormalizedQuestion],
    memberships: list[QuestionGroupMembership | None],
    start: int,
    end: int,
) -> None:
    shared_stem = _shared_prefix(tuple(question.stem for question in questions[start : end + 1]))
    normalized_shared = key_text(shared_stem)
    if len(normalized_shared) < _MIN_INFERRED_GROUP_PREFIX:
        return
    group_id = _digest("grp", normalized_shared, length=32)
    indexes = list(range(start, end + 1))
    sub_stems = [
        display_text(questions[index].stem[len(shared_stem) :]).lstrip(" ：:，,。")
        for index in indexes
    ]
    if any(not sub_stem for sub_stem in sub_stems):
        return
    for position, (index, sub_stem) in enumerate(zip(indexes, sub_stems, strict=True), start=1):
        question = questions[index]
        memberships[index] = QuestionGroupMembership(
            group_id=group_id,
            question_id=question.question_id,
            shared_stem=shared_stem,
            normalized_shared_stem=normalized_shared,
            sub_stem=sub_stem,
            normalized_sub_stem=key_text(sub_stem),
            position=position,
        )


def _shared_prefix(stems: tuple[str, ...]) -> str:
    if len(stems) < 2:
        return ""
    prefix = display_text(stems[0])
    for stem in stems[1:]:
        candidate = display_text(stem)
        common_length = 0
        for left, right in zip(prefix, candidate):
            if left != right:
                break
            common_length += 1
        prefix = prefix[:common_length]
        if len(key_text(prefix)) < _MIN_INFERRED_GROUP_PREFIX:
            return ""
    boundary = max(prefix.rfind(character) for character in _GROUP_BOUNDARIES)
    if boundary < 0:
        return ""
    shared = display_text(prefix[: boundary + 1])
    return shared if len(key_text(shared)) >= _MIN_INFERRED_GROUP_PREFIX else ""
