#!/bin/bash
# 医学搜题系统启动脚本
set -euo pipefail

export FLASK_ENV=production
export KNOWLEDGE_BASE_ROOT=/srv/projects/medical-web-server/knowledge-base
export APP_DB=/srv/projects/medical-web-server/state/app.sqlite3
export SETTINGS_DB=/srv/projects/medical-web-server/state/settings.sqlite3
export SETTINGS_KEY_PATH=/srv/projects/medical-web-server/state/settings.key
export SECURE_COOKIES=true
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

BACKEND_DIR=/srv/projects/medical-web-server/app/backend

# 检查工作目录
if [ ! -d "$BACKEND_DIR" ]; then
    echo "❌ 后端目录不存在: $BACKEND_DIR" >&2
    exit 1
fi

cd "$BACKEND_DIR"

# 检查核心文件
if [ ! -f "main.py" ]; then
    echo "❌ 主程序文件缺失: main.py" >&2
    exit 1
fi

# 启动 Gunicorn
exec gunicorn -c gunicorn_config.py main:app
