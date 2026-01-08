# HyperBot 更新监控系统使用说明

## 系统架构

```
用户点击"更新"
    ↓
容器内API接收请求
    ↓
写入触发文件 (/app/data/.update_trigger)
    ↓
宿主机监控脚本检测到触发文件
    ↓
执行更新脚本 (update.sh)
    ↓
拉取新镜像 → 重启容器 → 完成更新
```

## 多重保障机制

### 1. Systemd 服务（主要守护进程）
- **服务名称**: `hyperbot-update-monitor`
- **自动重启**: 进程崩溃后自动重启（RestartSec=10秒）
- **开机自启**: 服务器重启后自动启动

**管理命令**:
```bash
# 查看状态
systemctl status hyperbot-update-monitor

# 启动服务
systemctl start hyperbot-update-monitor

# 停止服务
systemctl stop hyperbot-update-monitor

# 重启服务
systemctl restart hyperbot-update-monitor

# 查看日志
journalctl -u hyperbot-update-monitor -f
```

### 2. Cron 健康检查（备用保障）
- **运行频率**: 每5分钟
- **检查内容**: systemd 服务是否运行
- **自动修复**: 如果服务停止，自动重启

**查看 cron 任务**:
```bash
crontab -l
```

**健康检查日志**:
```bash
tail -f /opt/trading-system/logs/health-check.log
```

### 3. 手动健康检查
```bash
# 执行健康检查脚本
/opt/trading-system/check-monitor-health.sh

# 查看完整状态
/opt/trading-system/monitor-status.sh
```

## 文件位置

### 脚本文件
- `/opt/trading-system/update.sh` - 更新执行脚本
- `/opt/trading-system/trigger-update-monitor.sh` - 监控主脚本
- `/opt/trading-system/check-monitor-health.sh` - 健康检查脚本
- `/opt/trading-system/monitor-status.sh` - 状态查看脚本

### 日志文件
- `/opt/trading-system/logs/monitor.log` - 监控日志
- `/opt/trading-system/logs/update.log` - 更新日志
- `/opt/trading-system/logs/health-check.log` - 健康检查日志

### 配置文件
- `/etc/systemd/system/hyperbot-update-monitor.service` - Systemd 服务配置

## 常见问题处理

### Q1: 监控脚本挂了怎么办？

**答**: 系统有两层自动恢复机制：

1. **Systemd 自动重启**（秒级恢复）
   - 进程崩溃后10秒内自动重启
   - 服务器重启后自动启动

2. **Cron 定期检查**（5分钟恢复）
   - 每5分钟检查一次服务状态
   - 如果停止，自动重启

**手动恢复**:
```bash
systemctl restart hyperbot-update-monitor
```

### Q2: 如何确认监控服务正常运行？

**方法1**: 使用状态检查脚本
```bash
/opt/trading-system/monitor-status.sh
```

**方法2**: 检查 systemd 服务
```bash
systemctl is-active hyperbot-update-monitor
# 输出 "active" 表示正常
```

**方法3**: 检查进程
```bash
ps aux | grep trigger-update-monitor
```

### Q3: 更新失败了怎么办？

**步骤1**: 查看更新日志
```bash
tail -50 /opt/trading-system/logs/update.log
```

**步骤2**: 查看容器日志
```bash
docker logs trading-system-app --tail 100
```

**步骤3**: 手动触发更新测试
```bash
echo "crpi-avgutp4svf3qvj1p.ap-northeast-1.personal.cr.aliyuncs.com/hyper-smart/hyper-smart:2.2.7" > /opt/trading-system/data/.update_trigger
```

**步骤4**: 手动执行更新脚本
```bash
/opt/trading-system/update.sh
```

### Q4: 如何查看监控日志？

```bash
# 实时查看监控日志
tail -f /opt/trading-system/logs/monitor.log

# 查看更新日志
tail -f /opt/trading-system/logs/update.log

# 查看健康检查日志
tail -f /opt/trading-system/logs/health-check.log
```

### Q5: 如何临时停止监控（不影响自动恢复）？

**不推荐停止监控**，但如果需要临时停止：

```bash
# 停止服务（cron会在5分钟内重启）
systemctl stop hyperbot-update-monitor

# 如果要永久停止（不推荐）
systemctl stop hyperbot-update-monitor
systemctl disable hyperbot-update-monitor
crontab -e  # 手动删除健康检查任务
```

### Q6: 服务器重启后监控会自动启动吗？

**答**: 会！systemd 服务配置了开机自启（enabled）。

验证方法：
```bash
systemctl is-enabled hyperbot-update-monitor
# 输出 "enabled" 表示开机自启已启用
```

## 测试故障恢复

### 测试1: 模拟进程崩溃
```bash
# 杀死监控进程
pkill -9 -f trigger-update-monitor

# 等待10秒，systemd会自动重启
sleep 10

# 检查服务状态
systemctl status hyperbot-update-monitor
```

### 测试2: 模拟服务停止
```bash
# 停止服务
systemctl stop hyperbot-update-monitor

# 等待5分钟，cron会检查并重启
sleep 300

# 或手动运行健康检查
/opt/trading-system/check-monitor-health.sh

# 检查服务状态
systemctl status hyperbot-update-monitor
```

### 测试3: 模拟更新流程
```bash
# 手动触发更新（使用当前版本）
CURRENT_IMAGE=$(docker inspect trading-system-app --format '{{.Config.Image}}')
echo "$CURRENT_IMAGE" > /opt/trading-system/data/.update_trigger

# 等待5-10秒，查看日志
tail -f /opt/trading-system/logs/update.log
```

## 监控指标

使用状态脚本可以看到：
- ✅ Systemd 服务运行状态和启动时间
- ✅ Cron 定时任务配置状态
- ✅ Docker 容器状态和版本号
- ✅ 最近的监控、更新、健康检查日志

```bash
/opt/trading-system/monitor-status.sh
```

## 应急联系

如果遇到无法解决的问题：
1. 保存所有日志文件
2. 运行状态检查脚本并保存输出
3. 联系技术支持

---

**最后更新**: 2026-01-05
**版本**: v1.0
