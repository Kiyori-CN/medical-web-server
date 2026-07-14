from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any


# Product rules rather than locale collation. They keep the learning sequence
# stable across Windows, Linux and SQLite versions.
ROOT_ORDER = (
    "专硕考研（306临床医学综合能力）",
    "学硕考研（699联考西医综合）",
    "执业医师",
    "三基考试",
)

MODULE_ORDER = (
    "考点顺序",
    "历年真题",
    "年份刷题",
    "系统刷题",
    "学科刷题",
    "单科模考",
    "考点乱序",
    "考点狂背",
    "基本理论",
    "基本知识",
    "基本技术",
)

# The first six entries are the explicit 306 learning order requested for the
# product. The remaining entries extend the rule to the other exam trees.
SUBJECT_ORDER = (
    "生理学",
    "病理学",
    "内科学",
    "外科学",
    "生物化学",
    "人文精神",
    "人体解剖学",
    "解剖学",
    "组织学与胚胎学",
    "病理生理学",
    "药理学",
    "诊断学",
    "医学免疫学",
    "免疫学",
    "医学微生物学",
    "微生物学",
    "传染病学",
    "神经病学",
    "精神病学",
    "妇产科学",
    "儿科学",
    "预防医学",
    "医学统计学",
    "医学心理学",
    "医学伦理学",
    "卫生法",
    "中医学基础",
)

SYSTEM_ORDER = (
    "总论",
    "绪论",
    "细胞",
    "血液循环",
    "心血管",
    "循环系统",
    "呼吸",
    "消化",
    "泌尿",
    "肾",
    "血液",
    "代谢",
    "内分泌",
    "风湿",
    "免疫",
    "神经",
    "精神",
    "运动",
    "骨",
    "女性生殖",
    "生殖",
    "儿科",
    "传染",
    "感染",
    "肿瘤",
    "其他",
)

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_ORDINAL_RE = re.compile(r"第([零〇一二两三四五六七八九十百千\d]+)(?:篇|章|节|部分|单元|套)")
_PAREN_ORDINAL_RE = re.compile(r"^[（(]([零〇一二两三四五六七八九十百千\d]+)[）)]")
_YEAR_RE = re.compile(r"^(19\d{2}|20\d{2})(?:\D|$)")
_NUMBER_RE = re.compile(r"\d+")


def chinese_number(value: str) -> int | None:
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if any(character not in _CN_DIGITS and character not in _CN_UNITS for character in value):
        return None
    total = 0
    current = 0
    for character in value:
        if character in _CN_DIGITS:
            current = _CN_DIGITS[character]
            continue
        total += (current or 1) * _CN_UNITS[character]
        current = 0
    return total + current


def _rank_exact(name: str, ordered: tuple[str, ...]) -> int | None:
    normalized = _compact(name)
    for index, value in enumerate(ordered):
        expected = _compact(value)
        if normalized == expected or normalized.startswith(expected + "（"):
            return index
    return None


def _rank_contains(name: str, ordered: tuple[str, ...]) -> int | None:
    normalized = _compact(name)
    matches = [
        (index, len(_compact(value)))
        for index, value in enumerate(ordered)
        if _compact(value) in normalized
    ]
    return min(matches, key=lambda item: (item[0], -item[1]))[0] if matches else None


def library_sort_key(name: str, parent_path: str = "") -> tuple[Any, ...]:
    display_name = PurePosixPath(str(name).replace("\\", "/")).name
    stem = display_name.rsplit(".", 1)[0]
    parent = str(parent_path or "").replace("\\", "/")

    root_rank = _rank_exact(stem, ROOT_ORDER)
    if not parent and root_rank is not None:
        return (0, root_rank, (), _natural_key(stem))

    module_rank = _rank_exact(stem, MODULE_ORDER)
    if module_rank is not None:
        return (1, module_rank, (), _natural_key(stem))

    subject_rank = _rank_exact(stem, SUBJECT_ORDER)
    if subject_rank is not None:
        return (2, subject_rank, (), _natural_key(stem))

    ordinals = tuple(
        number
        for number in (chinese_number(value) for value in _ORDINAL_RE.findall(stem))
        if number is not None
    )
    parenthesized = _PAREN_ORDINAL_RE.match(stem)
    if parenthesized:
        number = chinese_number(parenthesized.group(1))
        if number is not None:
            ordinals = (*ordinals, number)
    if ordinals:
        return (3, 0, ordinals, _natural_key(stem))

    year = _YEAR_RE.match(stem)
    if year and ("历年真题" in parent or "年份刷题" in parent):
        return (4, -int(year.group(1)), (), _natural_key(stem))

    system_rank = _rank_contains(stem, SYSTEM_ORDER)
    if system_rank is not None:
        return (5, system_rank, (), _natural_key(stem))

    subject_contains_rank = _rank_contains(stem, SUBJECT_ORDER)
    if subject_contains_rank is not None:
        return (6, subject_contains_rank, (), _natural_key(stem))

    return (9, 0, (), _natural_key(stem))


def sort_library_entries(items: list[dict[str, Any]], parent_path: str = "") -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: library_sort_key(str(item.get("name", "")), parent_path),
    )


def question_type_label(raw_type: str, canonical_type: str) -> str:
    raw = str(raw_type or "").strip()
    canonical = str(canonical_type or "").strip() or "未知题型"
    aliases = {
        "A1": "A1型题", "A2": "A2型题", "A3": "A3型题", "A4": "A4型题",
        "A3/A4": "A3/A4型题", "B": "B型题", "B1": "B1型题", "X": "X型题",
    }
    compact = raw.replace(" ", "").upper().replace("型题", "")
    return aliases.get(compact, raw or canonical)


def _compact(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).replace(" ", "").strip()


def _natural_key(value: str) -> tuple[Any, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    parts: list[Any] = []
    cursor = 0
    for match in _NUMBER_RE.finditer(normalized):
        parts.append(normalized[cursor:match.start()])
        parts.append(int(match.group()))
        cursor = match.end()
    parts.append(normalized[cursor:])
    return tuple(parts)
