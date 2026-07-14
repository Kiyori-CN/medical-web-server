from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "app" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from medical_v2.catalog import resolve_catalog_path  # noqa: E402
from medical_v2.settings import (  # noqa: E402
    DEFAULT_EMBEDDING_API_URL,
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    SettingsStore,
)
from medical_v2.vector_builder import activate_vector_index, build_vector_index  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a resumable question-level vector index")
    parser.add_argument(
        "--knowledge-base-root", type=Path, default=REPO_ROOT / "knowledge-base" / "v2"
    )
    parser.add_argument("--catalog-db", type=Path)
    parser.add_argument("--settings-db", type=Path, default=REPO_ROOT / "state" / "settings.sqlite3")
    parser.add_argument("--api-url")
    parser.add_argument("--model")
    parser.add_argument("--dimensions", type=int)
    parser.add_argument("--api-key-env", default="EMBEDDING_API_KEY")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--activate", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog_path = resolve_catalog_path(args.catalog_db, args.knowledge_base_root)
    stored = {
        "api_url": DEFAULT_EMBEDDING_API_URL,
        "model": DEFAULT_EMBEDDING_MODEL,
        "dimensions": DEFAULT_EMBEDDING_DIMENSIONS,
        "api_key": "",
    }
    if args.settings_db.is_file():
        stored = SettingsStore(args.settings_db).provider_config("embedding", include_secret=True)
    api_key = os.getenv(args.api_key_env, "") or stored.get("api_key", "")
    manifest = build_vector_index(
        catalog_path,
        args.knowledge_base_root,
        api_url=args.api_url or stored["api_url"],
        api_key=api_key,
        model=args.model or stored["model"],
        dimensions=args.dimensions or stored["dimensions"],
        batch_size=args.batch_size,
        timeout=args.timeout,
        max_retries=args.max_retries,
        force=args.force,
        progress=lambda value: print(json.dumps(value, ensure_ascii=False), flush=True),
    )
    result = {
        "build_id": manifest["build_id"],
        "catalog_build_id": manifest["catalog_build_id"],
        "model": manifest["model"],
        "dimensions": manifest["dimensions"],
        "count": manifest["count"],
        "reused": manifest.get("reused", False),
    }
    if args.activate:
        result["activation"] = activate_vector_index(
            args.knowledge_base_root, manifest["build_id"]
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
