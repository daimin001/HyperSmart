#!/bin/bash
################################################################################
# Docker 容器入口脚本 (改进版 - 使用 Supervisor 管理进程)
# 功能：容器启动时自动执行初始化任务，并使用 Supervisor 管理所有服务
################################################################################

set -e

echo "[HyperBot] 容器启动中..."

# 1. 初始化账户配置文件（如果不存在）
if [ ! -f /app/accounts_config.json ]; then
    echo "[HyperBot] 创建空的账户配置文件..."
    cat > /app/accounts_config.json <<'EOF'
{
  "accounts": []
}
EOF
    chmod 644 /app/accounts_config.json
    echo "[HyperBot] ✓ 账户配置文件已创建"
fi

# 2. 检查并初始化宿主机监控（仅首次）
if [ -f /app/scripts/init-host-monitor.sh ]; then
    echo "[HyperBot] 执行宿主机监控初始化检查..."
    bash /app/scripts/init-host-monitor.sh || echo "[HyperBot] 监控初始化检查完成"
fi

# 3. 创建日志目录
mkdir -p /app/logs

# 4. 检查是否启用自动启动
if [ "${ENABLE_AUTO_START_ACCOUNTS}" = "true" ]; then
    echo "[HyperBot] 自动启动模式已启用"
    echo "[HyperBot] 使用 Supervisor 管理所有服务（自动重启、日志轮转）"
    echo "[HyperBot] ✅ Monitor Service 崩溃后将在5秒内自动重启"

    # 使用 Supervisor 启动所有服务
    exec /usr/bin/supervisord -c /app/supervisord.conf
else
    echo "[HyperBot] 自动启动模式未启用，仅启动 API 服务器"
    # 只启动 API 服务器
    exec python3 api_server_simple.py
fi
