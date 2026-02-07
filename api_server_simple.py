#!/usr/bin/env python3
"""
简化版 API 服务器 - 优化版本
代码复用性优化，减少冗余
"""

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
from datetime import datetime, timedelta
import random
import os
import sys
import json
from decimal import Decimal
from functools import lru_cache
import time

# 添加src目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
sys.path.insert(0, os.path.dirname(__file__))

# 导入辅助函数模块
from api_helpers import (
    load_accounts_config,
    get_account_config,
    save_accounts_config,
    get_bybit_client,
    clear_bybit_client_cache,
    get_database,
    clear_database_cache,
    success_response,
    error_response,
    convert_bybit_side_to_display,
    convert_display_side_to_bybit,
    format_position_data,
    store_close_order,
    handle_api_errors
)

# 导入定时任务调度器
from scheduler import init_scheduler, get_scheduler

# 导入认证模块
from auth import get_auth_db
from auth_middleware import AuthMiddleware

# 导入授权验证模块
from license_validator import get_license_validator
from license_middleware import LicenseMiddleware

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 系统限制配置
MAX_CONCURRENT_ACCOUNTS = 5  # 最大同时运行的跟单账户数

# ==================== 持仓数据缓存 ====================
_positions_cache = {}  # {account_name: (positions, timestamp)}
POSITIONS_CACHE_TTL = 5  # 缓存5秒

# ==================== 余额数据缓存 ====================
_balance_cache = {}  # {account_name: (balance_data, timestamp)}
BALANCE_CACHE_TTL = 30  # 缓存30秒（余额变化较慢）

# ==================== Hyperliquid Info 实例缓存 ====================
# 全局共享 Info 实例，避免创建过多WebSocket连接导致超过Hyperliquid的100连接限制
_hyperliquid_info = None

def get_hyperliquid_info():
    """获取或创建共享的 Hyperliquid Info 实例"""
    global _hyperliquid_info
    if _hyperliquid_info is None:
        from hyperliquid.info import Info
        _hyperliquid_info = Info()
    return _hyperliquid_info

# ==================== 应用生命周期事件 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（启动和关闭）"""
    # ========== 启动阶段 ==========
    print("=" * 60)
    print("HyperBot Web API 启动中...")
    print("=" * 60)

    # 初始化定时任务调度器
    try:
        init_scheduler()
        print("✓ 定时任务调度器已启动")
    except Exception as e:
        print(f"✗ 定时任务调度器启动失败: {e}")
        import traceback
        traceback.print_exc()

    # 自动创建用户（Docker部署）
    try:
        auth_db = get_auth_db()
        result = auth_db.create_user_from_env()
        if result:
            user_id, totp_secret, qr_uri = result
            print("✓ 用户自动创建成功（从环境变量）")
            print(f"  - Bybit UID: {os.getenv('BYBIT_UID')}")
            print(f"  - 用户ID: {user_id}")
        else:
            print("ℹ 跳过自动创建用户")
    except Exception as e:
        print(f"✗ 自动创建用户失败: {e}")
        import traceback
        traceback.print_exc()

    # 验证系统授权
    try:
        license_validator = get_license_validator()

        # 确保加载user_id
        if not license_validator.user_id:
            license_validator.load_user_id()
            print(f"  - 加载的user_id: {license_validator.user_id}")

        is_authorized = license_validator.validate_license(force=True)

        if is_authorized:
            status = license_validator.get_status()
            print("✓ 系统授权验证通过")
            print(f"  - user_id: {license_validator.user_id}")
            print(f"  - 状态: {status['message']}")
            print(f"  - is_authorized: {license_validator.is_authorized}")
            if status.get('expires_at'):
                print(f"  - 到期时间: {status['expires_at']}")
        else:
            status = license_validator.get_status()
            print("⚠️  系统授权验证失败")
            print(f"  - user_id: {license_validator.user_id}")
            print(f"  - 原因: {status['message']}")
            print(f"  - is_authorized: {license_validator.is_authorized}")
            print("  - 系统将限制访问，请联系管理员续费")
    except Exception as e:
        print(f"✗ 授权验证失败: {e}")
        import traceback
        traceback.print_exc()

    print("=" * 60)
    print("✓ HyperBot Web API 启动完成")
    print("=" * 60)

    yield  # 应用运行期间

    # ========== 关闭阶段 ==========
    print("=" * 60)
    print("HyperBot Web API 关闭中...")
    print("=" * 60)

    # 停止定时任务调度器
    try:
        scheduler = get_scheduler()
        if scheduler:
            scheduler.stop()
            print("✓ 定时任务调度器已停止")
    except Exception as e:
        print(f"✗ 停止定时任务调度器失败: {e}")

    print("=" * 60)
    print("✓ HyperBot Web API 已关闭")
    print("=" * 60)

app = FastAPI(title="HyperBot Web UI", lifespan=lifespan)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境建议改为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 添加认证中间件
app.add_middleware(AuthMiddleware)

# 添加授权验证中间件（在用户认证之后）
app.add_middleware(LicenseMiddleware)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/")
async def read_root():
    """返回前端页面"""
    return FileResponse("web/index.html")

@app.get("/login.html")
async def login_page():
    """返回登录页面"""
    return FileResponse("web/login.html")

@app.get("/register.html")
async def register_page():
    """返回注册页面"""
    return FileResponse("web/register.html")

@app.get("/health")
async def health_check():
    """健康检查接口（用于Docker健康检查和监控）"""
    return {"status": "healthy", "service": "hyperbot-api"}

@app.get("/api/version/check")
async def check_version():
    """检查版本更新"""
    import requests
    import os

    try:
        # 读取当前版本（Docker部署）
        current_version = "unknown"
        version_paths = [
            "/app/version.txt",  # Docker部署路径
            os.path.join(BASE_DIR, "version.txt")  # 本地开发路径
        ]
        for path in version_paths:
            if os.path.exists(path):
                with open(path, "r") as f:
                    current_version = f.read().strip()
                break

        # 从版本服务器获取最新版本信息
        response = requests.get("http://43.156.4.146:3000/api/version/check", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                latest_version = data.get("latest_version")

                # 直接比对版本号，不再比对镜像标签
                update_available = (current_version != latest_version) and (current_version != "unknown")

                return {
                    "success": True,
                    "current_version": current_version,
                    "current_version_display": current_version,  # 直接显示version.txt中的版本号
                    "latest_version": latest_version,
                    "update_available": update_available,
                    "release_notes": data.get("release_notes", ""),
                    "release_date": data.get("release_date", ""),
                    "breaking_changes": data.get("breaking_changes", False),
                    "image": data.get("image", "")
                }

        return {
            "success": False,
            "message": "无法连接到版本服务器",
            "current_version": current_version
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"版本检查失败: {str(e)}",
            "current_version": current_version
        }

@app.get("/api/version/current")
async def get_current_version():
    """获取当前系统版本号"""
    try:
        # 读取version.txt文件获取当前版本
        version_file = os.path.join(BASE_DIR, "version.txt")
        if os.path.exists(version_file):
            with open(version_file, 'r') as f:
                current_version = f.read().strip()
        else:
            current_version = "unknown"

        return {
            "success": True,
            "version": current_version,
            "current_version": current_version
        }
    except Exception as e:
        return {
            "success": False,
            "version": "unknown",
            "error": str(e)
        }

@app.post("/api/system/update")
async def update_system(request: Request):
    """执行系统更新"""
    import subprocess
    import threading
    import requests
    import tempfile

    # 获取请求体中的目标镜像或版本
    try:
        body = await request.json()
        target_version = body.get("version", "")
        target_image = body.get("image", "")
    except Exception:
        return {
            "success": False,
            "message": "请求参数错误"
        }

    def perform_update_docker(image_name):
        """Docker部署的更新流程 - 通过触发文件通知宿主机"""
        try:
            import time
            time.sleep(1)

            # 写入触发文件（容器内路径，宿主机可见）
            trigger_file = "/app/data/.update_trigger"
            with open(trigger_file, 'w') as f:
                f.write(image_name)

            print(f"✅ 更新触发已创建: {trigger_file}")
            print(f"   目标镜像: {image_name}")
            print(f"   宿主机监控脚本将自动执行更新")

        except Exception as e:
            print(f"❌ Docker更新触发失败: {e}")

    # Docker部署环境（检查/.dockerenv文件）
    is_docker = os.path.exists("/.dockerenv")

    if is_docker and target_image:
        # Docker部署
        update_thread = threading.Thread(target=perform_update_docker, args=(target_image,), daemon=True)
        update_thread.start()
        return {
            "success": True,
            "message": f"更新已开始，正在升级到最新版本...",
            "deployment": "docker"
        }
    else:
        return {
            "success": False,
            "message": "系统仅支持Docker部署模式的更新"
        }

@app.get("/api/system/status")
async def get_system_status():
    """获取系统状态（模拟数据）"""
    return {
        "running": True,
        "mode": "实盘运行中",
        "accounts_count": 3,
        "total_positions": 8,
        "avg_latency_ms": 1.8
    }

@app.get("/api/accounts")
@handle_api_errors
async def get_accounts():
    """获取账户列表（从配置文件读取）"""
    config = load_accounts_config()

    accounts = []
    for acc in config.get('accounts', []):
        account_name = acc.get('account_name', '')

        # Docker环境下，根据配置文件的enabled字段判断运行状态
        running = acc.get('enabled', True)

        accounts.append({
            "account_name": account_name,
            "hyperliquid_address": acc.get('hyperliquid_address', ''),
            "bybit_mode": acc.get('bybit_mode', 'DEMO'),
            "running": running,
            "tracked_positions": 0,
            "symbol_whitelist": acc.get('symbol_whitelist', []),
            "enable_whitelist": acc.get('enable_symbol_whitelist', False),
            "avg_latency_ms": 0
        })

    return accounts

@app.get("/api/accounts/stats")
async def get_account_stats():
    """获取账户统计（模拟数据）"""
    return {
        "success": True,
        "statistics": {
            "total_pnl": 3256.80,
            "total_pnl_pct": 32.57,
            "total_trades": 342,
            "win_rate": 73.5,
            "profit_loss_ratio": 2.8,
            "max_drawdown": -8.3,
            "account_performance": [
                {"name": "主账户-跟单鲸鱼A", "profit": 2150.50},
                {"name": "备用账户-跟单鲸鱼B", "profit": 1106.30},
                {"name": "测试账户-DEMO", "profit": 0}
            ],
            "symbol_performance": [
                {"symbol": "BTC", "pnl": 1850.00},
                {"symbol": "ETH", "pnl": 756.80},
                {"symbol": "SOL", "pnl": 420.00},
                {"symbol": "HYPE", "pnl": 180.00},
                {"symbol": "BNB", "pnl": 50.00}
            ]
        }
    }

@app.get("/api/accounts/{account_name}/balance")
@handle_api_errors
async def get_account_balance(account_name: str):
    """获取账户总资产（真实数据，带缓存）- 返回totalWalletBalance"""
    global _balance_cache

    # 检查缓存
    cache_key = f"{account_name}_bybit"
    current_time = time.time()
    if cache_key in _balance_cache:
        cached_data, cached_time = _balance_cache[cache_key]
        if current_time - cached_time < BALANCE_CACHE_TTL:
            # 缓存未过期，直接返回
            return cached_data

    # 缓存过期或不存在，查询 Bybit API
    bybit_client = get_bybit_client(account_name)
    if not bybit_client:
        return error_response(f"无法创建账户 {account_name} 的Bybit客户端")

    # 获取账户总资产（totalWalletBalance）
    balance = bybit_client.get_account_equity()

    result = {
        "success": True,
        "balance": float(balance),
        "formatted_balance": f"{balance:,.2f} USDT"
    }

    # 更新缓存
    _balance_cache[cache_key] = (result, current_time)

    return result

@app.get("/api/accounts/{account_name}/hyperliquid_balance")
@handle_api_errors
async def get_hyperliquid_balance(account_name: str):
    """获取 Hyperliquid 账户余额（带缓存）"""
    global _balance_cache

    # 检查缓存
    cache_key = f"{account_name}_hyperliquid"
    current_time = time.time()
    if cache_key in _balance_cache:
        cached_data, cached_time = _balance_cache[cache_key]
        if current_time - cached_time < BALANCE_CACHE_TTL:
            # 缓存未过期，直接返回
            return cached_data

    # 缓存过期或不存在，查询 Hyperliquid API
    # 获取账户配置
    account_config = get_account_config(account_name)
    if not account_config:
        return error_response(f"账户 {account_name} 不存在")

    hyperliquid_address = account_config.get('hyperliquid_address')
    if not hyperliquid_address:
        return error_response(f"账户 {account_name} 未配置 Hyperliquid 地址")

    try:
        # 使用共享的 Info 实例，避免创建过多WebSocket连接
        info = get_hyperliquid_info()

        # 获取用户状态
        user_state = info.user_state(hyperliquid_address)

        if not user_state:
            return error_response("无法获取 Hyperliquid 账户状态")

        # 提取余额信息
        margin_summary = user_state.get('marginSummary', {})
        account_value = float(margin_summary.get('accountValue', 0))

        result = {
            "success": True,
            "balance": account_value,
            "formatted_balance": f"{account_value:,.2f} USDT"
        }

        # 更新缓存
        _balance_cache[cache_key] = (result, current_time)

        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        return error_response(f"获取 Hyperliquid 余额失败: {str(e)}")

@app.get("/api/accounts/{account_name}/positions")
@handle_api_errors
async def get_account_positions(account_name: str):
    """获取账户持仓（真实数据，带缓存）"""
    global _positions_cache

    # 检查缓存
    current_time = time.time()
    if account_name in _positions_cache:
        cached_positions, cached_time = _positions_cache[account_name]
        if current_time - cached_time < POSITIONS_CACHE_TTL:
            # 缓存未过期，直接返回
            return cached_positions

    # 缓存过期或不存在，查询 Bybit API
    bybit_client = get_bybit_client(account_name)
    if not bybit_client:
        return error_response(f"无法创建账户 {account_name} 的Bybit客户端")

    # 查询持仓（不使用缓存，获取最新数据）
    positions = bybit_client.query_positions(use_cache=False)
    if positions is None:
        return []

    # 过滤出有持仓的数据并格式化
    result = []
    for pos in positions:
        size = float(pos.get('size', '0'))
        if size == 0:  # 跳过空持仓
            continue

        result.append(format_position_data(pos))

    # 更新缓存
    _positions_cache[account_name] = (result, current_time)

    return result


@app.get("/api/accounts/{account_name}/trades")
@handle_api_errors
async def get_account_trades(account_name: str, limit: int = 50):
    """获取账户交易记录（真实数据）"""
    # 获取账户配置
    account_config = get_account_config(account_name)
    if not account_config:
        return error_response(f"账户 {account_name} 不存在")

    # 获取数据库路径
    db_path = account_config.get('db_path', os.path.join(BASE_DIR, f"data/{account_name}.db"))

    if not os.path.exists(db_path):
        return []

    try:
        # 使用缓存的数据库实例
        db = get_database(account_name, account_config)
        if not db:
            return []

        # 查询bybit_orders记录
        result = db.get_recent_bybit_orders(account_name=account_name, limit=limit, offset=0)

        # 转换格式
        trades = []
        for order in result['orders']:
            # 提取币种（去掉USDT后缀）
            coin = order['symbol'].replace('USDT', '')

            trades.append({
                "id": order['id'],
                "timestamp": order['timestamp'],
                "coin": coin,
                "side": order['side'],
                "trade_type": order.get('trade_type', '开仓'),
                "size": order['size'],
                "price": order['price'],
                "pnl": order.get('pnl'),
                "account_id": account_name
            })

        db.close()
        return trades

    except Exception as e:
        print(f"查询账户 {account_name} 的交易记录失败: {e}")
        import traceback
        traceback.print_exc()
        return []

@app.get("/api/trades")
async def get_all_trades(
    account: str = Query(None, description="过滤特定账户，留空查询所有"),
    limit: int = Query(50, description="每页记录数"),
    offset: int = Query(0, description="偏移量")
):
    """
    获取所有账户的Bybit交易订单记录（真实数据）
    """
    try:
        # 读取账户配置
        config_path = os.path.join(BASE_DIR, "accounts_config.json")
        if not os.path.exists(config_path):
            return {
                "success": False,
                "error": "配置文件不存在",
                "trades": [],
                "total": 0
            }

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        accounts = config.get('accounts', [])
        all_trades = []
        total_count = 0

        # 如果指定了账户，只查询该账户
        if account:
            accounts = [acc for acc in accounts if acc['account_name'] == account]

        # 遍历所有账户，查询数据库
        for acc in accounts:
            try:
                account_name = acc['account_name']
                # 获取数据库路径 (默认为data目录)
                db_path = acc.get('db_path', os.path.join(BASE_DIR, f"data/{account_name}.db"))

                if not os.path.exists(db_path):
                    continue

                # 使用缓存的数据库实例
                db = get_database(account_name, acc)
                if not db:
                    continue

                # ✅ 修复：查询所有记录用于合并排序，但记录总数用于分页计算
                # 由于可能有多个账户，需要先合并所有记录再排序分页
                result = db.get_recent_bybit_orders(account_name=account_name, limit=10000, offset=0)

                # 转换格式
                for order in result['orders']:
                    # 提取币种（去掉USDT后缀）
                    coin = order['symbol'].replace('USDT', '')

                    all_trades.append({
                        "id": order['id'],
                        "timestamp": order['timestamp'],
                        "account_id": account_name,
                        "coin": coin,
                        "side": order['side'],
                        "trade_type": order.get('trade_type', '开仓'),
                        "size": order['size'],
                        "price": order['price'],
                        "pnl": order.get('pnl'),  # 添加盈亏字段
                        "order_source": order.get('order_source', 'system')
                    })

                # ✅ 累加每个账户的总记录数
                total_count += result['total']

            except Exception as e:
                print(f"查询账户 {acc.get('account_name')} 的交易记录失败: {e}")
                continue

        # 按时间倒序排序
        all_trades.sort(key=lambda x: x['timestamp'], reverse=True)

        # 在合并后的结果上应用分页
        paginated_trades = all_trades[offset:offset+limit]

        # ✅ 使用累加的总数，而不是 len(all_trades)
        # total_count 是所有账户的订单总数
        actual_total = total_count

        return {
            "success": True,
            "trades": paginated_trades,
            "total": actual_total,
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "trades": [],
            "total": 0
        }

@app.get("/api/trades/{trade_id}/validation")
async def get_trade_validation(trade_id: int, account: str = Query(..., description="账户名称")):
    """
    获取订单的验证信息（延迟3秒后返回验证结果）

    此接口会:
    1. 延迟3秒（模拟飞书通知的延迟机制）
    2. 从数据库查询订单信息
    3. 返回验证结果（如果有的话）
    """
    import time
    import asyncio

    try:
        # ✅ 延迟3秒，模拟飞书通知的延迟机制
        await asyncio.sleep(3)

        # 读取账户配置
        config_path = os.path.join(BASE_DIR, "accounts_config.json")
        if not os.path.exists(config_path):
            return {
                "success": False,
                "error": "配置文件不存在"
            }

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 查找指定账户
        account_config = None
        for acc in config.get('accounts', []):
            if acc['account_name'] == account:
                account_config = acc
                break

        if not account_config:
            return {
                "success": False,
                "error": f"账户 {account} 不存在"
            }

        # 获取数据库路径
        db_path = account_config.get('db_path', os.path.join(BASE_DIR, f"data/{account}.db"))

        if not os.path.exists(db_path):
            return {
                "success": False,
                "error": "数据库文件不存在"
            }

        # 使用缓存的数据库实例
        db = get_database(account, account_config)
        if not db:
            return {
                "success": False,
                "error": "无法连接数据库"
            }

        # 查询订单信息
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 查询订单详情(包括验证警告信息)
        cursor.execute("""
            SELECT
                id, timestamp, account_id, symbol, side, size, price,
                order_id, trade_type, order_source, pnl,
                verification_status, verification_warnings, verified_qty, verified_price, verified_at
            FROM bybit_orders
            WHERE id = ?
        """, (trade_id,))

        order = cursor.fetchone()
        conn.close()

        if not order:
            return {
                "success": False,
                "error": "订单不存在"
            }

        # 解析订单数据
        order_data = {
            "id": order[0],
            "timestamp": order[1],
            "account_id": order[2],
            "symbol": order[3],
            "side": order[4],
            "size": order[5],
            "price": order[6],
            "order_id": order[7],
            "trade_type": order[8] if order[8] else "开仓",
            "order_source": order[9] if order[9] else "system",
            "pnl": order[10] if order[10] else None
        }

        # ✅ 从数据库读取验证结果
        verification_status = order[11]  # verification_status
        verified_qty = order[13]  # verified_qty
        verified_price = order[14]  # verified_price
        verified_at = order[15]  # verified_at

        # 构建验证结果 (不返回警告信息)
        validation_result = {
            "success": True,
            "validated": verification_status == 'success' if verification_status else False,
            "verification_status": verification_status or 'pending',
            "verified_qty": verified_qty,
            "verified_price": verified_price,
            "verified_at": verified_at,
            "order_data": order_data,
            "message": "订单数据验证完成" if verification_status == 'success' else "等待验证"
        }

        return validation_result

    except Exception as e:
        print(f"获取订单验证信息失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/api/accounts/{account_name}/stats")
async def get_account_specific_stats(account_name: str):
    """获取指定账户统计（模拟数据）"""
    # 返回模拟统计数据，适用于任何账户
    return {
        "total_trades": 156,
        "total_pnl": 2150.50,
        "win_rate": 75.6,
        "win_trades": 118,
        "loss_trades": 38
    }

@app.post("/api/system/control")
async def control_system(request: dict):
    """系统控制（模拟）"""
    action = request.get("action")
    if action == "start":
        return {
            "status": "success",
            "message": "系统启动中...（这是演示模式，实际系统未启动）"
        }
    elif action == "stop":
        return {
            "status": "success",
            "message": "系统已停止（这是演示模式）"
        }
    else:
        return {
            "status": "error",
            "message": "无效的操作"
        }

@app.post("/api/system/restart")
async def restart_system():
    """重启系统服务 - Docker模式"""
    # Docker环境下，重启需要通过docker compose命令
    return {
        "success": False,
        "message": "Docker环境下请使用命令: docker compose restart hyperbot-web"
    }

@app.get("/api/config/accounts")
async def get_accounts_config():
    """获取账户配置文件内容"""
    try:
        config_path = os.path.join(BASE_DIR, "accounts_config.json")
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {
            "success": True,
            "content": content,
            "path": config_path
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/api/config/accounts")
@handle_api_errors
async def save_accounts_config_api(request: dict):
    """保存账户配置文件"""
    content = request.get("content", "")

    # 验证JSON格式
    try:
        config = json.loads(content)
    except json.JSONDecodeError as e:
        return error_response(f"JSON格式错误: {str(e)}")

    # 保存配置
    success, message = save_accounts_config(config)

    if success:
        # 保存成功后，触发配置文件的 mtime 更新，让配置监听线程自动检测并智能更新
        # 这样只会更新变化的账户，不会影响其他正在运行的账户
        try:
            import subprocess
            import os

            # 使用 touch 更新文件的 mtime，触发配置监听器
            config_file = "/app/accounts_config.json"
            result = subprocess.run(
                ["touch", config_file],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                logger.info("✅ 配置已保存，已触发智能配置更新（只更新变化的账户，不影响其他账户）")
                return success_response(f"{message}\n配置监听器将在3秒内自动检测变化并智能更新")
            else:
                logger.warning(f"⚠️ 配置已保存，但触发更新失败: {result.stderr}")
                return success_response(f"{message}\n但自动更新触发失败，请手动重启服务")
        except Exception as e:
            logger.error(f"❌ 触发配置更新时出错: {e}", exc_info=True)
            return success_response(f"{message}\n但自动更新触发失败: {str(e)}")
    else:
        return error_response(message)

@app.get("/api/config/account/{account_name}")
@handle_api_errors
async def get_account_config_api(account_name: str):
    """获取单个账户配置"""
    account = get_account_config(account_name)

    if not account:
        return error_response("账户不存在")

    return success_response(data=account, config=account)

@app.post("/api/config/account/{account_name}")
async def save_account_config(account_name: str, request: dict):
    """保存单个账户配置"""
    try:
        import json
        import shutil
        import os

        config_path = os.path.join(BASE_DIR, "accounts_config.json")
        backup_path = os.path.join(BASE_DIR, "accounts_config.json.backup")

        # 读取现有配置
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 查找并更新账户
        account_found = False
        for i, acc in enumerate(config.get('accounts', [])):
            if acc['account_name'] == account_name:
                # 保留原有的不可修改字段
                original_account_name = acc.get('account_name')
                original_uid = acc.get('bybit_uid')
                original_db_path = acc.get('db_path')
                original_enabled = acc.get('enabled', True)  # 保留原有的启动/停止状态

                # 更新账户配置
                new_config = request.get('config', {})

                # 检测是否尝试修改账户名称
                attempted_name = new_config.get('account_name')
                if attempted_name and original_account_name and attempted_name != original_account_name:
                    import logging
                    logging.warning(f"用户尝试修改账户名称：从 {original_account_name} 改为 {attempted_name}，已自动阻止")

                config['accounts'][i] = new_config

                # 恢复不可修改的字段（保护 account_name, bybit_uid, db_path 和 enabled）
                config['accounts'][i]['account_name'] = original_account_name
                if original_uid:
                    config['accounts'][i]['bybit_uid'] = original_uid
                if original_db_path:
                    config['accounts'][i]['db_path'] = original_db_path

                # 如果前端没有发送enabled字段，则保留原有的状态
                if 'enabled' not in new_config:
                    config['accounts'][i]['enabled'] = original_enabled

                account_found = True
                break

        if not account_found:
            return {
                "success": False,
                "error": "账户不存在"
            }

        # 备份原配置
        if os.path.exists(config_path):
            shutil.copy2(config_path, backup_path)

        # 保存新配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return {
            "success": True,
            "message": "配置已保存"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/api/accounts/add")
async def add_account(request: dict):
    """添加新账户"""
    try:
        import json
        import shutil
        import os

        config_path = os.path.join(BASE_DIR, "accounts_config.json")
        backup_path = os.path.join(BASE_DIR, "accounts_config.json.backup")

        # 读取现有配置
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 确保accounts列表存在
        if 'accounts' not in config:
            config['accounts'] = []

        # 获取新账户数据
        new_account = request.get('account', {})
        account_name = new_account.get('account_name', '')

        if not account_name:
            return {
                "success": False,
                "error": "账户名称不能为空"
            }

        # 检查账户名称是否已存在
        for acc in config['accounts']:
            if acc.get('account_name') == account_name:
                return {
                    "success": False,
                    "error": f"账户 {account_name} 已存在"
                }

        # 检查Bybit API Key是否已被使用
        new_api_key = new_account.get('bybit_api_key', '')
        new_mode = new_account.get('bybit_mode', 'LIVE')

        if new_api_key:
            for acc in config['accounts']:
                existing_api_key = acc.get('bybit_api_key', '')
                existing_mode = acc.get('bybit_mode', 'LIVE')
                existing_account_name = acc.get('account_name', '未知账户')

                # 检查是否有相同的API Key和相同的模式（DEMO或LIVE）
                if existing_api_key == new_api_key and existing_mode == new_mode:
                    mode_text = "模拟盘" if new_mode == "DEMO" else "实盘"
                    return {
                        "success": False,
                        "error": f"检测到重复的Bybit API密钥！\n\n" +
                                f"该API密钥（{mode_text}模式）已被账户 '{existing_account_name}' 使用。\n\n" +
                                f"建议操作：\n" +
                                f"1. 在Bybit交易所创建子账户，并使用子账户的API密钥\n" +
                                f"2. 或者修改现有账户 '{existing_account_name}' 的跟单设置"
                    }

        # 设置默认数据库路径（如果未提供）
        if not new_account.get('db_path'):
            new_account['db_path'] = f"/home/sqlite/{account_name}.db"

        # 自动获取Bybit UID（如果未提供）
        if not new_account.get('bybit_uid'):
            try:
                # 导入所需模块
                import sys
                from pathlib import Path
                sys.path.insert(0, str(Path(BASE_DIR)))
                from bybit_client import Bybit
                from enums import RunningMode

                # 提取API凭证
                bybit_api_key = new_account.get('bybit_api_key', '')
                bybit_secret = new_account.get('bybit_api_secret', '') or new_account.get('bybit_secret', '')
                bybit_mode = new_account.get('bybit_mode', 'LIVE')
                running_mode_str = 'demo' if bybit_mode == 'DEMO' else 'live'

                if bybit_api_key and bybit_secret:
                    # 创建临时Bybit客户端
                    running_mode = RunningMode.LIVE if running_mode_str == 'live' else RunningMode.DEMO
                    temp_client = Bybit(
                        user=f"temp_{account_name}",
                        key=bybit_api_key,
                        secret=bybit_secret,
                        mode=running_mode
                    )

                    # 获取UID
                    uid = temp_client.get_account_uid()
                    if uid:
                        new_account['bybit_uid'] = uid
                        print(f"成功获取并保存 Bybit UID: {uid}")
                    else:
                        print("警告: 无法从Bybit API获取UID")
                else:
                    print("警告: 缺少API凭证，无法获取Bybit UID")
            except Exception as e:
                print(f"获取Bybit UID时出错: {e}")
                import logging
                logging.error(f"获取Bybit UID失败: {e}", exc_info=True)

        # 添加默认配置字段
        default_config = {
            "enabled": False,  # 新账户默认为停止状态
            "follow_mode": "ratio",
            "fixed_amount": 50,
            "min_copy_value": 10,
            "force_min_amount_on_small_order": False,
            "copy_existing_positions": False,
            "time_window_minutes": 60,
            "enable_order_time_filter": True,
            "order_max_age_hours": 2,
            "enable_fills_monitoring": True,
            "enable_orders_monitoring": True,
            "enable_feishu_notification": False,
            "feishu_webhook_url": "",
            "custom_leverage": {}
        }

        # 合并默认配置和用户提供的配置
        final_account = {**default_config, **new_account}

        # 添加到配置
        config['accounts'].append(final_account)

        # 备份原配置
        if os.path.exists(config_path):
            shutil.copy2(config_path, backup_path)

        # 保存新配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return {
            "success": True,
            "message": f"账户 {account_name} 添加成功"
        }
    except Exception as e:
        import logging
        logging.error(f"添加账户失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }

@app.delete("/api/accounts/{account_name}/delete")
async def delete_account(account_name: str):
    """删除账户"""
    try:
        import json
        import shutil
        import os
        import subprocess

        config_path = os.path.join(BASE_DIR, "accounts_config.json")
        backup_path = os.path.join(BASE_DIR, "accounts_config.json.backup")

        # 读取现有配置
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # 确保accounts列表存在
        if 'accounts' not in config:
            config['accounts'] = []

        # 查找账户
        account_found = False
        account_index = -1
        for i, acc in enumerate(config['accounts']):
            if acc.get('account_name') == account_name:
                account_found = True
                account_index = i
                break

        if not account_found:
            return {
                "success": False,
                "error": f"账户 {account_name} 不存在"
            }

        # 删除账户（Docker部署模式下直接删除）
        config['accounts'].pop(account_index)

        # 备份原配置
        if os.path.exists(config_path):
            shutil.copy2(config_path, backup_path)

        # 保存新配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        return {
            "success": True,
            "message": f"账户 {account_name} 已删除"
        }
    except Exception as e:
        import logging
        logging.error(f"删除账户失败: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }

@app.post("/api/account/{account_name}/toggle")
@handle_api_errors
async def toggle_account(account_name: str, request: dict):
    """启动/停止账户跟单 - Docker模式（动态热重载）"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"🔄 收到 toggle 请求: account={account_name}, request={request}")

    # 获取前端传递的start参数
    start = request.get('start', None)
    logger.info(f"   start参数: {start}")

    # 加载配置
    config = load_accounts_config(force_reload=True)
    logger.info(f"   已加载配置，账户数量: {len(config.get('accounts', []))}")

    # 查找账户
    account_found = False
    for account in config.get('accounts', []):
        if account.get('account_name') == account_name:
            account_found = True
            logger.info(f"   找到账户: {account_name}, 当前 enabled={account.get('enabled', True)}")

            # 根据start参数设置enabled状态
            if start is not None:
                # 前端明确指定了要设置的状态
                account['enabled'] = bool(start)
            else:
                # 如果没有指定，则切换状态（兼容旧逻辑）
                current_status = account.get('enabled', True)
                account['enabled'] = not current_status

            new_status = account['enabled']
            logger.info(f"   设置新状态: enabled={new_status}")
            break

    if not account_found:
        logger.warning(f"   ❌ 账户 {account_name} 不存在")
        return error_response(f"账户 {account_name} 不存在")

    # 保存配置
    logger.info(f"   准备保存配置...")
    success, message = save_accounts_config(config)
    logger.info(f"   保存结果: success={success}, message={message}")

    if success:
        status_text = "启用" if new_status else "停用"
        return success_response(f"账户 {account_name} 已{status_text}。配置已保存，将在3秒内自动生效。")
    else:
        return error_response(f"保存配置失败: {message}")

@app.get("/api/account/{account_name}/status")
async def get_account_status(account_name: str):
    """获取账户运行状态 - Docker模式"""
    # Docker环境下，所有账户由统一Kafka系统管理
    # 验证账户是否存在于配置中
    account_config = get_account_config(account_name)

    if not account_config:
        return {
            "success": False,
            "error": f"账户 {account_name} 不存在"
        }

    # 根据enabled字段返回账户状态
    is_enabled = account_config.get('enabled', True)
    return {
        "success": True,
        "running": is_enabled,
        "status": "online" if is_enabled else "stopped",
        "deployment": "docker-unified"
    }

def store_close_order(account_name: str, symbol: str, side: str, size: float, price: float,
                      order_source: str = 'web_ui', bybit_client=None, pnl: float = None):
    """
    记录平仓订单到数据库

    Args:
        account_name: 账户名称
        symbol: 交易对符号
        side: 平仓方向（Sell平多，Buy平空）
        size: 平仓数量
        price: 成交价格
        order_source: 订单来源 ('web_ui'/'manual'/'system')
        bybit_client: Bybit客户端（用于查询平仓后是否还有持仓）
        pnl: 盈亏（可选）
    """
    try:
        # 获取账户配置，找到数据库路径
        account_config = get_account_config(account_name)
        if not account_config:
            return

        db_path = account_config.get('db_path', os.path.join(BASE_DIR, f"data/{account_name}.db"))

        if not os.path.exists(db_path):
            return

        # 使用缓存的数据库实例
        db = get_database(account_name, account_config)
        if not db:
            return

        # 确保order_source列存在
        db.add_order_source_column()

        # 判断是清仓还是减仓
        trade_type = '减仓'  # 默认为减仓
        if bybit_client:
            try:
                # 查询平仓后的持仓
                positions = bybit_client.query_positions(use_cache=False)

                # 平仓的side和持仓的side是相反的
                # 例如：平多仓时，side=Sell，但原持仓是Buy
                position_side = 'Buy' if side == 'Sell' else 'Sell'

                # 检查是否还有该币种和方向的持仓
                has_position = False
                if positions:
                    for pos in positions:
                        if (pos.get('symbol') == symbol and
                            pos.get('side') == position_side and
                            float(pos.get('size', 0)) > 0):
                            has_position = True
                            break

                # 如果没有持仓了，说明是清仓
                if not has_position:
                    trade_type = '清仓'
                    print(f"判断为清仓: {symbol} {position_side} (无剩余持仓)")
                else:
                    trade_type = '减仓'
                    print(f"判断为减仓: {symbol} {position_side} (仍有持仓)")

            except Exception as e:
                print(f"查询持仓失败，默认为减仓: {e}")

        # 准备订单数据
        order_data = {
            'timestamp': datetime.now(),
            'account_name': account_name,
            'symbol': symbol,
            'side': side,
            'order_type': 'Market',
            'trade_type': trade_type,  # 根据是否还有持仓判断清仓/减仓
            'size': size,
            'price': price,
            'bybit_order_id': f"web_close_{int(datetime.now().timestamp())}_{symbol}",
            'status': 'filled',
            'order_source': order_source,
            'pnl': pnl  # 添加盈亏字段
        }

        # 存储订单
        db.store_bybit_order(order_data)
        db.close()

        print(f"✓ 已记录平仓订单到数据库: {symbol} {side} {size} @{price}, 盈亏: {pnl}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"记录平仓订单失败: {e}")

@app.post("/api/account/{account_name}/close-position")
@handle_api_errors
async def close_position(account_name: str, request: dict):
    """平仓操作"""
    coin = request.get("coin")
    side = request.get("side")

    if not coin or not side:
        return error_response("缺少必要参数: coin 和 side")

    # 获取账户配置
    account_config = get_account_config(account_name)
    if not account_config:
        return error_response(f"账户 {account_name} 不存在")

    # 获取 Bybit 客户端
    bybit_client = get_bybit_client(account_name)
    if not bybit_client:
        return error_response("创建 Bybit 客户端失败")

    # 查询持仓
    positions = bybit_client.query_positions(use_cache=False)
    if positions is None:
        return error_response("查询持仓失败")

    # 构建完整的币种符号
    symbol = f"{coin}USDT"
    bybit_side = convert_display_side_to_bybit(side)

    # 查找对应的持仓
    target_position = None
    for pos in positions:
        pos_symbol = pos.get('symbol', '')
        pos_side = pos.get('side', '')
        pos_size = float(pos.get('size', '0'))

        if pos_symbol == symbol and pos_side == bybit_side and pos_size > 0:
            target_position = pos
            break

    if not target_position:
        return error_response(f"未找到 {coin} {side} 方向的持仓")

    # 记录平仓前的价格
    entry_price = float(target_position.get('avgPrice', '0'))

    # 执行平仓（返回4个值：success, closed_size, pnl, error_code）
    success, closed_size, pnl, error_code = bybit_client.close_position(target_position, is_half=False)

    if success and closed_size > 0:
        # 构建平仓成功消息
        message = f"成功平仓 {coin} {side}，平仓数量: {closed_size}"

        # 如果有盈亏数据，添加到消息中
        if pnl is not None:
            pnl_value = float(pnl)
            pnl_sign = '+' if pnl_value > 0 else ''
            message += f"，盈亏: {pnl_sign}{pnl_value:.2f} USDT"

        return success_response(
            message,
            closed_size=str(closed_size),
            pnl=float(pnl) if pnl is not None else None
        )
    else:
        return error_response("平仓失败")

# ==================== 日志导出 ====================
@app.get("/api/logs/export")
@handle_api_errors
async def export_logs():
    """
    导出近3天的所有日志文件
    返回一个zip压缩包
    """
    import zipfile
    import tempfile
    from pathlib import Path

    # 获取当前时间和3天前的时间
    now = datetime.now()
    three_days_ago = now - timedelta(days=3)

    # 创建临时zip文件
    temp_dir = tempfile.gettempdir()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    zip_filename = f"hyperbot_logs_{timestamp}.zip"
    zip_path = os.path.join(temp_dir, zip_filename)

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 收集所有需要打包的日志文件
            log_files = []

            # 1. 根目录的日志文件
            root_logs = [os.path.join(BASE_DIR, "trading_bot.log")]
            for log_file in root_logs:
                if os.path.exists(log_file):
                    mtime = datetime.fromtimestamp(os.path.getmtime(log_file))
                    if mtime >= three_days_ago:
                        log_files.append((log_file, os.path.basename(log_file)))

            # 2. logs目录下的所有日志文件
            logs_dir = os.path.join(BASE_DIR, "logs")
            if os.path.exists(logs_dir):
                for filename in os.listdir(logs_dir):
                    if filename.endswith('.log'):
                        file_path = os.path.join(logs_dir, filename)
                        if os.path.isfile(file_path):
                            mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                            if mtime >= three_days_ago:
                                log_files.append((file_path, f"logs/{filename}"))

            # 如果没有找到近3天的日志，添加所有日志文件
            if not log_files:
                # 添加根目录日志
                for log_file in root_logs:
                    if os.path.exists(log_file):
                        log_files.append((log_file, os.path.basename(log_file)))

                # 添加logs目录所有日志
                if os.path.exists(logs_dir):
                    for filename in os.listdir(logs_dir):
                        if filename.endswith('.log'):
                            file_path = os.path.join(logs_dir, filename)
                            if os.path.isfile(file_path):
                                log_files.append((file_path, f"logs/{filename}"))

            # 将所有日志文件添加到zip中
            for file_path, arcname in log_files:
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    zipf.write(file_path, arcname)

            # 添加一个说明文件
            readme_content = f"""HyperBot 日志导出
导出时间: {now.strftime('%Y-%m-%d %H:%M:%S')}
时间范围: 近3天
文件数量: {len(log_files)}

日志文件列表:
"""
            for _, arcname in log_files:
                readme_content += f"- {arcname}\n"

            # 写入README
            readme_path = os.path.join(temp_dir, "README.txt")
            with open(readme_path, 'w', encoding='utf-8') as f:
                f.write(readme_content)
            zipf.write(readme_path, "README.txt")
            os.remove(readme_path)

        # 返回zip文件
        if os.path.exists(zip_path):
            return FileResponse(
                zip_path,
                media_type='application/zip',
                filename=zip_filename,
                background=None  # 不自动删除，让系统自动清理临时文件
            )
        else:
            return error_response("日志文件创建失败")

    except Exception as e:
        # 清理临时文件
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass
        raise

# ==================== 调度器状态查询 ====================
@app.get("/api/scheduler/status")
@handle_api_errors
async def get_scheduler_status():
    """
    获取定时任务调度器状态
    """
    try:
        scheduler = get_scheduler()
        if scheduler:
            status = scheduler.get_status()
            return success_response("获取调度器状态成功", **status)
        else:
            return error_response("调度器未初始化")
    except Exception as e:
        return error_response(f"获取调度器状态失败: {str(e)}")


# ==================== 认证系统 API ====================

@app.get("/api/auth/check")
async def check_auth_status():
    """检查系统是否已有用户注册，如果已注册则返回 UID"""
    try:
        auth_db = get_auth_db()
        has_user = auth_db.has_registered_user()

        response = {"registered": has_user}

        # 如果已注册，返回 bybit_uid
        if has_user:
            user = auth_db.get_registered_user()
            if user:
                response["bybit_uid"] = user['bybit_uid']

        return response
    except Exception as e:
        return {"registered": False, "error": str(e)}


@app.post("/api/auth/register")
async def register_user(request: dict, req: Request):
    """
    首次注册用户（简化版：仅需 Bybit UID）
    Body: {
        "bybit_uid": "123456789",
        "server_ip": "可选，用户填写的服务器IP"
    }
    注：密码会自动生成，登录仅使用 TOTP 验证码
    """
    try:
        import requests
        import socket

        auth_db = get_auth_db()

        # 检查是否已有用户
        if auth_db.has_registered_user():
            return error_response("系统已有用户，无法重复注册")

        # 获取参数
        bybit_uid = request.get("bybit_uid")
        user_server_ip = request.get("server_ip", "")  # 用户填写的服务器IP

        if not bybit_uid:
            return error_response("缺少必要参数: bybit_uid")

        # 创建用户（不需要密码，会自动生成）
        user_id, totp_secret, qr_uri = auth_db.create_user(bybit_uid)

        # 向授权服务器注册用户
        try:
            # 获取系统信息
            hostname = socket.gethostname()

            # 获取内网IP
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                internal_ip = s.getsockname()[0]
                s.close()
            except:
                internal_ip = "127.0.0.1"

            # 获取客户端真实IP
            client_ip = req.headers.get("x-forwarded-for") or req.headers.get("x-real-ip") or req.client.host
            if client_ip and client_ip.startswith("::ffff:"):
                client_ip = client_ip.replace("::ffff:", "")

            # 调用授权服务器注册API
            auth_server_response = requests.post(
                "http://43.156.4.146:3000/api/user/register",
                json={
                    "username": f"bybit_{bybit_uid}",
                    "email": f"{bybit_uid}@bybit.user",
                    "company": "Bybit User",
                    "hostname": hostname,
                    "internal_ip": internal_ip,
                    "public_ip": user_server_ip or client_ip,
                    "system_info": {
                        "bybit_uid": bybit_uid,
                        "registration_source": "hyper_smart",
                        "user_provided_ip": user_server_ip,
                        "detected_ip": client_ip
                    }
                },
                timeout=10
            )

            if auth_server_response.status_code != 200:
                print(f"授权服务器注册失败: {auth_server_response.text}")
            else:
                print(f"✓ 已向授权服务器注册用户: {bybit_uid}")
                print(f"  - 内网IP: {internal_ip}")
                print(f"  - 公网IP: {user_server_ip or client_ip}")

        except Exception as e:
            # 授权服务器注册失败不影响本地注册
            print(f"⚠ 向授权服务器注册失败: {str(e)}")

        return {
            "success": True,
            "totp_secret": totp_secret,
            "qr_code_uri": qr_uri,
            "message": "注册成功，请扫描二维码绑定Google Authenticator"
        }

    except ValueError as e:
        return error_response(str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return error_response(f"注册失败: {str(e)}")


@app.post("/api/auth/login")
async def login(request: dict, response: Response):
    """
    用户登录（简化版：仅验证TOTP）
    Body: {
        "bybit_uid": "123456789",
        "totp_code": "123456"
    }
    """
    try:
        auth_db = get_auth_db()

        bybit_uid = request.get("bybit_uid")
        totp_code = request.get("totp_code")

        if not all([bybit_uid, totp_code]):
            return error_response("缺少必要参数")

        # 检查失败尝试次数（5次/10分钟）
        failed_attempts = auth_db.get_recent_failed_attempts(bybit_uid, minutes=10)
        if failed_attempts >= 5:
            return error_response("验证失败次数过多，请10分钟后再试")

        # 验证用户（仅验证TOTP）
        user = auth_db.verify_user_totp_only(bybit_uid, totp_code)

        if not user:
            # 记录失败日志
            auth_db.log_login_attempt(
                bybit_uid=bybit_uid,
                success=False,
                failure_reason="验证码错误"
            )
            return error_response("验证码错误，请重试")

        # 创建session（3天有效期）
        session_token = auth_db.create_session(user['id'], days=3)

        # 设置cookie
        response.set_cookie(
            key="session_token",
            value=session_token,
            max_age=3 * 24 * 60 * 60,  # 3天
            httponly=True,              # 防止XSS
            secure=False,               # 生产环境改为True（需要HTTPS）
            samesite="lax"              # CSRF保护
        )

        # 记录成功日志
        auth_db.log_login_attempt(user_id=user['id'], success=True)

        return success_response("登录成功")

    except Exception as e:
        import traceback
        traceback.print_exc()
        return error_response(f"登录失败: {str(e)}")


@app.get("/api/auth/session")
async def check_session(request: Request):
    """检查当前session是否有效"""
    try:
        auth_db = get_auth_db()
        session_token = request.cookies.get("session_token")

        if not session_token:
            return {"valid": False}

        session = auth_db.get_session(session_token)

        if not session or session.get('expired', True):
            return {"valid": False}

        return {
            "valid": True,
            "expires_at": session['expires_at'],
            "days_remaining": session.get('days_remaining', 0)
        }

    except Exception as e:
        return {"valid": False, "error": str(e)}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    """用户登出"""
    try:
        auth_db = get_auth_db()
        session_token = request.cookies.get("session_token")

        if session_token:
            auth_db.delete_session(session_token)

        # 清除cookie
        response.delete_cookie("session_token")

        return success_response("已登出")

    except Exception as e:
        return error_response(f"登出失败: {str(e)}")


# ==================== HTML 页面路由 ====================

@app.get("/login")
async def login_page():
    """返回登录页面"""
    return FileResponse("web/login.html")


@app.get("/register")
async def register_page():
    """返回注册页面"""
    return FileResponse("web/register.html")


if __name__ == "__main__":
    # 配置日志级别：将WebSocket相关日志从ERROR降为DEBUG
    import logging

    # 设置uvicorn的access日志为INFO级别
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    # 关键：将websockets库的日志设置为WARNING级别，避免ERROR级别的Expired日志
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)
    logging.getLogger("websockets.server").setLevel(logging.WARNING)

    # uvicorn错误日志保持INFO级别
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("uvicorn").setLevel(logging.INFO)

    print("✓ WebSocket日志级别已调整为WARNING（过滤正常超时消息）")

    uvicorn.run(app, host="0.0.0.0", port=8000)
