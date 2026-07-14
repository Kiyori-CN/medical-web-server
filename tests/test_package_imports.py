from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_runtime_api_import_does_not_require_offline_reader_dependencies():
    backend = Path(__file__).resolve().parents[1] / "app" / "backend"
    script = f"""
import builtins
import sys

sys.path.insert(0, {str(backend)!r})
original_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.split('.', 1)[0] in {{'docx', 'openpyxl'}}:
        raise ModuleNotFoundError(f'offline dependency imported: {{name}}')
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import medical_v2.api
import medical_v2.practice_api
import medical_v2.settings_api
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
