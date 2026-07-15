from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from .auth import admin_required, csrf_required
from .settings import SettingsStore


class VectorOperationConflict(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class VectorOperationManager:
    def __init__(
        self,
        *,
        catalog_database: str | Path,
        knowledge_base_root: str | Path,
        settings_store: SettingsStore,
        state_root: str | Path,
        vector_status: Callable[[], dict[str, Any]],
    ):
        self.catalog_database = Path(catalog_database).resolve()
        self.knowledge_base_root = Path(knowledge_base_root).resolve()
        self.settings_store = settings_store
        self.state_root = Path(state_root).resolve()
        self.vector_status = vector_status
        self.status_path = self.state_root / "vector-operation.json"
        self.lock_path = self.state_root / "vector-operation.lock"
        self.log_path = self.state_root / "vector-build.log"

    def status(self) -> dict[str, Any]:
        operation = self._read_status()
        if operation.get("status") in {"queued", "running"}:
            pid = int(operation.get("pid") or 0)
            if pid and not _pid_exists(pid):
                operation = {
                    **operation,
                    "status": "failed",
                    "error": "向量后台任务已意外退出，可重新点击构建继续未完成进度",
                    "finished_at": _utc_now(),
                }
                _write_json_atomic(self.status_path, operation)
                self._release_stale_lock()
        return {"operation": operation, "vector_index": self.vector_status()}

    def start_build(self) -> dict[str, Any]:
        config = self.settings_store.provider_config("embedding", include_secret=True)
        if not config.get("api_key"):
            raise ValueError("请先保存嵌入模型 API 密钥")
        if not self.catalog_database.is_file():
            raise FileNotFoundError("当前 catalog 不存在")
        self._acquire_lock()
        operation_id = f"vop_{secrets.token_hex(12)}"
        queued = {
            "operation_id": operation_id,
            "type": "build",
            "status": "queued",
            "completed": 0,
            "total": 0,
            "started_at": _utc_now(),
            "resumable": True,
            "incremental": True,
        }
        _write_json_atomic(self.status_path, queued)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "medical_app.vector_task",
            "--operation-id",
            operation_id,
            "--catalog",
            str(self.catalog_database),
            "--knowledge-base-root",
            str(self.knowledge_base_root),
            "--settings-db",
            str(self.settings_store.database_path),
            "--settings-key",
            str(self.settings_store.key_path),
            "--status-file",
            str(self.status_path),
            "--lock-dir",
            str(self.lock_path),
        ]
        environment = {
            **os.environ,
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
        try:
            with self.log_path.open("ab") as log:
                process = subprocess.Popen(
                    command,
                    cwd=Path(__file__).resolve().parent.parent,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                    close_fds=True,
                )
        except Exception as error:
            _write_json_atomic(
                self.status_path,
                {
                    **queued,
                    "status": "failed",
                    "error": f"后台任务启动失败：{type(error).__name__}",
                    "finished_at": _utc_now(),
                },
            )
            self._release_stale_lock()
            raise
        return {**queued, "pid": process.pid}

    def delete_vectors(self) -> dict[str, Any]:
        current = self.status()["operation"]
        if current.get("status") in {"queued", "running"}:
            raise VectorOperationConflict("向量构建正在运行，完成后才能删除")
        self._acquire_lock()
        operation_id = f"vop_{secrets.token_hex(12)}"
        started = {
            "operation_id": operation_id,
            "type": "delete",
            "status": "running",
            "started_at": _utc_now(),
        }
        _write_json_atomic(self.status_path, started)
        trash = self.knowledge_base_root / f".vector-trash-{secrets.token_hex(8)}"
        indexes = (self.knowledge_base_root / "vector-indexes").resolve()
        pointer = (self.knowledge_base_root / "vector-current.json").resolve()
        if indexes.parent != self.knowledge_base_root or pointer.parent != self.knowledge_base_root:
            self._release_stale_lock()
            raise ValueError("向量目录安全校验失败")
        removed_builds = 0
        try:
            trash.mkdir(parents=False, exist_ok=False)
            if indexes.is_dir():
                removed_builds = sum(1 for path in indexes.iterdir() if path.is_dir())
                os.replace(indexes, trash / "vector-indexes")
            if pointer.is_file():
                os.replace(pointer, trash / "vector-current.json")
            shutil.rmtree(trash)
            completed = {
                **started,
                "status": "complete",
                "removed_builds": removed_builds,
                "finished_at": _utc_now(),
            }
            _write_json_atomic(self.status_path, completed)
            return completed
        except Exception as error:
            try:
                if (trash / "vector-indexes").exists() and not indexes.exists():
                    os.replace(trash / "vector-indexes", indexes)
                if (trash / "vector-current.json").exists() and not pointer.exists():
                    os.replace(trash / "vector-current.json", pointer)
                if trash.exists():
                    trash.rmdir()
            except OSError:
                pass
            failed = {
                **started,
                "status": "failed",
                "error": f"删除向量失败：{type(error).__name__}",
                "finished_at": _utc_now(),
            }
            _write_json_atomic(self.status_path, failed)
            raise
        finally:
            self._release_stale_lock()

    def _read_status(self) -> dict[str, Any]:
        if not self.status_path.is_file():
            return {"type": "", "status": "idle", "resumable": True, "incremental": True}
        try:
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {"type": "", "status": "failed", "error": "向量任务状态文件无效"}
        return payload if isinstance(payload, dict) else {"status": "failed"}

    def _acquire_lock(self) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        try:
            self.lock_path.mkdir()
        except FileExistsError as error:
            operation = self._read_status()
            pid = int(operation.get("pid") or 0)
            if operation.get("status") in {"queued", "running"} and (not pid or _pid_exists(pid)):
                raise VectorOperationConflict("已有向量任务正在运行") from error
            self._release_stale_lock()
            try:
                self.lock_path.mkdir()
            except FileExistsError as retry_error:
                raise VectorOperationConflict("向量任务锁仍被占用") from retry_error

    def _release_stale_lock(self) -> None:
        try:
            self.lock_path.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def create_vector_admin_blueprint(manager: VectorOperationManager) -> Blueprint:
    blueprint = Blueprint("vector_admin", __name__, url_prefix="/api/admin/vectors")

    @blueprint.errorhandler(VectorOperationConflict)
    def handle_conflict(error):
        return jsonify({"error": str(error)}), 409

    @blueprint.errorhandler(ValueError)
    @blueprint.errorhandler(FileNotFoundError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @blueprint.get("")
    @admin_required
    def status():
        return jsonify(manager.status())

    @blueprint.post("/build")
    @admin_required
    @csrf_required
    def build():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get("confirmed") is not True:
            raise ValueError("请确认后再开始构建")
        return jsonify(manager.start_build()), 202

    @blueprint.delete("")
    @admin_required
    @csrf_required
    def delete():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get("confirmed") is not True:
            raise ValueError("请确认后再删除向量")
        return jsonify(manager.delete_vectors())

    return blueprint
