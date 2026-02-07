#!/bin/bash

# ============================================================================
# Docker 镜像构建和推送脚本
# ============================================================================
# 用途: 自动化构建Docker镜像并推送到镜像仓库
# 用法: ./build-and-push.sh [版本号]
#
# 示例:
#   ./build-and-push.sh 1.0.0    # 指定版本号
#   ./build-and-push.sh          # 从version.txt读取版本号
# ============================================================================

set -e  # 遇到错误立即退出

# ==================== 配置项 ====================

# 选择仓库类型
REGISTRY_TYPE="aliyun"

# 阿里云配置
ALIYUN_REGISTRY="crpi-avgutp4svf3qvj1p.ap-northeast-1.personal.cr.aliyuncs.com"
ALIYUN_NAMESPACE="hyper-smart"
ALIYUN_REPO="hyper-smart"

# Docker Hub 配置（备用）
DOCKERHUB_USERNAME="your-dockerhub-username"
DOCKERHUB_REPO="trading-system"

# 项目名称
PROJECT_NAME="hyperbot-bybit"

# ==================== 颜色输出 ====================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 辅助函数 ====================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_separator() {
    echo ""
    echo "========================================================================"
}

# ==================== 版本号处理 ====================

get_version() {
    # 优先使用命令行参数
    if [ -n "$1" ]; then
        echo "$1"
        return
    fi

    # 尝试从version.txt读取
    if [ -f "version.txt" ]; then
        version=$(cat version.txt | tr -d '[:space:]')
        if [ -n "$version" ]; then
            echo "$version"
            return
        fi
    fi

    # 默认版本号
    echo "1.0.0"
}

# ==================== 仓库配置 ====================

get_image_name() {
    local version=$1

    if [ "$REGISTRY_TYPE" = "aliyun" ]; then
        echo "${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}/${ALIYUN_REPO}:${version}"
    elif [ "$REGISTRY_TYPE" = "dockerhub" ]; then
        echo "${DOCKERHUB_USERNAME}/${DOCKERHUB_REPO}:${version}"
    else
        log_error "不支持的仓库类型: $REGISTRY_TYPE"
        exit 1
    fi
}

get_latest_image_name() {
    if [ "$REGISTRY_TYPE" = "aliyun" ]; then
        echo "${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}/${ALIYUN_REPO}:latest"
    elif [ "$REGISTRY_TYPE" = "dockerhub" ]; then
        echo "${DOCKERHUB_USERNAME}/${DOCKERHUB_REPO}:latest"
    fi
}

# ==================== Docker登录 ====================

docker_login() {
    log_info "登录到镜像仓库..."

    if [ "$REGISTRY_TYPE" = "aliyun" ]; then
        log_info "登录到阿里云镜像仓库: $ALIYUN_REGISTRY"
        # 使用环境变量或直接登录
        if [ -n "$ALIYUN_USERNAME" ] && [ -n "$ALIYUN_PASSWORD" ]; then
            echo "$ALIYUN_PASSWORD" | docker login --username "$ALIYUN_USERNAME" --password-stdin $ALIYUN_REGISTRY
        else
            # 自动使用凭证
            echo "Shuxuetiancai1." | docker login --username "无敌豆腐乳" --password-stdin $ALIYUN_REGISTRY
        fi
    elif [ "$REGISTRY_TYPE" = "dockerhub" ]; then
        log_info "登录到Docker Hub"
        docker login
    fi

    if [ $? -eq 0 ]; then
        log_success "登录成功"
    else
        log_error "登录失败"
        exit 1
    fi
}

# ==================== 构建镜像 ====================

build_image() {
    local version=$1
    local image_name=$2
    local latest_image=$3

    print_separator
    log_info "开始构建Docker镜像..."
    log_info "版本号: $version"
    log_info "镜像名: $image_name"
    log_info "Latest: $latest_image"
    print_separator

    # 检查Dockerfile是否存在
    if [ ! -f "Dockerfile" ]; then
        log_error "Dockerfile 不存在"
        exit 1
    fi

    # 构建镜像（同时打上版本标签和latest标签）
    log_info "执行docker build..."
    docker build \
        --platform linux/amd64 \
        -t "$image_name" \
        -t "$latest_image" \
        .

    if [ $? -eq 0 ]; then
        log_success "镜像构建成功"
    else
        log_error "镜像构建失败"
        exit 1
    fi
}

# ==================== 测试镜像 ====================

test_image() {
    local image_name=$1

    print_separator
    log_info "测试镜像..."

    # 创建临时测试环境
    log_info "创建临时测试环境..."
    TEST_NETWORK="test-network-$$"
    TEST_KAFKA="test-kafka-$$"
    TEST_CONTAINER="test-${PROJECT_NAME}-$$"

    # 创建测试网络
    docker network create "$TEST_NETWORK" 2>/dev/null || true

    # 启动测试 Kafka
    log_info "启动测试 Kafka 容器..."
    docker run -d \
        --name "$TEST_KAFKA" \
        --network "$TEST_NETWORK" \
        -e KAFKA_NODE_ID=1 \
        -e KAFKA_PROCESS_ROLES=broker,controller \
        -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
        -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 \
        -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://$TEST_KAFKA:9092 \
        -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
        -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
        -e KAFKA_LOG_DIRS=/tmp/kraft-combined-logs \
        -e KAFKA_CLUSTER_ID=test-cluster-id \
        apache/kafka:3.7.1 \
        > /dev/null 2>&1

    # 等待 Kafka 启动
    log_info "等待 Kafka 启动..."
    sleep 10

    # 启动测试应用容器
    log_info "启动测试应用容器..."
    docker run -d \
        --name "$TEST_CONTAINER" \
        --network "$TEST_NETWORK" \
        -p 8888:8000 \
        -e KAFKA_ENABLED=true \
        -e KAFKA_BOOTSTRAP_SERVERS=$TEST_KAFKA:9092 \
        -e TZ=Asia/Shanghai \
        "$image_name" \
        > /dev/null 2>&1

    if [ $? -ne 0 ]; then
        log_error "测试容器启动失败"
        cleanup_test_env "$TEST_CONTAINER" "$TEST_KAFKA" "$TEST_NETWORK"
        exit 1
    fi

    # 等待容器启动
    log_info "等待应用启动..."
    sleep 10

    # 检查容器状态
    CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' "$TEST_CONTAINER" 2>/dev/null)

    if [ "$CONTAINER_STATUS" = "running" ]; then
        log_success "测试容器运行正常"

        # 测试健康检查
        log_info "测试 API 健康检查..."
        MAX_RETRIES=5
        RETRY_COUNT=0

        while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
            if curl -f http://localhost:8888/health 2>/dev/null; then
                log_success "API 健康检查通过"
                break
            else
                RETRY_COUNT=$((RETRY_COUNT + 1))
                if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
                    log_info "重试 $RETRY_COUNT/$MAX_RETRIES..."
                    sleep 3
                else
                    log_warning "API 健康检查失败（可能需要更长启动时间）"
                fi
            fi
        done
    else
        log_error "测试容器状态异常: $CONTAINER_STATUS"
        log_info "容器日志："
        docker logs "$TEST_CONTAINER" 2>&1 | tail -20
        cleanup_test_env "$TEST_CONTAINER" "$TEST_KAFKA" "$TEST_NETWORK"
        exit 1
    fi

    # 清理测试环境
    cleanup_test_env "$TEST_CONTAINER" "$TEST_KAFKA" "$TEST_NETWORK"

    log_success "镜像测试完成"
}

# 清理测试环境
cleanup_test_env() {
    local container=$1
    local kafka=$2
    local network=$3

    log_info "清理测试环境..."
    docker stop "$container" 2>/dev/null || true
    docker rm "$container" 2>/dev/null || true
    docker stop "$kafka" 2>/dev/null || true
    docker rm "$kafka" 2>/dev/null || true
    docker network rm "$network" 2>/dev/null || true
}

# ==================== 推送镜像 ====================

push_image() {
    local image_name=$1
    local latest_image=$2

    print_separator
    log_info "推送镜像到仓库..."

    # 推送版本标签
    log_info "推送版本标签: $image_name"
    docker push "$image_name"

    if [ $? -ne 0 ]; then
        log_error "推送版本镜像失败"
        exit 1
    fi
    log_success "版本镜像推送成功"

    # 推送latest标签
    log_info "推送latest标签: $latest_image"
    docker push "$latest_image"

    if [ $? -ne 0 ]; then
        log_error "推送latest镜像失败"
        exit 1
    fi
    log_success "Latest镜像推送成功"
}

# ==================== 清理 ====================

cleanup() {
    local image_name=$1
    local latest_image=$2

    print_separator
    log_info "清理本地镜像..."

    # 可选：删除本地镜像以节省空间
    read -p "是否删除本地镜像？(y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker rmi "$image_name" 2>/dev/null || true
        docker rmi "$latest_image" 2>/dev/null || true
        log_success "本地镜像已删除"
    else
        log_info "保留本地镜像"
    fi
}

# ==================== 显示摘要 ====================

show_summary() {
    local version=$1
    local image_name=$2
    local latest_image=$3

    print_separator
    echo ""
    echo "🎉 镜像构建和推送完成！"
    echo ""
    echo "版本信息:"
    echo "  版本号:     $version"
    echo "  项目名:     $PROJECT_NAME"
    echo "  仓库类型:   $REGISTRY_TYPE"
    echo ""
    echo "镜像地址:"
    echo "  版本标签:   $image_name"
    echo "  Latest:     $latest_image"
    echo ""
    echo "下一步操作:"
    echo "  1. 发布到版本服务器"
    echo "  2. 通知用户更新"
    echo ""
    print_separator
}

# ==================== 主流程 ====================

main() {
    print_separator
    echo "🚀 Docker 镜像构建和推送工具"
    print_separator

    # 1. 获取版本号
    VERSION=$(get_version "$1")
    log_info "版本号: $VERSION"

    # 2. 确认配置
    echo ""
    log_info "当前配置:"
    echo "  仓库类型: $REGISTRY_TYPE"
    if [ "$REGISTRY_TYPE" = "aliyun" ]; then
        echo "  阿里云地址: $ALIYUN_REGISTRY"
        echo "  命名空间: $ALIYUN_NAMESPACE"
        echo "  仓库名: $ALIYUN_REPO"
    fi
    echo ""

    read -p "是否继续？(y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "已取消"
        exit 0
    fi

    # 3. 更新 version.txt
    print_separator
    log_info "更新 version.txt 文件..."
    echo "$VERSION" > version.txt
    log_success "version.txt 已更新为: $VERSION"

    # 3.1. 更新 install.sh 中的版本号
    log_info "更新 install.sh 中的版本号..."
    if [ -f "install.sh" ]; then
        # 使用 sed 替换 IMAGE_TAG 的值
        sed -i "s/^IMAGE_TAG=\".*\"/IMAGE_TAG=\"$VERSION\"/" install.sh
        log_success "install.sh 已更新为: $VERSION"
    else
        log_warning "install.sh 文件不存在，跳过更新"
    fi

    # 4. 运行构建前安全检查
    print_separator
    log_info "运行构建前安全检查..."
    if [ -f "./pre-build-check.sh" ]; then
        # 运行安全检查并显示结果
        bash ./pre-build-check.sh 2>&1 || true

        # 提示：.dockerignore 会排除敏感文件
        echo ""
        log_info ".dockerignore 已配置，敏感文件不会被打包进镜像"
        log_success "安全检查完成，继续构建..."
    else
        log_warning "pre-build-check.sh 不存在，跳过安全检查"
    fi

    # 5. 生成镜像名称
    IMAGE_NAME=$(get_image_name "$VERSION")
    LATEST_IMAGE=$(get_latest_image_name)

    # 6. 登录镜像仓库
    docker_login

    # 7. 构建镜像
    build_image "$VERSION" "$IMAGE_NAME" "$LATEST_IMAGE"

    # 8. 测试镜像（可选）
    read -p "是否测试镜像？(Y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        test_image "$IMAGE_NAME"
    else
        log_warning "跳过镜像测试"
    fi

    # 9. 推送镜像
    push_image "$IMAGE_NAME" "$LATEST_IMAGE"

    # 10. 清理（可选）
    cleanup "$IMAGE_NAME" "$LATEST_IMAGE"

    # 11. 显示摘要
    show_summary "$VERSION" "$IMAGE_NAME" "$LATEST_IMAGE"
}

# ==================== 执行 ====================

main "$@"
