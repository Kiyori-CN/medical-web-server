from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "app" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from medical_app.builder import activate_build, build_catalog  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the medical SQLite catalog")
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "data")
    parser.add_argument(
        "--builds-root",
        type=Path,
        default=REPO_ROOT / "knowledge-base" / "builds",
    )
    parser.add_argument("--force", action="store_true", help="Rebuild an existing build ID")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Atomically point current.json at the validated build",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_catalog(args.source, args.builds_root, force=args.force)
    summary = {
        "build_id": manifest["build_id"],
        "snapshot_id": manifest["snapshot_id"],
        "schema_revision": manifest["schema_revision"],
        "stats": manifest["stats"],
        "reused": manifest.get("reused", False),
    }
    if args.activate:
        summary["activation"] = activate_build(args.builds_root.parent, manifest["build_id"])
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
