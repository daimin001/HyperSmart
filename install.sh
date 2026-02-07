#!/bin/bash
################################################################################
# HyperBot 跟单系统一键安装脚本
# 使用方法: curl -L https://raw.githubusercontent.com/daimin001/HyperSmart/main/install.sh | sudo bash
# 或者: sudo bash install.sh
################################################################################

set -e

################################################################################
# 配置变量
################################################################################
APP_NAME="hyperbot-bybit"
DEFAULT_INSTALL_DIR="/opt/${APP_NAME}"
INSTALL_DIR="${1:-$DEFAULT_INSTALL_DIR}"
IMAGE_REGISTRY="crpi-avgutp4svf3qvj1p.ap-northeast-1.personal.cr.aliyuncs.com"
IMAGE_NAMESPACE="hyper-smart"
IMAGE_REPO="hyper-smart"
IMAGE_TAG="2.4.7"
FULL_IMAGE="${IMAGE_REGISTRY}/${IMAGE_NAMESPACE}/${IMAGE_REPO}:${IMAGE_TAG}"
APP_PORT=8080

# 阿里云镜像仓库凭证（用于一键部署）
ALIYUN_USERNAME="无敌豆腐乳"
ALIYUN_PASSWORD="Shuxuetiancai1."

################################################################################
# 颜色定义
################################################################################
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

################################################################################
# 日志函数
################################################################################
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

print_separator() {
    echo -e "${CYAN}================================================================${NC}"
}

################################################################################
# 检查 root 权限
################################################################################
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "此脚本需要 root 权限运行"
        log_info "请使用: curl -L https://raw.githubusercontent.com/daimin001/HyperSmart/main/install.sh | sudo bash"
        exit 1
    fi
    log_success "Root 权限检查通过"
}

################################################################################
# 检查 CPU 架构
################################################################################
check_architecture() {
    log_info "检查 CPU 架构..."

    ARCH=$(uname -m)
    case $ARCH in
        x86_64|amd64)
            log_success "CPU 架构: $ARCH (支持)"
            ;;
        aarch64|arm64)
            log_success "CPU 架构: $ARCH (支持)"
            ;;
        *)
            log_error "不支持的 CPU 架构: $ARCH"
            exit 1
            ;;
    esac
}

################################################################################
# 检查操作系统
################################################################################
check_os() {
    log_info "检查操作系统..."

    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$NAME
        VERSION=$VERSION_ID
        OS_ID=$ID

        case $OS_ID in
            ubuntu|debian|centos|rhel|fedora)
                log_success "操作系统: $OS $VERSION (支持)"
                ;;
            *)
                log_warn "未测试的操作系统: $OS"
                ;;
        esac
    else
        log_error "无法识别操作系统"
        exit 1
    fi
}

################################################################################
# 检查并安装必要工具
################################################################################
install_required_tools() {
    log_info "检查必要工具..."

    # 检查 curl
    if ! command -v curl &> /dev/null; then
        log_info "安装 curl..."
        case $OS_ID in
            ubuntu|debian)
                apt-get update && apt-get install -y curl
                ;;
            centos|rhel|fedora)
                yum install -y curl
                ;;
        esac
    fi

    log_success "必要工具检查完成"
}

################################################################################
# 检查 Docker
################################################################################
check_docker() {
    if command -v docker &> /dev/null; then
        DOCKER_VERSION=$(docker --version | awk '{print $3}' | sed 's/,//')
        log_success "Docker 已安装: $DOCKER_VERSION"

        if systemctl is-active --quiet docker; then
            log_success "Docker 服务运行正常"
        else
            log_info "启动 Docker 服务..."
            systemctl start docker
            systemctl enable docker
            log_success "Docker 服务已启动"
        fi
        return 0
    else
        return 1
    fi
}

################################################################################
# 安装 Docker
################################################################################
install_docker() {
    log_step "安装 Docker"

    case $OS_ID in
        ubuntu|debian)
            log_info "使用 APT 安装 Docker..."
            apt-get update
            apt-get install -y ca-certificates curl gnupg lsb-release

            mkdir -p /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS_ID/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg

            echo \
                "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS_ID \
                $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null

            apt-get update
            apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;

        centos|rhel|fedora)
            log_info "使用 YUM 安装 Docker..."
            yum install -y yum-utils
            yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;

        *)
            log_error "不支持的操作系统: $OS_ID"
            exit 1
            ;;
    esac

    systemctl start docker
    systemctl enable docker

    log_success "Docker 安装完成"
}

################################################################################
# 创建安装目录
################################################################################
create_directories() {
    log_step "创建安装目录"

    log_info "创建目录: $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR/data"
    mkdir -p "$INSTALL_DIR/logs"
    mkdir -p "$INSTALL_DIR/kafka-data"

    log_success "目录创建完成"
}

################################################################################
# 登录阿里云镜像仓库
################################################################################
aliyun_login() {
    log_step "登录阿里云镜像仓库"

    log_info "镜像仓库: $IMAGE_REGISTRY"

    # 自动登录
    echo "$ALIYUN_PASSWORD" | docker login --username "$ALIYUN_USERNAME" --password-stdin "$IMAGE_REGISTRY" > /dev/null 2>&1

    if [ $? -eq 0 ]; then
        log_success "镜像仓库登录成功"
    else
        log_error "镜像仓库登录失败"
        exit 1
    fi
}

################################################################################
# 拉取镜像并提取配置模板
################################################################################
pull_and_extract_configs() {
    log_step "拉取镜像和配置模板"

    # 拉取镜像
    log_info "拉取 Docker 镜像: $FULL_IMAGE"
    docker pull "$FULL_IMAGE"

    if [ $? -ne 0 ]; then
        log_error "镜像拉取失败"
        exit 1
    fi
    log_success "镜像拉取成功"

    # 提取 .env.example
    log_info "提取 .env.example 模板..."
    docker run --rm --entrypoint="" "$FULL_IMAGE" cat /app/.env.example > "$INSTALL_DIR/.env.example"
    log_success ".env.example 已提取"

    # 提取 accounts_config.json.template
    log_info "提取 accounts_config.json.template 模板..."
    docker run --rm --entrypoint="" "$FULL_IMAGE" cat /app/accounts_config.json.template > "$INSTALL_DIR/accounts_config.json.template"
    log_success "accounts_config.json.template 已提取"

    # 提取 docker-compose.yml
    log_info "提取 docker-compose.yml 模板..."
    docker run --rm --entrypoint="" "$FULL_IMAGE" cat /app/docker-compose.yml > "$INSTALL_DIR/docker-compose.yml.template"
    log_success "docker-compose.yml 已提取"
}

################################################################################
# 创建配置文件
################################################################################
create_configs() {
    log_step "创建配置文件"

    # 创建 .env 文件（使用默认值）
    if [ ! -f "$INSTALL_DIR/.env" ]; then
        log_info "创建 .env 配置文件（使用默认值）..."
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        log_success ".env 文件已创建"
        log_warn "请编辑 $INSTALL_DIR/.env 配置您的参数"
    else
        log_info ".env 文件已存在，跳过创建"
    fi

    # 创建 accounts_config.json
    if [ ! -f "$INSTALL_DIR/accounts_config.json" ]; then
        log_info "创建 accounts_config.json 配置文件..."
        cat > "$INSTALL_DIR/accounts_config.json" << 'EOF'
{
  "accounts": []
}
EOF
        log_success "accounts_config.json 文件已创建"
        log_warn "请编辑 $INSTALL_DIR/accounts_config.json 配置您的交易账户"
    else
        log_info "accounts_config.json 文件已存在，跳过创建"
    fi

    # 创建 docker-compose.yml（使用正确的镜像地址）
    log_info "创建 docker-compose.yml..."
    cat > "$INSTALL_DIR/docker-compose.yml" << EOF
services:
  kafka:
    image: apache/kafka:3.7.1
    container_name: ${APP_NAME}-kafka
    restart: always
    environment:
      - KAFKA_NODE_ID=1
      - KAFKA_PROCESS_ROLES=broker,controller
      - KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka:9093
      - KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093
      - KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092
      - KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
      - KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER
      - KAFKA_LOG_DIRS=/tmp/kraft-combined-logs
      - KAFKA_CLUSTER_ID=4L6g3nShT-eMCtK--X86sw
      - KAFKA_AUTO_CREATE_TOPICS_ENABLE=true
      - KAFKA_NUM_PARTITIONS=12
      - KAFKA_DEFAULT_REPLICATION_FACTOR=1
      - KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1
      - KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1
      - KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1
      - KAFKA_MIN_INSYNC_REPLICAS=1
      - KAFKA_COMPRESSION_TYPE=lz4
      - KAFKA_LOG_RETENTION_HOURS=6
      - KAFKA_LOG_SEGMENT_BYTES=268435456
      - KAFKA_LOG_RETENTION_CHECK_INTERVAL_MS=300000
      - KAFKA_SOCKET_SEND_BUFFER_BYTES=131072
      - KAFKA_SOCKET_RECEIVE_BUFFER_BYTES=131072
      - KAFKA_SOCKET_REQUEST_MAX_BYTES=104857600
      - KAFKA_REPLICA_SOCKET_RECEIVE_BUFFER_BYTES=131072
      - KAFKA_LOG_FLUSH_INTERVAL_MESSAGES=10000
      - KAFKA_LOG_FLUSH_INTERVAL_MS=1000
      - KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS=0
      - KAFKA_HEAP_OPTS=-Xmx256m -Xms256m
    volumes:
      - ./kafka-data:/tmp/kraft-combined-logs
    ports:
      - "9092:9092"
    healthcheck:
      test: ["CMD", "bash", "-c", "/opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list"]
      interval: 15s
      timeout: 12s
      retries: 5
      start_period: 30s
    deploy:
      resources:
        limits:
          cpus: '0.8'
          memory: 512M
        reservations:
          cpus: '0.3'
          memory: 256M

  hyperbot-web:
    image: $FULL_IMAGE
    container_name: ${APP_NAME}-web
    restart: always
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "${APP_PORT}:8000"
    volumes:
      - ./logs:/app/logs
      - ./data:/home/sqlite
      - ./data:/app/data
      - ./accounts_config.json:/app/accounts_config.json
      - ./.env:/app/.env
    environment:
      - TZ=Asia/Shanghai
      - PYTHONUNBUFFERED=1
      - ENABLE_AUTO_START_ACCOUNTS=true
      - KAFKA_ENABLED=true
      - KAFKA_BOOTSTRAP_SERVERS=kafka:9092
      - KAFKA_TRADES_TOPIC=hyperliquid.trades
      - KAFKA_CONSUMER_GROUP=hyperliquid-bybit-sync-v2
      - KAFKA_SECURITY_PROTOCOL=PLAINTEXT
      - KAFKA_NUM_WORKERS=5
      - SQLITE_ASYNC_WRITE=false
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    logging:
      driver: "json-file"
      options:
        max-size: "5m"
        max-file: "2"
    deploy:
      resources:
        limits:
          cpus: '1.2'
          memory: 3G
        reservations:
          cpus: '0.5'
          memory: 1G
    depends_on:
      kafka:
        condition: service_healthy
EOF

    log_success "docker-compose.yml 文件已创建"
}

################################################################################
# 部署服务
################################################################################
deploy_services() {
    log_step "部署服务"

    cd "$INSTALL_DIR"

    # 启动服务
    log_info "启动 Docker Compose 服务..."
    docker compose up -d

    if [ $? -ne 0 ]; then
        log_error "服务启动失败"
        log_info "查看日志: docker compose logs"
        exit 1
    fi

    log_success "服务启动成功"

    # 等待服务就绪
    log_info "等待服务启动..."
    sleep 20
}

################################################################################
# 安装宿主机监控服务
################################################################################
install_host_monitoring() {
    log_step "安装宿主机监控服务"

    # 从容器中提取宿主机监控安装脚本
    log_info "提取宿主机监控安装脚本..."

    # 检查容器内是否有安装脚本
    if docker exec ${APP_NAME}-web test -f /app/data/.install-monitor.sh 2>/dev/null; then
        docker exec ${APP_NAME}-web cat /app/data/.install-monitor.sh > "$INSTALL_DIR/install-monitor.sh"
        chmod +x "$INSTALL_DIR/install-monitor.sh"

        log_info "执行宿主机监控安装..."
        bash "$INSTALL_DIR/install-monitor.sh" || log_warn "宿主机监控安装失败（非致命错误）"

        log_success "宿主机监控安装完成"
    else
        log_warn "容器内未找到监控安装脚本，跳过宿主机监控安装"
    fi
}

################################################################################
# 安装容器监控服务
################################################################################
install_container_monitoring() {
    log_step "安装容器监控服务"

    # 创建监控脚本
    log_info "创建容器监控脚本..."
    cat > "$INSTALL_DIR/monitor_containers.sh" << 'MONITOR_SCRIPT'
#!/bin/bash

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$INSTALL_DIR/logs/container_monitor.log"
APP_NAME="hyperbot-bybit"
CONTAINERS=("${APP_NAME}-kafka" "${APP_NAME}-web")

log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

check_container_running() {
    local container=$1
    docker ps --filter "name=$container" --filter "status=running" --format "{{.Names}}" | grep -q "^${container}$"
}

check_container_health() {
    local container=$1
    local health=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null)
    [ "$health" = "healthy" ] || [ "$health" = "" ]
}

restart_container() {
    local container=$1
    log_message "⚠️  容器 $container 异常，尝试重启..."
    cd "$INSTALL_DIR"
    docker compose restart "$container"
    if [ $? -eq 0 ]; then
        log_message "✅ 容器 $container 重启成功"
    else
        log_message "❌ 容器 $container 重启失败"
    fi
}

log_message "开始检查容器状态..."

for container in "${CONTAINERS[@]}"; do
    if ! check_container_running "${container}"; then
        log_message "❌ 容器 $container 未运行"
        restart_container "${container}"
    elif ! check_container_health "${container}"; then
        log_message "⚠️  容器 $container 健康检查失败"
        restart_container "${container}"
    else
        log_message "✅ 容器 $container 运行正常"
    fi
done

log_message "检查完成"
MONITOR_SCRIPT

    chmod +x "$INSTALL_DIR/monitor_containers.sh"
    log_success "监控脚本创建完成"

    # 创建 systemd 服务
    log_info "创建 systemd 服务..."
    cat > /etc/systemd/system/hyperbot-monitor.service << EOF
[Unit]
Description=HyperBot Container Monitor
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=$INSTALL_DIR/monitor_containers.sh
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    # 创建 systemd 定时器
    cat > /etc/systemd/system/hyperbot-monitor.timer << 'EOF'
[Unit]
Description=HyperBot Container Monitor Timer
Requires=hyperbot-monitor.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF

    # 重新加载并启动
    systemctl daemon-reload
    systemctl enable hyperbot-monitor.timer
    systemctl start hyperbot-monitor.timer

    log_success "容器监控服务已启动"
}

################################################################################
# 验证安装
################################################################################
verify_installation() {
    log_step "验证安装"

    cd "$INSTALL_DIR"

    # 检查容器状态
    log_info "检查容器状态..."
    sleep 10

    KAFKA_STATUS=$(docker inspect -f '{{.State.Status}}' ${APP_NAME}-kafka 2>/dev/null)
    WEB_STATUS=$(docker inspect -f '{{.State.Status}}' ${APP_NAME}-web 2>/dev/null)

    if [ "$KAFKA_STATUS" = "running" ]; then
        log_success "Kafka 容器运行正常"
    else
        log_error "Kafka 容器状态异常: $KAFKA_STATUS"
    fi

    if [ "$WEB_STATUS" = "running" ]; then
        log_success "HyperBot Web 容器运行正常"
    else
        log_error "HyperBot Web 容器状态异常: $WEB_STATUS"
    fi

    # 测试 API
    log_info "测试 API 健康检查..."
    MAX_RETRIES=15
    RETRY_COUNT=0

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -f http://localhost:${APP_PORT}/health 2>/dev/null; then
            log_success "API 健康检查通过"
            break
        else
            RETRY_COUNT=$((RETRY_COUNT + 1))
            if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
                log_info "等待服务启动... ($RETRY_COUNT/$MAX_RETRIES)"
                sleep 5
            else
                log_warn "API 健康检查超时（服务可能需要更长时间）"
                log_info "您可以稍后使用以下命令检查: curl http://localhost:${APP_PORT}/health"
            fi
        fi
    done

    # 检查监控服务
    log_info "检查监控服务..."
    if systemctl is-active --quiet hyperbot-monitor.timer; then
        log_success "容器监控服务运行正常"
    else
        log_warn "容器监控服务未运行"
    fi

    # 保存安装信息
    cat > "$INSTALL_DIR/.hyperbot_config" << EOF
# HyperBot 配置文件
INSTALL_DIR="$INSTALL_DIR"
INSTALL_DATE="$(date '+%Y-%m-%d %H:%M:%S')"
VERSION="$IMAGE_TAG"
IMAGE="$FULL_IMAGE"
APP_PORT="$APP_PORT"
EOF

    log_success "安装验证完成"
}

################################################################################
# 显示安装摘要
################################################################################
show_summary() {
    local SERVER_IP=$(hostname -I | awk '{print $1}')

    print_separator
    echo ""
    echo -e "${GREEN}🎉 HyperBot 跟单系统安装完成！${NC}"
    echo ""
    echo "安装信息:"
    echo "  安装目录:     $INSTALL_DIR"
    echo "  Docker镜像:   $FULL_IMAGE"
    echo "  Web 端口:     $APP_PORT"
    echo ""
    echo "访问地址:"
    echo "  Web 界面:     http://${SERVER_IP}:${APP_PORT}"
    echo "  健康检查:     http://localhost:${APP_PORT}/health"
    echo ""
    echo "配置文件:"
    echo "  环境变量:     $INSTALL_DIR/.env"
    echo "  账户配置:     $INSTALL_DIR/accounts_config.json"
    echo "  Compose:      $INSTALL_DIR/docker-compose.yml"
    echo ""
    echo -e "${YELLOW}⚠️  重要提示:${NC}"
    echo "  1. 请编辑配置文件设置您的交易参数:"
    echo "     - vi $INSTALL_DIR/.env"
    echo "     - vi $INSTALL_DIR/accounts_config.json"
    echo ""
    echo "  2. 配置完成后重启服务:"
    echo "     - cd $INSTALL_DIR && docker compose restart"
    echo ""
    echo "常用命令:"
    echo "  查看日志:     cd $INSTALL_DIR && docker compose logs -f"
    echo "  重启服务:     cd $INSTALL_DIR && docker compose restart"
    echo "  停止服务:     cd $INSTALL_DIR && docker compose stop"
    echo "  启动服务:     cd $INSTALL_DIR && docker compose start"
    echo "  查看状态:     cd $INSTALL_DIR && docker compose ps"
    echo ""
    echo "监控服务:"
    echo "  容器监控:     systemctl status hyperbot-monitor.timer"
    echo "  监控日志:     tail -f $INSTALL_DIR/logs/container_monitor.log"
    echo ""
    print_separator
}

################################################################################
# 主函数
################################################################################
main() {
    clear
    echo -e "${CYAN}"
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║                                                                ║"
    echo "║         HyperBot 跟单系统一键安装程序 v${IMAGE_TAG}              ║"
    echo "║                                                                ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}\n"

    # 步骤 1: 系统检查
    log_step "步骤 1/9: 系统检查"
    check_root
    check_architecture
    check_os
    install_required_tools

    # 步骤 2: Docker 环境
    log_step "步骤 2/9: Docker 环境检查"
    if ! check_docker; then
        log_info "Docker 未安装，开始自动安装..."
        install_docker
    fi

    # 步骤 3: 创建目录
    create_directories

    # 步骤 4: 登录镜像仓库
    aliyun_login

    # 步骤 5: 拉取镜像和配置
    pull_and_extract_configs

    # 步骤 6: 创建配置文件
    create_configs

    # 步骤 7: 部署服务
    deploy_services

    # 步骤 8: 安装监控
    install_container_monitoring
    install_host_monitoring

    # 步骤 9: 验证安装
    verify_installation

    # 显示摘要
    show_summary
}

# 执行主函数
main "$@"
