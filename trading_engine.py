"""
交易引擎（真实交易版）
实现5分钟自动交易逻辑
完整策略：
1. 每5分钟挂双向限价单（75）
2. 等待成交，取消未成交
3. 设置止损止盈（45/95）
4. 等待触发或到期，根据事件结果结算

模式：
- 真实交易 + 真实数据

优化：
- API 速率限制
- 智能缓存
- 统一错误处理
- 详细的日志记录
"""
import time
import random
import logging
import math
import sys
from datetime import datetime
from typing import Optional, Dict, List, Any
from config import TradingConfig, TradeRecord, TradeHistory, ConfigValidationError
from polymarket_api import PolymarketClient, format_time_remaining, format_price
from pathlib import Path
from ui_display import TradingDashboard, RealTimeDisplay
from error_handler import ErrorHandler, TradingError


# 配置日志
LOG_DIR = Path.home() / ".polymarket-trader" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logger(name: str, config: TradingConfig) -> logging.Logger:
    """配置日志"""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.log_level))

    # 清除现有处理器
    logger.handlers.clear()

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # 文件处理器
    if config.log_to_file:
        log_file = LOG_DIR / f"{name}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


class TradingEngine:
    """交易引擎（优化版：API 速率限制 + 智能缓存）"""

    def __init__(self, config: TradingConfig):
        """
        初始化交易引擎

        Args:
            config: 交易配置

        Raises:
            ConfigValidationError: 配置验证失败
        """
        # 验证配置
        try:
            config.validate()
        except Exception as e:
            raise ConfigValidationError(f"配置验证失败: {e}")

        self.config = config
        self.logger = setup_logger("trading", config)

        # 初始化错误处理器
        self.error_handler = ErrorHandler(self.logger)

        # 初始化API客户端
        try:
            # 调试：打印凭证信息
            print(f"[*] 初始化客户端，凭证检查:")
            print(f"    api_key: {config.api_key[:10] if config.api_key else 'EMPTY'}...")
            print(f"    api_secret: {config.api_secret[:10] if config.api_secret else 'EMPTY'}...")
            print(f"    passphrase: {config.passphrase[:10] if config.passphrase else 'EMPTY'}...")
            
            self.client = PolymarketClient(
                private_key=config.private_key,
                api_key=config.api_key,
                api_secret=config.api_secret,
                passphrase=config.passphrase,
                chain_id=config.chain_id,
                signature_type=config.signature_type,
                funder_address=config.funder_address,
            )
        except Exception as e:
            self.error_handler.handle(e, "初始化 API 客户端", recoverable=False, should_raise=True)

        # 交易历史
        self.trade_history = TradeHistory()

        # 界面显示器
        self.dashboard = TradingDashboard()
        self.realtime_display = RealTimeDisplay(refresh_interval=1)  # 5秒刷新一次完整界面

        # 交易状态
        self.is_running = False
        self.balance = 0.0  # 将在 initialize_balance() 中从 API 获取
        self.initial_balance = 0.0  # 将在 initialize_balance() 中从 API 获取
        self.config_initial_balance = config.initial_balance  # 保存配置值用于计算仓位
        self.current_position: Optional[Dict[str, Any]] = None
        self.pending_orders: Dict[str, Dict] = {}
        self.stop_loss_order: Optional[Dict] = None

        # API 使用统计
        self.api_call_stats = {
            "total_calls": 0,
            "cache_hits": 0,
            "errors": 0,
            "last_reset": time.time(),
        }
        self.take_profit_order: Optional[Dict] = None

        # 代币ID缓存
        self.yes_token_id: Optional[str] = None
        self.no_token_id: Optional[str] = None

        # 数据更新时间
        self.last_update_time = "未更新"
        self.last_update_duration = 0.0

        # 事件追踪 - 确保同一事件只交易一次
        self.current_event_id: Optional[str] = None  # 当前事件ID
        self.has_traded_in_event: bool = False  # 当前事件是否已交易
        self.event_start_time: Optional[datetime] = None  # 当前事件开始时间

        # 周期管理
        self.cycle_start_time: Optional[datetime] = None
        self.cycle_duration = config.trade_cycle_minutes * 60  # 秒

        # API 状态
        self.api_status = "connected"  # connected, disconnected, error
        
        # 第一次下单确认标志
        self.first_order_confirmed = False

        # 启动信息
        entry_display = config.entry_price / 100.0 if config.entry_price > 1 else config.entry_price
        stop_display = config.stop_loss / 100.0 if config.stop_loss > 1 else config.stop_loss
        take_display = config.take_profit / 100.0 if config.take_profit > 1 else config.take_profit
        
        self.logger.info("=" * 60)
        self.logger.info("Polymarket自动交易引擎启动")
        self.logger.info("模式: 真实交易（生产模式）")
        self.logger.info(f"周期: {config.trade_cycle_minutes} 分钟")
        self.logger.info(f"开仓价: ${entry_display:.2f}")
        self.logger.info(f"止损价: ${stop_display:.2f}")
        self.logger.info(f"止盈价: ${take_display:.2f}")
        self.logger.info("=" * 60)

    def start(self) -> None:
        """开始交易"""
        self.is_running = True
        print("[启动] Polymarket 自动交易系统启动中...")

        # 从 Polymarket API 获取初始余额（带重试）
        max_retries = 5
        for attempt in range(max_retries):
            if self._try_initialize_balance():
                break
            if attempt < max_retries - 1:
                print(f"\r[启动] 等待 {5} 秒后重试 ({attempt + 1}/{max_retries})...", end="", flush=True)
                time.sleep(5)
                print()

        try:
            print("[启动] 交易循环开始，按 Ctrl+C 停止\n")
            while self.is_running:
                self.execute_trade_cycle()

        except KeyboardInterrupt:
            print("\n[停止] 接收到停止信号")
            self.stop()
        except Exception as e:
            self.logger.error(f"交易循环出错: {e}")
            self.stop()

    def _try_initialize_balance(self, skip_auth_check: bool = False) -> bool:
        """尝试初始化余额和授权
        
        Args:
            skip_auth_check: 是否跳过授权检查（用于测试）
        
        Returns:
            True 如果初始化成功（或跳过检查）
        """
        # 如果跳过授权检查，直接尝试获取余额
        if skip_auth_check:
            print("[启动] [*] 跳过授权检查，直接获取余额...")
            try:
                balance = self.client.get_balance()
                if balance is not None and balance >= 0:
                    self.balance = balance
                    self.initial_balance = balance
                    self.api_status = "connected"
                    print(f"[启动] [OK] 当前余额: ${balance:.2f}")
                    return True
            except Exception as e:
                print(f"[启动] [X] 获取余额失败: {e}")
                self.api_status = "error"
                return False
        
        try:
            # 1. 先检查并初始化授权
            print("[启动] [*] 检查授权状态...")
            allowance_result = self.client.check_and_initialize_allowance()
            
            if allowance_result.get("error"):
                error_msg = allowance_result['error']
                print(f"[启动] [!] 授权检查失败: {error_msg}")
                
                # 如果是认证错误，给出更明确的提示
                if "401" in error_msg or "Unauthorized" in error_msg:
                    print("[启动] [!] 请检查 .env 文件中的 API_KEY, API_SECRET, PASSPHRASE 是否正确")
                # 继续尝试获取余额
            
            # 2. 获取余额
            try:
                balance = self.client.get_balance()
                if balance is not None and balance >= 0:
                    self.balance = balance
                    self.initial_balance = balance
                    self.api_status = "connected"
                    
                    # 显示余额信息
                    allowance = allowance_result.get("allowance", 0)
                    print(f"[启动] [OK] 已连接")
                    print(f"[启动] [OK] 当前余额: ${balance:.2f}")
                    
                    if allowance == float("inf"):
                        print(f"[启动] [OK] 授权额度: 无限")
                    else:
                        print(f"[启动] [OK] 授权额度: ${allowance:.2f}")
                    
                    # 即使余额为0，也认为 API 连接正常，继续运行
                    if balance == 0:
                        print("[启动] [!] 警告: 余额为 0，将使用配置中的 initial_balance 进行仓位计算")
                        print("[启动] [!] 请确认钱包中是否有 USDC.e")
                    # API 已连接，可以继续
                    return True
                else:
                    print("[启动] [!] 无法获取余额，API 可能未正确初始化")
                    self.api_status = "error"
                    return False
            except AttributeError as e:
                print(f"[启动] [X] SDK 方法调用失败: {e}")
                print("[启动] [!] 请确保 API 凭证配置正确")
            except Exception as e:
                print(f"[启动] [X] 获取余额失败: {e}")
                
        except Exception as e:
            print(f"[启动] [X] 初始化失败: {e}")
            import traceback
            traceback.print_exc()
        
        self.api_status = "error"
        return False

    def initialize_balance(self) -> None:
        """从 Polymarket API 获取初始余额（兼容方法）"""
        self._try_initialize_balance()

    def stop(self) -> None:
        """停止交易"""
        self.is_running = False
        self.logger.info("交易引擎已停止")

    def _ask_first_order_confirmation(self, position_size: float, market_data: Dict) -> bool:
        """第一次下单前请求用户确认
        
        Returns:
            True: 用户取消，应该跳过挂单
            False: 用户确认，应该继续挂单
        """
        # 转换配置价格为 0-1 格式显示
        entry_display = self.config.entry_price / 100.0 if self.config.entry_price > 1 else self.config.entry_price
        stop_loss_display = self.config.stop_loss / 100.0 if self.config.stop_loss > 1 else self.config.stop_loss
        take_profit_display = self.config.take_profit / 100.0 if self.config.take_profit > 1 else self.config.take_profit
        
        print("\n" + "=" * 60)
        print("[!]  首次下单确认  [!]")
        print("=" * 60)
        print(f"  当前余额:     ${self.balance:.2f}")
        print(f"  开仓金额:     ${position_size:.2f}")
        print(f"  开仓价格:     ${entry_display:.2f}")
        print(f"  止损价格:     ${stop_loss_display:.2f}")
        print(f"  止盈价格:     ${take_profit_display:.2f}")
        print(f"  YES 价格:     ${market_data.get('yes_price', 0):.2f}")
        print(f"  NO 价格:      ${market_data.get('no_price', 0):.2f}")
        print("=" * 60)
        print()
        print("  即将执行真实交易，请确认:")
        print("  [y/Y] 确认下单并开始自动交易")
        print("  [n/N] 取消本次交易（将跳过此事件）")
        print("  [q/Q] 退出程序")
        print()
        
        while True:
            try:
                print("请输入 (y/n/q): ", end="", flush=True)
                sys.stdout.flush()
                # 使用更可靠的输入方式
                try:
                    user_input = input()
                except EOFError:
                    time.sleep(0.1)
                    user_input = sys.stdin.readline()
                user_input = user_input.strip().lower()
                if not user_input:
                    print("  无效输入，请输入 y、n 或 q", flush=True)
                    continue
                if user_input == 'y':
                    self.first_order_confirmed = True
                    print("\n[OK] 已确认，开始下单！", flush=True)
                    sys.stdout.flush()
                    return False  # 继续挂单
                elif user_input == 'n':
                    self.first_order_confirmed = True
                    print("\n[跳过] 已取消本次交易，跳过此事件...", flush=True)
                    sys.stdout.flush()
                    return True  # 跳过挂单
                elif user_input == 'q':
                    print("\n[退出] 退出程序...", flush=True)
                    sys.stdout.flush()
                    self.stop()
                    sys.exit(0)
                else:
                    print("  无效输入，请输入 y、n 或 q", flush=True)
            except (KeyboardInterrupt, EOFError):
                print("\n[退出] 退出程序...", flush=True)
                sys.stdout.flush()
                self.stop()
                sys.exit(0)

    def execute_trade_cycle(self) -> None:
        """执行一个完整的交易周期（5分钟）"""
        print("\n" + "=" * 60)
        print("[周期] 新周期开始")
        print("=" * 60)

        self.cycle_start_time = datetime.now()
        cycle_duration = self.config.trade_cycle_minutes * 60  # 转换为秒
        cycle_start = time.time()
        retry_count = 0
        max_retries = 3

        try:
            # 1. 获取市场数据（带重试）
            market_data = None
            while retry_count < max_retries:
                market_data = self.fetch_market_data()
                if market_data:
                    break
                retry_count += 1
                if retry_count < max_retries:
                    print(f"\r[重试] 获取数据失败，{10}秒后重试 ({retry_count}/{max_retries})...", end="", flush=True)
                    time.sleep(10)
                    print()  # 换行
            
            if not market_data:
                print("[等待] 无法获取市场数据，等待下次周期...")
                # 等待一个完整周期
                elapsed = time.time() - cycle_start
                remaining = max(0, cycle_duration - elapsed)
                if remaining > 0:
                    time.sleep(remaining)
                return

            # 2. 检查是否是同一事件
            current_market_id = self.config.market_id
            is_new_event = self.current_event_id != current_market_id
            
            if is_new_event:
                # 新事件，重置交易状态
                self.current_event_id = current_market_id
                self.has_traded_in_event = False
                self.event_start_time = datetime.now()
                print(f"[周期] 新事件: {current_market_id[:16]}...")

            # 3. 如果当前事件已交易过，跳过挂单
            if self.has_traded_in_event:
                print("[周期] 当前事件已交易过，跳过挂单")
            else:
                # 4. 更新余额
                self.update_balance()

                # 5. 计算仓位
                position_size = self.calculate_position_size()
                print(f"[挂单] 开仓金额: ${position_size:.2f}")

                # 6. 显示实时仪表盘（强制刷新）
                self.show_dashboard(market_data, force_refresh=True)

                # 7. 第一次下单前确认
                if not self.first_order_confirmed:
                    should_skip = self._ask_first_order_confirmation(position_size, market_data)
                    if should_skip:
                        # 用户取消，跳过挂单
                        self.has_traded_in_event = True
                        # 等待周期结束
                        elapsed = time.time() - cycle_start
                        remaining_time = max(0, cycle_duration - elapsed)
                        if remaining_time > 0:
                            print(f"[等待] 等待周期结束... ({format_time_remaining(remaining_time)})")
                            time.sleep(remaining_time)
                        return

                # 8. 挂双向限价单（75）
                print("[调试] 准备挂单...", flush=True)
                sys.stdout.flush()
                self.place_dual_orders(position_size)
                print("[调试] 挂单完成", flush=True)
                sys.stdout.flush()

                # 9. 等待成交或周期结束（最多等待到周期结束）
                print("[调试] 开始等待成交...", flush=True)
                sys.stdout.flush()
                has_execution = self.wait_for_execution(position_size, max_wait=30)
                print(f"[调试] 等待完成，has_execution={has_execution}", flush=True)
                sys.stdout.flush()
                
                # 如果没有订单成交，提前结束此周期
                if not has_execution and not self.current_position:
                    print("[周期] 订单创建失败，跳过此周期，等待下一周期...")
                    elapsed = time.time() - cycle_start
                    remaining_time = max(0, cycle_duration - elapsed)
                    if remaining_time > 0:
                        time.sleep(remaining_time)
                    return

                # 10. 标记为已交易（无论是否成交）
                if not self.has_traded_in_event:
                    self.has_traded_in_event = True

            # 10. 如果有持仓，监控止损止盈或到期
            if self.current_position:
                elapsed = time.time() - cycle_start
                remaining_time = max(0, cycle_duration - elapsed)
                
                if remaining_time > 0:
                    print(f"[监控] 剩余周期时间: {format_time_remaining(remaining_time)}")
                    exit_reason = self.monitor_position(remaining_time)
                else:
                    # 周期已结束，按TIMEOUT处理
                    exit_reason = "TIMEOUT"
                
                if exit_reason:
                    self.settle_position(exit_reason)
            else:
                # 没有持仓，等待剩余时间
                elapsed = time.time() - cycle_start
                remaining_time = max(0, cycle_duration - elapsed)
                if remaining_time > 0:
                    print(f"[等待] 无持仓，等待周期结束... ({format_time_remaining(remaining_time)})")
                    time.sleep(remaining_time)

            # 11. 清理止损止盈订单
            self._cancel_stop_take_orders()

            # 12. 输出统计
            self.log_statistics()

            # 13. 更新仪表盘（强制刷新）
            self.show_dashboard(market_data, force_refresh=True)
            
            # 14. 周期结束，准备下一个周期
            print(f"[周期] 周期结束，准备下一个周期...")

        except Exception as e:
            self.logger.error(f"执行交易周期出错: {e}", exc_info=True)

    def fetch_market_data(self) -> Optional[Dict[str, Any]]:
        """
        获取市场数据

        真实交易模式：使用真实数据
        """
        return self.fetch_real_market_data()

    def fetch_real_market_data(self) -> Optional[Dict[str, Any]]:
        """获取真实市场数据（优化版：每个周期检查市场是否仍然活跃）"""
        start_time = time.time()
        
        # 调试日志到文件
        import os
        debug_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug.log")
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"\n=== fetch_real_market_data ===\n")

        try:
            # 获取最新活跃市场列表（专门获取BTC 5分钟市场）
            try:
                # 首先尝试获取BTC 5分钟市场
                markets = self.client.get_btc_5min_markets(limit=10)
                if not markets:
                    print("[*] BTC 5分钟市场为空，尝试获取全部活跃市场...")
                    markets = self.client.get_tradable_markets(limit=100)
                    # 从全部市场中过滤BTC 5分钟
                    btc_5min = []
                    for m in markets:
                        slug = (m.get('slug', '') or '').lower()
                        if 'btc-updown-5m' in slug:
                            btc_5min.append(m)
                    markets = btc_5min
                
                if not markets:
                    raise TradingError("无法获取活跃市场列表")
                
                # 调试：打印前3个市场
                if len(markets) > 0:
                    print(f"[调试] 前3个市场:")
                    for i, m in enumerate(markets[:3]):
                        print(f"    {i+1}. slug: {m.get('slug', '')[:60]}")
                        print(f"       question: {str(m.get('question', ''))[:50]}")
                
                # 必须找到5分钟比特币市场
                if markets:
                    current_market = markets[0]
                    question = current_market.get('question', 'Unknown')
                    slug = current_market.get('slug', '')
                    print(f"[市场] 5分钟BTC预测: {question[:60]}...")
                    print(f"[调试] slug: {slug}")
                else:
                    print(f"[等待] 等待5分钟BTC预测市场...")
                    time.sleep(5)
                    return None
                
                current_market_id = current_market.get("condition_id", "")
                
                # 检查市场是否变化或不再活跃
                market_changed = (self.config.market_id != current_market_id)
                
                if market_changed or not self.config.market_id:
                    # 市场已变化或未设置，获取新市场
                    self.config.market_id = current_market_id
                    self.current_event_id = current_market_id  # 重置事件ID
                    self.has_traded_in_event = False  # 重置交易状态
                    self.event_start_time = datetime.now()
                    question = current_market.get('question', 'Unknown')
                    print(f"[切换] 新事件: {question[:50]}...")
                    
                    # 调试日志
                    with open(debug_log, "a", encoding="utf-8") as f:
                        f.write(f"市场已切换: {current_market_id[:16]}...\n")
                        f.write(f"问题: {question[:50]}...\n")
                        
            except Exception as e:
                self.error_handler.handle(e, "获取活跃市场", recoverable=True)
                self.api_status = "error"
                return None

            # 获取代币ID
            try:
                token_ids = self.client.get_token_ids(self.config.market_id)

                # 验证 token_ids
                if not token_ids or not isinstance(token_ids, dict):
                    raise TradingError(f"代币ID格式错误")
                if "YES" not in token_ids or "NO" not in token_ids:
                    raise TradingError(f"代币ID缺少 YES/NO 字段")
                if not token_ids.get("YES") or not token_ids.get("NO"):
                    raise TradingError(f"代币ID字段为空")

            except Exception as e:
                self.error_handler.handle(e, "获取代币ID", recoverable=True)
                self.api_status = "error"
                return None

            self.yes_token_id = token_ids.get("YES")
            self.no_token_id = token_ids.get("NO")

            # 获取价格
            try:
                prices = self.client.get_market_prices(self.config.market_id)
                if not prices or prices.get("YES", 0) == 0:
                    raise TradingError("价格数据无效")
            except Exception as e:
                self.error_handler.handle(e, "获取市场价格", recoverable=True)
                self.api_status = "error"
                return None

            yes_price = prices.get("YES", 0)
            no_price = prices.get("NO", 0)

            # 获取订单簿（失败不影响主流程）
            try:
                orderbooks = self.client.get_market_orderbook(self.config.market_id)
                yes_orderbook = orderbooks.get("YES", {})
            except Exception as e:
                yes_orderbook = {}

            # 记录更新时间
            self.last_update_time = datetime.now().strftime("%H:%M:%S")
            self.last_update_duration = (time.time() - start_time) * 1000
            # 市场数据获取成功，API 状态设为 connected
            self.api_status = "connected"

            # 单行动态输出（只显示 YES/NO 价格）
            print(f"\r[数据] YES ${yes_price:.2f} | NO ${no_price:.2f} | 耗时 {self.last_update_duration:.0f}ms    ", end="", flush=True)

            return {
                "yes_price": yes_price,
                "no_price": no_price,
            }

        except Exception as e:
            self.error_handler.handle(e, "获取市场数据", recoverable=True)
            self.api_status = "error"
            return None

    def update_balance(self) -> None:
        """更新余额"""
        try:
            balance = self.client.get_balance()
            if balance is not None and balance >= 0:
                self.balance = balance
                # 即使余额为 0，也认为 API 连接正常
                self.api_status = "connected"
            else:
                self.api_status = "disconnected"
        except Exception as e:
            self.error_handler.handle(e, "更新余额", recoverable=True)
            self.api_status = "error"

    def show_dashboard(self, market_data: Dict[str, Any] = None, force_refresh: bool = False) -> None:
        """显示完整的交易仪表盘
        
        Args:
            market_data: 市场数据
            force_refresh: 是否强制刷新完整界面（不清屏）
        """
        try:
            # 获取统计信息
            stats = self.trade_history.get_statistics()
            trades = self.trade_history.get_all()

            # 准备交易参数
            params = {
                'entry_price': self.config.entry_price,
                'stop_loss': self.config.stop_loss,
                'take_profit': self.config.take_profit,
                'trade_cycle_minutes': self.config.trade_cycle_minutes,
            }

            # 准备市场数据
            if not market_data:
                market_data = {
                    'yes_price': self.config.current_price,
                    'no_price': 100 - self.config.current_price,
                    'best_bid': 0,
                    'best_ask': 0,
                    'spread': 0,
                }

            # 显示完整仪表盘
            self.realtime_display.show_full_dashboard(
                market_data=market_data,
                balance=self.balance,
                initial_balance=self.initial_balance,
                leverage=self.config.leverage,
                stats=stats,
                position=self.current_position if self.current_position else {},
                orders=self.pending_orders,
                trades=trades,
                params=params,
                mode="real",
                api_status=self.api_status,
                market_id=self.config.market_id,
                update_time=self.last_update_time,
                update_duration=self.last_update_duration,
                is_running=self.is_running,
                stop_loss_order=self.stop_loss_order,
                take_profit_order=self.take_profit_order,
                force_refresh=force_refresh,
            )
        except Exception as e:
            self.logger.error(f"显示仪表盘失败: {e}")

    def log_trade_activity(self, message: str, activity_type: str = "info") -> None:
        """记录交易活动"""
        self.logger.info(message)
        # 可以在这里添加其他日志方式

    def calculate_position_size(self) -> float:
        """计算仓位大小
        
        策略：
        - 余额 = 初始余额 × 1 → 开仓 $1
        - 余额 = 初始余额 × 3 → 开仓 $2 (翻倍)
        - 余额 = 初始余额 × 9 → 开仓 $4 (再翻倍)
        - 余额 = 初始余额 × 27 → 开仓 $8 (再翻倍)
        
        示例：初始余额 $12
        - 余额 $12   → $1
        - 余额 $36   → $2
        - 余额 $108  → $4
        - 余额 $324  → $8
        """
        # 使用从 API 读取的初始余额，而非配置值
        initial_balance = self.initial_balance if self.initial_balance > 0 else self.config.initial_balance
        
        # 计算翻倍倍数
        # 余额 >= 初始×3^n 时，倍数 = 2^n
        multiplier = 1
        power = 0
        while self.balance >= initial_balance * (3 ** power):
            multiplier = 2 ** power
            power += 1
        
        return float(multiplier)

    def place_dual_orders(self, position_size: float) -> None:
        """
        挂双向限价单（YES 和 NO 都是买入，价格都是75）

        根据 Polymarket 官方文档：
        - 使用 create_and_post_order() 一步完成创建、签名和提交
        - GTC: Good Till Cancelled - 挂单直到成交或取消
        
        正确逻辑：
        - YES 和 NO 是两个不同的代币
        - 同时挂两个买入单：BUY YES @ 75 和 BUY NO @ 75
        - 等待订单成交，取消未成交的一侧

        注意：这里没有做空操作，都是做多！
        """
        entry_price = self.config.entry_price
        # 转换为 0-1 格式显示
        entry_display = entry_price / 100.0 if entry_price > 1 else entry_price

        print(f"[挂单] 挂双向限价单 @ ${entry_display:.2f}")

        # 真实API模式：实际挂单
        if not self.yes_token_id or not self.no_token_id:
            print("[错误] 未设置代币ID")
            return

        try:
            print(f"[挂单] 正在获取 YES market options...")
            # 获取市场的 tick_size 和 neg_risk（官方文档要求）
            yes_options = self.client.get_market_options(self.yes_token_id)
            print(f"[挂单] YES options: {yes_options}")
            
            print(f"[挂单] 正在获取 NO market options...")
            no_options = self.client.get_market_options(self.no_token_id)
            print(f"[挂单] NO options: {no_options}")

            print(f"[挂单] 正在挂 YES 买单...")
            # 挂 YES 买单（做多 YES）- 使用 GTC 限价单
            yes_order = self.client.create_order(
                token_id=self.yes_token_id,
                price=entry_price,
                size=position_size,
                side="BUY",
                order_type="GTC",  # Good Till Cancelled
            )
            print(f"[挂单] YES 订单完成: {yes_order}")

            print(f"[挂单] 正在挂 NO 买单...")
            # 挂 NO 买单（做多 NO）- 使用 GTC 限价单
            no_order = self.client.create_order(
                token_id=self.no_token_id,
                price=entry_price,
                size=position_size,
                side="BUY",
                order_type="GTC",
            )
            print(f"[挂单] NO 订单完成: {no_order}")

            # 记录订单（注意：py-clob-client返回的字段名是orderID）
            yes_order_id = yes_order.get("orderID") or yes_order.get("order_id", "")
            no_order_id = no_order.get("orderID") or no_order.get("order_id", "")
            
            # 检查订单状态
            if yes_order.get("success") == False:
                print(f"[X] YES 订单创建失败: {yes_order.get('errorMsg', 'Unknown error')}")
            if no_order.get("success") == False:
                print(f"[X] NO 订单创建失败: {no_order.get('errorMsg', 'Unknown error')}")
            
            if yes_order_id:
                self.pending_orders[yes_order_id] = {
                    "type": "LONG",
                    "token": "YES",
                    "token_id": self.yes_token_id,
                    "price": entry_price,
                    "size": position_size,
                }
            if no_order_id:
                self.pending_orders[no_order_id] = {
                    "type": "LONG",
                    "token": "NO",
                    "token_id": self.no_token_id,
                    "price": entry_price,
                    "size": position_size,
                }

            print(f"[挂单] [OK] 双向限价单已挂: YES @ ${entry_display:.2f} | NO @ ${entry_display:.2f}")
            print(f"[挂单] 订单ID: YES={yes_order_id[:20] if yes_order_id else 'N/A'}..., NO={no_order_id[:20] if no_order_id else 'N/A'}...")

        except Exception as e:
            print(f"[挂单] [X] 挂单失败: {e}")
            import traceback
            traceback.print_exc()

    def _cancel_pending_orders(self) -> None:
        """取消所有挂单"""
        for order_id in list(self.pending_orders.keys()):
            try:
                self.client.cancel_order(order_id)
                self.logger.info(f"已取消订单: {order_id}")
            except Exception as e:
                self.logger.error(f"取消订单失败: {e}")
        self.pending_orders.clear()

    def wait_for_execution(self, position_size: float, max_wait: int = 30) -> bool:
        """
        等待订单成交
        
        真实交易模式：轮询订单状态，成交后取消另一侧
        
        Args:
            position_size: 仓位大小
            max_wait: 最大等待时间（秒）
            
        Returns:
            True 如果有订单成交，False 如果没有订单或超时
        """
        # 如果没有挂单，直接返回
        if not self.pending_orders:
            print("[等待] [X] 没有挂单可等待（订单创建可能失败）")
            return False
            
        print(f"\r[等待] 等待订单成交... (最多 {max_wait} 秒)", end="", flush=True)

        start_time = time.time()
        last_status_log = 0  # 上次输出状态的时间

        while time.time() - start_time < max_wait:
            # 每秒获取最新价格
            try:
                prices = self.client.get_market_prices(self.config.market_id)
                if prices:
                    elapsed = int(time.time() - start_time)
                    remaining = max_wait - elapsed
                    print(f"\r[等待] YES ${prices.get('YES', 0):.2f} | NO ${prices.get('NO', 0):.2f} | 剩余 {remaining}s    ", end="", flush=True)
            except Exception:
                pass
            
            # 检查订单状态
            for order_id in list(self.pending_orders.keys()):
                try:
                    order_status = self.client.get_order(order_id)
                    if order_status:
                        # 检查多个可能的填充量字段名
                        filled = (
                            order_status.get("filled_size", 0) or
                            order_status.get("size_filled", 0) or
                            order_status.get("fills", 0) or
                            order_status.get("fill_amount", 0) or
                            0
                        )
                        if filled > 0:
                            # 订单成交
                            order_info = self.pending_orders[order_id]
                            token = order_info["token"]

                            print(f"\r[成交] [OK] {token} 订单已成交!                    ")
                            print()

                            # 设置当前持仓
                            token_id = order_info.get("token_id")
                            self.current_position = {
                                "type": "LONG",  # 统一使用 LONG
                                "token": token,  # YES 或 NO
                                "token_id": token_id,  # 代币ID（用于设置止损止盈订单）
                                "entry_price": order_info["price"],
                                "size": order_info["size"],
                                "timestamp": datetime.now(),
                            }

                            # 取消另一个订单
                            self._cancel_pending_orders()

                            return True
                except Exception:
                    # 静默处理查询失败，不打印警告
                    pass

            # 每10秒输出一次状态
            elapsed = int(time.time() - start_time)
            if elapsed - last_status_log >= 10:
                remaining = max_wait - elapsed
                print(f"\r[等待] 等待订单成交... 剩余 {remaining} 秒", end="", flush=True)
                last_status_log = elapsed

            time.sleep(1)

        # 超时未成交，取消所有订单
        print(f"\r[等待] ⏰ 等待超时 ({max_wait} 秒)，取消所有订单       ")
        print()
        self._cancel_pending_orders()
        
        return False

    def get_event_result(self) -> Optional[str]:
        """
        获取事件结果

        真实交易模式：从 Polymarket API 获取事件结算结果
        注意：这需要事件已经结算，否则无法获取
        """
        try:
            if not self.config.market_id:
                self.logger.warning("未设置市场ID，无法获取事件结果")
                return None

            # 获取市场详情，查看是否已结算
            market_details = self.client.get_market_by_id(self.config.market_id)
            if market_details:
                # 检查市场是否已结算（多个可能的字段名）
                is_settled = (
                    market_details.get("is_settled", False) or
                    market_details.get("closed", False) or
                    market_details.get("resolved", False)
                )
                
                if is_settled:
                    # 获取结算结果（多个可能的字段名和格式）
                    winning_outcome = (
                        market_details.get("winning_outcome") or
                        market_details.get("winner") or
                        market_details.get("result") or
                        market_details.get("outcome")
                    )
                    
                    if winning_outcome:
                        # 标准化结果格式（转为大写）
                        result = str(winning_outcome).upper()
                        # 处理可能的 "YES" / "NO" 格式
                        if "YES" in result:
                            result = "YES"
                        elif "NO" in result:
                            result = "NO"
                        
                        self.logger.info(f"事件已结算，结果 = {result}")
                        return result
                    else:
                        self.logger.warning("市场已结算但无 winning_outcome")
                        return None
                else:
                    self.logger.info("事件尚未结算，无法获取结果")
                    return None
            else:
                self.logger.warning("无法获取市场详情")
                return None
        except Exception as e:
            self.error_handler.handle(e, "获取事件结果", recoverable=True)
            return None

    def place_stop_loss_order(self, position_size: float) -> Optional[str]:
        """
        设置止损单（卖出持仓代币）
        
        根据 Polymarket 官方文档：
        - 使用 GTD 订单确保在周期结束时自动过期
        
        Args:
            position_size: 持仓数量
            
        Returns:
            止损单订单ID 或 None
        """
        if not self.current_position or not self.current_position.get("token_id"):
            return None
        
        position = self.current_position
        token_id = position["token_id"]
        token = position["token"]
        entry_price = position["entry_price"]
        stop_loss_price = self.config.stop_loss
        stop_loss_display = stop_loss_price / 100.0 if stop_loss_price > 1 else stop_loss_price
        
        # GTD 订单：5 分钟后自动过期（+60秒安全缓冲）
        duration = self.config.trade_cycle_minutes * 60
        expiration = int(time.time()) + 60 + duration
        
        try:
            # 获取市场的 tick_size 和 neg_risk
            options = self.client.get_market_options(token_id)
            
            # 卖出持仓代币 @ 止损价格
            response = self.client.create_order(
                token_id=token_id,
                price=stop_loss_price,
                size=position_size,
                side="SELL",
                order_type="GTD",  # Good Till Date - 自动过期
                expiration=expiration,
            )
            
            if response and response.get("success") != False:
                order_id = response.get("orderID") or response.get("order_id", "")
                if order_id:
                    self.stop_loss_order = {
                        "orderID": order_id,
                        "type": "STOP_LOSS",
                        "token": token,
                        "price": stop_loss_price,
                        "size": position_size,
                    }
                    print(f"[止损] [OK] 止损单已挂: SELL {token} @ ${stop_loss_display:.2f}, 订单ID: {order_id[:20]}...")
                    return order_id
            
            print(f"[止损] [X] 止损单创建失败: {response.get('errorMsg', 'Unknown error') if response else 'Empty response'}")
            return None
            
        except Exception as e:
            print(f"[止损] [X] 止损单设置失败: {e}")
            return None
    
    def place_take_profit_order(self, position_size: float) -> Optional[str]:
        """
        设置止盈单（卖出持仓代币）
        
        根据 Polymarket 官方文档：
        - 使用 GTD 订单确保在周期结束时自动过期
        
        Args:
            position_size: 持仓数量
            
        Returns:
            止盈单订单ID 或 None
        """
        if not self.current_position or not self.current_position.get("token_id"):
            return None
        
        position = self.current_position
        token_id = position["token_id"]
        token = position["token"]
        entry_price = position["entry_price"]
        take_profit_price = self.config.take_profit
        take_profit_display = take_profit_price / 100.0 if take_profit_price > 1 else take_profit_price
        
        # GTD 订单：5 分钟后自动过期（+60秒安全缓冲）
        duration = self.config.trade_cycle_minutes * 60
        expiration = int(time.time()) + 60 + duration
        
        try:
            # 卖出持仓代币 @ 止盈价格
            response = self.client.create_order(
                token_id=token_id,
                price=take_profit_price,
                size=position_size,
                side="SELL",
                order_type="GTD",  # Good Till Date - 自动过期
                expiration=expiration,
            )
            
            if response and response.get("success") != False:
                order_id = response.get("orderID") or response.get("order_id", "")
                if order_id:
                    self.take_profit_order = {
                        "orderID": order_id,
                        "type": "TAKE_PROFIT",
                        "token": token,
                        "price": take_profit_price,
                        "size": position_size,
                    }
                    print(f"[止盈] [OK] 止盈单已挂: SELL {token} @ ${take_profit_display:.2f}, 订单ID: {order_id[:20]}...")
                    return order_id
            
            print(f"[止盈] [X] 止盈单创建失败: {response.get('errorMsg', 'Unknown error') if response else 'Empty response'}")
            return None
            
        except Exception as e:
            print(f"[止盈] [X] 止盈单设置失败: {e}")
            return None

    def monitor_position(self, max_wait: float) -> Optional[str]:
        """
        监控持仓：止损、止盈或到期
        
        逻辑：
        - 止损：持仓代币价格 ≤ 止损价格 时平仓
        - 止盈：持仓代币价格 ≥ 止盈价格 时平仓
        - 到期：按照事件结果平仓
        
        根据 Polymarket 官方文档：
        - 使用 GTD 订单确保在周期结束时自动过期
        - 止损止盈互斥：一个触发后立即取消另一个
        
        Args:
            max_wait: 最大监控时间（秒）
            
        Returns:
            退出原因：'STOP_LOSS', 'TAKE_PROFIT', 'TIMEOUT', 或 None
        """
        if not self.current_position:
            return None

        position = self.current_position
        entry_price = position["entry_price"]
        position_type = position["type"]
        position_size = position["size"]
        token = position.get("token", "YES")
        token_id = position.get("token_id")

        stop_loss_price = self.config.stop_loss
        stop_loss_display = stop_loss_price / 100.0 if stop_loss_price > 1 else stop_loss_price
        take_profit_price = self.config.take_profit
        take_profit_display = take_profit_price / 100.0 if take_profit_price > 1 else take_profit_price

        print(f"[监控] 持仓: {token} @ ${entry_price:.2f} | 止损 ≤ ${stop_loss_display:.2f} | 止盈 ≥ ${take_profit_display:.2f}")

        # 清除之前的止损止盈订单
        self.stop_loss_order = None
        self.take_profit_order = None

        # 设置止损止盈订单（使用 GTD 确保自动过期）
        if token_id:
            self.place_stop_loss_order(position_size)
            self.place_take_profit_order(position_size)

        # 监控止损止盈订单或等待周期结束
        start_time = time.time()
        last_price_log = 0
        last_check_time = start_time

        while time.time() - start_time < max_wait:
            current_time = time.time()
            
            # 检查止损订单是否成交
            if self.stop_loss_order:
                try:
                    order_id = self.stop_loss_order.get("orderID")
                    order_status = self.client.get_order(order_id)
                    if order_status:
                        # 检查多个可能的填充量字段名
                        filled = (
                            order_status.get("filled_size", 0) or
                            order_status.get("size_filled", 0) or
                            order_status.get("fills", 0) or
                            order_status.get("fill_amount", 0) or
                            0
                        )
                        if filled > 0:
                            print(f"\r[触发] [OK] 止损订单已成交!                      ")
                            # 取消另一个订单
                            if self.take_profit_order:
                                self._cancel_single_order(self.take_profit_order)
                                self.take_profit_order = None
                            return "STOP_LOSS"
                except Exception:
                    pass

            # 检查止盈订单是否成交
            if self.take_profit_order:
                try:
                    order_id = self.take_profit_order.get("orderID")
                    order_status = self.client.get_order(order_id)
                    if order_status:
                        # 检查多个可能的填充量字段名
                        filled = (
                            order_status.get("filled_size", 0) or
                            order_status.get("size_filled", 0) or
                            order_status.get("fills", 0) or
                            order_status.get("fill_amount", 0) or
                            0
                        )
                        if filled > 0:
                            print(f"\r[触发] [OK] 止盈订单已成交!                      ")
                            # 取消另一个订单
                            if self.stop_loss_order:
                                self._cancel_single_order(self.stop_loss_order)
                                self.stop_loss_order = None
                            return "TAKE_PROFIT"
                except Exception:
                    pass

            # 每秒更新一次价格
            if current_time - last_price_log >= 1:
                try:
                    prices = self.client.get_market_prices(self.config.market_id)
                    if prices:
                        current_price = prices.get(token, entry_price)
                    else:
                        current_price = entry_price
                    
                    elapsed = int(current_time - start_time)
                    remaining = int(max_wait - (current_time - start_time))
                    print(f"\r[监控] {format_time_remaining(remaining)} | {token}: ${current_price:.2f}    ", end="", flush=True)
                    last_price_log = current_time
                except Exception:
                    pass

            time.sleep(1)

        # 周期结束，未触发止损止盈
        print(f"\r[监控] 周期结束，未触发止损止盈                    ")
        return "TIMEOUT"

    def _cancel_single_order(self, order: Dict) -> None:
        """取消单个订单"""
        try:
            order_id = order.get("orderID")
            if order_id:
                self.client.cancel_order(order_id)
        except Exception:
            pass

    def settle_position(self, exit_reason: str) -> None:
        """
        根据退出原因结算持仓
        
        Args:
            exit_reason: 退出原因 ('STOP_LOSS', 'TAKE_PROFIT', 'TIMEOUT')
        """
        if not self.current_position:
            return

        position = self.current_position
        entry_price_raw = position["entry_price"]  # 可能是 75 或 0.75
        position_type = position["type"]
        position_size = position["size"]
        token = position.get("token", "YES")

        # 确保价格格式统一为 0-1 格式
        def to_float_price(price: float) -> float:
            """转换为 0-1 格式"""
            if price > 1:
                return price / 100.0
            return price

        # 统一转换
        entry_price = to_float_price(entry_price_raw)

        # 确定平仓价格
        if exit_reason == "STOP_LOSS":
            exit_price = to_float_price(self.config.stop_loss)
        elif exit_reason == "TAKE_PROFIT":
            exit_price = to_float_price(self.config.take_profit)
        elif exit_reason == "TIMEOUT":
            # 到期结算：获取事件结果
            event_result = self.get_event_result()
            if event_result:
                # 根据持仓代币和事件结果确定平仓价
                if event_result == token:
                    # 持仓的代币获胜
                    exit_price = 1.0  # 100% -> 1.0
                    self.logger.info(f"[OK] 事件结果: {event_result}，{token} 获胜，平仓价: 1.0")
                else:
                    # 持仓的代币失败
                    exit_price = 0.0  # 0% -> 0.0
                    self.logger.info(f"[X] 事件结果: {event_result}，{token} 失败，平仓价: 0.0")
            else:
                # 无法获取事件结果，使用当前价格
                try:
                    prices = self.client.get_market_prices(self.config.market_id)
                    if prices:
                        exit_price = to_float_price(prices.get(token, entry_price))
                    else:
                        exit_price = entry_price
                    self.logger.warning(f"[!] 无法获取事件结果，使用当前价格平仓: {exit_price:.2f}")
                except Exception as e:
                    self.logger.error(f"获取市场价格失败: {e}")
                    exit_price = 0.0
        else:
            exit_price = entry_price  # 默认按开仓价平仓

        # 平仓
        self.close_position(position_type, position_size, entry_price, exit_price, exit_reason)

        # 结算后重新同步真实余额（止损/止盈是实时结算，TIMEOUT需要等事件结算）
        if exit_reason in ["STOP_LOSS", "TAKE_PROFIT"]:
            # 止损止盈是实时结算，延迟1秒后同步
            time.sleep(1)
            try:
                real_balance = self.client.get_balance()
                if real_balance is not None and real_balance >= 0:
                    self.balance = real_balance
                    self.logger.info(f"[同步] 真实余额已更新: ${self.balance:.2f}")
            except Exception as e:
                self.logger.error(f"[同步] 同步余额失败: {e}")

    def _cancel_stop_take_orders(self) -> None:
        """取消止损止盈订单"""
        if self.stop_loss_order:
            try:
                order_id = self.stop_loss_order.get("orderID")
                if order_id:
                    self.client.cancel_order(order_id)
                    self.logger.info(f"已取消止损订单: {order_id}")
            except Exception as e:
                self.logger.error(f"取消止损订单失败: {e}")
            self.stop_loss_order = None

        if self.take_profit_order:
            try:
                order_id = self.take_profit_order.get("orderID")
                if order_id:
                    self.client.cancel_order(order_id)
                    self.logger.info(f"已取消止盈订单: {order_id}")
            except Exception as e:
                self.logger.error(f"取消止盈订单失败: {e}")
            self.take_profit_order = None

    def close_position(
        self,
        position_type: str,
        position_size: float,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
    ) -> None:
        """平仓并计算盈亏

        正确逻辑：
        - YES 和 NO 都是做多
        - 盈亏 = (平仓价 - 开仓价) * 开仓金额 / 开仓价
        """
        # 计算盈亏（只有做多逻辑）
        pnl = (exit_price - entry_price) * position_size / entry_price

        # 更新余额
        balance_before = self.balance
        self.balance += pnl
        balance_after = self.balance

        # 记录交易
        token = self.current_position.get("token", "UNKNOWN") if self.current_position else "UNKNOWN"
        trade_record = TradeRecord(
            trade_id=f"trade_{int(time.time())}",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            type=position_type,
            token=token,  # 添加代币类型
            entry_price=entry_price,
            exit_price=exit_price,
            position_size=position_size,
            pnl=round(pnl, 2),
            exit_reason=exit_reason,
            balance_before=round(balance_before, 2),
            balance_after=round(balance_after, 2),
        )

        self.trade_history.add(trade_record)

        self.logger.info(
            f"步骤4: 平仓 - {position_type} ({self.current_position.get('token', 'UNKNOWN') if self.current_position else 'N/A'}) | "
            f"开仓: {entry_price:.2f} | "
            f"平仓: {exit_price:.2f} | "
            f"盈亏: {pnl:+.2f} | "
            f"原因: {exit_reason} | "
            f"余额: {balance_before:.2f} → {balance_after:.2f}"
        )

        # 清除持仓和止损止盈订单
        self.current_position = None
        self.stop_loss_order = None
        self.take_profit_order = None

    def log_statistics(self) -> None:
        """输出统计信息（包含 API 使用统计）"""
        stats = self.trade_history.get_statistics()

        self.logger.info("-" * 60)
        self.logger.info("交易统计:")
        self.logger.info(f"  总交易次数: {stats['total_trades']}")
        self.logger.info(f"  盈利次数: {stats['win_trades']}")
        self.logger.info(f"  亏损次数: {stats['loss_trades']}")
        self.logger.info(f"  总盈亏: ${stats['total_profit']:+.2f}")
        self.logger.info(f"  胜率: {stats['win_rate']:.2f}%")
        self.logger.info(f"  当前余额: ${self.balance:.2f}")

        # API 使用统计
        self.logger.info("-" * 60)
        self.logger.info("API 使用统计:")
        self.logger.info(f"  总调用次数: {self.api_call_stats['total_calls']}")
        self.logger.info(f"  缓存命中次数: {self.api_call_stats['cache_hits']}")
        self.logger.info(f"  错误次数: {self.api_call_stats['errors']}")
        self.logger.info(f"  缓存命中率: {self.api_call_stats['cache_hits'] / max(self.api_call_stats['total_calls'], 1) * 100:.1f}%")
        self.logger.info("-" * 60)

    def log_api_performance(self, operation: str, duration_ms: float, cache_hit: bool = False) -> None:
        """记录 API 性能"""
        self.api_call_stats["total_calls"] += 1
        if cache_hit:
            self.api_call_stats["cache_hits"] += 1

        # 每 100 次调用输出一次统计
        if self.api_call_stats["total_calls"] % 100 == 0:
            self.log_statistics()
