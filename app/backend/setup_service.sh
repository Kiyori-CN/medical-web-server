#!/bin/bash
# 医学搜题系统服务初始化脚本
set -euo pipefail

echo "🔧 初始化服务环境..."
echo ""

ERRORS=0

# 1. 创建日志目录
mkdir -p /var/log/medical_exam
chmod 755 /var/log/medical_exam
echo "✅ 日志目录: /var/log/medical_exam"

# 2. 检查知识库路径
KB_PATH="/srv/projects/medical-web-server/knowledge-base/西医题库"
if [ -d "$KB_PATH" ]; then
    KB_FILES=$(find "$KB_PATH" -type f | wc -l)
    echo "✅ 知识库路径正常 ($KB_FILES 个文件)"
else
    echo "❌ 知识库路径不存在: $KB_PATH"
    ERRORS=$((ERRORS + 1))
fi

# 3. 检查 Python 依赖
cd /srv/projects/medical-web-server/app/backend
if python3 -c "import flask, flask_cors, ikbs_pysearch" 2>/dev/null; then
    echo "✅ Python 依赖检查通过"
else
    echo "⚠️  缺少 Python 依赖，正在安装..."
    pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if python3 -c "import flask, flask_cors, ikbs_pysearch" 2>/dev/null; then
        echo "✅ Python 依赖安装成功"
    else
        echo "❌ Python 依赖安装失败"
        ERRORS=$((ERRORS + 1))
    fi
fi

# 4. 测试配置文件
if python3 -c "from config import get_config; c = get_config(); print(f'   KB_PATH={c.KB_PATH}, PORT={c.PORT}')" 2>/dev/null; then
    echo "✅ 配置文件正常"
else
    echo "❌ 配置文件加载失败"
    ERRORS=$((ERRORS + 1))
fi

# 5. 检查 Gunicorn 进程
if pgrep -f "gunicorn.*main:app" > /dev/null 2>&1; then
    PID=$(pgrep -f 'gunicorn.*main:app' | head -1)
    echo "✅ Gunicorn 服务正在运行 (PID: $PID)"
else
    echo "⚠️  Gunicorn 服务未运行"
fi

# 6. 检查 Nginx 配置
if command -v nginx > /dev/null 2>&1; then
    if nginx -t 2>&1 | grep -q "successful"; then
        echo "✅ Nginx 配置正常"
    else
        echo "❌ Nginx 配置错误"
        nginx -t 2>&1 | head -5
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "⚠️  Nginx 未安装"
fi

echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "🎉 服务环境检查完成，一切正常！"
else
    echo "⚠️  检查完成，发现 $ERRORS 个问题需要修复"
fi

echo ""
echo "📡 服务地址："
echo "   前端: http://106.54.201.246"
echo "   后端: http://106.54.201.246:8080/api/health"
echo ""
echo "📝 日志位置："
echo "   访问日志: /var/log/medical_exam/access.log"
echo "   错误日志: /var/log/medical_exam/error.log"
