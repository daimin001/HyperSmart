import logging
import time
import threading
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime

# 导入现有的Bybit客户端
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from bybit_client import Bybit
from enums import RunningMode
from utils import ensure_short_symbol, ensure_full_symbol
from hyperliquid_monitor.position_calculator import PositionCalculator
from hyperliquid_monitor.symbol_filter import symbol_filter
from hyperliquid_monitor.reverse_position_handler import ReversePositionHandler
from hyperliquid_monitor.twap_manager import TWAPManager
from hyperliquid_monitor.config import (
    ENABLE_ORDER_TIME_FILTER, ORDER_MAX_AGE_HOURS,
    MAX_LEVERAGE, CUSTOM_LEVERAGE_CONFIG, MIN_COPY_VALUE
)
from hyperliquid_monitor.enhanced_retry import api_retry, critical_retry, ErrorClassifier

logger = logging.getLogger(__name__)

@dataclass
class SyncConfig:
    """同步配置"""
    max_leverage: int = 20  # 最大杠杆
    min_position_size: float = 0.001  # 最小持仓大小
    price_tolerance: float = 0.001  # 价格容差 (0.1%)
    sync_interval: int = 5  # 同步检查间隔（秒）
    max_retry: int = 3  # 最大重试次数

class BybitSyncManager:
    """
    Bybit交易同步管理器
    负责将数据库中的交易数据同步到Bybit交易所
    """

    def __init__(self,
                 api_key: str,
                 api_secret: str,
                 mode: RunningMode = RunningMode.DEMO,
                 config: Optional[SyncConfig] = None,
                 db_path: Optional[str] = None,
                 position_opened_callback = None,
                 position_closed_callback = None,
                 account_name: str = None,
                 feishu_notifier = None,
                 hyperliquid_address: Optional[str] = None,
                 follow_mode: Optional[str] = None,
                 fixed_amount: Optional[float] = None,
                 base_margin_amount: Optional[float] = None,
                 min_copy_value: Optional[float] = None,
                 force_min_amount_on_small_order: Optional[bool] = None):
        """
        初始化同步管理器

        Args:
            api_key: Bybit API密钥
            api_secret: Bybit API密钥
            mode: 运行模式 (DEMO/LIVE)
            config: 同步配置
            db_path: 数据库路径
            position_opened_callback: 开仓成功后的回调函数
            position_closed_callback: 平仓成功后的回调函数
            account_name: 账户名称（用于存储订单记录）
            feishu_notifier: 飞书通知器实例（可选）
            hyperliquid_address: Hyperliquid钱包地址（用于获取账户权益）
            follow_mode: 跟单模式 ('fixed' 或 'ratio')，None则使用全局配置
            fixed_amount: 固定金额模式的金额，None则使用全局配置
            base_margin_amount: 比例跟单的基础保证金，None则使用全局配置
            min_copy_value: 最小跟单金额，None则使用全局配置
            force_min_amount_on_small_order: 小订单强制最小金额，None则使用全局配置
        """
        self.config = config or SyncConfig()
        self.bybit = Bybit("sync_manager", api_key, api_secret, mode)
        self._stop_event = threading.Event()
        self._sync_thread = None
        self._position_opened_callback = position_opened_callback
        self._position_closed_callback = position_closed_callback
        self.account_name = account_name or "Unknown"
        self.feishu_notifier = feishu_notifier  # 添加飞书通知器
        self.hyperliquid_address = hyperliquid_address  # 保存Hyperliquid地址

        # 数据库连接（用于状态管理）
        self.db = None
        if db_path:
            from hyperliquid_monitor.database import TradeDatabase
            self.db = TradeDatabase(db_path)
            # 确保状态列存在
            self.db.add_status_column()

        # 记录已同步的交易，避免重复同步
        self._synced_fills = set()
        self._synced_orders = set()
        self._synced_tx_hashes = set()  # 记录已处理的tx_hash，防止重复处理同一笔交易
        self._notified_order_ids = set()  # 记录已发送飞书通知的订单ID，防止重复发送通知

        # Hyperliquid order_id -> Bybit order_id 的映射关系
        self._order_id_mapping: Dict[int, str] = {}
        self._order_mapping_lock = threading.Lock()

        # 平仓过滤管理
        self._closed_symbols = set()  # 记录已全部平仓的币种，防止重复平仓

        # 清仓记录（包括跟单清仓和强制清仓）
        # 格式: {symbol_side: {'time': datetime, 'type': str, 'reason': str, ...}}
        # type: 'follow' (跟单清仓) 或 'forced' (强制清仓)
        self._forced_liquidations: Dict[str, Dict] = {}
        self._forced_liquidations_lock = threading.Lock()

        # 初始化仓位计算器，传入账户级配置
        self.position_calculator = PositionCalculator(
            bybit_client=self.bybit,
            hyperliquid_address=self.hyperliquid_address,
            follow_mode=follow_mode,
            fixed_amount=fixed_amount,
            base_margin_amount=base_margin_amount,
            min_copy_value=min_copy_value,
            force_min_amount_on_small_order=force_min_amount_on_small_order
        )

        # 初始化反向开仓处理器
        self.reverse_handler = ReversePositionHandler(
            bybit_client=self.bybit,
            position_calculator=self.position_calculator
        )

        # 初始化 TWAP 订单管理器
        self.twap_manager = TWAPManager()
        logger.info("TWAP 订单管理器已初始化")

        # 加载杠杆配置
        self._leverage_config = self._load_leverage_config()
        logger.info(f"杠杆配置: 默认={MAX_LEVERAGE}x, 自定义={self._leverage_config}")

        # 初始化交易历史同步器
        self.trade_history_sync = None
        if self.db:
            from hyperliquid_monitor.bybit_trade_sync import BybitTradeHistorySync
            self.trade_history_sync = BybitTradeHistorySync(
                bybit_client=self.bybit,
                db=self.db,
                account_name=self.account_name,
                sync_interval=60  # 60秒同步一次
            )
            logger.info(f"交易历史同步器已初始化: 同步间隔=60秒")

        logger.info(f"Bybit同步管理器初始化完成，模式: {mode.value}")
        logger.info(f"订单时效过滤: {'启用' if ENABLE_ORDER_TIME_FILTER else '禁用'}, 最大时效: {ORDER_MAX_AGE_HOURS}小时")
        logger.info(f"反向开仓处理: 已启用")

    def _load_leverage_config(self) -> Dict[str, int]:
        """
        加载自定义杠杆配置

        Returns:
            币种 -> 杠杆倍数的映射字典
        """
        leverage_map = {}
        if CUSTOM_LEVERAGE_CONFIG:
            try:
                # 解析格式: BTC:50,ETH:30,SOL:25
                for pair in CUSTOM_LEVERAGE_CONFIG.split(','):
                    pair = pair.strip()
                    if ':' in pair:
                        coin, leverage = pair.split(':')
                        leverage_map[coin.strip().upper()] = int(leverage.strip())
                logger.info(f"自定义杠杆配置加载成功: {leverage_map}")
            except Exception as e:
                logger.error(f"解析自定义杠杆配置失败: {e}, 使用默认配置")
        return leverage_map

    def _get_leverage_for_symbol(self, symbol: str) -> int:
        """
        获取指定币种的杠杆倍数

        Args:
            symbol: 币种符号（如 BTC, ETH, SOLUSDT）

        Returns:
            杠杆倍数
        """
        coin = ensure_short_symbol(symbol).upper()
        return self._leverage_config.get(coin, MAX_LEVERAGE)

    def _convert_side_to_bybit_format(self, side: str, direction: str = None) -> str:
        """
        转换交易方向为Bybit API格式

        Args:
            side: 原始交易方向 (BUY/SELL)
            direction: 可选的详细方向 (Open Long/Open Short/Close Long等)

        Returns:
            Bybit格式的交易方向 (Buy/Sell)
        """
        if direction:
            if 'Long' in direction or 'long' in direction:
                return 'Buy'
            elif 'Short' in direction or 'short' in direction:
                return 'Sell'

        # 备用方案：转换大写到首字母大写
        if side == 'BUY':
            return 'Buy'
        elif side == 'SELL':
            return 'Sell'
        else:
            return side.capitalize() if side else 'Buy'

    def _check_symbol_support(self, coin: str, record_id: int, is_fill: bool = True) -> bool:
        """
        检查币种是否支持（白名单和Bybit支持检查）

        Args:
            coin: 币种符号
            record_id: 记录ID
            is_fill: 是否是fills记录（用于更新不同的状态表）

        Returns:
            True: 支持, False: 不支持
        """
        # 检查币种是否在白名单中
        if not symbol_filter.is_symbol_allowed(coin):
            logger.info(f"币种 {coin} 不在白名单中，跳过记录 {record_id}")
            if is_fill:
                if record_id is not None:
                    self._synced_fills.add(record_id)
                    if self.db:
                        self.db.update_fill_status(record_id, 'filtered')
            else:
                if record_id is not None:
                    self._synced_orders.add(record_id)
                    if self.db:
                        self.db.update_order_status(record_id, 'filtered')
            return False

        # 当白名单未启用时，检查 Bybit 是否支持该币种
        if not symbol_filter.enabled:
            symbol_full = ensure_full_symbol(coin)
            if not self.bybit.support_symbol(symbol_full):
                logger.info(f"币种 {coin} 在 Bybit 不支持，跳过记录 {record_id}（白名单未启用）")
                if is_fill:
                    if record_id is not None:
                        self._synced_fills.add(record_id)
                        if self.db:
                            self.db.update_fill_status(record_id, 'unsupported')
                else:
                    if record_id is not None:
                        self._synced_orders.add(record_id)
                        if self.db:
                            self.db.update_order_status(record_id, 'unsupported')
                return False

        return True

    def _parse_timestamp(self, timestamp_str: str) -> datetime:
        """
        解析时间戳字符串

        Args:
            timestamp_str: 时间戳字符串

        Returns:
            datetime对象
        """
        try:
            if timestamp_str:
                return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
            else:
                return datetime.now()
        except (ValueError, TypeError) as e:
            logger.warning(f"无法解析时间戳 {timestamp_str}，使用当前时间: {e}")
            return datetime.now()

    def _validate_order_freshness(self, record_id: int, timestamp_str: str, is_fill: bool = True) -> Tuple[bool, datetime]:
        """
        验证订单时效性

        Args:
            record_id: 记录ID
            timestamp_str: 时间戳字符串
            is_fill: 是否是fills记录

        Returns:
            (is_fresh, timestamp): 是否新鲜, 解析后的时间戳
        """
        order_timestamp = self._parse_timestamp(timestamp_str)

        if not self._is_order_fresh(order_timestamp):
            logger.info(f"记录 {record_id} 订单过期，跳过处理")
            if is_fill:
                if record_id is not None:
                    self._synced_fills.add(record_id)
                    if self.db:
                        self.db.update_fill_status(record_id, 'filtered')
            else:
                if record_id is not None:
                    self._synced_orders.add(record_id)
                    if self.db:
                        self.db.update_order_status(record_id, 'filtered')
            return False, order_timestamp

        return True, order_timestamp

    def _is_order_fresh(self, order_timestamp: datetime) -> bool:
        """
        检查订单是否在有效时间范围内

        Args:
            order_timestamp: 订单时间戳

        Returns:
            True: 订单足够新，可以处理
            False: 订单过期，应该跳过
        """
        if not ENABLE_ORDER_TIME_FILTER:
            logger.debug("订单时效过滤已禁用，允许处理所有订单")
            return True

        current_time = datetime.now()
        time_diff = current_time - order_timestamp
        age_hours = time_diff.total_seconds() / 3600

        if age_hours > ORDER_MAX_AGE_HOURS:
            logger.info(f"订单过期：订单时间={order_timestamp}, 当前时间={current_time}, "
                       f"时间差={age_hours:.1f}小时, 最大允许={ORDER_MAX_AGE_HOURS}小时")
            return False
        else:
            logger.debug(f"订单有效：订单时间={order_timestamp}, 当前时间={current_time}, "
                        f"时间差={age_hours:.1f}小时")
            return True


    def _get_min_order_qty(self, coin: str) -> float:
        """
        获取币种的最小交易数量

        Args:
            coin: 币种符号（如BTC, ETH）

        Returns:
            最小交易数量，如果获取失败返回0
        """
        try:
            # 获取交易对信息
            pair_info = self.bybit._pairs.get(coin)
            if pair_info and 'min_qty' in pair_info:
                min_qty = float(pair_info['min_qty'])
                logger.debug(f"获取 {coin} 最小交易量: {min_qty}")
                return min_qty
            else:
                logger.warning(f"无法获取 {coin} 的最小交易量信息")
                return 0
        except Exception as e:
            logger.error(f"获取 {coin} 最小交易量失败: {e}")
            return 0

    def sync_fill_record(self, record: Dict[str, Any]):
        """
        同步成交记录

        Args:
            record: 数据库中的fills记录
        """
        record_id = record.get('id')

        # 如果缺少id字段，通过hash去重
        if record_id is None:
            tx_hash = record.get('hash') or record.get('tx_hash')
            if tx_hash and tx_hash in self._synced_tx_hashes:
                logger.debug(f"成交记录 (通过hash去重) 已同步，跳过")
                return
            logger.warning(f"成交记录缺少id字段，将使用hash进行去重: {record.get('coin')} {record.get('side')}")
        elif record_id in self._synced_fills:
            logger.debug(f"成交记录 {record_id} 已同步，跳过")
            return

        try:
            logger.info(f"开始同步成交记录: {record}")

            coin = record['coin']
            side = record['side']  # BUY/SELL
            size = float(record['size'])
            price = float(record['price'])
            direction = record.get('direction', '')
            closed_pnl = float(record.get('closed_pnl', 0))
            tx_hash = record.get('tx_hash', '')

            # ✅ 检查tx_hash去重（排除0x0000...的特殊hash）
            if tx_hash and tx_hash != '0x0000000000000000000000000000000000000000000000000000000000000000':
                if tx_hash in self._synced_tx_hashes:
                    logger.info(f"成交记录 {record_id} 的tx_hash {tx_hash[:10]}... 已处理过，跳过重复记录")
                    if record_id is not None:
                        self._synced_fills.add(record_id)
                        if hasattr(self, 'db') and self.db:
                            self.db.update_fill_status(record_id, 'duplicate')
                    return
                else:
                    # 记录此tx_hash
                    self._synced_tx_hashes.add(tx_hash)
                    logger.debug(f"记录新的tx_hash: {tx_hash[:10]}...")

            # 检查币种是否支持（白名单和Bybit支持检查）
            if not self._check_symbol_support(coin, record_id, is_fill=True):
                return

            # 检查订单时效性
            timestamp_str = record.get('timestamp', '')
            is_fresh, order_timestamp = self._validate_order_freshness(record_id, timestamp_str, is_fill=True)
            if not is_fresh:
                return

            # 将币种转换为Bybit格式
            symbol = ensure_full_symbol(coin)

            # 🎯 检测是否为 TWAP 订单
            is_twap = self.twap_manager.is_twap_fill(record)
            twap_oid = record.get('oid') if is_twap else None

            if is_twap:
                logger.info(f"🎯 检测到 TWAP 订单分片: oid={twap_oid}, {coin} {side} {size} @ ${price:.2f}")
                # 记录到 TWAP 管理器
                twap_order, twap_slice = self.twap_manager.add_slice(
                    fill_id=record_id,
                    oid=twap_oid,
                    timestamp=datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now(),
                    coin=coin,
                    side=side,
                    size=size,
                    price=price,
                    direction=direction,
                    tx_hash=tx_hash
                )
                logger.info(f"TWAP 订单 {twap_oid}: 分片 {twap_order.followed_count}/{twap_order.slice_count}, 累计 {twap_order.total_size}")

            # 🔄 优先检测反向开仓信号（多转空/空转多）
            # 反向信号特征：direction包含 ">" 符号，如 "Long > Short" 或 "Short > Long"
            # 注意：反向信号检测必须在其他处理之前进行，因为它有自己的持仓查询逻辑
            if '>' in direction:
                logger.info(f"🔄 检测到可能的反向开仓信号: direction={direction}")

                # 解析反向信号：判断是多转空还是空转多
                if 'Long > Short' in direction or 'long > short' in direction.lower():
                    # 多转空：需要先平多单，再开空单
                    logger.info(f"🔄 识别为多转空信号: {coin}")
                    new_side = 'Sell'  # 新仓位是空单
                elif 'Short > Long' in direction or 'short > long' in direction.lower():
                    # 空转多：需要先平空单，再开多单
                    logger.info(f"🔄 识别为空转多信号: {coin}")
                    new_side = 'Buy'  # 新仓位是多单
                else:
                    logger.warning(f"⚠️ 无法识别的反向信号格式: {direction}")
                    new_side = None

                if new_side:
                    # 使用ReversePositionHandler处理反向开仓
                    reverse_signal = self.reverse_handler.detect_reverse_signal(
                        symbol=symbol,
                        direction=direction,
                        new_side=new_side,
                        new_size=size,
                        new_price=price
                    )

                    if reverse_signal:
                        # 这是反向开仓信号，使用特殊处理流程
                        logger.info(f"🔄 确认反向开仓信号: {reverse_signal.reverse_type}")

                        # 处理反向开仓
                        success, msg = self.reverse_handler.handle_reverse_signal(reverse_signal)

                        if success:
                            logger.info(f"✅ 反向开仓成功: {msg}")
                            # 清除平仓标记
                            if coin in self._closed_symbols:
                                self._closed_symbols.remove(coin)
                        else:
                            logger.error(f"❌ 反向开仓失败: {msg}")
                            # 标记为失败
                            if self.db and record_id is not None:
                                self.db.update_fill_status(record_id, 'failed')

                        if record_id is not None:
                            self._synced_fills.add(record_id)
                        return
                    else:
                        logger.warning(f"⚠️ 反向信号检测返回None，可能当前无持仓，按普通开仓处理")
                        # 如果检测不到反向信号（比如当前无持仓），按普通开仓处理
                        bybit_side = new_side
                        success = self._handle_open_position(
                            symbol, bybit_side, size, price, record,
                            is_twap=is_twap,
                            twap_oid=twap_oid,
                            twap_order=self.twap_manager.get_order(twap_oid) if twap_oid else None
                        )
                        if success and coin in self._closed_symbols:
                            self._closed_symbols.remove(coin)
                        if record_id is not None:
                            self._synced_fills.add(record_id)
                        return

            # 🚨 关键修复：优先检查是否为完全清仓（防止清仓失败导致剩余持仓）
            start_position = float(record.get('start_position', 0))
            is_complete_close = False

            # 判断是否为完全清仓：size等于start_position的绝对值（允许0.5%误差）
            if start_position != 0:
                close_ratio = abs(size / abs(start_position))
                is_complete_close = close_ratio >= 0.995
                if is_complete_close:
                    logger.warning(f"🚨 检测到Hyperliquid完全清仓信号: {symbol} {direction}, "
                                  f"size={size}, start_position={start_position}, 比例={close_ratio*100:.2f}%")

            # 根据direction和PnL判断是开仓还是平仓，并确定正确的交易方向
            if is_complete_close:
                # ✅ 完全清仓：必须平掉Bybit所有对应持仓
                logger.warning(f"🔴 执行强制完全清仓: {symbol}")
                self._handle_force_close_all(symbol, coin, record, reason="Hyperliquid完全清仓")
            elif closed_pnl != 0:  # 有已实现盈亏，表示平仓
                self._handle_close_position(symbol, side, size, price, record)
            elif 'Close' in direction or 'close' in direction.lower():  # 减仓操作
                self._handle_reduce_position(symbol, coin, record)
            elif 'Open' in direction:  # 开仓操作
                # 🔄 步骤1: 检测是否为反向开仓信号（多转空/空转多）
                reverse_signal = self.reverse_handler.detect_reverse_signal(
                    symbol=symbol,
                    direction=direction,
                    new_side=side,
                    new_size=size,
                    new_price=price
                )

                if reverse_signal:
                    # 这是反向开仓信号，使用特殊处理流程
                    logger.info(f"🔄 检测到反向开仓信号: {reverse_signal.reverse_type}")

                    # 处理反向开仓
                    success, msg = self.reverse_handler.handle_reverse_signal(reverse_signal)

                    if success:
                        logger.info(f"✅ 反向开仓成功: {msg}")
                        # 清除平仓标记
                        if coin in self._closed_symbols:
                            self._closed_symbols.remove(coin)
                    else:
                        logger.error(f"❌ 反向开仓失败: {msg}")
                        # 标记为失败
                        if self.db and record_id is not None:
                            self.db.update_fill_status(record_id, 'failed')

                    if record_id is not None:
                        self._synced_fills.add(record_id)
                    return

                # 转换交易方向为Bybit格式
                bybit_side = self._convert_side_to_bybit_format(side, direction)

                # 执行开仓操作
                success = self._handle_open_position(
                    symbol, bybit_side, size, price, record,
                    is_twap=is_twap,
                    twap_oid=twap_oid,
                    twap_order=self.twap_manager.get_order(twap_oid) if twap_oid else None
                )

                # 如果开仓成功，清除平仓标记
                if success:
                    # 清除该币种的平仓标记，允许后续减仓操作
                    if coin in self._closed_symbols:
                        self._closed_symbols.remove(coin)
                        logger.info(f"新的开仓操作成功，清除币种 {coin} 的平仓标记")
            else:
                logger.info(f"成交记录 {record_id if record_id else '(无ID)'} 无需特殊处理")

            # 标记为已同步
            if record_id is not None:
                self._synced_fills.add(record_id)
                # 标记为已处理
                if hasattr(self, 'db') and self.db:
                    self.db.update_fill_status(record_id, 'processed')
                logger.info(f"成交记录 {record_id} 同步完成")
            else:
                # 没有id时，记录hash以防止重复处理
                tx_hash = record.get('hash') or record.get('tx_hash')
                if tx_hash:
                    self._synced_tx_hashes.add(tx_hash)
                logger.info(f"成交记录 (无ID, hash={tx_hash[:10] if tx_hash else 'N/A'}...) 同步完成")

        except Exception as e:
            logger.error(f"同步成交记录 {record_id if record_id else '(无ID)'} 失败: {e}")
            # 标记为失败
            if hasattr(self, 'db') and self.db and record_id is not None:
                self.db.update_fill_status(record_id, 'failed')

    def sync_order_record(self, record: Dict[str, Any]):
        """
        同步订单记录

        Args:
            record: 数据库中的orders记录
        """
        record_id = record['id']

        if record_id in self._synced_orders:
            logger.debug(f"订单记录 {record_id} 已同步，跳过")
            return

        try:
            logger.info(f"开始同步订单记录: {record}")

            coin = record['coin']
            action = record['action']  # placed/canceled
            side = record['side']  # BUY/SELL
            size = float(record['size'])
            price = float(record['price'])
            order_id = record.get('order_id')

            # 检查币种是否支持（白名单和Bybit支持检查）
            if not self._check_symbol_support(coin, record_id, is_fill=False):
                return

            # 检查订单时效性
            timestamp_str = record.get('timestamp', '')
            is_fresh, order_timestamp = self._validate_order_freshness(record_id, timestamp_str, is_fill=False)
            if not is_fresh:
                return

            symbol = ensure_full_symbol(coin)

            # 处理下单操作
            if action == 'placed':
                direction = f"Open {'Long' if side == 'BUY' else 'Short'}"  # 订单默认为开仓

                # 转换交易方向为Bybit格式
                bybit_side = self._convert_side_to_bybit_format(side, direction)

                # 执行下单
                self._handle_place_order(symbol, bybit_side, size, price, record)

                # 标记为已处理
                self._synced_orders.add(record_id)
                if hasattr(self, 'db') and self.db:
                    self.db.update_order_status(record_id, 'processed')

            elif action == 'canceled':
                # 取消订单操作
                self._handle_cancel_order(symbol, order_id, record)

                # 标记为已处理
                self._synced_orders.add(record_id)
                if hasattr(self, 'db') and self.db:
                    self.db.update_order_status(record_id, 'processed')

            logger.info(f"订单记录 {record_id} 同步完成")

        except Exception as e:
            logger.error(f"同步订单记录 {record_id} 失败: {e}")
            # 标记为失败
            if hasattr(self, 'db') and self.db:
                self.db.update_order_status(record_id, 'failed')

    @critical_retry(max_retries=5)
    def _handle_open_position(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        record: Dict,
        is_twap: bool = False,
        twap_oid: Optional[int] = None,
        twap_order = None
    ) -> bool:
        """
        处理开仓操作（带智能重试）

        Args:
            symbol: 交易对
            side: 方向
            size: 数量
            price: 价格
            record: 记录字典
            is_twap: 是否为 TWAP 订单
            twap_oid: TWAP 订单ID
            twap_order: TWAP 订单对象

        Returns:
            bool: True表示开仓成功，False表示开仓失败
        """
        original_value = size * price
        twap_prefix = f"[TWAP {twap_oid}] " if is_twap else ""
        logger.info(f"{twap_prefix}执行开仓（市价单）: {symbol} {side} {size} (目标价格参考: ${price:.2f}, 原始价值: ${original_value:.2f})")

        # ✅ 幂等性检查：如果该记录已处理过，直接返回成功（防止重试时重复下单）
        record_id = record.get('id') if hasattr(record, 'get') else None
        if record_id and record_id in self._synced_fills:
            logger.info(f"✅ 订单记录 {record_id} 已处理过，跳过重复执行（幂等性保护）")
            return True

        try:
            # 使用仓位计算器计算复制仓位大小（传递symbol参数）
            copy_size = self.position_calculator.calculate_copy_size(size, price, symbol)

            # 金额太小则跳过
            if copy_size <= 0:
                coin = symbol.replace('USDT', '')

                # 计算原始仓位价值和跟单金额用于通知
                target_value = size * price
                if self.position_calculator.follow_mode == "fixed":
                    calculated_value = self.position_calculator.fixed_amount
                else:
                    calculated_value = target_value * self.position_calculator.base_margin_amount

                # 发送飞书通知（明确失败原因）
                if self.feishu_notifier:
                    leverage = self._get_leverage_for_symbol(symbol)
                    self.feishu_notifier.send_trade_failure(
                        account_name=self.account_name,
                        symbol=symbol,
                        side=side,
                        reason=f"跟单金额 ${calculated_value:.2f} 小于交易所最小下单金额 ${self.position_calculator.min_copy_value:.2f}",
                        original_size=size,
                        original_price=price,
                        leverage=leverage,
                        is_new_position=True
                    )

                logger.info(f"币种 {coin} 跟单金额过小（${calculated_value:.2f} < ${self.position_calculator.min_copy_value:.2f}），跟单失败")
                # 标记为已处理(被策略过滤)
                if record_id:
                    self._synced_fills.add(record_id)
                    if self.db:
                        self.db.update_fill_status(record_id, 'filtered')
                return False
            else:
                copy_value = copy_size * price

            logger.info(f"仓位复制: 原始({size} × ${price:.3f} = ${original_value:.2f}) -> 复制({copy_size:.6f} × ${price:.3f} = ${copy_value:.2f})")

            # 使用交易所最大杠杆（自动最小化保证金占用）
            leverage = self.bybit.set_max_leverage(symbol, use_exchange_max=True)
            logger.info(f"使用交易所最大杠杆: {symbol} = {leverage}x")

            # 计算订单数量
            qty = self.bybit.clamp_order_quantity(symbol, str(price), str(copy_size))

            if float(qty) < self.config.min_position_size:
                logger.warning(f"复制订单数量 {qty} 小于最小持仓大小，跳过开仓")
                # 标记为已处理(被策略过滤)
                if record_id:
                    self._synced_fills.add(record_id)
                    if self.db:
                        self.db.update_fill_status(record_id, 'filtered')
                return False

            # 检查是否已有该币种+方向的持仓（用于区分开仓/加仓）
            is_new_position = True
            try:
                existing_positions = self.bybit.query_positions()
                coin = symbol.replace('USDT', '')
                for pos in existing_positions:
                    pos_symbol = pos.get('symbol', '')
                    pos_side = pos.get('side', '')
                    pos_size = float(pos.get('size', 0))
                    if pos_symbol == symbol and pos_side == side and pos_size > 0:
                        is_new_position = False
                        logger.info(f"检测到已有持仓: {symbol} {side} {pos_size}，本次为加仓操作")
                        break
            except Exception as e:
                logger.warning(f"检查已有持仓失败，默认为开仓: {e}")

            # 下单开仓 - 对于fills使用市价单立即成交
            success, order_id = self.bybit.open_market_order(
                symbol=symbol,
                side=side,
                qty=qty
            )

            if success and order_id:
                logger.info(f"市价开仓订单提交成功: {order_id}")

                # 等待订单执行
                self._wait_for_order_execution(order_id)

                # 查询订单获取真实成交信息
                actual_filled_qty = float(qty)  # 默认使用计算的数量
                actual_filled_price = float(price)  # 默认使用参考价格

                try:
                    # 使用 get_executions API 查询最近的成交记录
                    import time as time_module
                    current_time = int(time_module.time() * 1000)
                    start_time = current_time - 30000  # 查询最近30秒的成交

                    response = self.bybit._client.get_executions(
                        category="linear",
                        symbol=symbol,
                        startTime=start_time,
                        endTime=current_time,
                        limit=50
                    )

                    if response.get('retCode') == 0:
                        executions = response.get('result', {}).get('list', [])

                        # 查找匹配 orderLinkId 的成交记录
                        total_qty = 0
                        total_value = 0
                        found = False

                        for execution in executions:
                            exec_order_link_id = execution.get('orderLinkId', '')
                            if exec_order_link_id == order_id:
                                exec_qty = float(execution.get('execQty', 0))
                                exec_price = float(execution.get('execPrice', 0))
                                total_qty += exec_qty
                                total_value += exec_qty * exec_price
                                found = True

                        if found and total_qty > 0:
                            actual_filled_qty = total_qty
                            actual_filled_price = total_value / total_qty  # 加权平均价格
                            logger.info(f"从成交记录获取真实数据: 数量={actual_filled_qty:.8f}, 价格=${actual_filled_price:.2f}")
                        else:
                            logger.warning(f"未找到订单 {order_id} 的成交记录，使用计算值")
                    else:
                        logger.warning(f"查询成交记录失败: {response.get('retMsg')}，使用计算值")

                except Exception as e:
                    logger.warning(f"查询成交记录异常: {e}，使用计算值")

                # 注释：订单记录由交易历史同步器统一从Bybit API读取，避免重复记录
                # 存储订单记录到数据库
                # if self.db:
                #     try:
                #         # 确定交易类型：开仓或加仓
                #         trade_type = "开仓" if is_new_position else "加仓"
                #
                #         order_data = {
                #             'timestamp': datetime.now(),
                #             'account_name': self.account_name,
                #             'symbol': symbol,
                #             'side': side,
                #             'order_type': 'Market',
                #             'trade_type': trade_type,
                #             'size': actual_filled_qty,  # 使用真实成交数量
                #             'price': actual_filled_price,  # 使用真实成交价格
                #             'bybit_order_id': order_id,
                #             'status': 'filled',
                #             'order_source': 'system'
                #         }
                #         self.db.store_bybit_order(order_data)
                #         logger.info(f"市价订单记录已存储到数据库 (类型: {trade_type}, 数量: {actual_filled_qty}, 价格: ${actual_filled_price:.2f})")
                #     except Exception as e:
                #         logger.error(f"存储市价订单记录失败: {e}")
                logger.info(f"开仓订单执行完成，订单记录将由交易历史同步器自动记录")

                # ✅ 立即标记为已处理（防止重试时重复下单和通知）
                if record_id:
                    self._synced_fills.add(record_id)
                    logger.info(f"✅ 订单 {order_id} 已成功执行，标记记录 {record_id} 为已处理")

                # 调用回调函数，立即更新持仓跟踪（使用真实成交数量）
                # 使用 try-except 包裹，确保即使失败也不影响主流程
                if self._position_opened_callback:
                    try:
                        self._position_opened_callback(symbol, side, actual_filled_qty, actual_filled_price)
                    except Exception as e:
                        logger.error(f"调用持仓回调失败: {e}", exc_info=True)

                # 发送飞书成功通知（使用真实成交数量和价格）
                # 使用 try-except 包裹，确保即使通知失败也不影响主流程
                if self.feishu_notifier:
                    try:
                        # ✅ 强制检查订单ID
                        if not order_id:
                            logger.error(f"⚠️ 开仓成功但订单ID为空，跳过发送飞书通知: {symbol} {side} {actual_filled_qty}")
                        elif order_id in self._notified_order_ids:
                            # ✅ 去重检查：已发送过通知的订单ID，跳过
                            logger.info(f"✅ 订单 {order_id} 的通知已发送过，跳过重复通知（去重保护）")
                        else:
                            # 准备 TWAP 进度信息
                            twap_progress = None
                            if is_twap and twap_order:
                                twap_progress = f"{twap_order.followed_count}/{twap_order.slice_count} (已跟 {twap_order.followed_size:.6f})"

                            self.feishu_notifier.send_trade_success(
                                account_name=self.account_name,
                                symbol=symbol,
                                side=side,
                                size=actual_filled_qty,
                                price=actual_filled_price,
                                order_id=order_id,
                                leverage=leverage,
                                is_new_position=is_new_position,
                                is_twap=is_twap,
                                twap_progress=twap_progress
                            )

                            # ✅ 标记已发送通知
                            self._notified_order_ids.add(order_id)
                            logger.info(f"✅ 开仓成功通知已发送: {symbol} {side} 订单ID={order_id}")
                    except Exception as e:
                        logger.error(f"发送飞书通知失败: {e}", exc_info=True)

                # 如果是 TWAP 订单，标记该分片已跟单
                # 使用 try-except 包裹，确保即使标记失败也不影响主流程
                if is_twap and twap_oid and record_id:
                    try:
                        self.twap_manager.mark_slice_followed(
                            oid=twap_oid,
                            fill_id=record_id,
                            bybit_order_id=order_id,
                            follow_size=actual_filled_qty
                        )
                    except Exception as e:
                        logger.error(f"标记 TWAP 分片失败: {e}", exc_info=True)

                return True
            else:
                logger.error(f"开仓失败: {symbol} {side} {qty} @ {price}")

                # 标记为已处理（失败状态，防止重试重复通知）
                if record_id:
                    self._synced_fills.add(record_id)
                    if self.db:
                        self.db.update_fill_status(record_id, 'failed')

                # 发送飞书失败通知
                if self.feishu_notifier:
                    try:
                        self.feishu_notifier.send_trade_failure(
                            account_name=self.account_name,
                            symbol=symbol,
                            side=side,
                            reason="订单提交失败",
                            original_size=size,
                            original_price=price,
                            leverage=leverage,
                            is_new_position=is_new_position
                        )
                    except Exception as e:
                        logger.error(f"发送飞书失败通知异常: {e}", exc_info=True)

                return False

        except Exception as e:
            logger.error(f"处理开仓操作失败: {e}")

            # 标记为已处理（异常状态，防止重试重复通知）
            if record_id:
                self._synced_fills.add(record_id)
                if self.db:
                    self.db.update_fill_status(record_id, 'failed')

            # 发送飞书失败通知
            if self.feishu_notifier:
                try:
                    self.feishu_notifier.send_trade_failure(
                        account_name=self.account_name,
                        symbol=symbol,
                        side=side,
                        reason=f"异常: {str(e)}",
                        original_size=size,
                        original_price=price,
                        leverage=self._get_leverage_for_symbol(symbol),
                        is_new_position=is_new_position
                    )
                except Exception as notify_error:
                    logger.error(f"发送飞书失败通知异常: {notify_error}", exc_info=True)

            return False

    @critical_retry(max_retries=5)
    def _handle_close_position(self, symbol: str, side: str, size: float, price: float, record: Dict):
        """
        处理平仓操作：精确跟单模式（带智能重试）
        - 使用 start_position 判断是清仓还是减仓
        - 清仓：Bybit清仓
        - 减仓：Bybit按比例减仓
        """
        # 获取 Hyperliquid 的开始持仓量（用于判断是否清仓）
        start_position = float(record.get('start_position', 0))

        # 判断是否为清仓（允许1%误差）
        is_full_close_hl = False
        if start_position != 0:
            close_ratio = abs(size / start_position)
            is_full_close_hl = close_ratio >= 0.99
            logger.info(f"Hyperliquid平仓: {symbol} {side} size={size:.6f}, start_position={start_position:.6f}, "
                       f"比例={close_ratio*100:.1f}%, 类型={'清仓' if is_full_close_hl else '减仓'}")
        else:
            logger.warning(f"记录中缺少 start_position 字段，按减仓处理")

        try:
            # 从币种名称中提取短币种名
            coin = symbol.replace('USDT', '')

            # 直接从Bybit API获取所有持仓
            positions = self.bybit.query_positions()
            if not positions:
                logger.info(f"未找到任何持仓，无需平仓")
                return

            # 查找该币种的所有持仓（不限定方向）
            coin_positions = []
            for position in positions:
                pos_symbol = position.get('symbol', '')
                if pos_symbol.startswith(coin) and pos_symbol.endswith('USDT'):
                    pos_size = float(position.get('size', 0))
                    if pos_size > 0:  # 有持仓
                        coin_positions.append(position)
                        logger.info(f"找到需要平仓的持仓: {pos_symbol}, 持仓量: {pos_size}, 方向: {position.get('side', '')}")

            if not coin_positions:
                logger.info(f"币种 {coin} 在Bybit上无持仓，无需平仓")
                return

            # 平仓找到的所有持仓
            success_count = 0
            total_closed_size = 0
            for position in coin_positions:
                try:
                    pos_symbol = position.get('symbol', '')
                    pos_side = position.get('side', '')
                    pos_size = float(position.get('size', 0))

                    # 根据 Hyperliquid 的平仓类型决定 Bybit 的平仓方式
                    was_forced_full_close = False  # 标记是否因最小数量限制强制全部清仓
                    min_qty = 0  # 最小交易量

                    if is_full_close_hl:
                        # Hyperliquid 清仓 → Bybit 清仓
                        logger.info(f"🔴 Hyperliquid清仓 → Bybit清仓: {pos_symbol} {pos_side} 数量: {pos_size}")
                        is_partial = False
                        # 清仓时不指定custom_qty，使用is_half=False来平掉整个仓位
                        success, closed_size, pnl, error_code = self.bybit.close_position(position, is_half=False)
                    else:
                        # Hyperliquid 减仓 → Bybit 按比例减仓
                        close_size = min(size, pos_size)

                        # ✅ 检查最小平仓数量限制
                        min_qty = self._get_min_order_qty(coin)
                        if min_qty > 0 and close_size < min_qty:
                            old_size = close_size
                            close_size = min(min_qty, pos_size)  # 使用最小值，但不超过持仓量
                            logger.warning(f"⚠️ 按比例计算的平仓量 {old_size:.8f} 小于最小交易量 {min_qty:.8f}，"
                                         f"调整为 {close_size:.8f}")

                            # 如果调整后等于全部持仓量，说明被强制清仓
                            if close_size >= pos_size:
                                was_forced_full_close = True
                                logger.warning(f"⚠️ 因最小交易量限制，减仓变为清仓: {pos_symbol} {pos_side}")

                        is_partial = close_size < pos_size
                        logger.info(f"🟡 Hyperliquid减仓 → Bybit按比例减仓: {pos_symbol} {pos_side} 数量: {close_size}")

                        # 执行减仓：使用 custom_qty 参数指定精确的平仓数量
                        success, closed_size, pnl, error_code = self.bybit.close_position(position, custom_qty=float(close_size))

                    if success and closed_size > 0:
                        success_count += 1
                        total_closed_size += float(closed_size)

                        # 判断实际平仓类型（考虑强制清仓）
                        actual_full_close = is_full_close_hl or was_forced_full_close

                        # 根据平仓类型显示不同的日志
                        if actual_full_close:
                            if was_forced_full_close:
                                logger.info(f"✅ 强制清仓成功: {pos_symbol} {pos_side} 平仓数量: {closed_size} (原因: 最小交易量限制)")
                            else:
                                logger.info(f"✅ 清仓成功: {pos_symbol} {pos_side} 平仓数量: {closed_size}")
                        else:
                            logger.info(f"✅ 减仓成功: {pos_symbol} {pos_side} 平仓数量: {closed_size}")

                        # 获取平仓成交价格（用于飞书通知）
                        close_price = None
                        try:
                            # 查询最近的成交记录，获取成交价格
                            executions = self.bybit.get_executions(symbol=pos_symbol, limit=10)
                            if executions:
                                # 计算加权平均成交价格
                                total_qty = 0
                                weighted_price_sum = 0
                                for exec in executions:
                                    exec_qty = float(exec.get('execQty', 0))
                                    exec_price = float(exec.get('execPrice', 0))
                                    if exec_qty > 0 and exec_price > 0:
                                        weighted_price_sum += exec_price * exec_qty
                                        total_qty += exec_qty
                                        # 只计算本次平仓的成交记录
                                        if total_qty >= float(closed_size):
                                            break

                                if total_qty > 0:
                                    close_price = weighted_price_sum / total_qty
                                    logger.info(f"获取到平仓成交价格: {close_price:.2f}")
                        except Exception as e:
                            logger.warning(f"获取平仓成交价格失败: {e}")

                        # 注释：订单记录由交易历史同步器统一从Bybit API读取，避免重复记录
                        # 存储平仓订单记录到数据库
                        # if self.db:
                        #     try:
                        #         # 确定交易类型：清仓或减仓
                        #         trade_type = "清仓" if actual_full_close else "减仓"
                        #
                        #         # 平仓订单的side是反向的（比如平多仓是Sell）
                        #         close_side = "Sell" if pos_side == "Buy" else "Buy"
                        #         order_data = {
                        #             'timestamp': datetime.now(),
                        #             'account_name': self.account_name,
                        #             'symbol': pos_symbol,
                        #             'side': close_side,
                        #             'order_type': 'Market',
                        #             'trade_type': trade_type,
                        #             'size': float(closed_size),
                        #             'price': float(price),
                        #             'bybit_order_id': f"close_{int(datetime.now().timestamp())}",
                        #             'status': 'filled',
                        #             'order_source': 'system'
                        #         }
                        #         self.db.store_bybit_order(order_data)
                        #         logger.info(f"平仓订单记录已存储到数据库 (类型: {trade_type})")
                        #     except Exception as e:
                        #         logger.error(f"存储平仓订单记录失败: {e}")
                        logger.info(f"平仓订单执行完成，订单记录将由交易历史同步器自动记录")

                        # 如果是强制清仓，记录到字典中
                        if was_forced_full_close:
                            pos_key = f"{pos_symbol}_{pos_side}"
                            with self._forced_liquidations_lock:
                                self._forced_liquidations[pos_key] = {
                                    'time': datetime.now(),
                                    'type': 'forced',
                                    'reason': '减仓数量小于最小交易量，系统自动清仓',
                                    'original_close_size': size,
                                    'min_qty': min_qty,
                                    'actual_size': float(closed_size)
                                }
                            logger.info(f"📝 已记录强制清仓: {pos_key}")

                        # 发送飞书平仓成功通知
                        if self.feishu_notifier:
                            # 确定平仓类型和原因
                            if was_forced_full_close:
                                close_type = "清仓"
                                close_reason = f"减仓数量({size:.8f})小于最小交易量({min_qty:.8f})，系统强制清仓"
                                # 针对减仓变清仓的特殊标题和内容
                                notification_title = "✅ 减仓因限制执行为清仓"
                                notification_content = f"减仓信号因最小交易量限制执行为清仓 {pos_symbol}"
                            elif is_full_close_hl:
                                close_type = "清仓"
                                close_reason = "跟随交易员清仓"
                                notification_title = "✅ 清仓成功"
                                notification_content = f"成功清仓 {pos_symbol}"
                            else:
                                close_type = "减仓"
                                close_reason = "跟随交易员减仓"
                                notification_title = "✅ 减仓成功"
                                notification_content = f"成功减仓 {pos_symbol}"

                            # 构建通知字段
                            # 注意：pnl 已从 Bybit API 获取（不含手续费的真实盈亏）
                            extra_fields = {
                                "账户": self.account_name,
                                "交易对": pos_symbol,
                                "方向": pos_side,
                                "平仓数量": f"{closed_size}",
                                "类型": close_type,
                                "原因": close_reason
                            }

                            # 添加跟单价格（平仓成交价格）
                            if close_price is not None:
                                extra_fields["跟单价格"] = f"${close_price:,.2f}"

                            # 如果是强制清仓，添加更多详细信息
                            if was_forced_full_close:
                                extra_fields["减仓目标"] = f"{size:.8f}"
                                extra_fields["最小交易量"] = f"{min_qty:.8f}"
                                extra_fields["实际执行"] = f"{closed_size} (全部清仓)"

                            # 添加盈亏信息
                            if pnl is not None:
                                extra_fields["盈亏"] = f"+${pnl:,.2f}" if pnl > 0 else f"-${abs(pnl):,.2f}"

                            self.feishu_notifier.send_notification(
                                title=notification_title,
                                content=notification_content,
                                notification_type="success",
                                extra_fields=extra_fields
                            )

                        # 处理平仓后的回调
                        if actual_full_close:
                            # 清仓：调用回调函数移除持仓跟踪
                            logger.info(f"🔄 {coin} 清仓")

                            # 如果不是强制清仓，记录为跟单清仓
                            if not was_forced_full_close:
                                pos_key = f"{pos_symbol}_{pos_side}"
                                with self._forced_liquidations_lock:
                                    self._forced_liquidations[pos_key] = {
                                        'time': datetime.now(),
                                        'type': 'follow',
                                        'reason': '跟随交易员清仓',
                                        'size': float(closed_size)
                                    }
                                logger.debug(f"📝 已记录跟单清仓: {pos_key}")

                            # 调用回调函数移除持仓跟踪
                            if self._position_closed_callback:
                                try:
                                    self._position_closed_callback(pos_symbol, pos_side)
                                except Exception as e:
                                    logger.error(f"调用平仓回调失败: {e}", exc_info=True)
                        else:
                            # 减仓
                            logger.info(f"📉 {coin} 减仓")
                    else:
                        logger.warning(f"❌ 平仓失败: {pos_symbol} {pos_side}, 错误码: {error_code}")

                        # 检查是否是"持仓为零"错误（错误码 110017）
                        should_send_failure_notification = True
                        if error_code == "110017":
                            logger.info(f"⏰ 检测到错误码110017（持仓为零），延迟5秒后验证持仓状态...")
                            import time
                            time.sleep(5)

                            # 重新查询持仓（不使用缓存）
                            current_positions = self.bybit.query_positions(use_cache=False)
                            if current_positions:
                                # 检查该交易对和方向的持仓是否为0
                                target_pos_size = 0
                                for pos in current_positions:
                                    if pos.get('symbol') == pos_symbol and pos.get('side') == pos_side:
                                        target_pos_size = float(pos.get('size', 0))
                                        break

                                if target_pos_size == 0:
                                    logger.info(f"✅ 验证成功：{pos_symbol} {pos_side} 持仓确实为0，目标已达成，不发送失败通知")
                                    should_send_failure_notification = False
                                    # 将其视为成功（持仓已经是0，目标达成）
                                    success_count += 1
                                else:
                                    logger.warning(f"⚠️ 验证失败：{pos_symbol} {pos_side} 持仓仍为 {target_pos_size}，这是真正的失败")
                            else:
                                logger.warning(f"⚠️ 无法验证持仓状态，将发送失败通知")

                        # 发送飞书平仓失败通知（仅当确实失败时）
                        if should_send_failure_notification and self.feishu_notifier:
                            close_type = "清仓" if is_full_close_hl else "减仓"
                            extra_fields = {
                                "账户": self.account_name,
                                "交易对": pos_symbol,
                                "方向": pos_side,
                                "类型": close_type,
                                "目标平仓数量": f"{close_size if not is_full_close_hl else pos_size}",
                                "失败原因": f"平仓操作失败 (错误码: {error_code})" if error_code else "平仓操作失败"
                            }

                            # 如果有价格信息，添加到通知中
                            if price and price > 0:
                                extra_fields["参考价格"] = f"${price:,.2f}"

                            self.feishu_notifier.send_notification(
                                title=f"❌ {close_type}失败",
                                content=f"跟随{close_type} {pos_symbol} 失败",
                                notification_type="error",
                                extra_fields=extra_fields
                            )

                except Exception as e:
                    logger.error(f"平仓 {position.get('symbol')} 时发生错误: {e}")

            if success_count > 0:
                close_type_text = "清仓" if is_full_close_hl else "减仓"
                logger.info(f"币种 {coin} {close_type_text}完成，成功平仓 {success_count} 个仓位，总平仓数量: {total_closed_size}")
            else:
                logger.warning(f"币种 {coin} 平仓失败")

        except Exception as e:
            logger.error(f"处理平仓操作失败: {e}")

            # 发送飞书平仓失败通知
            if self.feishu_notifier:
                close_type = "清仓" if is_full_close_hl else "减仓"
                extra_fields = {
                    "账户": self.account_name,
                    "交易对": symbol,
                    "类型": close_type,
                    "目标平仓数量": f"{size}",
                    "失败原因": f"异常: {str(e)}"
                }

                # 如果有价格信息，添加到通知中
                if price and price > 0:
                    extra_fields["参考价格"] = f"${price:,.2f}"

                self.feishu_notifier.send_notification(
                    title=f"❌ {close_type}失败",
                    content=f"跟随{close_type} {symbol} 失败",
                    notification_type="error",
                    extra_fields=extra_fields
                )

    def _handle_force_close_all(self, symbol: str, coin: str, record: Dict, reason: str = "强制清仓"):
        """
        强制清空所有持仓（用于Hyperliquid完全清仓时）

        Args:
            symbol: Bybit交易对（如BTCUSDT）
            coin: 币种名（如BTC）
            record: 原始成交记录
            reason: 清仓原因
        """
        record_id = record.get('id')
        logger.warning(f"🚨 {reason}: {symbol}, 记录ID: {record_id}")

        # 检查是否已经平仓过该币种
        if coin in self._closed_symbols:
            logger.info(f"币种 {coin} 已经执行过全部平仓，跳过此次操作")
            return

        try:
            # 直接从Bybit API获取所有持仓
            positions = self.bybit.query_positions()
            if not positions:
                logger.info(f"未找到任何持仓，无需平仓")
                # 即使没有持仓也标记为已清仓，防止重复处理
                self._closed_symbols.add(coin)
                return

            # 查找该币种的所有持仓（不限定方向）
            coin_positions = []
            for position in positions:
                pos_symbol = position.get('symbol', '')
                # 确保匹配对应的币种（如BTC匹配BTCUSDT，FARTCOIN匹配FARTCOINUSDT）
                if pos_symbol.startswith(coin) and pos_symbol.endswith('USDT'):
                    pos_size = float(position.get('size', 0))
                    if pos_size > 0:  # 有持仓
                        coin_positions.append(position)
                        logger.warning(f"🔴 找到需要强制清仓的持仓: {pos_symbol}, 持仓量: {pos_size}, 方向: {position.get('side', '')}")

            if not coin_positions:
                logger.info(f"币种 {coin} 在Bybit上无持仓，无需平仓")
                # 标记为已清仓
                self._closed_symbols.add(coin)
                return

            logger.warning(f"🔴 开始强制清仓币种 {coin} 的 {len(coin_positions)} 个持仓")

            # 强制平掉该币种的所有持仓
            success_count = 0
            total_closed_size = 0
            for position in coin_positions:
                try:
                    pos_symbol = position.get('symbol', '')
                    pos_side = position.get('side', '')
                    pos_size = float(position.get('size', 0))

                    logger.warning(f"🔴 正在强制清仓: {pos_symbol} {pos_side} 数量: {pos_size}")

                    # 强制清仓：使用is_half=False平掉整个仓位
                    success, closed_size, pnl, error_code = self.bybit.close_position(position, is_half=False)

                    if success and closed_size > 0:
                        success_count += 1
                        total_closed_size += closed_size
                        logger.warning(f"✅ 强制清仓成功: {pos_symbol} {pos_side} 平仓数量: {closed_size}, 盈亏: {pnl}")

                        # 发送飞书通知
                        if self.feishu_notifier:
                            extra_fields = {
                                "账户": self.account_name,
                                "交易对": pos_symbol,
                                "方向": pos_side,
                                "平仓数量": f"{closed_size}",
                                "实现盈亏": f"${pnl:,.2f}" if pnl else "N/A",
                                "原因": reason
                            }
                            self.feishu_notifier.send_notification(
                                title=f"🔴 强制完全清仓",
                                content=f"跟随Hyperliquid完全清仓 {pos_symbol}",
                                notification_type="close",
                                extra_fields=extra_fields
                            )
                    else:
                        logger.error(f"❌ 强制清仓失败: {pos_symbol} {pos_side}, 错误码: {error_code}")
                        # 发送失败通知
                        if self.feishu_notifier:
                            extra_fields = {
                                "账户": self.account_name,
                                "交易对": pos_symbol,
                                "方向": pos_side,
                                "持仓量": f"{pos_size}",
                                "失败原因": f"错误码 {error_code}" if error_code else "未知错误",
                                "清仓原因": reason
                            }
                            self.feishu_notifier.send_notification(
                                title=f"❌ 强制清仓失败",
                                content=f"跟随强制清仓 {pos_symbol} 失败",
                                notification_type="error",
                                extra_fields=extra_fields
                            )

                except Exception as e:
                    logger.error(f"❌ 处理单个持仓清仓失败: {e}")
                    import traceback
                    traceback.print_exc()

            # 如果所有持仓都成功清仓，标记该币种为已清仓
            if success_count == len(coin_positions):
                self._closed_symbols.add(coin)
                logger.warning(f"✅ 币种 {coin} 所有持仓已强制清空，共平仓 {total_closed_size}")
            else:
                logger.error(f"⚠️ 币种 {coin} 部分清仓失败: 成功 {success_count}/{len(coin_positions)}")

        except Exception as e:
            logger.error(f"❌ 强制清仓失败: {e}")
            import traceback
            traceback.print_exc()

            # 发送异常通知
            if self.feishu_notifier:
                extra_fields = {
                    "账户": self.account_name,
                    "交易对": symbol,
                    "类型": "强制清仓",
                    "失败原因": f"异常: {str(e)}",
                    "清仓原因": reason
                }
                self.feishu_notifier.send_notification(
                    title=f"❌ 强制清仓异常",
                    content=f"跟随强制清仓 {symbol} 发生异常",
                    notification_type="error",
                    extra_fields=extra_fields
                )

    def _handle_reduce_position(self, symbol: str, coin: str, record: Dict):
        """
        处理减仓操作：直接从Bybit API获取持仓并平掉对应币种的全部仓位
        如果仓位小于等于最小交易量，则视为意外清仓
        """
        record_id = record.get('id')
        logger.info(f"检测到减仓信号: {symbol}, 记录ID: {record_id}")

        # 检查是否已经平仓过该币种
        if coin in self._closed_symbols:
            logger.info(f"币种 {coin} 已经执行过全部平仓，跳过此次减仓操作")
            return

        try:
            # 直接从Bybit API获取所有持仓
            positions = self.bybit.query_positions()
            if not positions:
                logger.info(f"未找到任何持仓，无需平仓")
                return

            # 查找该币种的所有持仓
            coin_positions = []
            for position in positions:
                pos_symbol = position.get('symbol', '')
                # 确保匹配对应的币种（如BTC匹配BTCUSDT）
                if pos_symbol.startswith(coin) and pos_symbol.endswith('USDT'):
                    size = float(position.get('size', 0))
                    if size > 0:  # 有持仓
                        coin_positions.append(position)
                        logger.info(f"找到需要平仓的持仓: {pos_symbol}, 持仓量: {size}, 方向: {position.get('side', '')}")

            if not coin_positions:
                logger.info(f"币种 {coin} 在Bybit上无持仓，无需平仓")
                return

            logger.info(f"开始平仓币种 {coin} 的 {len(coin_positions)} 个持仓")

            # 获取最小交易量
            min_qty = self._get_min_order_qty(coin)

            # 平掉该币种的所有持仓
            success_count = 0
            for position in coin_positions:
                try:
                    pos_symbol = position.get('symbol', '')
                    pos_side = position.get('side', '')
                    pos_size = float(position.get('size', 0))

                    # 判断是否为意外清仓（仓位小于等于最小交易量）
                    is_accidental_full_close = False
                    if min_qty > 0 and pos_size <= min_qty:
                        is_accidental_full_close = True
                        logger.warning(f"⚠️ 检测到意外清仓场景: {pos_symbol} {pos_side}, 持仓量 {pos_size:.8f} <= 最小交易量 {min_qty:.8f}")

                    logger.info(f"正在平仓: {pos_symbol} {pos_side} 数量: {pos_size}")

                    success, closed_size, pnl = self.bybit.close_position(position, is_half=False)
                    if success and closed_size > 0:
                        success_count += 1
                        logger.info(f"平仓成功: {pos_symbol} {pos_side} 平仓数量: {closed_size}")

                        # 注释：订单记录由交易历史同步器统一从Bybit API读取，避免重复记录
                        logger.info(f"反向开仓平仓完成，订单记录将由交易历史同步器自动记录")

                        # 如果是意外清仓，记录到强制清仓字典中，避免被识别为手动平仓
                        if is_accidental_full_close:
                            pos_key = f"{pos_symbol}_{pos_side}"
                            with self._forced_liquidations_lock:
                                self._forced_liquidations[pos_key] = {
                                    'time': datetime.now(),
                                    'type': 'forced',
                                    'reason': f'减仓时仓位({pos_size:.8f})小于等于最小交易量({min_qty:.8f})，执行为意外清仓',
                                    'original_size': pos_size,
                                    'min_qty': min_qty,
                                    'actual_size': float(closed_size)
                                }
                            logger.info(f"📝 已记录意外清仓: {pos_key}")

                        # 发送飞书通知
                        if self.feishu_notifier:
                            # 根据是否为意外清仓，确定通知类型
                            if is_accidental_full_close:
                                notification_title = "✅ 意外清仓"
                                notification_content = f"减仓信号因仓位过小执行为意外清仓 {pos_symbol}"
                                close_type = "意外清仓"
                                close_reason = f"减仓时仓位({pos_size:.8f})小于等于最小交易量({min_qty:.8f})"

                                extra_fields = {
                                    "账户": self.account_name,
                                    "交易对": pos_symbol,
                                    "方向": pos_side,
                                    "平仓数量": f"{closed_size}",
                                    "类型": close_type,
                                    "原因": close_reason,
                                    "原始持仓": f"{pos_size:.8f}",
                                    "最小交易量": f"{min_qty:.8f}"
                                }
                            else:
                                notification_title = "✅ 清仓成功"
                                notification_content = f"成功跟随减仓/清仓 {pos_symbol}"
                                close_type = "清仓"
                                close_reason = "跟随交易员减仓"

                                extra_fields = {
                                    "账户": self.account_name,
                                    "交易对": pos_symbol,
                                    "方向": pos_side,
                                    "平仓数量": f"{closed_size}",
                                    "类型": close_type,
                                    "原因": close_reason
                                }

                            # 添加盈亏信息
                            if pnl is not None:
                                extra_fields["盈亏"] = f"+${pnl:,.2f}" if pnl > 0 else f"-${abs(pnl):,.2f}"

                            self.feishu_notifier.send_notification(
                                title=notification_title,
                                content=notification_content,
                                notification_type="success",
                                extra_fields=extra_fields
                            )
                    else:
                        logger.warning(f"平仓失败: {pos_symbol} {pos_side}")
                except Exception as e:
                    logger.error(f"平仓 {position.get('symbol')} 时发生错误: {e}")

            # 如果至少有一个仓位平仓成功，就标记该币种
            if success_count > 0:
                self._closed_symbols.add(coin)
                logger.info(f"币种 {coin} 全部平仓完成，已标记防止重复平仓。成功平仓 {success_count} 个仓位")
            else:
                logger.warning(f"币种 {coin} 全部平仓失败")

                # 发送飞书减仓失败通知
                if self.feishu_notifier:
                    self.feishu_notifier.send_notification(
                        title="❌ 清仓失败",
                        content=f"跟随减仓/清仓 {symbol} 失败",
                        notification_type="error",
                        extra_fields={
                            "账户": self.account_name,
                            "交易对": symbol,
                            "失败原因": "所有持仓平仓失败"
                        }
                    )

        except Exception as e:
            logger.error(f"处理减仓操作失败: {e}")

            # 发送飞书减仓失败通知
            if self.feishu_notifier:
                self.feishu_notifier.send_notification(
                    title="❌ 清仓失败",
                    content=f"跟随减仓/清仓 {symbol} 失败",
                    notification_type="error",
                    extra_fields={
                        "账户": self.account_name,
                        "交易对": symbol,
                        "失败原因": f"异常: {str(e)}"
                    }
                )

    @api_retry(max_retries=3)
    def _handle_place_order(self, symbol: str, side: str, size: float, price: float, record: Dict):
        """处理下单操作（限价单）"""
        original_value = size * price
        logger.info(f"执行下单（限价单）: {symbol} {side} {size} @ ${price:.2f} (原始价值: ${original_value:.2f})")

        try:
            # 使用仓位计算器计算复制订单大小
            copy_size = self.position_calculator.calculate_copy_size(size, price, symbol)

            # 检查是否因为金额过小被跳过（copy_size为0表示跳过）
            if copy_size <= 0:
                coin = symbol.replace('USDT', '')

                # 计算原始仓位价值和跟单金额用于通知
                target_value = size * price
                if self.position_calculator.follow_mode == "fixed":
                    calculated_value = self.position_calculator.fixed_amount
                else:
                    calculated_value = target_value * self.position_calculator.base_margin_amount

                # 发送飞书通知（明确失败原因）
                if self.feishu_notifier:
                    leverage = self._get_leverage_for_symbol(symbol)
                    self.feishu_notifier.send_trade_failure(
                        account_name=self.account_name,
                        symbol=symbol,
                        side=side,
                        reason=f"跟单金额 ${calculated_value:.2f} 小于交易所最小下单金额 ${self.position_calculator.min_copy_value:.2f}",
                        original_size=size,
                        original_price=price,
                        leverage=leverage,
                        is_new_position=True
                    )

                logger.info(f"币种 {coin} 跟单金额过小（${calculated_value:.2f} < ${self.position_calculator.min_copy_value:.2f}），跟单失败")
                if hasattr(record, 'get') and record.get('id'):
                    if self.db:
                        self.db.update_order_status(record['id'], 'filtered')
                return

            copy_value = copy_size * price
            logger.info(f"订单复制: 原始({size} × ${price:.3f} = ${original_value:.2f}) -> 复制({copy_size:.6f} × ${price:.3f} = ${copy_value:.2f})")

            # 检查最小订单大小
            if copy_size < self.config.min_position_size:
                logger.warning(f"复制订单数量 {copy_size} 小于最小持仓大小，跳过下单")
                if hasattr(record, 'get') and record.get('id'):
                    if self.db:
                        self.db.update_order_status(record['id'], 'filtered')
                return

            # 检查是否已存在相同的订单
            orders = self.bybit.query_open_orders()
            if orders and self.bybit.contain_order(orders, symbol, side, str(price)):
                logger.info(f"相同订单已存在，跳过下单: {symbol} {side} @ ${price}")
                return

            # 使用交易所最大杠杆（自动最小化保证金占用）
            leverage = self.bybit.set_max_leverage(symbol, use_exchange_max=True)
            logger.info(f"使用交易所最大杠杆: {symbol} = {leverage}x")

            # 计算订单数量（使用复制后的数量）
            qty = self.bybit.clamp_order_quantity(symbol, str(price), str(copy_size))

            # 下限价单
            success, bybit_order_id = self.bybit.open_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=str(price)
            )

            if success and bybit_order_id:
                logger.info(f"✅ 限价下单成功: Bybit订单ID={bybit_order_id}, {symbol} {side} {qty} @ ${price}")

                # 记录 order_id 映射关系（Hyperliquid order_id -> Bybit order_id）
                hl_order_id = record.get('order_id')
                if hl_order_id:
                    with self._order_mapping_lock:
                        self._order_id_mapping[hl_order_id] = bybit_order_id
                        logger.info(f"记录订单映射: Hyperliquid订单{hl_order_id} -> Bybit订单{bybit_order_id}")

                # 注释：订单记录由交易历史同步器统一从Bybit API读取，避免重复记录
                # 存储订单记录到数据库
                # if self.db:
                #     try:
                #         # 检查是否已有持仓来判断是开仓还是加仓
                #         trade_type = "开仓"
                #         try:
                #             existing_positions = self.bybit.query_positions()
                #             for pos in existing_positions:
                #                 if pos.get('symbol') == symbol and pos.get('side') == side and float(pos.get('size', 0)) > 0:
                #                     trade_type = "加仓"
                #                     break
                #         except Exception as e:
                #             logger.debug(f"检查持仓失败，默认为开仓: {e}")
                #
                #         order_data = {
                #             'timestamp': datetime.now(),
                #             'account_name': self.account_name,
                #             'symbol': symbol,
                #             'side': side,
                #             'order_type': 'Limit',
                #             'trade_type': trade_type,
                #             'size': float(qty),
                #             'price': float(price),
                #             'bybit_order_id': bybit_order_id,
                #             'status': 'filled',
                #             'order_source': 'system'
                #         }
                #         self.db.store_bybit_order(order_data)
                #         logger.info(f"Bybit订单记录已存储到数据库 (类型: {trade_type})")
                #     except Exception as e:
                #         logger.error(f"存储Bybit订单记录失败: {e}")
                logger.info(f"限价单执行完成，订单记录将由交易历史同步器自动记录")

            else:
                logger.error(f"❌ 限价下单失败: {symbol} {side} {qty} @ ${price}")

        except Exception as e:
            logger.error(f"处理下单操作失败: {e}")

    @api_retry(max_retries=3)
    def _handle_cancel_order(self, symbol: str, hl_order_id: Optional[int], record: Dict):
        """处理撤单操作"""
        if not hl_order_id:
            logger.warning("撤单操作缺少 Hyperliquid 订单ID")
            return

        logger.info(f"执行撤单: {symbol} Hyperliquid订单ID: {hl_order_id}")

        try:
            # 查找对应的 Bybit 订单ID
            bybit_order_id = None
            with self._order_mapping_lock:
                bybit_order_id = self._order_id_mapping.get(hl_order_id)

            if not bybit_order_id:
                logger.warning(f"未找到 Hyperliquid 订单 {hl_order_id} 对应的 Bybit 订单ID映射")
                logger.info(f"尝试通过价格和方向匹配查找 Bybit 订单...")

                # 如果没有映射，尝试通过价格匹配查找订单
                price = float(record.get('price', 0))
                side = record.get('side', '')

                if price > 0:
                    open_orders = self.bybit.query_open_orders()
                    if open_orders:
                        for order in open_orders:
                            if (order.get('symbol') == symbol and
                                order.get('side') == side and
                                abs(float(order.get('price', 0)) - price) < 0.01):
                                bybit_order_id = order.get('orderId')
                                logger.info(f"通过价格匹配找到 Bybit 订单: {bybit_order_id}")
                                break

                if not bybit_order_id:
                    logger.warning(f"无法找到对应的 Bybit 订单，撤单操作跳过")
                    return

            # 查询订单是否存在
            order = self.bybit.query_order(str(bybit_order_id))

            if not order:
                logger.info(f"Bybit 订单 {bybit_order_id} 不存在或已取消")
                # 清理映射关系
                with self._order_mapping_lock:
                    if hl_order_id in self._order_id_mapping:
                        del self._order_id_mapping[hl_order_id]
                return

            # 撤销订单
            success = self.bybit.cancel_order(symbol, str(bybit_order_id))

            if success:
                logger.info(f"✅ 撤单成功: Bybit订单{bybit_order_id} (对应Hyperliquid订单{hl_order_id})")
                # 清理映射关系
                with self._order_mapping_lock:
                    if hl_order_id in self._order_id_mapping:
                        del self._order_id_mapping[hl_order_id]
            else:
                logger.error(f"❌ 撤单失败: Bybit订单{bybit_order_id}")

        except Exception as e:
            logger.error(f"处理撤单操作失败: {e}")

    def _wait_for_order_execution(self, order_link_id: str, timeout: int = 30):
        """等待订单执行完成"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                order = self.bybit.query_order(order_link_id, is_link_id=True)

                if not order:
                    logger.info(f"订单 {order_link_id} 已执行完成或已取消")
                    break

                status = order.get('orderStatus', '')

                if status == 'Filled':
                    logger.info(f"订单 {order_link_id} 已完全成交")
                    break
                elif status in ['Cancelled', 'Rejected']:
                    logger.warning(f"订单 {order_link_id} 状态: {status}")
                    break

                time.sleep(1)

            except Exception as e:
                logger.error(f"查询订单状态失败: {e}")
                break

    def check_position_sync(self, db_positions: List[Dict], bybit_positions: List[Dict]) -> List[Dict]:
        """
        检查持仓同步状态，返回需要同步的差异

        Args:
            db_positions: 数据库中的预期持仓
            bybit_positions: Bybit实际持仓

        Returns:
            需要同步的操作列表
        """
        sync_actions = []

        # 构建Bybit持仓字典 {symbol_side: position}
        bybit_pos_dict = {}
        for pos in bybit_positions:
            symbol = pos.get('symbol', '')
            side = pos.get('side', '')
            size = float(pos.get('size', 0))

            if size > 0:  # 只记录有效持仓
                key = f"{symbol}_{side}"
                bybit_pos_dict[key] = pos

        # 检查每个数据库预期持仓
        for db_pos in db_positions:
            symbol = ensure_full_symbol(db_pos['coin'])
            side = db_pos['side']
            expected_size = float(db_pos['size'])

            key = f"{symbol}_{side}"
            bybit_pos = bybit_pos_dict.get(key)

            if not bybit_pos and expected_size > 0:
                # Bybit没有持仓，但数据库显示应该有持仓
                sync_actions.append({
                    'action': 'open',
                    'symbol': symbol,
                    'side': side,
                    'size': expected_size,
                    'reason': 'missing_position'
                })
            elif bybit_pos:
                # 比较持仓大小
                actual_size = float(bybit_pos.get('size', 0))
                size_diff = abs(expected_size - actual_size)

                if size_diff > self.config.min_position_size:
                    sync_actions.append({
                        'action': 'adjust',
                        'symbol': symbol,
                        'side': side,
                        'expected_size': expected_size,
                        'actual_size': actual_size,
                        'reason': 'size_mismatch'
                    })

        return sync_actions

    def get_forced_liquidation(self, symbol: str, side: str) -> Optional[Dict]:
        """
        获取强制清仓记录

        Args:
            symbol: 交易对符号
            side: 交易方向

        Returns:
            强制清仓记录，如果不存在或已过期则返回None
        """
        pos_key = f"{symbol}_{side}"
        with self._forced_liquidations_lock:
            if pos_key in self._forced_liquidations:
                record = self._forced_liquidations[pos_key]
                # 检查记录是否在5分钟内（超过5分钟的记录视为过期）
                time_diff = (datetime.now() - record['time']).total_seconds()
                if time_diff < 300:  # 5分钟 = 300秒
                    return record
                else:
                    # 删除过期记录
                    del self._forced_liquidations[pos_key]
                    logger.debug(f"删除过期的强制清仓记录: {pos_key}")
        return None

    def clear_forced_liquidation(self, symbol: str, side: str):
        """
        清除强制清仓记录

        Args:
            symbol: 交易对符号
            side: 交易方向
        """
        pos_key = f"{symbol}_{side}"
        with self._forced_liquidations_lock:
            if pos_key in self._forced_liquidations:
                del self._forced_liquidations[pos_key]
                logger.debug(f"已清除强制清仓记录: {pos_key}")

    def start_sync_monitoring(self):
        """启动同步监控线程"""
        if self._sync_thread is None or not self._sync_thread.is_alive():
            self._stop_event.clear()
            self._sync_thread = threading.Thread(
                target=self._sync_monitoring_loop,
                name="BybitSyncMonitor",
                daemon=True
            )
            self._sync_thread.start()
            logger.info("Bybit同步监控线程已启动")

    def stop_sync_monitoring(self):
        """停止同步监控"""
        logger.info("正在停止Bybit同步监控...")
        self._stop_event.set()

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=10)
            logger.info("Bybit同步监控已停止")

    def _sync_monitoring_loop(self):
        """同步监控循环"""
        while not self._stop_event.is_set():
            try:
                # 保留此循环用于未来可能的监控任务
                pass

            except Exception as e:
                logger.error(f"同步监控循环出错: {e}")

            self._stop_event.wait(self.config.sync_interval)

    def get_position_analysis(self) -> Dict:
        """获取仓位分析报告"""
        return self.position_calculator.get_analysis_report()

    def get_symbol_filter_status(self) -> Dict:
        """获取币种过滤器状态"""
        return symbol_filter.get_filter_status()

    def cleanup(self):
        """清理资源，关闭数据库连接等"""
        try:
            if hasattr(self, 'db') and self.db:
                logger.info("正在关闭数据库连接...")
                self.db.close()
                logger.info("数据库连接已关闭")
        except Exception as e:
            logger.error(f"清理资源时发生错误: {e}", exc_info=True)

    def __del__(self):
        """析构函数，确保资源被释放"""
        try:
            self.cleanup()
        except:
            pass  # 析构函数中不应抛出异常


if __name__ == "__main__":
    # 测试代码
    import os
    from dotenv import load_dotenv

    load_dotenv()

    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")

    if not api_key or not api_secret:
        print("请在.env文件中配置BYBIT_API_KEY和BYBIT_API_SECRET")
        exit(1)

    # 创建同步管理器
    sync_manager = BybitSyncManager(
        api_key=api_key,
        api_secret=api_secret,
        mode=RunningMode.DEMO  # 使用DEMO模式测试
    )

    print("Bybit同步管理器创建成功")
    print("测试完成")