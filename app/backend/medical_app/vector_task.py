from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .settings import SettingsStore
from .vector_builder import activate_vector_index, build_vector_index


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> int:
    status_path = Path(args.status_file).resolve()
    lock_path = Path(args.lock_dir).resolve()
    base_status = {
        "operation_id": args.operation_id,
        "type": "build",
        "status": "running",
        "pid": os.getpid(),
        "completed": 0,
        "total": 0,
        "started_at": _utc_now(),
        "resumable": True,
        "incremental": True,
    }
    _write_json_atomic(status_path, base_status)

    def progress(item: dict[str, Any]) -> None:
        _write_json_atomic(
            status_path,
            {
                **base_status,
                **item,
                "status": "running",
                "updated_at": _utc_now(),
            },
        )

    try:
        store = SettingsStore(args.settings_db, args.settings_key)
        config = store.provider_config("embedding", include_secret=True)
        manifest = build_vector_index(
            args.catalog,
            args.knowledge_base_root,
            api_url=config["api_url"],
            api_key=config["api_key"],
            model=config["model"],
            dimensions=int(config["dimensions"]),
            batch_size=32,
            timeout=120,
            max_retries=8,
            incremental=True,
            progress=progress,
        )
        activation = activate_vector_index(args.knowledge_base_root, manifest["build_id"])
        completed = {
            **base_status,
            "status": "complete",
            "build_id": manifest["build_id"],
            "completed": manifest["count"],
            "total": manifest["count"],
            "reused": bool(manifest.get("reused")),
            "incremental_result": manifest.get("incremental", {}),
            "activated_at": activation["activated_at"],
            "finished_at": _utc_now(),
        }
        _write_json_atomic(status_path, completed)
        print(json.dumps({"status": "complete", "build_id": manifest["build_id"]}))
        return 0
    except Exception as error:
        failed = {
            **base_status,
            "status": "failed",
            "error": f"{type(error).__name__}: {str(error)[:300]}",
            "finished_at": _utc_now(),
        }
        _write_json_atomic(status_path, failed)
        print(json.dumps({"status": "failed", "error": failed["error"]}))
        return 1
    finally:
        try:
            lock_path.rmdir()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one resumable vector build operation")
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--knowledge-base-root", required=True)
    parser.add_argument("--settings-db", required=True)
    parser.add_argument("--settings-key", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--lock-dir", required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
