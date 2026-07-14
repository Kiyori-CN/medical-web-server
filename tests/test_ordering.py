from medical_app.ordering import (
    chinese_number,
    question_type_label,
    sort_library_entries,
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
