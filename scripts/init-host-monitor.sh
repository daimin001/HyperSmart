#!/bin/bash
################################################################################
# 容器启动时自动初始化宿主机监控（在容器内执行）
# 通过挂载的数据卷传递安装脚本到宿主机
################################################################################

SCRIPTS_DIR="/app/scripts/host"
DATA_DIR="/app/data"
MARKER_FILE="$DATA_DIR/.monitor_initialized"

# 检查是否已初始化
if [ -f "$MARKER_FILE" ]; then
    echo "[$(date)] 宿主机监控已初始化，跳过"
    exit 0
fi

echo "[$(date)] 检测到首次启动，准备初始化宿主机监控..."

# 将安装脚本复制到数据卷（宿主机可见）
if [ -d "$SCRIPTS_DIR" ]; then
    cp "$SCRIPTS_DIR/install-monitor.sh" "$DATA_DIR/.install-monitor.sh" 2>/dev/null || {
        echo "[$(date)] 复制安装脚本失败"
        exit 1
    }
    chmod +x "$DATA_DIR/.install-monitor.sh"

    echo "[$(date)] ✅ 安装脚本已准备好"
    echo "[$(date)] 📋 请在宿主机上执行以下命令完成监控系统安装："
    echo ""
    echo "      docker exec trading-system-app cat /app/data/.install-monitor.sh > /tmp/install-monitor.sh"
    echo "      chmod +x /tmp/install-monitor.sh"
    echo "      sudo /tmp/install-monitor.sh"
    echo ""
    echo "[$(date)] 或者使用一键命令："
    echo ""
    echo "      docker exec trading-system-app cat /app/data/.install-monitor.sh | sudo bash"
    echo ""

    # 创建标记文件
    touch "$MARKER_FILE"
else
    echo "[$(date)] 未找到监控脚本目录: $SCRIPTS_DIR"
fi
