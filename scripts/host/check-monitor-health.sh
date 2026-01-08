#!/bin/bash
################################################################################
# 监控脚本健康检查（通过cron定期运行）
# 如果监控脚本挂了，自动重启
################################################################################

SERVICE_NAME="hyperbot-update-monitor"

# 检查 systemd 服务状态
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 监控服务已停止，正在重启..." >> /opt/trading-system/logs/health-check.log
    systemctl start "$SERVICE_NAME"

    # 等待2秒后再次检查
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ✅ 监控服务重启成功" >> /opt/trading-system/logs/health-check.log
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ 监控服务重启失败" >> /opt/trading-system/logs/health-check.log
    fi
else
    # 服务运行正常，不需要输出日志（避免日志过多）
    :
fi
