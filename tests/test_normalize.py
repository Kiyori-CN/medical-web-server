from pathlib import Path

from medical_app.models import RawQuestion, SourceFile
from medical_app.normalize import infer_question_groups, normalize_question


def _source() -> SourceFile:
    return SourceFile(
        absolute_path=Path("sample.xlsx"),
        relative_path="执业医师/年份刷题/2020/sample.xlsx",
        extension=".xlsx",
        sha256="0" * 64,
        size_bytes=1,
        exam="执业医师",
        collection_kind="年份刷题",
        subject="2020",
        title="sample",
        year=2020,
    )


def test_source_code_and_option_labels_do_not_change_question_identity():
    first = RawQuestion(
        source=_source(),
        sheet_name="题目",
        row_number=2,
        position=1,
        raw_type="A1型题",
        stem="2020N1A 细胞膜运输方式属于",
        options=("A.易化扩散", "B.主动转运"),
        answer="A",
    )
    second = RawQuestion(
        source=_source(),
        sheet_name="题目",
        row_number=3,
        position=2,
        raw_type="单选题",
        stem="细胞膜运输方式属于",
        options=("易化扩散", "主动转运"),
        answer="B",
    )

    normalized_first = normalize_question(first)
    normalized_second = normalize_question(second)

    assert normalized_first.question_id == normalized_second.question_id
    assert normalized_first.source_code == "2020N1A"
    assert normalized_first.options == ("易化扩散", "主动转运")
    assert normalized_first.canonical_type == "单选题"
    assert normalized_first.version_id != normalized_second.version_id


def test_multiple_choice_answer_is_normalized_without_affecting_fingerprint():
    raw = RawQuestion(
        source=_source(),
        sheet_name="题目",
        row_number=2,
        position=1,
        raw_type="X型题",
        stem="可出现的表现有",
        options=("甲", "乙", "丙", "丁"),
        answer="A、C、D",
    )
    normalized = normalize_question(raw)
    assert normalized.canonical_type == "多选题"
    assert normalized.normalized_answer == "ACD"


def test_source_code_variants_and_a3_a4_groups_are_structured():
    raw_questions = [
        RawQuestion(
            source=_source(),
            sheet_name="题目",
            row_number=2 + index,
            position=1 + index,
            raw_type="A3/A4型题",
            stem=stem,
            options=("甲", "乙"),
            answer="A",
        )
        for index, stem in enumerate(
            (
                "2007N179A-临床 (临床类)女性,35岁,产后哺乳期,右乳红肿。查体体温36.8°C,乳房发硬无压痛,右腋下触及活动性淋巴结。初步诊断是",
                "2007N180A-临床 (临床类)女性,35岁,产后哺乳期,右乳红肿。查体体温36.8°C,乳房发硬无压痛,右腋下触及活动性淋巴结。首选治疗是",
            )
        )
    ]
    normalized = [normalize_question(raw) for raw in raw_questions]
    memberships = infer_question_groups(normalized)

    assert [question.source_code for question in normalized] == ["2007N179A", "2007N180A"]
    assert normalized[0].stem.startswith("(临床类)女性")
    assert memberships[0] is not None
    assert memberships[1] is not None
    assert memberships[0].group_id == memberships[1].group_id
    assert memberships[0].position == 1
    assert memberships[1].position == 2
    assert memberships[0].shared_stem.endswith("活动性淋巴结。")
    assert memberships[0].sub_stem == "初步诊断是"


def test_explicit_shared_stem_is_authoritative():
    questions = [
        normalize_question(
            RawQuestion(
                source=_source(),
                sheet_name="题目",
                row_number=2 + index,
                position=1 + index,
                raw_type="A3/A4型题",
                stem=f"共用题干:患者发热、咳嗽3天。问题:{sub_stem}",
                options=("甲", "乙"),
                answer="A",
            )
        )
        for index, sub_stem in enumerate(("首选检查是", "首选治疗是"))
    ]
    memberships = infer_question_groups(questions)

    assert all(membership is not None for membership in memberships)
    assert memberships[0].shared_stem == "患者发热、咳嗽3天。"
    assert memberships[1].sub_stem == "首选治疗是"
    assert memberships[0].group_id == memberships[1].group_id
