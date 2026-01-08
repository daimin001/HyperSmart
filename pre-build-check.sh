#!/bin/bash
################################################################################
# Docker构建前安全检查脚本
# 用途：防止敏感数据被打包进Docker镜像
# 优化版本：智能检查 .dockerignore 配置
################################################################################

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

FAILED=0
WARNINGS=0

echo "========================================================================"
echo "                  Docker 构建前安全检查"
echo "========================================================================"
echo ""

# 辅助函数：检查文件/模式是否在 .dockerignore 中
is_ignored() {
    local pattern="$1"

    if [ ! -f ".dockerignore" ]; then
        return 1
    fi

    # 检查精确匹配
    if grep -q "^${pattern}$" .dockerignore 2>/dev/null; then
        return 0
    fi

    # 检查带通配符的匹配
    if grep -q "^${pattern}\*$" .dockerignore 2>/dev/null; then
        return 0
    fi

    # 检查扩展名通配符（如 *.db）
    if grep -q "^\*\.${pattern##*.}$" .dockerignore 2>/dev/null; then
        return 0
    fi

    # 对于目录检查（如 data/），也检查 data/*.db 这样的规则
    if [[ "$pattern" == */ ]]; then
        local dir_pattern="${pattern%/}"
        if grep -q "^${dir_pattern}/\*" .dockerignore 2>/dev/null; then
            return 0
        fi
    fi

    return 1
}

# 检查1: .dockerignore 文件存在
echo -n "检查 .dockerignore 文件... "
if [ -f ".dockerignore" ]; then
    echo -e "${GREEN}✓ 已配置${NC}"
else
    echo -e "${RED}✗ 缺少 .dockerignore 文件${NC}"
    FAILED=1
fi

# 检查2: accounts_config.json
echo -n "检查 accounts_config.json... "
if [ -f "accounts_config.json" ]; then
    # 检查是否包含敏感信息
    if grep -q "bybit_api_key" accounts_config.json 2>/dev/null || \
       grep -q "bybit_api_secret" accounts_config.json 2>/dev/null || \
       grep -q "feishu_webhook_url.*http" accounts_config.json 2>/dev/null; then

        # 包含敏感信息，检查是否被 .dockerignore 排除
        if is_ignored "accounts_config.json"; then
            echo -e "${GREEN}✓ 包含敏感信息但已被 .dockerignore 排除${NC}"
        else
            echo -e "${RED}✗ 包含敏感信息且未被 .dockerignore 排除！${NC}"
            FAILED=1
        fi
    else
        # 不包含敏感信息
        ACCOUNT_COUNT=$(grep -o '"accounts"' accounts_config.json | wc -l)
        if [ "$ACCOUNT_COUNT" -eq 1 ]; then
            echo -e "${GREEN}✓ 空配置（安全）${NC}"
        else
            echo -e "${YELLOW}⚠ 配置文件不为空但无敏感信息${NC}"
            ((WARNINGS++))
        fi
    fi
else
    echo -e "${GREEN}✓ 文件不存在（将在容器启动时创建）${NC}"
fi

# 检查3: .env 文件
echo -n "检查 .env 文件... "
if [ -f ".env" ]; then
    if is_ignored ".env"; then
        echo -e "${GREEN}✓ 文件存在但已被 .dockerignore 排除${NC}"
    else
        echo -e "${RED}✗ 文件存在且未被 .dockerignore 排除！${NC}"
        FAILED=1
    fi
else
    echo -e "${GREEN}✓ 文件不存在${NC}"
fi

# 检查4: .env.* 文件
echo -n "检查 .env.* 文件... "
ENV_FILES=$(find . -maxdepth 1 -name ".env.*" 2>/dev/null | wc -l)
if [ "$ENV_FILES" -gt 0 ]; then
    if is_ignored ".env"; then
        echo -e "${GREEN}✓ 发现 $ENV_FILES 个文件但已被 .dockerignore 排除${NC}"
    else
        echo -e "${RED}✗ 发现 $ENV_FILES 个文件且未被排除！${NC}"
        FAILED=1
    fi
else
    echo -e "${GREEN}✓ 无 .env.* 文件${NC}"
fi

# 检查5: version.txt 存在且有效
echo -n "检查 version.txt... "
if [ -f "version.txt" ]; then
    VERSION=$(cat version.txt | tr -d '[:space:]')
    if [ -n "$VERSION" ]; then
        echo -e "${GREEN}✓ 版本: $VERSION${NC}"
    else
        echo -e "${RED}✗ 版本号为空${NC}"
        FAILED=1
    fi
else
    echo -e "${RED}✗ 文件不存在${NC}"
    FAILED=1
fi

# 检查6: data 目录
echo -n "检查 data/ 目录... "
if [ -d "data" ]; then
    DB_COUNT=$(find data -name "*.db" 2>/dev/null | wc -l)
    if [ "$DB_COUNT" -gt 0 ]; then
        if is_ignored "data/"; then
            echo -e "${GREEN}✓ 包含 $DB_COUNT 个数据库但已被 .dockerignore 排除${NC}"
        else
            echo -e "${RED}✗ 包含 $DB_COUNT 个数据库且未被排除！${NC}"
            FAILED=1
        fi
    else
        echo -e "${GREEN}✓ 无数据库文件${NC}"
    fi
else
    echo -e "${GREEN}✓ 目录不存在${NC}"
fi

# 检查7: 日志文件
echo -n "检查日志文件... "
LOG_COUNT=$(find . -maxdepth 2 -name "*.log" -o -name "nohup.out" 2>/dev/null | wc -l)
if [ "$LOG_COUNT" -gt 0 ]; then
    if grep -q "^\*\.log$" .dockerignore 2>/dev/null && \
       grep -q "^logs/$" .dockerignore 2>/dev/null; then
        echo -e "${GREEN}✓ 发现 $LOG_COUNT 个日志但已被 .dockerignore 排除${NC}"
    else
        echo -e "${YELLOW}⚠ 发现 $LOG_COUNT 个日志文件${NC}"
        ((WARNINGS++))
    fi
else
    echo -e "${GREEN}✓ 无日志文件${NC}"
fi

# 检查8: 备份文件
echo -n "检查备份文件... "
BACKUP_COUNT=$(find . -maxdepth 2 -name "*.backup" -o -name "*.bak" -o -name "backup_*" 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt 0 ]; then
    if grep -q "^\*\.backup$" .dockerignore 2>/dev/null && \
       grep -q "^backup_\*/$" .dockerignore 2>/dev/null; then
        echo -e "${GREEN}✓ 发现 $BACKUP_COUNT 个备份但已被 .dockerignore 排除${NC}"
    else
        echo -e "${YELLOW}⚠ 发现 $BACKUP_COUNT 个备份文件${NC}"
        ((WARNINGS++))
    fi
else
    echo -e "${GREEN}✓ 无备份文件${NC}"
fi

# 检查9: Git目录
echo -n "检查 .git 目录... "
if [ -d ".git" ]; then
    if is_ignored ".git/"; then
        echo -e "${GREEN}✓ 目录存在但已被 .dockerignore 排除${NC}"
    else
        echo -e "${YELLOW}⚠ 目录存在且未被排除（会增加镜像大小）${NC}"
        ((WARNINGS++))
    fi
else
    echo -e "${GREEN}✓ 目录不存在${NC}"
fi

# 检查10: Python缓存
echo -n "检查 __pycache__ 目录... "
PYCACHE_COUNT=$(find . -type d -name "__pycache__" 2>/dev/null | wc -l)
if [ "$PYCACHE_COUNT" -gt 0 ]; then
    if grep -q "^__pycache__/$" .dockerignore 2>/dev/null; then
        echo -e "${GREEN}✓ 发现 $PYCACHE_COUNT 个缓存但已被 .dockerignore 排除${NC}"
    else
        echo -e "${YELLOW}⚠ 发现 $PYCACHE_COUNT 个 Python 缓存目录${NC}"
        ((WARNINGS++))
    fi
else
    echo -e "${GREEN}✓ 无 Python 缓存${NC}"
fi

echo ""
echo "========================================================================"

# 最终结果
if [ "$FAILED" -eq 0 ]; then
    if [ "$WARNINGS" -eq 0 ]; then
        echo -e "${GREEN}✅ 所有安全检查通过，可以安全构建 Docker 镜像${NC}"
    else
        echo -e "${GREEN}✅ 安全检查通过（有 $WARNINGS 个警告）${NC}"
        echo -e "${YELLOW}ℹ️  警告项已被 .dockerignore 排除，不影响安全性${NC}"
    fi
    echo "========================================================================"
    exit 0
else
    echo -e "${RED}❌ 安全检查失败！发现 $FAILED 个严重问题${NC}"
    echo "========================================================================"
    echo ""
    echo -e "${RED}严重问题：${NC}"
    echo "  以下文件包含敏感信息但未被 .dockerignore 排除："
    echo ""
    echo -e "${BLUE}建议操作：${NC}"
    echo "  1. 确保 .dockerignore 包含以下规则："
    echo "     accounts_config.json*"
    echo "     .env"
    echo "     .env.*"
    echo "     data/*.db"
    echo "     *.db"
    echo "     logs/"
    echo "     *.log"
    echo ""
    echo "  2. 或者清理敏感文件："
    echo "     echo '{\"accounts\": []}' > accounts_config.json"
    echo "     rm -f .env .env.*"
    echo "     rm -rf data/*.db"
    echo ""
    exit 1
fi
