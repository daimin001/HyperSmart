# Docker 部署配置

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置时区（重要：确保定时任务按正确时区执行）
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装系统依赖（包括编译bcrypt所需的依赖、SQLite和Supervisor）
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    git \
    curl \
    libffi-dev \
    libssl-dev \
    python3-dev \
    sqlite3 \
    libsqlite3-dev \
    supervisor \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements_web.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements_web.txt

# 复制应用代码
COPY . .

# 确保宿主机监控脚本、入口脚本和健康检查脚本可执行
RUN chmod +x scripts/host/*.sh scripts/init-host-monitor.sh docker-entrypoint.sh healthcheck.sh 2>/dev/null || true

# 创建必要的目录（包括auth.py硬编码的路径）
RUN mkdir -p /app/logs \
             /app/data \
             /home/sqlite \
             /home/hyperBot-bybit/data \
             /home/hyperBot-bybit/logs \
    && chmod 777 /app/logs \
    && chmod 777 /app/data \
    && chmod 777 /home/sqlite \
    && chmod 777 /home/hyperBot-bybit/data \
    && chmod 777 /home/hyperBot-bybit/logs

# 暴露端口
EXPOSE 8000

# 启动命令
ENTRYPOINT ["/app/docker-entrypoint.sh"]
