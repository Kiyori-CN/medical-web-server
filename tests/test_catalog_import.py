from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook
from werkzeug.datastructures import FileStorage

from medical_app.catalog_import import CatalogImportPreviewStore


def _upload(filename: str, content: str) -> FileStorage:
    return FileStorage(stream=BytesIO(content.encode("utf-8")), filename=filename)


def _xlsx_upload(filename: str = "questions.xlsx") -> FileStorage:
    workbook = Workbook()
    first = workbook.active
    first.title = "第一章"
    first.append(["题型", "题目", "选项A", "选项B", "答案", "答案解析"])
    first.append(["单选题", "人体最大的器官是", "皮肤", "肝脏", "A", "皮肤覆盖体表。"])
    second = workbook.create_sheet("第二章")
    second.append(["题型", "题目", "选项A", "选项B", "答案", "答案解析"])
    second.append(["判断题", "成人通常有 32 颗恒牙", "正确", "错误", "A", "不计智齿时会有差异。"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    stream.seek(0)
    return FileStorage(stream=stream, filename=filename)


def _shared_string_xlsx_upload(
    filename: str = "shared.xlsx", workbook_prefix: str = ""
) -> FileStorage:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0' encoding='UTF-8'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>",
        )
        archive.writestr(
            "xl/workbook.xml",
            workbook_prefix
            + "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'><sheets><sheet name='共享题' sheetId='1' r:id='rId1'/></sheets></workbook>",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'><Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet' Target='worksheets/sheet1.xml'/></Relationships>",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            "<sst xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><si><t>题目</t></si><si><t>答案</t></si><si><r><t>共享</t></r><r><t>字符串题干</t></r></si><si><t>A</t></si></sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData><row r='1'><c r='A1' t='s'><v>0</v></c><c r='B1' t='s'><v>1</v></c></row><row r='2'><c r='A2' t='s'><v>2</v></c><c r='B2' t='s'><v>3</v></c></row></sheetData></worksheet>",
        )
    stream.seek(0)
    return FileStorage(stream=stream, filename=filename)


def test_catalog_import_preview_auto_mapping_errors_and_cleanup(tmp_path):
    store = CatalogImportPreviewStore(tmp_path / "state" / "import-staging")
    store.initialize()
    preview = store.create_preview(
        _upload(
            "questions.csv",
            "题型,题目,选项A,选项B,答案,答案解析\n"
            "单选题,人体最大的器官是,皮肤,肝脏,A,皮肤覆盖体表。\n",
        )
    )
    assert preview["status"] == "ready"
    assert preview["mapping"]["stem"] == "题目"
    assert preview["total_rows"] == preview["valid_rows"] == 1
    assert preview["preview"][0]["options"] == ["皮肤", "肝脏"]
    assert store.list_previews()[0]["import_id"] == preview["import_id"]

    store.delete(preview["import_id"])
    assert store.list_previews() == []


def test_catalog_import_preview_manual_mapping_and_jsonl_errors(tmp_path):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    preview = store.create_preview(
        _upload(
            "custom.jsonl",
            '{"q":"心脏有几个腔室","a":"4","choices":["2","3","4"]}\n'
            "not-json\n",
        )
    )
    assert preview["status"] == "needs_mapping"
    analyzed = store.analyze(
        preview["import_id"],
        {"stem": "q", "answer": "a", "options": "choices"},
    )
    assert analyzed["status"] == "has_errors"
    assert analyzed["total_rows"] == 2
    assert analyzed["valid_rows"] == analyzed["error_rows"] == 1
    assert analyzed["preview"][0]["options"] == ["2", "3", "4"]
    assert analyzed["errors"][0]["row"] == 2


def test_catalog_import_preview_supports_json_questions_array_and_empty_csv(tmp_path):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    json_preview = store.create_preview(
        _upload(
            "questions.json",
            '{"questions":[{"stem":"正常成人有多少颗恒牙","options":["28","30","32"],"answer":"32"}]}',
        )
    )
    assert json_preview["status"] == "ready"
    assert json_preview["preview"][0]["options"] == ["28", "30", "32"]

    empty_preview = store.create_preview(_upload("empty.csv", "题目,答案\n"))
    assert empty_preview["status"] == "has_errors"
    assert empty_preview["total_rows"] == 0
    assert empty_preview["errors"][0]["message"] == "没有可分析的数据行"


def test_catalog_import_preview_supports_read_only_multisheet_xlsx(tmp_path):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    preview = store.create_preview(_xlsx_upload())

    assert preview["status"] == "ready"
    assert preview["extension"] == ".xlsx"
    assert preview["mapping"]["stem"] == "题目"
    assert preview["total_rows"] == preview["valid_rows"] == 2
    assert [row["row"] for row in preview["preview"]] == ["第一章!2", "第二章!2"]
    assert preview["preview"][0]["options"] == ["皮肤", "肝脏"]


def test_catalog_import_preview_supports_shared_and_rich_xlsx_strings(tmp_path):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    preview = store.create_preview(_shared_string_xlsx_upload())

    assert preview["status"] == "ready"
    assert preview["total_rows"] == preview["valid_rows"] == 1
    assert preview["preview"][0]["row"] == "共享题!2"
    assert preview["preview"][0]["stem"] == "共享字符串题干"
    assert preview["preview"][0]["answer"] == "A"


def test_catalog_import_preview_rejects_late_xlsx_dtd(tmp_path):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    prefix = "<?xml version='1.0'?>" + (" " * 9_000) + "<!DOCTYPE workbook [<!ENTITY x 'blocked'>]>"
    with pytest.raises(ValueError, match="DTD"):
        store.create_preview(_shared_string_xlsx_upload("dtd.xlsx", prefix))
    assert store.list_previews() == []


def test_catalog_import_preview_rejects_invalid_or_expanded_xlsx(
    tmp_path, monkeypatch
):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    with pytest.raises(ValueError, match="XLSX 文件格式无效"):
        store.create_preview(_upload("broken.xlsx", "not-an-xlsx"))
    monkeypatch.setattr(
        "medical_app.catalog_import.MAX_XLSX_UNCOMPRESSED_BYTES", 1
    )
    with pytest.raises(ValueError, match="解压后不能超过"):
        store.create_preview(_xlsx_upload("expanded.xlsx"))
    assert store.list_previews() == []


def test_catalog_import_preview_rejects_unsafe_inputs(tmp_path, monkeypatch):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    with pytest.raises(ValueError, match="仅支持"):
        store.create_preview(_upload("questions.xls", "not-an-xls"))
    monkeypatch.setattr("medical_app.catalog_import.MAX_IMPORT_BYTES", 4)
    with pytest.raises(ValueError, match="不能超过"):
        store.create_preview(_upload("questions.csv", "题目\n内容过长\n"))
    assert store.list_previews() == []
    with pytest.raises(FileNotFoundError):
        store.delete("../state")
