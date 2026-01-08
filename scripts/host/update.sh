#!/bin/bash
################################################################################
# HyperBot Docker 自动更新脚本（宿主机端）
# 功能：拉取最新镜像并重启容器
################################################################################

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 参数：目标镜像（可选）
TARGET_IMAGE="$1"

# 如果没有指定镜像，从版本服务器获取
if [ -z "$TARGET_IMAGE" ]; then
    log_info "从版本服务器获取最新镜像信息..."
    VERSION_INFO=$(curl -s http://43.156.4.146:3000/api/version/check)
    TARGET_IMAGE=$(echo "$VERSION_INFO" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('image', ''))" 2>/dev/null || echo "")

    if [ -z "$TARGET_IMAGE" ]; then
        log_error "无法获取目标镜像信息"
        exit 1
    fi
fi

log_info "目标镜像: $TARGET_IMAGE"

# 拉取新镜像
log_info "正在拉取新镜像..."
if ! docker pull "$TARGET_IMAGE"; then
    log_error "拉取镜像失败"
    exit 1
fi
log_success "镜像拉取成功"

# 停止并删除旧容器
log_info "停止旧容器..."
docker stop trading-system-app 2>/dev/null || true
docker rm trading-system-app 2>/dev/null || true
log_success "旧容器已删除"

# 启动新容器
log_info "启动新容器..."
docker run -d \
    --name trading-system-app \
    --restart always \
    --health-cmd='curl -f http://localhost:8000/health || exit 1' \
    --health-interval=30s \
    --health-timeout=10s \
    --health-retries=3 \
    --health-start-period=40s \
    -p 8080:8000 \
    -v /opt/trading-system/.env:/app/.env:ro \
    -v /opt/trading-system/data:/app/data \
    -v /opt/trading-system/logs:/app/logs \
    -e TZ=Asia/Shanghai \
    "$TARGET_IMAGE"

log_success "新容器已启动"

# 等待容器健康检查
log_info "等待容器启动..."
sleep 5

# 检查容器状态
if docker ps | grep -q trading-system-app; then
    log_success "更新完成！容器运行正常"

    # 显示新版本
    NEW_VERSION=$(docker exec trading-system-app cat /app/version.txt 2>/dev/null || echo "unknown")
    log_info "当前版本: $NEW_VERSION"
else
    log_error "容器启动失败，请检查日志"
    exit 1
fi

# 检查并确保日志清理任务运行
log_info "检查日志清理定时任务..."
INSTALL_DIR="/opt/trading-system"
CLEANUP_SCRIPT="${INSTALL_DIR}/cleanup_logs.sh"

# 检查清理脚本是否存在
if [ ! -f "$CLEANUP_SCRIPT" ]; then
    log_info "创建日志清理脚本..."
    cat > "$CLEANUP_SCRIPT" <<'CLEANUP_SCRIPT'
#!/bin/bash
# 自动清理超过30天的日志文件

INSTALL_DIR="/opt/trading-system"
LOG_DIRS=("${INSTALL_DIR}/logs" "/root/.pm2/logs")
DAYS_TO_KEEP=30

find_and_delete() {
    local log_dir="$1"
    if [ -d "$log_dir" ]; then
        echo "清理目录: $log_dir"
        find "$log_dir" -name "*.log" -type f -mtime +${DAYS_TO_KEEP} -delete 2>/dev/null
        find "$log_dir" -name "*.log.gz" -type f -mtime +${DAYS_TO_KEEP} -delete 2>/dev/null
        find "$log_dir" -name "*.log.zip" -type f -mtime +${DAYS_TO_KEEP} -delete 2>/dev/null
    fi
}

echo "$(date '+%Y-%m-%d %H:%M:%S') - 开始清理旧日志..."
for dir in "${LOG_DIRS[@]}"; do
    find_and_delete "$dir"
done
echo "$(date '+%Y-%m-%d %H:%M:%S') - 日志清理完成"
CLEANUP_SCRIPT
    chmod +x "$CLEANUP_SCRIPT"
    log_success "日志清理脚本已创建"
fi

# 检查cron任务是否存在
if ! crontab -l 2>/dev/null | grep -q "${CLEANUP_SCRIPT}"; then
    log_info "添加日志清理定时任务..."
    (crontab -l 2>/dev/null; echo "0 3 * * * ${CLEANUP_SCRIPT} >> ${INSTALL_DIR}/logs/cleanup.log 2>&1") | crontab -
    log_success "日志清理定时任务已添加（每天凌晨3点执行）"
else
    log_success "日志清理定时任务已存在"
fi
