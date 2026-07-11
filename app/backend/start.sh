#!/bin/bash
# 医学搜题系统启动脚本
set -euo pipefail

export FLASK_ENV=production
export KB_PATH=/srv/projects/medical-web-server/knowledge-base/西医题库
: "${EMBED_API_KEY:?Set EMBED_API_KEY in the environment or /etc/medical-exam.env}"
export EMBED_API_URL=https://api.siliconflow.cn/v1/embeddings
export EMBED_MODEL=BAAI/bge-m3
: "${LLM_API_KEY:?Set LLM_API_KEY in the environment or /etc/medical-exam.env}"
export LLM_API_URL=https://api.siliconflow.cn/v1/chat/completions

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
