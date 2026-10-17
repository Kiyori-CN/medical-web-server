from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_demo_uses_isolated_paths_and_real_health_endpoint(tmp_path: Path):
    sentinel = tmp_path / "production.sqlite3"
    sentinel.write_bytes(b"do-not-touch")
    environment = dict(os.environ)
    for key in ("CATALOG_DB", "APP_DB", "SETTINGS_DB", "SETTINGS_KEY_PATH", "KNOWLEDGE_BASE_ROOT", "FRONTEND_DIR"):
        environment[key] = str(sentinel)
    environment["FLASK_ENV"] = "production"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["TMP"] = str(tmp_path)
    environment["TEMP"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_demo.py"), "--check"],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["demo"] is True
    assert report["health"]["setup_required"] is True
    assert sentinel.read_bytes() == b"do-not-touch"
    assert not list(tmp_path.glob("medical-web-demo-*"))


def test_demo_refuses_existing_directory(tmp_path: Path):
    spec = importlib.util.spec_from_file_location("demo_entry", ROOT / "scripts" / "run_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sentinel = tmp_path / "existing.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="必须为空"):
        module.prepare_demo(tmp_path)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
