from __future__ import annotations

import importlib
import sys
from pathlib import Path

from medical_v2.builder import build_catalog
from medical_v2.catalog import Catalog
from medical_v2.practice import PracticeStore


def test_main_serves_public_assets_and_v2_health(
    sample_source_tree: Path, tmp_path: Path, monkeypatch
):
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    catalog_path = tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3"
    practice_path = tmp_path / "state" / "practice.sqlite3"
    PracticeStore(practice_path, Catalog(catalog_path)).initialize()

    monkeypatch.setenv("FLASK_ENV", "development")
    monkeypatch.setenv("CATALOG_DB", str(catalog_path))
    monkeypatch.setenv("PRACTICE_DB", str(practice_path))
    monkeypatch.setenv("LEGACY_SEARCH_ENABLED", "false")
    for module_name in ("main", "config"):
        sys.modules.pop(module_name, None)
    main = importlib.import_module("main")
    client = main.app.test_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["engine"] == "sqlite-fts5"
    assert health.get_json()["legacy"] == {"enabled": False, "status": "disabled"}

    index = client.get("/")
    assert index.status_code == 200
    assert "本地医学题库搜题与刷题" in index.get_data(as_text=True)
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/v2/practice/health").status_code == 200
