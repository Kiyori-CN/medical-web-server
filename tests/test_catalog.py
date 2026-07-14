from pathlib import Path

from flask import Flask

from medical_v2.api import create_catalog_blueprint
from medical_v2.builder import build_catalog
from medical_v2.catalog import Catalog


def _catalog(sample_source_tree: Path, tmp_path: Path) -> Catalog:
    manifest = build_catalog(sample_source_tree, tmp_path / "builds")
    database = tmp_path / "builds" / manifest["build_id"] / "catalog.sqlite3"
    return Catalog(database)


def test_catalog_search_filters_and_solution_boundary(sample_source_tree: Path, tmp_path: Path):
    catalog = _catalog(sample_source_tree, tmp_path)

    exact = catalog.search("2020N1A 细胞膜运输方式属于", limit=5)
    assert exact[0]["match_kind"] == "exact"
    assert exact[0]["answer"] == "A"

    by_source_code = catalog.search("2007N179A", limit=5, include_solution=False)
    assert by_source_code[0]["match_kind"] == "source_code"
    assert by_source_code[0]["source_code"] == "2007N179A"
    assert all(question["match_kind"] == "source_code" for question in by_source_code)

    fuzzy = catalog.search(
        "细胞膜运输",
        exam="执业医师",
        canonical_type="单选题",
        include_solution=False,
    )
    assert fuzzy and fuzzy[0]["stem"] == "细胞膜运输方式属于"
    assert "answer" not in fuzzy[0]
    assert "explanation" not in fuzzy[0]

    assert catalog.search("细胞膜运输", exam="三基考试") == []
    assert catalog.health()["total_questions"] == 5
    assert catalog.stats()["counts"]["question_occurrences"] == 6

    grouped = catalog.search("初步诊断是", include_solution=False)
    assert grouped and grouped[0]["groups"]
    assert grouped[0]["groups"][0]["sub_stem"] == "初步诊断是"


def test_catalog_collections_and_blueprint(sample_source_tree: Path, tmp_path: Path):
    catalog = _catalog(sample_source_tree, tmp_path)
    collections = catalog.list_collections(exam="执业医师")
    assert len(collections) == 3
    assert {collection["extension"] for collection in collections} == {".xlsx", ".docx"}
    first_chapter_files = [
        collection for collection in collections if "第一章." in collection["relative_path"]
    ]
    assert len(first_chapter_files) == 2
    assert first_chapter_files[0]["collection_id"] != first_chapter_files[1]["collection_id"]
    collection_questions = catalog.list_collection_questions(collections[0]["collection_id"])
    assert collection_questions
    assert "answer" not in collection_questions[0]

    root = catalog.browse_library()
    assert root["directories"][0]["name"] == "执业医师"
    leaf = catalog.browse_library("执业医师/学科刷题/生理学")
    assert {item["name"] for item in leaf["collections"]} == {"第一章.xlsx", "第一章.docx"}

    app = Flask(__name__)
    app.register_blueprint(create_catalog_blueprint(catalog.database_path))
    client = app.test_client()

    health = client.get("/api/v2/health")
    assert health.status_code == 200
    assert health.get_json()["engine"] == "sqlite-fts5"

    response = client.post(
        "/api/v2/search",
        json={"query": "细胞膜运输", "include_solution": False},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] >= 1
    assert "answer" not in payload["results"][0]

    missing = client.get("/api/v2/questions/q_missing")
    assert missing.status_code == 404

    assert client.post("/api/v2/search", json=[]).status_code == 400
    assert client.post(
        "/api/v2/search",
        json={"query": "细胞膜运输", "include_solution": "false"},
    ).status_code == 400
