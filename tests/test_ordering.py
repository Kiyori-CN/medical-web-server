from medical_app.ordering import (
    chinese_number,
    normalize_practice_order_mode,
    question_type_label,
    question_type_order,
    question_year,
    sort_library_entries,
    sort_practice_items,
)


def _names(values, parent=""):
    return [
        item["name"]
        for item in sort_library_entries([{"name": value} for value in values], parent)
    ]


def test_fixed_medical_subject_and_module_order():
    values = ["人文精神", "内科学", "病理学", "生物化学", "外科学", "生理学"]
    assert _names(values) == ["生理学", "病理学", "内科学", "外科学", "生物化学", "人文精神"]
    assert _names(["考点乱序", "历年真题", "考点顺序"]) == ["考点顺序", "历年真题", "考点乱序"]


def test_chapter_parenthesized_and_year_order_are_numeric():
    chapters = ["第十二章 生殖.xlsx", "第二章 细胞.xlsx", "第一章 绪论.xlsx", "第十章 神经.xlsx"]
    assert _names(chapters) == ["第一章 绪论.xlsx", "第二章 细胞.xlsx", "第十章 神经.xlsx", "第十二章 生殖.xlsx"]
    sections = ["（十）肺栓塞.xlsx", "（二）肺高压.xlsx", "（一）慢阻肺.xlsx"]
    assert _names(sections) == ["（一）慢阻肺.xlsx", "（二）肺高压.xlsx", "（十）肺栓塞.xlsx"]
    assert _names(["2024", "2000", "2026"], "专硕/历年真题") == ["2026", "2024", "2000"]
    assert chinese_number("七十三") == 73


def test_outline_preserves_original_question_type_labels():
    assert question_type_label("A1型题", "单选题") == "A1型题"
    assert question_type_label("B1", "单选题") == "B1型题"
    assert question_type_label("", "判断题") == "判断题"


def test_practice_ordering_uses_original_position_type_and_year_metadata():
    items = [
        {"collection_position": 1, "occurrence_id": 1, "type_order": 0, "question_year": 2020},
        {"collection_position": 2, "occurrence_id": 2, "type_order": 20, "question_year": None},
        {"collection_position": 3, "occurrence_id": 3, "type_order": 2, "question_year": 2007},
        {"collection_position": 4, "occurrence_id": 4, "type_order": 2, "question_year": 2007},
    ]

    positions = lambda mode: [
        item["collection_position"] for item in sort_practice_items(items, mode)
    ]
    assert positions("sequential") == [1, 2, 3, 4]
    assert positions("position_desc") == [4, 3, 2, 1]
    assert positions("type") == [1, 3, 4, 2]
    assert positions("type_desc") == [2, 3, 4, 1]
    assert positions("year_desc") == [1, 3, 4, 2]
    assert positions("year_asc") == [3, 4, 1, 2]

    assert question_year("2020N1A") == 2020
    assert question_year("", 2026) == 2026
    assert question_year("无年份", None) is None
    assert question_type_order("A3/A4型题", "单选题") < question_type_order(
        "填空题", "填空题"
    )
    assert normalize_practice_order_mode("YEAR_DESC") == "year_desc"
    assert normalize_practice_order_mode("TYPE_DESC") == "type_desc"

    missing_type_order = [
        {"collection_position": 1, "occurrence_id": 1},
        {"collection_position": 2, "occurrence_id": 2, "type_order": 1},
    ]
    assert [
        item["collection_position"]
        for item in sort_practice_items(missing_type_order, "type")
    ] == [2, 1]
