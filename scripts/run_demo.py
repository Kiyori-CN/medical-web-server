"""Run the real application against disposable, self-authored sample questions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "app" / "backend"
sys.path.insert(0, str(BACKEND_ROOT))


def prepare_demo(root: Path) -> dict[str, str]:
    """Build only inside a new directory; never activate a user's real catalog."""
    from openpyxl import Workbook
    from medical_app.builder import build_catalog

    if any(root.iterdir()):
        raise ValueError("演示目录必须为空，不能复用已有题库或账号状态。")
    samples = json.loads((REPO_ROOT / "examples" / "demo_questions.json").read_text(encoding="utf-8"))
    source = root / "sources" / "演示题库" / "平台入门"
    source.mkdir(parents=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "功能演示"
    sheet.append(["题型", "题目", "考点还原", "答案", "答案解析", "选项A", "选项B", "选项C", "选项D"])
    for item in samples:
        sheet.append([item["type"], item["question"], item["topic"], item["answer"], item["explanation"], *item["options"]])
    workbook.save(source / "自编功能样例.xlsx")
    workbook.close()
    knowledge = root / "knowledge-base"
    manifest = build_catalog(root / "sources", knowledge / "builds")
    # Override every data/secret location even if a production environment was
    # inherited. Demo setup must never initialize or modify the real state.
    return {
        "FLASK_ENV": "development",
        "FLASK_DEBUG": "false",
        "FLASK_HOST": "127.0.0.1",
        "SECURE_COOKIES": "false",
        "CORS_ORIGINS": "",
        "CATALOG_DB": str(knowledge / "builds" / manifest["build_id"] / "catalog.sqlite3"),
        "KNOWLEDGE_BASE_ROOT": str(knowledge),
        "APP_DB": str(root / "state" / "app.sqlite3"),
        "SETTINGS_DB": str(root / "state" / "settings.sqlite3"),
        "SETTINGS_KEY_PATH": str(root / "state" / "settings.key"),
        "FRONTEND_DIR": str(REPO_ROOT / "public"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="使用自编样例启动隔离的本地题库体验，不需要私人题库或模型密钥。")
    parser.add_argument("--port", type=int, default=5091)
    parser.add_argument("--check", action="store_true", help="构建样例并检查真实应用健康接口，不监听端口。")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port 必须在 1–65535 之间")
    with TemporaryDirectory(prefix="medical-web-demo-") as temporary:
        environment = dict(os.environ, **prepare_demo(Path(temporary)))
        environment["PYTHONPATH"] = str(BACKEND_ROOT)
        environment["PYTHONIOENCODING"] = "utf-8"
        if args.check:
            command = (
                "import json; from main import app; "
                "response=app.test_client().get('/api/health'); "
                "assert response.status_code == 200, response.status_code; "
                "print(json.dumps({'demo':True,'health':response.get_json()},ensure_ascii=False))"
            )
        else:
            print(f"本地功能演示：http://127.0.0.1:{args.port}/", flush=True)
            print("仅含自编平台操作样例；首次访问自行创建临时管理员。", flush=True)
            print("未配置外部模型，AI/语义检索不可用。Ctrl+C 退出后临时题库与账号一并清理。", flush=True)
            command = f"from main import app; app.run(host='127.0.0.1',port={args.port},debug=False,use_reloader=False)"
        # The child owns all application SQLite handles. Waiting for its exit
        # before cleanup also releases Windows file locks deterministically.
        child = subprocess.Popen([sys.executable, "-c", command], env=environment)
        try:
            return child.wait()
        except KeyboardInterrupt:
            return 0
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
