#!/bin/bash

# ============================================================================
# 跟单系统 Docker 一键安装脚本
# 支持的系统: Ubuntu/Debian/CentOS/RHEL/Fedora
# ============================================================================

set -e

# ============================================================================
# 配置变量
# ============================================================================
APP_NAME="trading-system"
CONTAINER_NAME="${APP_NAME}-app"
INSTALL_DIR="/opt/${APP_NAME}"
IMAGE_NAME="crpi-avgutp4svf3qvj1p.ap-northeast-1.personal.cr.aliyuncs.com/hyper-smart/hyper-smart"  # 修改为您的镜像地址
IMAGE_TAG="latest"
APP_PORT=8080
INTERNAL_PORT=8000

# ============================================================================
# 颜色定义
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# ============================================================================
# 日志函数
# ============================================================================
log_info() {
    echo -e "${GREEN}[$(date +'%H:%M:%S')]${NC} ℹ️  $1"
}

log_success() {
    echo -e "${GREEN}[$(date +'%H:%M:%S')]${NC} ✅ $1"
}

log_warn() {
    echo -e "${YELLOW}[$(date +'%H:%M:%S')]${NC} ⚠️  $1"
}

log_error() {
    echo -e "${RED}[$(date +'%H:%M:%S')]${NC} ❌ $1"
}

log_step() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}▶  $1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
}

# ============================================================================
# 检查root权限
# ============================================================================
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "此脚本需要root权限运行"
        log_info "请使用: sudo bash install.sh"
        exit 1
    fi
    log_success "Root权限检查通过"
}

# ============================================================================
# 检查CPU架构
# ============================================================================
check_architecture() {
    log_info "检查CPU架构..."

    ARCH=$(uname -m)
    case $ARCH in
        x86_64|amd64)
            log_success "CPU架构: $ARCH (支持)"
            ;;
        aarch64|arm64)
            log_success "CPU架构: $ARCH (支持)"
            ;;
        *)
            log_error "不支持的CPU架构: $ARCH"
            log_error "此脚本仅支持 x86_64/amd64 和 aarch64/arm64 架构"
            exit 1
            ;;
    esac
}

# ============================================================================
# 检查操作系统
# ============================================================================
check_os() {
    log_info "检查操作系统..."

    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$NAME
        VERSION=$VERSION_ID
        OS_ID=$ID
        log_success "操作系统: $OS $VERSION"

        case $OS_ID in
            ubuntu|debian|centos|rhel|fedora|opensuse|sles|amzn|rocky|almalinux)
                log_success "支持的Linux发行版"
                ;;
            *)
                log_warn "未经测试的Linux发行版: $OS_ID"
                log_warn "脚本将继续运行，但可能遇到问题"
                ;;
        esac
    else
        log_error "无法识别操作系统"
        exit 1
    fi
}

# ============================================================================
# 检查Docker是否已安装
# ============================================================================
check_docker() {
    log_info "检查Docker安装状态..."

    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | awk '{print $3}' | sed 's/,//')
        log_success "Docker已安装 (版本: $DOCKER_VERSION)"

        # 检查Docker服务状态
        if systemctl is-active --quiet docker 2>/dev/null; then
            log_success "Docker服务正在运行"
        else
            log_info "启动Docker服务..."
            systemctl start docker
            systemctl enable docker
            log_success "Docker服务已启动"
        fi

        # 检查Docker权限
        if docker info &> /dev/null; then
            log_success "Docker权限正常"
            return 0
        else
            log_error "Docker权限检查失败"
            exit 1
        fi
    else
        log_warn "Docker未安装"
        return 1
    fi
}

# ============================================================================
# 安装Docker
# ============================================================================
install_docker() {
    log_step "开始安装Docker"

    if command -v apt-get &> /dev/null; then
        # Ubuntu/Debian
        log_info "检测到 Debian/Ubuntu 系统，使用 apt 安装..."

        # 更新包索引
        apt-get update -y

        # 安装依赖
        apt-get install -y \
            apt-transport-https \
            ca-certificates \
            curl \
            gnupg \
            lsb-release

        # 添加Docker官方GPG密钥
        log_info "添加Docker官方GPG密钥..."
        mkdir -p /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/${OS_ID}/gpg | \
            gpg --dearmor -o /etc/apt/keyrings/docker.gpg

        # 添加Docker APT仓库
        log_info "添加Docker APT仓库..."
        echo \
            "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
            https://download.docker.com/linux/${OS_ID} \
            $(lsb_release -cs) stable" | \
            tee /etc/apt/sources.list.d/docker.list > /dev/null

        # 更新包索引
        apt-get update -y

        # 安装Docker
        log_info "安装Docker Engine..."
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    elif command -v yum &> /dev/null; then
        # CentOS/RHEL/AlmaLinux/Rocky
        log_info "检测到 RHEL/CentOS 系统，使用 yum 安装..."

        # 安装依赖
        yum install -y yum-utils

        # 添加Docker仓库
        log_info "添加Docker仓库..."
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

        # 安装Docker
        log_info "安装Docker Engine..."
        yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    elif command -v dnf &> /dev/null; then
        # Fedora
        log_info "检测到 Fedora 系统，使用 dnf 安装..."

        # 安装依赖
        dnf -y install dnf-plugins-core

        # 添加Docker仓库
        log_info "添加Docker仓库..."
        dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo

        # 安装Docker
        log_info "安装Docker Engine..."
        dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    else
        log_error "不支持的包管理器，请手动安装Docker"
        exit 1
    fi

    # 启动Docker服务
    log_info "启动Docker服务..."
    systemctl start docker
    systemctl enable docker
    systemctl daemon-reload

    # 验证安装
    if docker --version &> /dev/null; then
        DOCKER_VERSION=$(docker --version | awk '{print $3}' | sed 's/,//')
        log_success "Docker安装成功 (版本: $DOCKER_VERSION)"
    else
        log_error "Docker安装失败"
        exit 1
    fi
}

# ============================================================================
# 生成随机字符串
# ============================================================================
generate_random_string() {
    local length=$1
    local chars="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    local result=""

    for i in $(seq 1 $length); do
        result="${result}${chars:RANDOM%${#chars}:1}"
    done

    echo "$result"
}

# ============================================================================
# IP地址验证
# ============================================================================
validate_ip() {
    local ip=$1

    # 检查是否为空
    if [ -z "$ip" ]; then
        return 1
    fi

    # 检查基本格式：应该有3个点
    if [ "$(echo "$ip" | tr -cd '.' | wc -c)" -ne 3 ]; then
        return 1
    fi

    # 分割IP并验证每一段
    IFS='.' read -r part1 part2 part3 part4 <<< "$ip"

    # 检查每一段是否在0-255之间
    for part in "$part1" "$part2" "$part3" "$part4"; do
        # 检查是否为数字
        if ! [[ "$part" =~ ^[0-9]+$ ]]; then
            return 1
        fi
        # 检查范围0-255
        if [ "$part" -lt 0 ] || [ "$part" -gt 255 ]; then
            return 1
        fi
        # 检查前导零（除了"0"本身）
        if [ "${#part}" -gt 1 ] && [ "${part:0:1}" = "0" ]; then
            return 1
        fi
    done

    return 0
}

# ============================================================================
# 获取服务器IP地址
# ============================================================================
get_server_ip() {
    log_step "配置服务器IP地址"

    # 尝试自动获取公网IP
    log_info "正在自动检测公网IP..."
    auto_ip=$(curl -s --connect-timeout 5 https://api.ipify.org || \
              curl -s --connect-timeout 5 https://ifconfig.me || \
              curl -s --connect-timeout 5 https://icanhazip.com || \
              true)

    if validate_ip "$auto_ip"; then
        echo ""
        log_success "检测到公网IP: ${CYAN}$auto_ip${NC}"
        SERVER_IP="$auto_ip"
        log_success "已使用自动检测的IP: $SERVER_IP"
        return
    else
        log_warn "无法自动检测公网IP"
    fi

    # 手动输入IP
    echo ""
    log_info "请手动输入服务器公网IP地址"
    while true; do
        read -p "$(echo -e ${CYAN}IP地址:${NC} )" SERVER_IP < /dev/tty

        if validate_ip "$SERVER_IP"; then
            log_success "IP地址验证通过: $SERVER_IP"
            break
        else
            log_error "IP地址格式无效（示例: 192.168.1.1）"
        fi
    done
}

# ============================================================================
# 生成配置文件
# ============================================================================
generate_config() {
    log_step "生成系统配置"

    # 创建安装目录
    log_info "创建安装目录: $INSTALL_DIR"
    mkdir -p ${INSTALL_DIR}/{data,config,logs,backups}

    # 生成随机密钥
    log_info "生成安全密钥..."
    JWT_SECRET=$(generate_random_string 64)
    DB_PASSWORD=$(generate_random_string 32)

    log_success "安全密钥生成完成"

    # 创建 .env 配置文件
    log_info "创建配置文件..."
    cat > ${INSTALL_DIR}/.env << EOF
# ============================================================================
# 跟单系统配置文件
# 生成时间: $(date)
# ============================================================================

# 应用配置
NODE_ENV=production
PORT=${INTERNAL_PORT}
APP_NAME=${APP_NAME}

# 服务器配置
SERVER_IP=${SERVER_IP}
ALLOWED_DOMAIN=${SERVER_IP}

# JWT配置
JWT_SECRET=${JWT_SECRET}
JWT_EXPIRES_IN=240h

# 数据库配置
DB_HOST=localhost
DB_PORT=5432
DB_NAME=${APP_NAME}
DB_USER=${APP_NAME}
DB_PASSWORD=${DB_PASSWORD}

# 版本信息
VERSION=1.0.0
INSTALL_DATE=$(date +%Y-%m-%d)

# 更新服务器
UPDATE_CHECK_URL=https://your-update-server.com/api/version-check
EOF

    chmod 600 ${INSTALL_DIR}/.env
    log_success "配置文件已保存: ${INSTALL_DIR}/.env"

    # 导出环境变量供后续使用
    export SERVER_IP
}

# ============================================================================
# 停止并删除旧容器
# ============================================================================
cleanup_old_container() {
    log_info "清理旧容器和数据..."

    # 检查是否存在旧容器
    if docker ps -a | grep -q ${CONTAINER_NAME}; then
        log_info "发现旧容器，正在停止并删除..."
        docker stop ${CONTAINER_NAME} 2>/dev/null || true
        docker rm ${CONTAINER_NAME} 2>/dev/null || true
        log_success "旧容器已清理"
    else
        log_info "未发现旧容器"
    fi

    # 检查是否存在旧数据目录（自动删除，确保全新安装）
    if [ -d "${INSTALL_DIR}" ] && [ "$(ls -A ${INSTALL_DIR} 2>/dev/null)" ]; then
        echo ""
        log_warn "检测到旧的安装数据: ${INSTALL_DIR}"
        log_info "正在备份旧数据..."

        # 备份旧数据
        BACKUP_DIR="${INSTALL_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
        mv ${INSTALL_DIR} ${BACKUP_DIR}

        log_success "旧数据已备份到: ${BACKUP_DIR}"
        log_info "将进行全新安装，安装后需要重新注册账号"
        echo ""
    fi
}

# ============================================================================
# 拉取Docker镜像
# ============================================================================
pull_docker_image() {
    log_step "拉取Docker镜像"

    log_info "正在拉取镜像: ${IMAGE_NAME}:${IMAGE_TAG}"
    log_warn "首次安装可能需要较长时间，请耐心等待..."

    if docker pull ${IMAGE_NAME}:${IMAGE_TAG}; then
        log_success "镜像拉取成功"
    else
        log_error "镜像拉取失败"
        log_error "请检查网络连接和镜像地址"
        exit 1
    fi
}

# ============================================================================
# 启动Docker容器
# ============================================================================
start_container() {
    log_step "启动应用容器"

    log_info "正在启动容器..."

    docker run -d \
        --name ${CONTAINER_NAME} \
        --restart always \
        --health-cmd="curl -f http://localhost:${INTERNAL_PORT}/health || exit 1" \
        --health-interval=30s \
        --health-timeout=10s \
        --health-retries=3 \
        --health-start-period=40s \
        -p ${APP_PORT}:${INTERNAL_PORT} \
        -v "${INSTALL_DIR}/.env:/app/.env:ro" \
        -v "${INSTALL_DIR}/data:/app/data" \
        -v "${INSTALL_DIR}/logs:/app/logs" \
        -e TZ=Asia/Shanghai \
        ${IMAGE_NAME}:${IMAGE_TAG}

    if [ $? -eq 0 ]; then
        log_success "容器启动成功"
    else
        log_error "容器启动失败"
        exit 1
    fi
}

# ============================================================================
# 等待服务就绪
# ============================================================================
wait_for_service() {
    log_step "等待服务就绪"

    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        log_info "健康检查 (${attempt}/${max_attempts})..."

        if docker ps | grep -q ${CONTAINER_NAME}; then
            # 检查容器健康状态
            health_status=$(docker inspect --format='{{.State.Health.Status}}' ${CONTAINER_NAME} 2>/dev/null || echo "none")

            if [ "$health_status" = "healthy" ]; then
                log_success "服务已就绪，健康状态: ${health_status}"
                return 0
            elif [ "$health_status" = "none" ]; then
                # 如果没有健康检查，尝试直接访问
                if curl -f -s http://localhost:${APP_PORT}/health &> /dev/null; then
                    log_success "服务已就绪"
                    return 0
                fi
            fi

            log_info "当前状态: ${health_status}, 等待中..."
        else
            log_error "容器已停止"
            docker logs --tail 50 ${CONTAINER_NAME}
            exit 1
        fi

        sleep 2
        ((attempt++))
    done

    log_error "服务启动超时"
    log_error "查看容器日志:"
    docker logs --tail 50 ${CONTAINER_NAME}
    exit 1
}

# ============================================================================
# 安装宿主机监控系统
# ============================================================================
install_monitor_system() {
    log_step "安装自动更新监控系统"

    log_info "正在从容器安装监控系统..."

    # 等待容器完全启动
    sleep 3

    # 检查容器是否运行
    if ! docker ps | grep -q ${CONTAINER_NAME}; then
        log_error "容器未运行，无法安装监控系统"
        return 1
    fi

    # 从容器提取安装脚本
    log_info "从容器提取监控安装脚本..."
    if docker exec ${CONTAINER_NAME} cat /app/data/.install-monitor.sh > /tmp/install-monitor.sh 2>/dev/null; then
        chmod +x /tmp/install-monitor.sh
        log_success "监控安装脚本已准备好"
    else
        log_warn "无法从容器提取安装脚本，将在容器日志中查看手动安装命令"
        log_info "请查看容器启动日志："
        docker logs ${CONTAINER_NAME} | grep -A 5 "请在宿主机上执行以下命令" || true
        return 1
    fi

    # 执行监控安装脚本
    log_info "执行监控系统安装..."
    if bash /tmp/install-monitor.sh "${INSTALL_DIR}" "${CONTAINER_NAME}" > /tmp/monitor-install.log 2>&1; then
        log_success "监控系统安装成功"

        # 显示监控服务状态
        if systemctl is-active --quiet hyperbot-update-monitor; then
            log_success "监控服务已启动并运行"
        else
            log_warn "监控服务未运行，请检查日志"
        fi

        # 清理临时文件
        rm -f /tmp/install-monitor.sh /tmp/monitor-install.log

        return 0
    else
        log_error "监控系统安装失败，查看日志："
        cat /tmp/monitor-install.log
        log_warn "您可以稍后手动安装监控系统："
        log_warn "  docker exec ${CONTAINER_NAME} cat /app/data/.install-monitor.sh | sudo bash"
        return 1
    fi
}

# ============================================================================
# 验证安装配置
# ============================================================================
verify_installation() {
    log_step "验证安装配置"

    local error_count=0

    # 1. 检查Docker服务开机自启
    log_info "检查Docker服务配置..."
    if systemctl is-enabled docker &>/dev/null; then
        log_success "Docker服务已设置为开机自启"
    else
        log_warn "Docker服务未设置为开机自启，正在修复..."
        systemctl enable docker
        ((error_count++))
    fi

    # 2. 检查容器运行状态
    log_info "检查容器运行状态..."
    if docker ps | grep -q ${CONTAINER_NAME}; then
        log_success "容器正在运行"
    else
        log_error "容器未运行"
        ((error_count++))
    fi

    # 3. 检查容器重启策略
    log_info "检查容器重启策略..."
    restart_policy=$(docker inspect --format='{{.HostConfig.RestartPolicy.Name}}' ${CONTAINER_NAME} 2>/dev/null)
    if [ "$restart_policy" = "always" ]; then
        log_success "容器重启策略: always"
    else
        log_error "容器重启策略异常: $restart_policy"
        ((error_count++))
    fi

    # 4. 检查健康检查配置
    log_info "检查健康检查配置..."
    health_check=$(docker inspect --format='{{.Config.Healthcheck}}' ${CONTAINER_NAME} 2>/dev/null)
    if [ -n "$health_check" ] && [ "$health_check" != "<nil>" ]; then
        log_success "健康检查已配置"
    else
        log_warn "健康检查未配置"
        ((error_count++))
    fi

    # 5. 检查配置文件
    log_info "检查配置文件..."
    if [ -f "${INSTALL_DIR}/.env" ]; then
        log_success "配置文件存在: ${INSTALL_DIR}/.env"
    else
        log_error "配置文件缺失"
        ((error_count++))
    fi

    # 6. 检查卷挂载
    log_info "检查卷挂载..."
    if docker inspect ${CONTAINER_NAME} --format='{{range .Mounts}}{{.Source}}:{{.Destination}}{{"\n"}}{{end}}' | grep -q ".env"; then
        log_success "配置文件已正确挂载"
    else
        log_error "配置文件挂载异常"
        ((error_count++))
    fi

    # 7. 检查端口映射
    log_info "检查端口映射..."
    if docker port ${CONTAINER_NAME} | grep -q "${APP_PORT}"; then
        log_success "端口映射正确: ${APP_PORT}"
    else
        log_error "端口映射异常"
        ((error_count++))
    fi

    echo ""
    if [ $error_count -eq 0 ]; then
        log_success "所有验证项通过"
    else
        log_warn "发现 $error_count 个问题，但安装已完成"
    fi
}

# ============================================================================
# 设置日志清理定时任务
# ============================================================================
setup_log_cleanup_cron() {
    log_step "配置日志自动清理任务"

    # 创建日志清理脚本
    log_info "创建日志清理脚本..."
    cat > ${INSTALL_DIR}/cleanup_logs.sh <<'CLEANUP_SCRIPT'
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

    chmod +x ${INSTALL_DIR}/cleanup_logs.sh
    log_success "日志清理脚本已创建: ${INSTALL_DIR}/cleanup_logs.sh"

    # 添加到crontab
    log_info "配置定时任务..."

    # 检查是否已存在该任务
    if crontab -l 2>/dev/null | grep -q "${INSTALL_DIR}/cleanup_logs.sh"; then
        log_info "日志清理任务已存在，跳过添加"
    else
        # 添加新的cron任务（每天凌晨3点执行）
        (crontab -l 2>/dev/null; echo "0 3 * * * ${INSTALL_DIR}/cleanup_logs.sh >> ${INSTALL_DIR}/logs/cleanup.log 2>&1") | crontab -
        log_success "日志清理定时任务已添加（每天凌晨3点执行）"
    fi

    # 验证cron任务
    if crontab -l 2>/dev/null | grep -q "${INSTALL_DIR}/cleanup_logs.sh"; then
        log_success "定时任务配置成功"
    else
        log_warn "定时任务配置可能失败，请手动检查"
    fi
}

# ============================================================================
# 创建管理脚本
# ============================================================================
create_management_scripts() {
    log_step "创建管理脚本"

    # ========== start.sh ==========
    cat > ${INSTALL_DIR}/start.sh <<'SCRIPT_END'
#!/bin/bash
cd $(dirname $0)

echo "🚀 启动服务..."
docker start trading-system-app

sleep 3
if docker ps | grep -q trading-system-app; then
    echo "✅ 服务已启动"
    docker ps | grep trading-system-app
else
    echo "❌ 服务启动失败"
    docker logs --tail 20 trading-system-app
    exit 1
fi
SCRIPT_END

    # ========== stop.sh ==========
    cat > ${INSTALL_DIR}/stop.sh <<'SCRIPT_END'
#!/bin/bash
cd $(dirname $0)

echo "🛑 停止服务..."
docker stop trading-system-app

if [ $? -eq 0 ]; then
    echo "✅ 服务已停止"
else
    echo "❌ 服务停止失败"
    exit 1
fi
SCRIPT_END

    # ========== restart.sh ==========
    cat > ${INSTALL_DIR}/restart.sh <<'SCRIPT_END'
#!/bin/bash
cd $(dirname $0)

echo "🔄 重启服务..."
docker restart trading-system-app

sleep 3
if docker ps | grep -q trading-system-app; then
    echo "✅ 服务已重启"
    docker ps | grep trading-system-app
else
    echo "❌ 服务重启失败"
    exit 1
fi
SCRIPT_END

    # ========== status.sh ==========
    cat > ${INSTALL_DIR}/status.sh <<'SCRIPT_END'
#!/bin/bash
cd $(dirname $0)

echo "📊 服务状态"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
docker ps -a | grep trading-system-app
echo ""

if docker ps | grep -q trading-system-app; then
    echo "✅ 容器运行中"

    # 显示健康状态
    health_status=$(docker inspect --format='{{.State.Health.Status}}' trading-system-app 2>/dev/null || echo "none")
    echo "🏥 健康状态: $health_status"

    # 显示资源使用
    echo ""
    echo "📈 资源使用:"
    docker stats --no-stream trading-system-app
else
    echo "❌ 容器未运行"
fi
SCRIPT_END

    # ========== logs.sh ==========
    cat > ${INSTALL_DIR}/logs.sh <<'SCRIPT_END'
#!/bin/bash
cd $(dirname $0)

# 默认显示最后100行，可通过参数指定
LINES=${1:-100}

echo "📋 查看日志 (最后 ${LINES} 行)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "提示: 按 Ctrl+C 退出实时日志"
echo ""

docker logs -f --tail ${LINES} trading-system-app
SCRIPT_END

    # 设置执行权限
    chmod +x ${INSTALL_DIR}/*.sh

    log_success "管理脚本创建完成"
    log_info "脚本位置: ${INSTALL_DIR}/"
}

# ============================================================================
# 显示安装完成信息
# ============================================================================
show_completion_info() {
    clear

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                                                                                ║${NC}"
    echo -e "${GREEN}║                          🎉 安装完成！🎉                                         ║${NC}"
    echo -e "${GREEN}║                                                                                ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  📋 系统信息${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  🌐 访问地址:  ${CYAN}http://${SERVER_IP}:${APP_PORT}${NC}"
    echo ""
    echo -e "  📁 安装目录:   ${GREEN}${INSTALL_DIR}${NC}"
    echo -e "  📄 配置文件:   ${GREEN}${INSTALL_DIR}/.env${NC}"
    echo -e "  📊 数据目录:   ${GREEN}${INSTALL_DIR}/data${NC}"
    echo -e "  📝 日志目录:   ${GREEN}${INSTALL_DIR}/logs${NC}"
    echo ""

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  ⚠️  重要提示${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  1️⃣  首次访问请注册账号并绑定 Google Authenticator"
    echo -e "  2️⃣  建议配置防火墙规则"
    echo -e "  3️⃣  配置文件包含敏感信息，请妥善保管"
    echo ""

    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  🎁 永久使用权限${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${YELLOW}本产品免费公测结束后，填写了 R39ZX1N 邀请码的用户可以永久使用。${NC}"
    echo ""
    echo -e "  📌 Bybit 邀请链接: ${CYAN}https://www.bybitglobal.com/invite?ref=R39ZX1N${NC}"
    echo ""

    echo -e "${GREEN}✅ 感谢使用！如有问题请查看文档或联系技术支持${NC}"
    echo ""
}

# ============================================================================
# 错误处理
# ============================================================================
error_handler() {
    log_error "安装过程中发生错误 (行号: $1)"
    log_info "正在清理..."

    # 清理可能创建的容器
    docker stop ${CONTAINER_NAME} 2>/dev/null || true
    docker rm ${CONTAINER_NAME} 2>/dev/null || true

    log_error "安装失败，请检查错误信息后重试"
    exit 1
}

trap 'error_handler $LINENO' ERR

# ============================================================================
# 主函数
# ============================================================================
main() {
    # 显示欢迎信息
    clear
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║                                                                                ║${NC}"
    echo -e "${CYAN}║                      🚀 跟单系统 Docker 一键安装脚本 🚀                          ║${NC}"
    echo -e "${CYAN}║                                                                                ║${NC}"
    echo -e "${CYAN}║                              版本: 1.0.0                                        ║${NC}"
    echo -e "${CYAN}║                                                                                ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    log_info "开始安装，请稍候..."
    sleep 2

    # 执行安装步骤
    log_step "系统环境检查"
    check_root
    check_architecture
    check_os

    # Docker检查和安装
    log_step "Docker环境配置"
    if ! check_docker; then
        install_docker
    fi

    # 获取服务器IP
    get_server_ip

    # 清理旧容器（必须在生成配置之前执行）
    cleanup_old_container

    # 生成配置
    generate_config

    # 拉取镜像
    pull_docker_image

    # 启动容器
    start_container

    # 等待服务就绪
    wait_for_service

    # 安装监控系统
    install_monitor_system

    # 验证安装
    verify_installation

    # 创建管理脚本
    create_management_scripts

    # 设置日志清理定时任务
    setup_log_cleanup_cron

    # 显示完成信息
    show_completion_info
}

# ============================================================================
# 执行主函数
# ============================================================================
main "$@"
