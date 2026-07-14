from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "app" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from medical_app.auth import AuthStore  # noqa: E402
from medical_app.catalog import Catalog, resolve_catalog_path  # noqa: E402
from medical_app.practice import PracticeStore  # noqa: E402
from medical_app.settings import SettingsStore  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize the local application database")
    parser.add_argument(
        "--knowledge-base-root",
        type=Path,
        default=REPO_ROOT / "knowledge-base",
    )
    parser.add_argument("--catalog-db", type=Path)
    parser.add_argument(
        "--app-db",
        type=Path,
        default=REPO_ROOT / "state" / "app.sqlite3",
    )
    parser.add_argument(
        "--settings-db",
        type=Path,
        default=REPO_ROOT / "state" / "settings.sqlite3",
    )
    parser.add_argument(
        "--settings-key-path",
        type=Path,
        default=REPO_ROOT / "state" / "settings.key",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog_path = resolve_catalog_path(args.catalog_db, args.knowledge_base_root)
    catalog = Catalog(catalog_path)
    catalog.health()
    result = {
        "accounts": AuthStore(args.app_db).initialize(),
        "practice": PracticeStore(
            args.app_db, catalog, profile_id="system"
        ).initialize(),
        "settings": SettingsStore(
            args.settings_db, args.settings_key_path
        ).initialize(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
