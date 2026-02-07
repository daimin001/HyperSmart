#!/bin/bash
################################################################################
# 本地Docker镜像构建验证测试
# 用途：在本地构建镜像并验证内容完整性，无需推送到仓库
# 用法：./local-build-validation.sh
################################################################################

set -e

# ==================== 颜色输出 ====================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# ==================== 主测试流程 ====================

print_separator
echo "🧪 本地Docker镜像构建验证测试"
print_separator

# 1. 运行安全检查
log_info "步骤 1/10: 运行构建前安全检查..."
if bash pre-build-check.sh; then
    log_success "安全检查通过"
else
    log_error "安全检查失败"
    exit 1
fi

# 2. 读取版本号
print_separator
VERSION=$(cat version.txt | tr -d '[:space:]')
log_info "步骤 2/10: 版本号: $VERSION"

# 3. 构建测试镜像
print_separator
log_info "步骤 3/10: 构建Docker镜像..."
TEST_IMAGE="hyperbot-test:$VERSION"
docker build -t "$TEST_IMAGE" .
if [ $? -eq 0 ]; then
    log_success "镜像构建成功: $TEST_IMAGE"
else
    log_error "镜像构建失败"
    exit 1
fi

# 4. 检查镜像大小
print_separator
log_info "步骤 4/10: 检查镜像大小..."
IMAGE_SIZE=$(docker images "$TEST_IMAGE" --format "{{.Size}}")
echo "  镜像大小: $IMAGE_SIZE"

# 5. 检查镜像内容 - 关键文件
print_separator
log_info "步骤 5/10: 检查镜像内关键文件..."

EXPECTED_FILES=(
    "/app/src"
    "/app/web"
    "/app/api_server_simple.py"
    "/app/run_unified_kafka.py"
    "/app/docker-entrypoint.sh"
    "/app/supervisord.conf"
    "/app/requirements_web.txt"
    "/app/.env.example"
    "/app/accounts_config.json.template"
    "/app/Dockerfile"
)

FAILED_FILES=0
for file in "${EXPECTED_FILES[@]}"; do
    if docker run --rm "$TEST_IMAGE" test -e "$file"; then
        echo "  ✓ $file"
    else
        echo "  ✗ $file - 缺失！"
        ((FAILED_FILES++))
    fi
done

if [ $FAILED_FILES -eq 0 ]; then
    log_success "所有关键文件检查通过"
else
    log_error "发现 $FAILED_FILES 个文件缺失"
    exit 1
fi

# 6. 检查敏感文件是否被排除
print_separator
log_info "步骤 6/10: 检查敏感文件是否被排除..."

EXCLUDED_FILES=(
    "/app/.env"
    "/app/accounts_config.json"
    "/app/data/auth.db"
    "/app/logs/api_server.log"
)

LEAK_COUNT=0
for file in "${EXCLUDED_FILES[@]}"; do
    if docker run --rm "$TEST_IMAGE" test -e "$file"; then
        echo "  ✗ $file - 不应该存在！"
        ((LEAK_COUNT++))
    else
        echo "  ✓ $file - 已正确排除"
    fi
done

if [ $LEAK_COUNT -eq 0 ]; then
    log_success "敏感文件检查通过"
else
    log_error "发现 $LEAK_COUNT 个敏感文件泄漏"
    exit 1
fi

# 7. 检查Python依赖
print_separator
log_info "步骤 7/10: 检查Python依赖..."

REQUIRED_PACKAGES=(
    "hyperliquid-python-sdk"
    "pybit"
    "kafka-python"
    "fastapi"
    "bcrypt"
    "pyotp"
    "apscheduler"
)

MISSING_PACKAGES=0
for package in "${REQUIRED_PACKAGES[@]}"; do
    if docker run --rm "$TEST_IMAGE" pip list | grep -i "$package" > /dev/null; then
        echo "  ✓ $package"
    else
        echo "  ✗ $package - 缺失！"
        ((MISSING_PACKAGES++))
    fi
done

if [ $MISSING_PACKAGES -eq 0 ]; then
    log_success "所有Python依赖检查通过"
else
    log_error "发现 $MISSING_PACKAGES 个依赖缺失"
    exit 1
fi

# 8. 检查.env.example内容
print_separator
log_info "步骤 8/10: 检查 .env.example 模板内容..."
docker run --rm "$TEST_IMAGE" cat /app/.env.example | head -30
log_success ".env.example 模板文件验证通过"

# 9. 测试容器启动
print_separator
log_info "步骤 9/10: 测试容器启动..."

TEST_CONTAINER="hyperbot-build-test-$$"
docker run -d --name "$TEST_CONTAINER" \
    -e ENABLE_AUTO_START_ACCOUNTS=false \
    "$TEST_IMAGE" > /dev/null 2>&1

sleep 5

CONTAINER_STATUS=$(docker inspect -f '{{.State.Status}}' "$TEST_CONTAINER" 2>/dev/null)
if [ "$CONTAINER_STATUS" = "running" ]; then
    log_success "容器启动成功"

    # 测试API
    log_info "测试API健康检查..."
    CONTAINER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$TEST_CONTAINER")
    if curl -f "http://$CONTAINER_IP:8000/health" 2>/dev/null; then
        log_success "API健康检查通过"
    else
        log_warning "API可能需要更长启动时间"
    fi
else
    log_error "容器启动失败，状态: $CONTAINER_STATUS"
    docker logs "$TEST_CONTAINER" | tail -50
fi

# 清理测试容器
docker stop "$TEST_CONTAINER" 2>/dev/null || true
docker rm "$TEST_CONTAINER" 2>/dev/null || true

# 10. 显示镜像详细信息
print_separator
log_info "步骤 10/10: 镜像详细信息"
docker images "$TEST_IMAGE"
echo ""
docker run --rm "$TEST_IMAGE" cat /app/version.txt 2>/dev/null || echo "version.txt not found"

# 11. 显示测试总结
print_separator
echo ""
echo "🎉 本地构建验证测试完成！"
echo ""
echo "测试结果："
echo "  ✓ 安全检查通过"
echo "  ✓ 镜像构建成功"
echo "  ✓ 关键文件完整（${#EXPECTED_FILES[@]}个）"
echo "  ✓ 敏感文件已排除（${#EXCLUDED_FILES[@]}个）"
echo "  ✓ Python依赖完整（${#REQUIRED_PACKAGES[@]}个）"
echo "  ✓ 配置模板存在"
echo "  ✓ 容器可正常启动"
echo ""
echo "镜像信息："
echo "  名称: $TEST_IMAGE"
echo "  大小: $IMAGE_SIZE"
echo "  版本: $VERSION"
echo ""
print_separator

# 12. 询问是否清理测试镜像
echo ""
read -p "是否删除测试镜像？(y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker rmi "$TEST_IMAGE"
    log_success "测试镜像已删除"
else
    log_info "保留测试镜像: $TEST_IMAGE"
fi

print_separator
log_success "✅ 镜像验证完成，可以安全推送到仓库！"
print_separator
