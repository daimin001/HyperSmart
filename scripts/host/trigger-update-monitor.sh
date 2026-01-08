#!/bin/bash
################################################################################
# 更新触发监控脚本（宿主机端）
# 监控容器写入的更新触发文件，自动执行更新
################################################################################

TRIGGER_FILE="/opt/trading-system/data/.update_trigger"
UPDATE_SCRIPT="/opt/trading-system/update.sh"

log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] $1"
}

log_info "启动更新触发监控..."

# 每5秒检查一次触发文件
while true; do
    if [ -f "$TRIGGER_FILE" ]; then
        log_info "检测到更新触发请求"

        # 读取目标镜像
        TARGET_IMAGE=$(cat "$TRIGGER_FILE" 2>/dev/null)

        # 删除触发文件
        rm -f "$TRIGGER_FILE"

        # 执行更新
        log_info "开始执行更新..."
        if [ -n "$TARGET_IMAGE" ]; then
            bash "$UPDATE_SCRIPT" "$TARGET_IMAGE" >> /opt/trading-system/logs/update.log 2>&1
        else
            bash "$UPDATE_SCRIPT" >> /opt/trading-system/logs/update.log 2>&1
        fi

        log_info "更新完成"
    fi

    sleep 5
done
