from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from flask import Blueprint, jsonify, request
from werkzeug.datastructures import FileStorage

from .auth import admin_required, csrf_required
from .normalize import display_text


MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_IMPORT_ROWS = 50_000
MAX_IMPORT_FIELDS = 128
MAX_STAGED_IMPORTS = 30
MAX_STAGED_BYTES = 256 * 1024 * 1024
PREVIEW_ROWS = 10
SUPPORTED_IMPORT_EXTENSIONS = {".csv", ".json", ".jsonl"}
IMPORT_FIELDS = (
    "stem",
    "raw_type",
    "source_code",
    "answer",
    "point",
    "explanation",
    "options",
    *(f"option_{label.lower()}" for label in "ABCDEFGH"),
)
_IMPORT_ID_RE = re.compile(r"^imp_[A-Za-z0-9_-]{16,40}$")
_ALIASES = {
    "stem": ("stem", "题目", "题干", "question", "content"),
    "raw_type": ("raw_type", "题型", "类型", "question_type", "type"),
    "source_code": ("source_code", "原题号", "题号", "编号", "code"),
    "answer": ("answer", "答案", "正确答案", "standard_answer"),
    "point": ("point", "考点", "考点还原", "知识点", "knowledge_point"),
    "explanation": (
        "explanation",
        "解析",
        "答案解析",
        "标准解析",
        "analysis",
    ),
    "options": ("options", "选项", "choices"),
}
for _label in "ABCDEFGH":
    _ALIASES[f"option_{_label.lower()}"] = (
        f"option_{_label.lower()}",
        f"option{_label}",
        f"选项{_label}",
        _label,
    )


def create_catalog_import_blueprint(state_root: str | Path) -> Blueprint:
    blueprint = Blueprint(
        "catalog_import", __name__, url_prefix="/api/admin/catalog/imports"
    )
    store = CatalogImportPreviewStore(Path(state_root) / "import-staging")
    store.initialize()

    @blueprint.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.errorhandler(FileNotFoundError)
    def handle_not_found(error):
        return jsonify({"error": str(error)}), 404

    @blueprint.get("")
    @admin_required
    def list_imports():
        return jsonify({"imports": store.list_previews()})

    @blueprint.post("")
    @admin_required
    @csrf_required
    def create_import():
        upload = request.files.get("file")
        if upload is None:
            raise ValueError("请选择 CSV、JSONL 或 JSON 文件")
        return jsonify(store.create_preview(upload)), 201

    @blueprint.post("/<import_id>/analyze")
    @admin_required
    @csrf_required
    def analyze_import(import_id: str):
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get("mapping"), dict):
            raise ValueError("字段映射必须是 JSON 对象")
        return jsonify(store.analyze(import_id, data["mapping"]))

    @blueprint.delete("/<import_id>")
    @admin_required
    @csrf_required
    def delete_import(import_id: str):
        store.delete(import_id)
        return jsonify({"removed": True})

    return blueprint


class CatalogImportPreviewStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def create_preview(self, upload: FileStorage) -> dict[str, Any]:
        self._ensure_capacity()
        filename = _safe_filename(upload.filename)
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_IMPORT_EXTENSIONS:
            raise ValueError("导入预检仅支持 CSV、JSONL 或 JSON 文件")
        import_id = self._new_import_id()
        directory = self._directory(import_id)
        directory.mkdir(mode=0o700)
        source_path = directory / f"source{extension}"
        digest = hashlib.sha256()
        size = 0
        try:
            with source_path.open("xb") as destination:
                while chunk := upload.stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_IMPORT_BYTES:
                        raise ValueError("导入预检文件不能超过 16 MB")
                    digest.update(chunk)
                    destination.write(chunk)
            if size == 0:
                raise ValueError("导入预检文件不能为空")
            analysis = _analyze_file(source_path, extension, mapping=None)
            manifest = {
                "import_id": import_id,
                "filename": filename,
                "extension": extension,
                "size_bytes": size,
                "sha256": digest.hexdigest(),
                "created_at": _utc_now(),
                **analysis,
            }
            self._write_manifest(directory, manifest)
            return manifest
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def list_previews(self) -> list[dict[str, Any]]:
        manifests = []
        for path in self.root.glob("imp_*/manifest.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and _IMPORT_ID_RE.fullmatch(
                str(payload.get("import_id", ""))
            ):
                manifests.append(payload)
        manifests.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return manifests[:30]

    def analyze(self, import_id: str, mapping: dict[str, Any]) -> dict[str, Any]:
        directory = self._directory(import_id)
        manifest = self._read_manifest(directory)
        source_path = directory / f"source{manifest['extension']}"
        if not source_path.is_file():
            raise FileNotFoundError("导入预检源文件不存在")
        analysis = _analyze_file(source_path, manifest["extension"], mapping=mapping)
        manifest.update(analysis)
        manifest["updated_at"] = _utc_now()
        self._write_manifest(directory, manifest)
        return manifest

    def delete(self, import_id: str) -> None:
        directory = self._directory(import_id)
        if not directory.is_dir():
            raise FileNotFoundError("导入预检记录不存在")
        shutil.rmtree(directory)

    def _new_import_id(self) -> str:
        for _ in range(10):
            import_id = f"imp_{secrets.token_urlsafe(15)}"
            if not self._directory(import_id).exists():
                return import_id
        raise RuntimeError("无法创建导入预检标识")

    def _ensure_capacity(self) -> None:
        sources = list(self.root.glob("imp_*/source.*"))
        if len(sources) >= MAX_STAGED_IMPORTS:
            raise ValueError("导入预检暂存已达 30 份，请先删除旧记录")
        total_bytes = sum(path.stat().st_size for path in sources if path.is_file())
        if total_bytes + MAX_IMPORT_BYTES > MAX_STAGED_BYTES:
            raise ValueError("导入预检暂存空间不足，请先删除旧记录")

    def _directory(self, import_id: str) -> Path:
        import_id = str(import_id or "")
        if not _IMPORT_ID_RE.fullmatch(import_id):
            raise FileNotFoundError("导入预检记录不存在")
        directory = (self.root / import_id).resolve()
        if directory.parent != self.root:
            raise FileNotFoundError("导入预检记录不存在")
        return directory

    @staticmethod
    def _read_manifest(directory: Path) -> dict[str, Any]:
        path = directory / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FileNotFoundError("导入预检记录不存在") from error
        if not isinstance(payload, dict):
            raise FileNotFoundError("导入预检记录不存在")
        return payload

    @staticmethod
    def _write_manifest(directory: Path, payload: dict[str, Any]) -> None:
        temporary = directory / "manifest.json.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, directory / "manifest.json")


def _analyze_file(
    path: Path, extension: str, mapping: dict[str, Any] | None
) -> dict[str, Any]:
    columns = _discover_columns(path, extension)
    suggested = _suggest_mapping(columns)
    selected = _validate_mapping(mapping if mapping is not None else suggested, columns)
    if not selected.get("stem"):
        return {
            "status": "needs_mapping",
            "columns": columns,
            "mapping": selected,
            "total_rows": 0,
            "valid_rows": 0,
            "error_rows": 0,
            "preview": [],
            "errors": [
                {"row": 0, "message": "请选择题干字段后重新分析"}
            ],
        }

    total_rows = 0
    valid_rows = 0
    errors: list[dict[str, Any]] = []
    preview = []
    for row_number, record, issue in _iter_records(path, extension):
        total_rows += 1
        if total_rows > MAX_IMPORT_ROWS:
            raise ValueError("单次导入预检最多分析 50,000 行")
        if issue:
            if len(errors) < 50:
                errors.append({"row": row_number, "message": issue})
            continue
        try:
            normalized = _normalize_record(record or {}, selected)
        except ValueError as error:
            if len(errors) < 50:
                errors.append({"row": row_number, "message": str(error)})
            continue
        valid_rows += 1
        if len(preview) < PREVIEW_ROWS:
            preview.append({"row": row_number, **normalized})
    error_rows = total_rows - valid_rows
    if total_rows == 0:
        errors.append({"row": 0, "message": "没有可分析的数据行"})
    return {
        "status": "ready" if valid_rows and not error_rows else "has_errors",
        "columns": columns,
        "mapping": selected,
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "error_rows": error_rows,
        "preview": preview,
        "errors": errors,
    }


def _discover_columns(path: Path, extension: str) -> list[str]:
    if extension == ".csv":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle), [])
        except UnicodeDecodeError as error:
            raise ValueError("文件必须使用 UTF-8 编码") from error
        columns = list(
            dict.fromkeys(display_text(header) for header in headers if display_text(header))
        )
        if len(columns) > MAX_IMPORT_FIELDS:
            raise ValueError("导入字段不能超过 128 个")
        if not columns:
            raise ValueError("没有识别到可用字段")
        return columns
    columns: list[str] = []
    seen = set()
    valid_records = 0
    for _, record, _ in _iter_records(path, extension):
        if not record:
            continue
        valid_records += 1
        for key in record:
            name = display_text(key)
            if name and name not in seen:
                if len(columns) >= MAX_IMPORT_FIELDS:
                    raise ValueError("导入字段不能超过 128 个")
                seen.add(name)
                columns.append(name)
        if valid_records >= 50:
            break
    if not columns:
        raise ValueError("没有识别到可用字段")
    return columns


def _iter_records(path: Path, extension: str):
    if extension == ".csv":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames:
                    raise ValueError("CSV 缺少表头")
                for row_number, row in enumerate(reader, start=2):
                    yield row_number, dict(row), ""
        except UnicodeDecodeError as error:
            raise ValueError("文件必须使用 UTF-8 编码") from error
        return
    if extension == ".jsonl":
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                for row_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        yield row_number, None, "该行不是有效 JSON"
                        continue
                    if not isinstance(item, dict):
                        yield row_number, None, "每行必须是 JSON 对象"
                        continue
                    yield row_number, item, ""
        except UnicodeDecodeError as error:
            raise ValueError("文件必须使用 UTF-8 编码") from error
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as error:
        raise ValueError("文件必须使用 UTF-8 编码") from error
    except json.JSONDecodeError as error:
        raise ValueError("JSON 文件格式无效") from error
    if isinstance(payload, dict):
        payload = payload.get("questions")
    if not isinstance(payload, list):
        raise ValueError("JSON 顶层必须是数组，或包含 questions 数组")
    for row_number, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            yield row_number, None, "数组成员必须是 JSON 对象"
            continue
        yield row_number, item, ""


def _suggest_mapping(columns: list[str]) -> dict[str, str]:
    normalized_columns = {_normalized_field(column): column for column in columns}
    mapping = {}
    for field, aliases in _ALIASES.items():
        for alias in aliases:
            source = normalized_columns.get(_normalized_field(alias))
            if source:
                mapping[field] = source
                break
    return mapping


def _validate_mapping(
    mapping: dict[str, Any], columns: list[str]
) -> dict[str, str]:
    if not isinstance(mapping, dict):
        raise ValueError("字段映射必须是 JSON 对象")
    allowed_columns = set(columns)
    selected = {}
    for field, source in mapping.items():
        if field not in IMPORT_FIELDS:
            raise ValueError(f"不支持的目标字段：{field}")
        source_name = display_text(source)
        if not source_name:
            continue
        if source_name not in allowed_columns:
            raise ValueError(f"源字段不存在：{source_name}")
        selected[field] = source_name
    return selected


def _normalize_record(record: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    def text(field: str, maximum: int) -> str:
        value = display_text(record.get(mapping.get(field, ""), ""))
        if len(value) > maximum:
            raise ValueError(f"{field} 超过 {maximum} 个字符")
        return value

    stem = text("stem", 20_000)
    if not stem:
        raise ValueError("题干不能为空")
    options = []
    structured_field = mapping.get("options")
    if structured_field:
        options = _structured_options(record.get(structured_field))
    if not options:
        for label in "ABCDEFGH":
            value = text(f"option_{label.lower()}", 5_000)
            if value:
                options.append(value)
    return {
        "raw_type": text("raw_type", 120),
        "source_code": text("source_code", 200),
        "stem": stem,
        "options": options,
        "answer": text("answer", 5_000),
        "point": text("point", 20_000),
        "explanation": text("explanation", 50_000),
    }


def _structured_options(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("结构化选项必须是 JSON 数组或对象") from error
    if isinstance(value, dict):
        values = [value.get(label, "") for label in "ABCDEFGH"]
    elif isinstance(value, list):
        values = [
            item.get("text", "") if isinstance(item, dict) else item for item in value
        ]
    else:
        raise ValueError("结构化选项必须是数组或对象")
    options = [display_text(item) for item in values if display_text(item)]
    if len(options) > 8:
        raise ValueError("选项不能超过 8 个")
    if any(len(option) > 5_000 for option in options):
        raise ValueError("单个选项不能超过 5,000 个字符")
    return options


def _normalized_field(value: object) -> str:
    return re.sub(r"[\s_-]+", "", display_text(value).casefold())


def _safe_filename(value: object) -> str:
    name = PurePosixPath(str(value or "").replace("\\", "/")).name
    name = display_text(name)
    if not name or len(name) > 180:
        raise ValueError("文件名无效")
    return name


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
