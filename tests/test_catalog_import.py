from io import BytesIO

import pytest
from werkzeug.datastructures import FileStorage

from medical_app.catalog_import import CatalogImportPreviewStore


def _upload(filename: str, content: str) -> FileStorage:
    return FileStorage(stream=BytesIO(content.encode("utf-8")), filename=filename)


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


def test_catalog_import_preview_rejects_unsafe_inputs(tmp_path, monkeypatch):
    store = CatalogImportPreviewStore(tmp_path / "staging")
    store.initialize()
    with pytest.raises(ValueError, match="仅支持"):
        store.create_preview(_upload("questions.xlsx", "not-an-xlsx"))
    monkeypatch.setattr("medical_app.catalog_import.MAX_IMPORT_BYTES", 4)
    with pytest.raises(ValueError, match="不能超过"):
        store.create_preview(_upload("questions.csv", "题目\n内容过长\n"))
    assert store.list_previews() == []
    with pytest.raises(FileNotFoundError):
        store.delete("../state")
