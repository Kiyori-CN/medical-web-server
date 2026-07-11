#!/bin/bash
# 医学搜题系统服务重启脚本
set -euo pipefail

echo "🔄 重启医学搜题系统..."

BACKEND_DIR=/srv/projects/medical-web-server/app/backend
LOG_DIR=/var/log/medical_exam

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# 1. 停止旧进程
echo "⏹️  停止旧进程..."
if pgrep -f "gunicorn.*main:app" > /dev/null 2>&1; then
    pkill -f "gunicorn.*main:app" || true
    sleep 2

    if pgrep -f "gunicorn.*main:app" > /dev/null 2>&1; then
        echo "⚠️  强制终止残留进程..."
        pkill -9 -f "gunicorn.*main:app" || true
        sleep 1
    fi
    echo "   旧进程已停止"
else
    echo "   无运行中的进程"
fi

# 2. 清理日志（保留最近 1000 行）
for logfile in access.log error.log; do
    filepath="$LOG_DIR/$logfile"
    if [ -f "$filepath" ] && [ "$(wc -l < "$filepath")" -gt 1000 ]; then
        tail -1000 "$filepath" > "${filepath}.tmp" && mv "${filepath}.tmp" "$filepath"
        echo "   已清理 $logfile"
    fi
done

# 3. 启动服务
echo "🚀 启动服务..."
cd "$BACKEND_DIR"
nohup bash start.sh > "$LOG_DIR/startup.log" 2>&1 &

# 4. 等待启动
echo "   等待服务就绪..."
sleep 4

# 5. 检查服务状态
if pgrep -f "gunicorn.*main:app" > /dev/null 2>&1; then
    PID=$(pgrep -f 'gunicorn.*main:app' | head -1)
    echo "✅ 服务启动成功！PID: $PID"

    for i in 1 2 3; do
        sleep 2
        if curl -sf --connect-timeout 5 http://127.0.0.1:8080/api/health | grep -q "healthy"; then
            echo "✅ 健康检查通过"
            break
        fi
        if [ "$i" -eq 3 ]; then
            echo "⚠️  健康检查未通过，请查看日志: tail -50 $LOG_DIR/error.log"
        else
            echo "   健康检查重试 ($i/3)..."
        fi
    done
else
    echo "❌ 服务启动失败，请查看日志："
    echo "   tail -50 $LOG_DIR/error.log"
    echo "   cat $LOG_DIR/startup.log"
    exit 1
fi

echo ""
echo "🎉 服务重启完成！"
echo "   访问地址: http://106.54.201.246"
