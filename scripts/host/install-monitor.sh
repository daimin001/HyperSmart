#!/bin/bash
################################################################################
# HyperBot 监控系统自动安装脚本（宿主机端执行）
# 用途：在宿主机上部署更新监控系统
################################################################################

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo "============================================================"
echo "         HyperBot 监控系统安装程序"
echo "============================================================"
echo ""

# 检查是否以root运行
if [ "$EUID" -ne 0 ]; then
    log_error "请使用 root 权限运行此脚本"
    echo "使用方法: sudo bash install-monitor.sh"
    exit 1
fi

# 默认安装目录
INSTALL_DIR="${1:-/opt/trading-system}"
CONTAINER_NAME="${2:-trading-system-app}"

log_info "安装目录: $INSTALL_DIR"
log_info "容器名称: $CONTAINER_NAME"
echo ""

# 1. 创建目录结构
log_info "步骤 1/6: 创建目录结构..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/data"
mkdir -p "$INSTALL_DIR/backups"
log_success "目录创建完成"

# 2. 从容器复制脚本文件
log_info "步骤 2/6: 从容器复制监控脚本..."

if docker ps -a | grep -q "$CONTAINER_NAME"; then
    docker cp "$CONTAINER_NAME:/app/scripts/host/update.sh" "$INSTALL_DIR/" 2>/dev/null || log_warning "update.sh 复制失败"
    docker cp "$CONTAINER_NAME:/app/scripts/host/trigger-update-monitor.sh" "$INSTALL_DIR/" 2>/dev/null || log_warning "trigger-update-monitor.sh 复制失败"
    docker cp "$CONTAINER_NAME:/app/scripts/host/check-monitor-health.sh" "$INSTALL_DIR/" 2>/dev/null || log_warning "check-monitor-health.sh 复制失败"
    docker cp "$CONTAINER_NAME:/app/scripts/host/monitor-status.sh" "$INSTALL_DIR/" 2>/dev/null || log_warning "monitor-status.sh 复制失败"
    docker cp "$CONTAINER_NAME:/app/scripts/host/UPDATE-MONITOR-README.md" "$INSTALL_DIR/" 2>/dev/null || log_warning "README 复制失败"

    log_success "脚本文件复制完成"
else
    log_error "容器 '$CONTAINER_NAME' 不存在，请先启动容器"
    exit 1
fi

# 3. 设置文件权限
log_info "步骤 3/6: 设置文件权限..."
chmod +x "$INSTALL_DIR"/*.sh 2>/dev/null || true
log_success "权限设置完成"

# 4. 安装 systemd 服务
log_info "步骤 4/6: 安装 systemd 服务..."
docker cp "$CONTAINER_NAME:/app/scripts/host/hyperbot-update-monitor.service" /tmp/hyperbot-update-monitor.service 2>/dev/null || log_warning "服务文件复制失败"

if [ -f /tmp/hyperbot-update-monitor.service ]; then
    # 替换安装目录路径
    sed -i "s|/opt/trading-system|$INSTALL_DIR|g" /tmp/hyperbot-update-monitor.service

    # 复制到systemd目录
    cp /tmp/hyperbot-update-monitor.service /etc/systemd/system/
    rm -f /tmp/hyperbot-update-monitor.service

    # 重载并启用服务
    systemctl daemon-reload
    systemctl enable hyperbot-update-monitor
    systemctl start hyperbot-update-monitor

    log_success "Systemd 服务已安装并启动"
else
    log_warning "未找到 systemd 服务文件，跳过"
fi

# 5. 配置 cron 健康检查
log_info "步骤 5/6: 配置 cron 定时任务..."
CRON_JOB="*/5 * * * * $INSTALL_DIR/check-monitor-health.sh"

# 检查是否已存在
if crontab -l 2>/dev/null | grep -q "check-monitor-health.sh"; then
    log_warning "Cron 任务已存在，跳过"
else
    # 添加 cron 任务
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    log_success "Cron 定时任务已配置（每5分钟检查一次）"
fi

# 6. 验证安装
log_info "步骤 6/6: 验证安装..."
echo ""

# 检查 systemd 服务
if systemctl is-active --quiet hyperbot-update-monitor; then
    log_success "✓ Systemd 服务运行正常"
else
    log_warning "✗ Systemd 服务未运行"
fi

# 检查 cron 任务
if crontab -l 2>/dev/null | grep -q "check-monitor-health.sh"; then
    log_success "✓ Cron 定时任务已配置"
else
    log_warning "✗ Cron 定时任务未配置"
fi

# 检查容器
if docker ps | grep -q "$CONTAINER_NAME"; then
    log_success "✓ Docker 容器运行正常"
else
    log_warning "✗ Docker 容器未运行"
fi

echo ""
echo "============================================================"
log_success "监控系统安装完成！"
echo "============================================================"
echo ""
log_info "管理命令："
echo "  查看状态: $INSTALL_DIR/monitor-status.sh"
echo "  查看日志: tail -f $INSTALL_DIR/logs/monitor.log"
echo "  服务管理: systemctl status hyperbot-update-monitor"
echo ""
log_info "文档位置："
echo "  $INSTALL_DIR/UPDATE-MONITOR-README.md"
echo ""
