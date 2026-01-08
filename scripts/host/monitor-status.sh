#!/bin/bash
################################################################################
# 查看监控服务状态
################################################################################

echo "============================================================"
echo "         HyperBot 更新监控服务状态"
echo "============================================================"
echo ""

# 1. Systemd 服务状态
echo "【Systemd 服务】"
if systemctl is-active --quiet hyperbot-update-monitor; then
    echo "✅ 状态: 运行中"
    UPTIME=$(systemctl show hyperbot-update-monitor -p ActiveEnterTimestamp --value)
    echo "   启动时间: $UPTIME"
    PID=$(systemctl show hyperbot-update-monitor -p MainPID --value)
    echo "   进程 PID: $PID"
else
    echo "❌ 状态: 已停止"
fi
echo ""

# 2. Cron 定时任务
echo "【Cron 定时任务】"
if crontab -l 2>/dev/null | grep -q check-monitor-health; then
    echo "✅ 健康检查任务已配置"
    echo "   间隔: 每5分钟"
else
    echo "❌ 健康检查任务未配置"
fi
echo ""

# 3. Docker 容器状态
echo "【Docker 容器】"
CONTAINER_STATUS=$(docker ps --filter name=trading-system-app --format '{{.Status}}')
CONTAINER_IMAGE=$(docker ps --filter name=trading-system-app --format '{{.Image}}')
if [ -n "$CONTAINER_STATUS" ]; then
    echo "✅ 容器运行中"
    echo "   镜像: $CONTAINER_IMAGE"
    echo "   状态: $CONTAINER_STATUS"

    # 读取容器内版本
    VERSION=$(docker exec trading-system-app cat /app/version.txt 2>/dev/null || echo "unknown")
    echo "   版本: $VERSION"
else
    echo "❌ 容器未运行"
fi
echo ""

# 4. 最近日志
echo "【最近活动日志】"
if [ -f /opt/trading-system/logs/monitor.log ]; then
    echo "监控日志（最近3行）:"
    tail -3 /opt/trading-system/logs/monitor.log | sed 's/^/   /'
else
    echo "   无监控日志"
fi

if [ -f /opt/trading-system/logs/update.log ]; then
    echo ""
    echo "更新日志（最近5行）:"
    tail -5 /opt/trading-system/logs/update.log | sed 's/^/   /'
fi

if [ -f /opt/trading-system/logs/health-check.log ]; then
    echo ""
    echo "健康检查日志（最近3行）:"
    tail -3 /opt/trading-system/logs/health-check.log | sed 's/^/   /'
fi

echo ""
echo "============================================================"
