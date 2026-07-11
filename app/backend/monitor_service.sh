#!/bin/bash
# 医学搜题系统服务监控脚本（用于定时任务）
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

LOG_DIR="/var/log/medical_exam"
LOG_FILE="$LOG_DIR/monitor.log"
MAX_RESTART=3  # 1小时内最多重启3次
HEALTH_URL="http://127.0.0.1:8080/api/health"

mkdir -p "$LOG_DIR"

# 记录日志
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# 检查进程是否运行
PROCESS_ALIVE=false
if pgrep -f "gunicorn.*main:app" > /dev/null 2>&1; then
    PROCESS_ALIVE=true
fi

# 如果进程存在，检查健康状态
if [ "$PROCESS_ALIVE" = true ]; then
    if curl -sf --connect-timeout 5 --max-time 10 "$HEALTH_URL" | grep -q "healthy"; then
        # 服务正常，无需操作
        exit 0
    else
        log "⚠️  健康检查失败，准备重启服务"
    fi
else
    log "❌ 服务进程不存在，准备重启"
fi

# 检查重启次数（防止频繁重启）
RESTART_COUNT_FILE="/tmp/medical_exam_restart_count"
CURRENT_HOUR=$(date +%Y%m%d%H)

COUNT=1
if [ -f "$RESTART_COUNT_FILE" ]; then
    LAST_HOUR=$(cut -d: -f1 < "$RESTART_COUNT_FILE")
    LAST_COUNT=$(cut -d: -f2 < "$RESTART_COUNT_FILE")

    if [ "$CURRENT_HOUR" = "$LAST_HOUR" ]; then
        if [ "$LAST_COUNT" -ge "$MAX_RESTART" ]; then
            log "❌ 1小时内重启次数过多(${LAST_COUNT}次)，停止自动重启。请人工排查。"
            exit 1
        fi
        COUNT=$((LAST_COUNT + 1))
    fi
fi

echo "$CURRENT_HOUR:$COUNT" > "$RESTART_COUNT_FILE"

# 执行重启
log "🔄 开始重启服务（本小时第 $COUNT 次）"
if bash /srv/projects/medical-web-server/app/backend/restart_service.sh >> "$LOG_FILE" 2>&1; then
    log "✅ 服务重启成功"
else
    log "❌ 服务重启失败"
    exit 1
fi
