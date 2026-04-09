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
from typing import Optional, Dict, List, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        
        # 价格监控标志（等待价格涨到目标价）
        self.waiting_for_entry: bool = False
        self.entry_target_price: float = 0.0
        self.entry_position_size: float = 0.0

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

        # 【优化】启动时提前确认交易参数（避免周期内延迟）
        if not self.first_order_confirmed:
            self._startup_confirmation()

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
    
    def _startup_confirmation(self) -> None:
        """启动时确认交易参数（只确认一次）"""
        print("\n" + "=" * 60)
        print("[!] 启动确认 - 交易参数 [!]")
        print("=" * 60)
        
        # 转换配置价格为 0-1 格式显示
        entry_display = self.config.entry_price / 100.0 if self.config.entry_price > 1 else self.config.entry_price
        stop_loss_display = self.config.stop_loss / 100.0 if self.config.stop_loss > 1 else self.config.stop_loss
        take_profit_display = self.config.take_profit / 100.0 if self.config.take_profit > 1 else self.config.take_profit
        
        print(f"  当前余额:     ${self.balance:.2f}")
        print(f"  配置余额:     ${self.config_initial_balance:.2f}")
        print(f"  开仓价格:     ${entry_display:.2f} (YES和NO同时挂单)")
        print(f"  止损价格:     ${stop_loss_display:.2f}")
        print(f"  止盈价格:     ${take_profit_display:.2f}")
        print(f"  交易周期:     {self.config.trade_cycle_minutes} 分钟")
        print("=" * 60)
        print()
        print("  即将开始自动交易:")
        print("  - 每5分钟自动挂双向限价单（YES和NO各一单）")
        print("  - 一侧成交后立即取消另一侧")
        print("  - 成交后立即设置止损止盈")
        print("  - 止损或止盈触发后自动平仓")
        print()
        print("  [y/Y] 确认开始自动交易")
        print("  [n/N] 退出程序")
        print()
        
        while True:
            try:
                print("请输入 (y/n): ", end="", flush=True)
                sys.stdout.flush()
                try:
                    user_input = input()
                except EOFError:
                    time.sleep(0.1)
                    user_input = sys.stdin.readline()
                user_input = user_input.strip().lower()
                if not user_input:
                    print("  无效输入，请输入 y 或 n", flush=True)
                    continue
                if user_input == 'y':
                    self.first_order_confirmed = True
                    print("\n[OK] 已确认，开始自动交易！\n", flush=True)
                    sys.stdout.flush()
                    return
                elif user_input == 'n':
                    print("\n[退出] 退出程序...", flush=True)
                    sys.stdout.flush()
                    self.stop()
                    sys.exit(0)
                else:
                    print("  无效输入，请输入 y 或 n", flush=True)
            except (KeyboardInterrupt, EOFError):
                print("\n[退出] 退出程序...", flush=True)
                sys.stdout.flush()
                self.stop()
                sys.exit(0)

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
        
        # 使用 market_id 获取市场详情（比 token_id 更可靠）
        market_info = None
        market_question = "Unknown"
        market_slug = "Unknown"
        condition_id = self.config.market_id if self.config.market_id else "Unknown"
        
        print(f"[诊断] 确认页 - market_id: {self.config.market_id[:30] if self.config.market_id else 'None'}...")
        print(f"[诊断] 确认页 - market_data: {market_data}")
        
        if self.config.market_id:
            try:
                print(f"[诊断] 正在通过 API 获取市场详情...")
                market_info = self.client.get_market_by_id(self.config.market_id)
                print(f"[诊断] API 返回: {type(market_info)}")
                if market_info:
                    market_question = market_info.get("question", "Unknown")
                    market_slug = market_info.get("slug", "Unknown")
                    print(f"[诊断] question: {market_question[:50]}...")
                    print(f"[诊断] slug: {market_slug}")
                else:
                    print(f"[诊断] market_info 为空")
            except Exception as e:
                print(f"[诊断] 获取市场详情失败: {e}")
        
        print("\n" + "=" * 60)
        print("[!]  首次下单确认  [!]")
        print("=" * 60)
        print(f"  市场问题:     {market_question[:50]}..." if len(market_question) > 50 else f"  市场问题:     {market_question}")
        print(f"  市场Slug:     {market_slug}")
        print(f"  Condition ID: {condition_id[:30]}..." if len(condition_id) > 30 else f"  Condition ID: {condition_id}")
        print(f"  YES Token:    {self.yes_token_id[:20]}..." if self.yes_token_id else "  YES Token:    N/A")
        print(f"  NO Token:     {self.no_token_id[:20]}..." if self.no_token_id else "  NO Token:     N/A")
        print("-" * 60)
        print(f"  当前余额:     ${self.balance:.2f}")
        print(f"  开仓金额:     ${position_size:.2f}")
        print(f"  开仓价格:     ${entry_display:.2f}")
        print(f"  止损价格:     ${stop_loss_display:.2f}")
        print(f"  止盈价格:     ${take_profit_display:.2f}")
        print(f"  YES 当前价:   ${market_data.get('yes_price', 0):.2f}")
        print(f"  NO 当前价:    ${market_data.get('no_price', 0):.2f}")
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

    def _wait_next_cycle(self) -> None:
        """等待到下一个5分钟周期边界"""
        from datetime import datetime, timezone, timedelta
        
        edt = timezone(timedelta(hours=-4))
        now_edt = datetime.now(edt)
        minute = now_edt.minute
        second = now_edt.second
        
        # 计算到下一个5分钟边界的时间
        current_period_minute = (minute // 5) * 5
        next_period_minute = current_period_minute + 5
        
        if next_period_minute >= 60:
            # 跨小时
            next_period = now_edt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        else:
            next_period = now_edt.replace(minute=next_period_minute, second=0, microsecond=0)
        
        wait_seconds = (next_period - now_edt).total_seconds()
        
        print(f"[等待] 等待到下一个周期边界: {next_period.strftime('%H:%M:%S')} (等待{int(wait_seconds)}秒)")
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def execute_trade_cycle(self) -> None:
        """执行一个完整的交易周期（5分钟）"""
        print("\n" + "=" * 60)
        print("[周期] 新周期开始")
        print("=" * 60)

        # 安全检查：清除之前的挂单（如果有）
        if self.pending_orders:
            print(f"[安全] 清除上周期残留的 {len(self.pending_orders)} 个挂单")
            self._cancel_pending_orders()
        
        # 安全检查：如果还有持仓，先平仓（可能是上周期异常）
        if self.current_position:
            print(f"[安全] 检测到残留持仓: {self.current_position.get('token', 'Unknown')}")
            # 获取当前价格平仓
            try:
                prices = self.client.get_market_prices(self.config.market_id)
                if prices:
                    token = self.current_position.get("token", "YES")
                    exit_price = prices.get(token, 0.5)
                    self.close_position(
                        self.current_position["type"],
                        self.current_position["size"],
                        self.current_position["entry_price"],
                        exit_price,
                        "FORCED_CLOSE"  # 强制平仓
                    )
                    print(f"[安全] 已强制平仓残留持仓")
            except Exception as e:
                print(f"[错误] 强制平仓失败: {e}")

        self.cycle_start_time = datetime.now()
        cycle_start = time.time()
        retry_count = 0
        max_retries = 3

        try:
            # 1. 【优化】快速获取市场数据（单次尝试，失败则跳过此周期）
            print("[快速] 获取市场数据...")
            market_data = self.fetch_market_data()
            
            if not market_data:
                print("[等待] 无法获取市场数据，等待下次周期...")
                # 等待到下一个5分钟边界
                self._wait_next_cycle()
                return
            
            # 【关键修复】使用事件的实际剩余时间
            event_remaining = market_data.get("remaining_seconds", 300)
            cycle_duration = int(event_remaining)
            
            print(f"[*] 事件剩余时间: {cycle_duration} 秒 ({cycle_duration/60:.1f} 分钟)")
            
            # 如果剩余时间少于30秒，跳过此周期
            if cycle_duration < 30:
                print(f"[跳过] 事件即将结束（剩余{cycle_duration}秒），等待下一周期...")
                self._wait_next_cycle()
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
                # 4. 【优化】并行获取余额和计算仓位（减少延迟）
                self.update_balance()
                position_size = self.calculate_position_size()
                print(f"[挂单] 开仓金额: ${position_size:.2f}")

                # 5. 检查价格是否达到目标，或开始监控
                print("[监控] 检查价格是否达到目标...", flush=True)
                sys.stdout.flush()
                self.place_dual_orders(position_size)

                # 计算周期剩余时间
                elapsed = time.time() - cycle_start
                remaining_time = max(0, cycle_duration - elapsed)
                
                # 6. 如果等待价格触发，进入监控阶段
                if self.waiting_for_entry and not self.current_position:
                    print(f"[监控] 进入价格监控阶段...")
                    has_execution = self._monitor_price_for_entry(position_size, max_wait=int(remaining_time))
                elif self.current_position:
                    # 已经买入（价格已经达到目标）
                    has_execution = True
                else:
                    has_execution = False

                # 如果没有买入，跳过此周期
                if not has_execution and not self.current_position:
                    print("[周期] 价格未触发，跳过此周期...")
                    
                    # 等待剩余时间（如果有）
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
        """获取真实市场数据（简化版）"""
        import traceback
        start_time = time.time()
        
        print("[诊断] >>> 进入 fetch_real_market_data")
        
        try:
            # 步骤1: 计算当前 5分钟周期的 slug
            print("[诊断] 步骤1: 计算 BTC 5分钟市场 slug...")
            from datetime import datetime, timezone, timedelta
            
            # 美东时区 (EDT in April = UTC-4)
            edt = timezone(timedelta(hours=-4))
            now_edt = datetime.now(edt)
            
            # 计算当前 5分钟周期的开始时间（向下取整）
            minute = now_edt.minute
            current_period_minute = (minute // 5) * 5
            current_period_start = now_edt.replace(minute=current_period_minute, second=0, microsecond=0)
            
            # 转换为 Unix 时间戳
            current_period_ts = int(current_period_start.timestamp())
            
            # 生成 slug
            current_slug = f"btc-updown-5m-{current_period_ts}"
            
            print(f"[*] 美东时间: {now_edt.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"[*] 当前5分钟周期: {current_period_start.strftime('%Y-%m-%d %H:%M')} (时间戳: {current_period_ts})")
            print(f"[*] Slug: {current_slug}")
            
            # 步骤2: 直接通过 slug 获取市场
            print(f"[诊断] 步骤2: 通过 slug 获取市场...")
            market = self.client.get_market_by_slug(current_slug)
            
            if not market:
                print(f"[*] 通过 slug 未找到，尝试获取列表...")
                # 步骤3: 从列表中搜索
                markets = self.client.get_tradable_markets(limit=200)
                
                if not markets:
                    print("[错误] 无法获取市场列表")
                    return None
                
                print(f"[*] 搜索 btc-updown-5m 市场...")
                
                # 搜索 btc-updown-5m
                for m in markets:
                    slug = (m.get('slug', '') or '').lower()
                    if 'btc-updown-5m' in slug:
                        market = m
                        print(f"[*] 找到 BTC 市场: {slug}")
                        break
            
            if not market:
                print("[错误] 没有找到 BTC 5分钟市场")
                return None
            
            # 步骤3: 提取市场信息
            # 检查所有可能的 ID 字段
            current_market_id = market.get("condition_id", "")
            if not current_market_id:
                current_market_id = market.get("id", "")
            if not current_market_id:
                current_market_id = market.get("conditionId", "")
            
            current_slug = market.get("slug", "")
            current_question = market.get("question", "")
            
            print(f"[诊断] 步骤3: 选择市场")
            print(f"  market keys: {list(market.keys())}")
            print(f"  condition_id: {current_market_id[:30] if current_market_id else 'None'}...")
            print(f"  slug: {current_slug}")
            print(f"  question: {current_question[:50] if current_question else 'None'}")
            
            if not current_market_id:
                print("[错误] 市场 condition_id 为空")
                print(f"[诊断] 完整 market: {market}")
                return None
            
            # 步骤4: 设置 market_id（关键步骤）
            print(f"[诊断] 步骤4: 设置 self.config.market_id")
            self.config.market_id = current_market_id
            self.current_event_id = current_market_id
            self.has_traded_in_event = False
            self.event_start_time = datetime.now()
            
            print(f"[诊断] 确认 market_id 已设置: {self.config.market_id[:30] if self.config.market_id else 'None'}...")
            
            # 步骤5: 直接从市场数据获取 token IDs（避免重复API调用）
            print(f"[诊断] 步骤5: 从市场数据获取 token IDs...")
            clob_token_ids = market.get("clobTokenIds", [])
            
            if isinstance(clob_token_ids, str):
                try:
                    import json
                    clob_token_ids = json.loads(clob_token_ids)
                except:
                    clob_token_ids = []
            
            if isinstance(clob_token_ids, list) and len(clob_token_ids) >= 2:
                self.yes_token_id = clob_token_ids[0]
                self.no_token_id = clob_token_ids[1]
                print(f"[诊断] 直接从市场数据获取 token_ids 成功")
            else:
                # 备用方案：调用 API 获取
                print(f"[诊断] 市场数据中没有 clobTokenIds，尝试调用 API...")
                token_ids = self.client.get_token_ids(current_market_id)
                if not token_ids or "YES" not in token_ids or "NO" not in token_ids:
                    print(f"[错误] token_ids 格式错误: {token_ids}")
                    return None
                self.yes_token_id = token_ids.get("YES")
                self.no_token_id = token_ids.get("NO")
            
            print(f"[诊断] YES token: {self.yes_token_id[:20]}...")
            print(f"[诊断] NO token: {self.no_token_id[:20]}...")
            
            # 步骤6: 获取事件的结束时间（关键！）
            print(f"[诊断] 步骤6: 获取事件结束时间...")
            end_timestamp = None
            
            # 优先尝试 endDate 字段（时间戳，毫秒）
            end_date_raw = market.get("endDate")
            if end_date_raw:
                try:
                    # endDate 通常是毫秒时间戳
                    end_timestamp = float(end_date_raw) / 1000.0
                    print(f"[*] 事件结束时间(endDate): {end_timestamp}")
                except:
                    pass
            
            # 如果没有，尝试 end_timestamp 字段
            if not end_timestamp:
                end_ts_raw = market.get("end_timestamp") or market.get("endTimestamp") or market.get("end_ts")
                if end_ts_raw:
                    try:
                        end_timestamp = float(end_ts_raw)
                        print(f"[*] 事件结束时间戳: {end_timestamp}")
                    except:
                        pass
            
            # 如果还是没有，尝试 endDateIso（注意：可能只有日期没有时间）
            if not end_timestamp:
                end_date_iso = market.get("endDateIso") or market.get("end_date_iso")
                if end_date_iso:
                    try:
                        # 检查是否包含时间部分（有冒号表示有时间）
                        if ':' in str(end_date_iso):
                            end_timestamp = datetime.fromisoformat(end_date_iso.replace('Z', '+00:00')).timestamp()
                            print(f"[*] 事件结束时间(endDateIso): {end_date_iso} (Unix: {end_timestamp})")
                        else:
                            # 只有日期，不使用
                            print(f"[!] endDateIso 只有日期没有时间: {end_date_iso}")
                    except Exception as e:
                        print(f"[!] 解析结束时间失败: {e}")
            
            # 如果还是没有，计算下一个5分钟边界
            if not end_timestamp:
                # 计算当前5分钟周期的结束时间
                edt = timezone(timedelta(hours=-4))
                now_edt = datetime.now(edt)
                minute = now_edt.minute
                current_period_minute = (minute // 5) * 5
                next_period_minute = current_period_minute + 5
                
                if next_period_minute >= 60:
                    # 跨小时
                    next_period = now_edt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                else:
                    next_period = now_edt.replace(minute=next_period_minute, second=0, microsecond=0)
                
                end_timestamp = next_period.timestamp()
                print(f"[*] 计算的事件结束时间: {next_period.strftime('%H:%M:%S')} (Unix: {end_timestamp})")
            
            # 步骤7: 获取价格
            print(f"[诊断] 步骤7: 获取价格...")
            prices = self.client.get_market_prices(current_market_id)
            print(f"[诊断] 价格: {prices}")
            
            if not prices:
                print(f"[错误] 价格获取失败")
                return None
            
            yes_price = prices.get("YES", 0)
            no_price = prices.get("NO", 0)
            
            # 计算剩余时间（秒）
            current_timestamp = time.time()
            remaining_seconds = max(0, end_timestamp - current_timestamp)
            
            print(f"[*] 当前时间: {datetime.now().strftime('%H:%M:%S')}")
            print(f"[*] 事件剩余时间: {int(remaining_seconds)} 秒 ({remaining_seconds/60:.1f} 分钟)")
            
            # 记录更新时间
            self.last_update_time = datetime.now().strftime("%H:%M:%S")
            self.last_update_duration = (time.time() - start_time) * 1000
            self.api_status = "connected"
            
            print(f"[OK] 市场数据获取成功: YES ${yes_price:.2f} | NO ${no_price:.2f}")
            
            return {
                "yes_price": yes_price,
                "no_price": no_price,
                "end_timestamp": end_timestamp,
                "remaining_seconds": remaining_seconds,
            }
            
        except Exception as e:
            print(f"[错误] 获取市场数据异常: {e}")
            traceback.print_exc()
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
        """计算开仓金额（美元）
        
        规则：
        1. 基础开仓金额 = 初始余额 / 12
        2. 当余额 ≥ 初始余额 × 3^n 时，开仓金额 = 基础金额 × 2^n
        
        示例：初始余额 $12
        - 余额 $12   → 开仓 $1  (基础)
        - 余额 $36   → 开仓 $2  (12×3=36, 翻倍)
        - 余额 $108  → 开仓 $4  (36×3=108, 再翻倍)
        - 余额 $324  → 开仓 $8  (108×3=324, 再翻倍)
        
        Returns:
            开仓金额（美元）
        """
        # 使用从 API 读取的初始余额
        initial_balance = self.initial_balance if self.initial_balance > 0 else self.config.initial_balance
        
        # 基础开仓金额 = 初始余额 / 12
        base_amount = initial_balance / 12.0
        
        # 计算翻倍倍数
        # 当余额 >= 初始余额 × 3^n 时，倍数 = 2^n
        multiplier = 1
        power = 0
        while self.balance >= initial_balance * (3 ** power):
            multiplier = 2 ** power
            power += 1
        
        position_amount = base_amount * multiplier
        
        # 确保最小开仓金额为 $1
        if position_amount < 1.0:
            position_amount = 1.0
        
        print(f"[仓位] 初始余额: ${initial_balance:.2f}, 当前余额: ${self.balance:.2f}")
        print(f"[仓位] 基础金额: ${base_amount:.2f}, 倍数: {multiplier}x")
        print(f"[仓位] 开仓金额: ${position_amount:.2f}")
        
        return position_amount

    def place_dual_orders(self, position_size: float) -> None:
        """
        等待价格触发买入（正确实现"等待价格涨到 75 再买入"）

        【策略逻辑】：
        - 不挂限价单，而是监控价格
        - 当 YES 或 NO 价格涨到目标价（如 75）时
        - 立即下市价单买入
        - 设置止损止盈

        【为什么不能用限价单挂单】：
        - 限价买单 @ 75 = 以不高于 75 的价格买入
        - 如果当前价格 51 < 75，订单会立即以 51 成交
        - 这不是"等待涨到 75"，而是"立即买入"

        【正确方案】：
        - 监控价格变化
        - 当价格达到目标价时，下市价单（FOK/FAK）买入
        """
        entry_price = self.config.entry_price
        # 转换为 0-1 格式
        entry_price_float = entry_price / 100.0 if entry_price > 1 else entry_price
        entry_display = int(entry_price_float * 100)  # 显示为整数百分比

        # 获取当前市场价格
        print(f"[监控] 获取当前市场价格...")
        try:
            prices = self.client.get_market_prices(self.config.market_id)
            if not prices:
                print("[错误] 无法获取市场价格")
                return
            
            yes_price = prices.get("YES", 0.5)
            no_price = prices.get("NO", 0.5)
            print(f"[监控] 当前市场价格: YES=${yes_price:.2f} ({int(yes_price*100)}%), NO=${no_price:.2f} ({int(no_price*100)}%)")
            print(f"[监控] 目标开仓价格: {entry_display}%")
            print(f"[监控] 策略: 等待价格涨到 {entry_display}% 时买入")
            
            # 检查当前价格是否已经达到目标
            yes_reached = yes_price >= entry_price_float
            no_reached = no_price >= entry_price_float
            
            if yes_reached and no_reached:
                # 两边都达到目标价，买价格更高的（趋势更强）
                print(f"\n[触发] 两边价格都已达到目标!")
                if yes_price > no_price:
                    self._execute_market_buy("YES", position_size, yes_price)
                else:
                    self._execute_market_buy("NO", position_size, no_price)
                return
            elif yes_reached:
                print(f"\n[触发] YES 已达到目标价 {entry_display}%")
                self._execute_market_buy("YES", position_size, yes_price)
                return
            elif no_reached:
                print(f"\n[触发] NO 已达到目标价 {entry_display}%")
                self._execute_market_buy("NO", position_size, no_price)
                return
            else:
                # 两边都未达到目标价，设置监控标志
                print(f"\n[监控] 两边价格都未达到目标价 {entry_display}%")
                print(f"[监控] 等待价格触发...")
                self.waiting_for_entry = True
                self.entry_target_price = entry_price_float
                self.entry_position_size = position_size
                return
                
        except Exception as e:
            print(f"[错误] 获取市场价格失败: {e}")
            return

    def _execute_market_buy(self, token: str, position_size: float, current_price: float) -> None:
        """
        执行市价买入（使用 FOK 订单确保立即成交）

        Args:
            token: 代币类型 ("YES" 或 "NO")
            position_size: 开仓金额（美元）
            current_price: 当前价格（0-1 格式）
        """
        import math
        
        print(f"\n[买入] 执行市价买入 {token}")
        print(f"  当前价格: {current_price:.2f} ({int(current_price*100)}%)")
        print(f"  开仓金额: ${position_size:.2f}")
        
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        # 计算股数（以当前价格计算）
        raw_size = position_size / current_price
        actual_size = math.ceil(raw_size)
        
        # 确保订单金额 >= $1
        order_value = actual_size * current_price
        if order_value < 1.0:
            actual_size = math.ceil(1.0 / current_price)
        
        print(f"  计算股数: {raw_size:.2f} → {actual_size} 股")
        print(f"  预计金额: ${actual_size * current_price:.2f}")
        
        try:
            # 使用 FOK 订单（全部成交或取消，相当于市价单）
            # 价格设为略高于当前价，确保能成交
            buy_price = min(current_price + 0.05, 0.99)  # 高 5%，最高 0.99
            
            print(f"  下单参数: price={buy_price:.2f}, size={actual_size}, type=FOK")
            
            order = self.client.create_order(
                token_id=token_id,
                price=int(buy_price * 100),  # 转换为美分
                size=float(actual_size),
                side="BUY",
                order_type="FOK",  # Fill-Or-Kill，市价单类型
            )
            
            order_id = order.get("orderID") or order.get("order_id", "")
            
            if order_id and order.get("success") != False:
                # 获取实际成交价格
                actual_price = (
                    order.get("price") or
                    order.get("avg_price") or
                    buy_price
                )
                if isinstance(actual_price, str):
                    actual_price = float(actual_price)
                if actual_price < 1:
                    actual_price = actual_price * 100  # 转换为美分
                
                # 记录持仓
                self.current_position = {
                    "type": "LONG",
                    "token": token,
                    "token_id": token_id,
                    "entry_price": actual_price,  # 实际成交价格（美分单位）
                    "size": actual_size,
                    "timestamp": datetime.now(),
                }
                
                print(f"[买入] ✓ 买入成功!")
                print(f"  实际成交价: {actual_price} (美分单位)")
                print(f"  成交股数: {actual_size}")
                print(f"  成交金额: ${actual_size * actual_price / 100:.2f}")
                
                # 设置止损止盈
                print(f"\n[止损止盈] 设置止损止盈...")
                self.stop_loss_order = None
                self.take_profit_order = None
                
                stop_result = self.place_stop_loss_order(actual_size)
                take_result = self.place_take_profit_order(actual_size)
                
                if stop_result:
                    print(f"[止损止盈] ✓ 止损单设置成功: {stop_result[:20]}...")
                if take_result:
                    print(f"[止损止盈] ✓ 止盈单设置成功: {take_result[:20]}...")
                    
                self.waiting_for_entry = False
            else:
                error_msg = order.get("errorMsg", "Unknown error")
                print(f"[买入] ✗ 买入失败: {error_msg}")
                
        except Exception as e:
            print(f"[买入] ✗ 买入失败: {e}")
            import traceback
            traceback.print_exc()

    def _monitor_price_for_entry(self, position_size: float, max_wait: int) -> bool:
        """
        监控价格，等待达到目标价时买入（优化版：智能频率调整 + 极速模式）

        【优化策略】：
        - 当价格远离目标价时：降低检查频率（1秒），减少 API 调用
        - 当价格接近目标价时：提高检查频率（0.1秒），提高实时性
        - 当价格非常接近时：进入极速模式（0.05秒），最低延迟
        - 动态调整检查频率，平衡实时性和 API 调用

        Args:
            position_size: 开仓金额
            max_wait: 最大等待时间（秒）

        Returns:
            True 如果成功买入，False 如果超时未触发
        """
        if not self.waiting_for_entry:
            return False

        entry_target = self.entry_target_price
        print(f"\n[监控] 开始监控价格，等待涨到 {int(entry_target * 100)}%...")

        start_time = time.time()
        last_log_time = 0

        while time.time() - start_time < max_wait:
            try:
                prices = self.client.get_market_prices(self.config.market_id)
                if not prices:
                    time.sleep(0.5)
                    continue

                yes_price = prices.get("YES", 0.5)
                no_price = prices.get("NO", 0.5)

                # 计算距离目标价的距离
                yes_distance = abs(yes_price - entry_target)
                no_distance = abs(no_price - entry_target)
                min_distance = min(yes_distance, no_distance)

                # 【动态调整检查频率】
                # 距离 > 10%: 1秒检查一次（低频）- 省资源
                # 距离 5-10%: 0.5秒检查一次（中频）- 平衡
                # 距离 2-5%: 0.1秒检查一次（高频）- 提高实时性
                # 距离 < 2%: 0.05秒检查一次（极速）- 最低延迟
                if min_distance > 0.10:
                    check_interval = 1.0
                    freq_display = "低频"
                elif min_distance > 0.05:
                    check_interval = 0.5
                    freq_display = "中频"
                elif min_distance > 0.02:
                    check_interval = 0.1
                    freq_display = "高频"
                else:
                    check_interval = 0.05  # 极速模式：50毫秒
                    freq_display = "极速"

                # 每 2 秒输出一次价格（极速模式下每 0.5 秒输出）
                log_interval = 0.5 if check_interval == 0.05 else 2
                current_time = time.time()
                if current_time - last_log_time >= log_interval:
                    elapsed = int(current_time - start_time)
                    remaining = max_wait - elapsed
                    print(f"\r[监控] YES={int(yes_price*100)}% NO={int(no_price*100)}% | 距离{int(min_distance*100)}% | {freq_display}({check_interval}s) | 剩余 {remaining}s    ", end="", flush=True)
                    last_log_time = current_time

                # 检查是否达到目标价
                yes_reached = yes_price >= entry_target
                no_reached = no_price >= entry_target

                if yes_reached or no_reached:
                    # 优先买先达到的，如果都达到则买价格更高的
                    if yes_reached and no_reached:
                        token = "YES" if yes_price > no_price else "NO"
                        price = max(yes_price, no_price)
                    elif yes_reached:
                        token = "YES"
                        price = yes_price
                    else:
                        token = "NO"
                        price = no_price

                    print(f"\n\n[触发] {token} 价格达到目标 {int(entry_target * 100)}%!")
                    self._execute_market_buy(token, position_size, price)
                    return True

                # 使用动态调整的检查间隔
                time.sleep(check_interval)

            except Exception as e:
                print(f"\n[错误] 监控价格失败: {e}")
                time.sleep(0.5)

        print(f"\n[监控] 超时未触发，跳过此周期")
        self.waiting_for_entry = False
        return False

    def _place_single_order(self, token: str, position_size: float, entry_price: float, entry_price_float: float, expected_price: float) -> None:
        """挂单个订单（避免双持仓风险）
        
        当检测到两个订单都会立即成交时，只挂价格更优的一方。
        
        Args:
            token: 代币类型 ("YES" 或 "NO")
            position_size: 开仓金额
            entry_price: 挂单价格（美分）
            entry_price_float: 挂单价格（0-1格式）
            expected_price: 预期成交价格（当前市场价格）
        """
        import math
        
        # 计算股数
        raw_size = position_size / expected_price  # 使用预期成交价格计算
        actual_size = math.ceil(raw_size)
        order_value = actual_size * expected_price
        if order_value < 1.0:
            actual_size = math.ceil(1.0 / expected_price)
        
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        print(f"\n[单边挂单] 只挂 {token} 单")
        print(f"  挂单价格: ${entry_price_float:.2f} (限价)")
        print(f"  预期成交价: ${expected_price:.2f} (当前市价)")
        print(f"  开仓金额: ${position_size:.2f}")
        print(f"  股数: {actual_size}")
        
        try:
            order = self.client.create_order(
                token_id=token_id,
                price=entry_price,
                size=float(actual_size),
                side="BUY",
                order_type="GTC",
            )
            
            order_id = order.get("orderID") or order.get("order_id", "")
            
            if order_id:
                self.pending_orders[order_id] = {
                    "type": "LONG",
                    "token": token,
                    "token_id": token_id,
                    "price": entry_price,  # 记录挂单价格
                    "expected_price": expected_price,  # 记录预期成交价格
                    "size": actual_size,
                }
                print(f"[单边挂单] [OK] {token} 限价单已挂，预期成交价: ${expected_price:.2f}")
            else:
                print(f"[单边挂单] [X] {token} 订单创建失败: {order.get('errorMsg', 'Unknown')}")
                
        except Exception as e:
            print(f"[单边挂单] [X] 挂单失败: {e}")

    def _cancel_pending_orders(self) -> None:
        """取消所有挂单"""
        if not self.pending_orders:
            print(f"[取消] 没有待取消的订单")
            return
            
        print(f"[取消] 准备取消 {len(self.pending_orders)} 个挂单...")
        success_count = 0
        fail_count = 0
        
        for order_id in list(self.pending_orders.keys()):
            try:
                token = self.pending_orders[order_id].get("token", "Unknown")
                print(f"[取消] 正在取消 {token} 订单: {order_id[:20]}...", end="", flush=True)
                
                result = self.client.cancel_order(order_id)
                
                if result and result.get("success") != False:
                    print(f" ✓ 成功")
                    self.logger.info(f"已取消订单: {order_id}")
                    success_count += 1
                else:
                    error_msg = result.get("errorMsg", "Unknown") if result else "Empty response"
                    print(f" ✗ 失败: {error_msg}")
                    self.logger.error(f"取消订单失败: {order_id}, 错误: {error_msg}")
                    fail_count += 1
            except Exception as e:
                print(f" ✗ 异常: {e}")
                self.logger.error(f"取消订单失败: {order_id}, 异常: {e}")
                fail_count += 1
        
        self.pending_orders.clear()
        print(f"[取消] 完成: 成功 {success_count}, 失败 {fail_count}")
    
    def _cancel_single_order(self, order_info: Dict) -> bool:
        """立即取消单个订单（用于成交后快速取消另一侧）"""
        try:
            order_id = order_info.get("orderID") or order_info.get("order_id")
            if not order_id:
                return False
            
            token = order_info.get("token", "Unknown")
            print(f"[极速取消] {token} 订单: {order_id[:20]}...", end="", flush=True)
            
            result = self.client.cancel_order(order_id)
            
            if result and result.get("success") != False:
                print(f" ✓ 成功")
                return True
            else:
                error_msg = result.get("errorMsg", "Unknown") if result else "Empty response"
                print(f" ✗ 失败: {error_msg}")
                return False
        except Exception as e:
            print(f" ✗ 异常: {e}")
            return False
    
    def _check_orders_parallel(self) -> Optional[Tuple[str, Dict]]:
        """
        并行查询所有订单状态（优化速度）
        
        注意：会检测是否两个订单都成交（双持仓风险）
        
        Returns:
            (order_id, order_status) 如果有订单成交，否则 None
        """
        if not self.pending_orders:
            return None
        
        filled_orders = []  # 记录所有成交的订单
        
        # 使用线程池并行查询所有订单
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 提交所有订单查询任务
            future_to_order = {
                executor.submit(self.client.get_order, order_id): order_id
                for order_id in self.pending_orders.keys()
            }
            
            # 检查结果，收集所有成交的订单
            for future in as_completed(future_to_order):
                order_id = future_to_order[future]
                try:
                    order_status = future.result()
                    if order_status:
                        # 检查是否成交
                        filled = (
                            order_status.get("filled_size", 0) or
                            order_status.get("size_filled", 0) or
                            order_status.get("fills", 0) or
                            order_status.get("fill_amount", 0) or
                            0
                        )
                        if filled > 0:
                            filled_orders.append((order_id, order_status))
                except Exception:
                    pass
        
        # 检测双持仓风险
        if len(filled_orders) > 1:
            print(f"\n[警告] 检测到双持仓风险！两个订单同时成交！")
            # 选择第一个作为持仓，取消第二个
            first_order_id, first_status = filled_orders[0]
            second_order_id, second_status = filled_orders[1]
            
            # 获取第二个订单的信息
            second_order_info = self.pending_orders.get(second_order_id, {})
            second_token = second_order_info.get("token", "Unknown")
            
            print(f"[警告] 将保留第一个成交订单，卖出第二个: {second_token}")
            
            # 立即卖出第二个订单的持仓（市价卖出）
            try:
                # 获取当前价格卖出
                prices = self.client.get_market_prices(self.config.market_id)
                if prices:
                    second_token_id = second_order_info.get("token_id")
                    if second_token_id:
                        # 创建卖出订单
                        sell_order = self.client.create_order(
                            token_id=second_token_id,
                            price=99,  # 以接近100的价格卖出，相当于市价
                            size=second_order_info.get("size", 1),
                            side="SELL",
                            order_type="GTC",
                        )
                        print(f"[警告] 已卖出第二个持仓: {second_token}")
            except Exception as e:
                print(f"[错误] 卖出第二个持仓失败: {e}")
            
            # 返回第一个成交的订单
            return (first_order_id, first_status)
        
        # 返回第一个成交的订单
        if filled_orders:
            return filled_orders[0]
        
        return None
    
    def _place_stop_take_parallel(self, position_size: float) -> Tuple[Optional[str], Optional[str]]:
        """
        并行设置止损止盈订单（优化速度）
        
        Returns:
            (stop_loss_order_id, take_profit_order_id)
        """
        stop_loss_result = None
        take_profit_result = None
        
        # 使用线程池并行设置止损和止盈
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 提交止损和止盈任务
            stop_future = executor.submit(self.place_stop_loss_order, position_size)
            take_future = executor.submit(self.place_take_profit_order, position_size)
            
            # 等待结果
            try:
                stop_loss_result = stop_future.result(timeout=5)
            except Exception as e:
                self.logger.error(f"并行设置止损单失败: {e}")
            
            try:
                take_profit_result = take_future.result(timeout=5)
            except Exception as e:
                self.logger.error(f"并行设置止盈单失败: {e}")
        
        return (stop_loss_result, take_profit_result)

    def wait_for_execution(self, position_size: float, max_wait: int = 300) -> bool:
        """
        等待订单成交（优化版：并行查询 + 并行止损止盈）
        
        优化点：
        1. 并行查询订单状态（同时查询YES和NO）
        2. 并行设置止损止盈（同时设置两个订单）
        3. 降低价格显示频率（每3次循环更新一次）
        4. 优化检查间隔（从0.5秒到0.3秒）
        
        Args:
            position_size: 仓位大小
            max_wait: 最大等待时间（秒），默认300秒（5分钟）
            
        Returns:
            True 如果有订单成交，False 如果超时未成交
        """
        # 如果没有挂单，直接返回
        if not self.pending_orders:
            print("[等待] [X] 没有挂单可等待（订单创建可能失败）")
            return False
            
        print(f"\r[等待] 等待订单成交... (周期剩余 {max_wait} 秒)", end="", flush=True)

        start_time = time.time()
        last_status_log = 0
        last_price_update = 0
        loop_count = 0

        while time.time() - start_time < max_wait:
            loop_count += 1
            current_time = time.time()
            
            # 【优化】价格显示频率降低：每5次循环更新一次（约1秒）
            if loop_count % 5 == 0:
                try:
                    prices = self.client.get_market_prices(self.config.market_id)
                    if prices:
                        elapsed = int(current_time - start_time)
                        remaining = max_wait - elapsed
                        print(f"\r[等待] YES ${prices.get('YES', 0):.2f} | NO ${prices.get('NO', 0):.2f} | 剩余 {remaining}s    ", end="", flush=True)
                except Exception:
                    pass
            
            # 【优化】并行查询所有订单状态
            result = self._check_orders_parallel()
            
            if result:
                order_id, order_status = result
                order_info = self.pending_orders[order_id]
                token = order_info["token"]
                
                # 获取成交数量
                filled = (
                    order_status.get("filled_size", 0) or
                    order_status.get("size_filled", 0) or
                    order_status.get("fills", 0) or
                    order_status.get("fill_amount", 0) or
                    0
                )
                
                # 【关键修复】获取实际成交价格
                # 限价单可能以更优的价格成交，需要获取实际成交价格
                actual_entry_price = (
                    order_status.get("price") or
                    order_status.get("avg_price") or
                    order_status.get("filled_price") or
                    order_status.get("execution_price") or
                    order_status.get("avgFilledPrice") or
                    order_info["price"]  # 兜底：使用挂单价格
                )
                
                # 价格格式转换（确保是数字）
                if isinstance(actual_entry_price, str):
                    try:
                        actual_entry_price = float(actual_entry_price)
                    except:
                        actual_entry_price = order_info["price"]
                
                # 如果返回的是 0-1 格式，转换为美分格式（统一存储格式）
                if actual_entry_price < 1:
                    actual_entry_price = actual_entry_price * 100
                
                print(f"\r\n[成交] ✓ 订单成交详情:")
                print(f"  订单ID: {order_id[:20]}...")
                print(f"  代币: {token}")
                print(f"  挂单价格: {order_info['price']} (美分单位)")
                print(f"  实际成交价: {actual_entry_price} (美分单位)")
                print(f"  成交股数: {filled}")
                print(f"  记录股数: {order_info['size']}")
                print()

                # 设置当前持仓（使用实际成交价格）
                token_id = order_info.get("token_id")
                self.current_position = {
                    "type": "LONG",
                    "token": token,
                    "token_id": token_id,
                    "entry_price": actual_entry_price,  # 使用实际成交价格
                    "size": order_info["size"],
                    "timestamp": datetime.now(),
                }
                
                print(f"[成交] 持仓已记录:")
                print(f"  代币: {token}")
                print(f"  开仓价: {actual_entry_price} (实际成交价)")
                print(f"  股数: {order_info['size']}")

                # 【极速优化】并行执行：取消另一侧 + 设置止损止盈
                print(f"\n[极速操作] 并行执行：取消另一侧 + 设置止损止盈...")
                
                # 准备止损止盈参数
                self.stop_loss_order = None
                self.take_profit_order = None
                
                # 使用线程池并行执行所有操作
                with ThreadPoolExecutor(max_workers=3) as executor:
                    # 任务1：取消另一侧订单
                    cancel_futures = []
                    for other_order_id, other_order_info in list(self.pending_orders.items()):
                        if other_order_id != order_id:
                            cancel_future = executor.submit(self._cancel_single_order, other_order_info)
                            cancel_futures.append((other_order_id, cancel_future))
                            break  # 只有一个另一侧订单
                    
                    # 任务2&3：并行设置止损止盈
                    stop_future = executor.submit(self.place_stop_loss_order, order_info['size'])
                    take_future = executor.submit(self.place_take_profit_order, order_info['size'])
                    
                    # 等待取消订单结果
                    for other_order_id, cancel_future in cancel_futures:
                        try:
                            cancel_future.result(timeout=3)
                            if other_order_id in self.pending_orders:
                                del self.pending_orders[other_order_id]
                        except Exception as e:
                            self.logger.error(f"取消订单失败: {e}")
                    
                    # 等待止损止盈结果
                    try:
                        stop_loss_result = stop_future.result(timeout=5)
                        if stop_loss_result:
                            print(f"[极速操作] ✓ 止损单设置成功: {stop_loss_result[:20]}...")
                        else:
                            print(f"[极速操作] ✗ 止损单设置失败！")
                    except Exception as e:
                        self.logger.error(f"止损单设置失败: {e}")
                        stop_loss_result = None
                    
                    try:
                        take_profit_result = take_future.result(timeout=5)
                        if take_profit_result:
                            print(f"[极速操作] ✓ 止盈单设置成功: {take_profit_result[:20]}...")
                        else:
                            print(f"[极速操作] ✗ 止盈单设置失败！")
                    except Exception as e:
                        self.logger.error(f"止盈单设置失败: {e}")
                        take_profit_result = None
                
                # 清除已成交的订单
                self.pending_orders.clear()
                
                print(f"\n[极速操作] ✓ 成交后处理完成，进入监控阶段")
                return True

            # 每10秒输出一次状态
            elapsed = int(current_time - start_time)
            if elapsed - last_status_log >= 10:
                remaining = max_wait - elapsed
                print(f"\r[等待] 等待订单成交... 剩余 {remaining} 秒", end="", flush=True)
                last_status_log = elapsed

            # 【优化】检查间隔从0.3秒改为0.2秒（检测更快）
            time.sleep(0.2)

        # 超时未成交，但**不取消订单**，让订单继续挂着
        elapsed = int(time.time() - start_time)
        print(f"\r[等待] ⏰ 等待时间结束 ({elapsed} 秒)，订单继续挂着直到周期结束       ")
        print()
        # 不取消订单，让它们继续挂着
        # self._cancel_pending_orders()  # 注释掉取消订单的逻辑
        
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
        设置止损（价格监控方式，不创建订单）

        【重要】Polymarket 限价卖单无法实现真正的止损！

        限价卖单 @ X = 以不低于 X 的价格卖出
        - 止损应该是价格 <= 45 时触发
        - 但限价卖单 @ 45 只会在价格 >= 45 时成交
        - 这与止损的逻辑相反！

        【解决方案】：
        - 不创建止损订单（无意义）
        - 使用价格监控，当价格 <= 止损价时主动卖出
        - 在 monitor_position() 中实现

        Args:
            position_size: 持仓数量

        Returns:
            None（不创建订单）
        """
        if not self.current_position or not self.current_position.get("token_id"):
            return None

        position = self.current_position
        token = position["token"]
        stop_loss_price = self.config.stop_loss
        stop_loss_display = stop_loss_price / 100.0 if stop_loss_price > 1 else stop_loss_price

        print(f"\n[止损] 止损策略说明:")
        print(f"  代币: {token}")
        print(f"  止损价: {stop_loss_price}% (${stop_loss_display:.2f})")
        print(f"  策略: 价格监控（当价格 <= {stop_loss_price}% 时主动卖出）")
        print(f"  说明: 限价卖单无法实现止损，使用价格监控代替")

        # 不创建止损订单，返回 None
        return None
    
    def place_take_profit_order(self, position_size: float) -> Optional[str]:
        """
        设置止盈单（卖出持仓代币）
        
        根据 Polymarket 官方文档：
        - 使用 GTD 订单确保在周期结束时自动过期
        
        **使用限价单（GTD），不是市价单**
        
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
        
        print(f"\n[止盈] 设置止盈单:")
        print(f"  代币: {token}")
        print(f"  代币ID: {token_id[:20]}...")
        print(f"  开仓价: {entry_price}")
        print(f"  止盈价: {take_profit_price} → ${take_profit_display:.2f}")
        print(f"  卖出股数: {position_size}")
        
        # GTD 订单：5 分钟后自动过期（+60秒安全缓冲）
        duration = self.config.trade_cycle_minutes * 60
        expiration = int(time.time()) + 60 + duration
        print(f"  过期时间: {expiration} (当前时间 + {duration + 60}秒)")
        
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

        【策略说明】：
        - 止损：价格监控方式，当价格 <= 止损价时主动卖出
        - 止盈：限价卖单 + 价格监控双重保障
        - 到期：按照事件结果平仓

        【重要】：
        - 止损只能通过价格监控实现（限价卖单无法实现止损）
        - 止盈有限价卖单和价格监控双重保障

        Args:
            max_wait: 最大监控时间（秒）

        Returns:
            退出原因：'STOP_LOSS', 'TAKE_PROFIT', 'TIMEOUT', 或 None
        """
        if not self.current_position:
            return None

        position = self.current_position
        entry_price = position["entry_price"]
        position_size = position["size"]
        token = position.get("token", "YES")

        stop_loss_price = self.config.stop_loss
        stop_loss_display = stop_loss_price / 100.0 if stop_loss_price > 1 else stop_loss_price
        take_profit_price = self.config.take_profit
        take_profit_display = take_profit_price / 100.0 if take_profit_price > 1 else take_profit_price

        # 转换entry_price为0-1格式用于显示
        entry_price_display = entry_price / 100.0 if entry_price > 1 else entry_price

        print(f"[监控] 持仓详情:")
        print(f"  代币: {token}")
        print(f"  开仓价格: {entry_price_display:.2f} ({int(entry_price_display*100)}%)")
        print(f"  持仓股数: {position_size}")
        print(f"  止损价格: {stop_loss_display:.2f} ({int(stop_loss_display*100)}%) - 价格监控")
        print(f"  止盈价格: {take_profit_display:.2f} ({int(take_profit_display*100)}%) - 限价单 + 价格监控")
        print(f"  监控时长: {max_wait:.1f}秒")

        # 监控止损止盈
        start_time = time.time()
        last_log_time = 0

        print(f"\n[监控] 开始监控止损止盈...")

        while time.time() - start_time < max_wait:
            current_time = time.time()

            try:
                # 获取当前价格
                prices = self.client.get_market_prices(self.config.market_id)
                if not prices:
                    time.sleep(0.5)
                    continue

                current_price = prices.get(token, 0.5)

                # 价格格式转换
                stop_loss_price_float = stop_loss_price / 100.0 if stop_loss_price > 1 else stop_loss_price
                take_profit_price_float = take_profit_price / 100.0 if take_profit_price > 1 else take_profit_price

                # 【止损检查】价格 <= 止损价
                if current_price <= stop_loss_price_float:
                    print(f"\r\n[触发] ⚠️ 止损触发! {token} 价格 {current_price:.2f} <= 止损价 {stop_loss_price_float:.2f}    ")
                    # 取消止盈订单
                    if self.take_profit_order:
                        self._cancel_single_order(self.take_profit_order)
                        self.take_profit_order = None
                    return "STOP_LOSS"

                # 【止盈检查】价格 >= 止盈价
                if current_price >= take_profit_price_float:
                    print(f"\r\n[触发] ✓ 止盈触发! {token} 价格 {current_price:.2f} >= 止盈价 {take_profit_price_float:.2f}    ")
                    # 取消止盈订单（如果还在）
                    if self.take_profit_order:
                        self._cancel_single_order(self.take_profit_order)
                        self.take_profit_order = None
                    return "TAKE_PROFIT"

                # 【止盈订单成交检查】检查止盈订单是否已成交
                if self.take_profit_order:
                    try:
                        order_id = self.take_profit_order.get("orderID")
                        order_status = self.client.get_order(order_id)
                        if order_status:
                            filled = (
                                order_status.get("filled_size", 0) or
                                order_status.get("size_filled", 0) or
                                order_status.get("fills", 0) or
                                order_status.get("fill_amount", 0) or
                                0
                            )
                            if filled > 0:
                                print(f"\r\n[触发] ✓ 止盈订单已成交!                      ")
                                self.take_profit_order = None
                                return "TAKE_PROFIT"
                    except Exception:
                        pass

                # 每 2 秒输出一次状态
                if current_time - last_log_time >= 2.0:
                    elapsed = int(current_time - start_time)
                    remaining = int(max_wait - elapsed)
                    print(f"\r[监控] {token}: {current_price:.2f} | 止损{stop_loss_display:.2f} 止盈{take_profit_display:.2f} | 剩余{remaining}s    ", end="", flush=True)
                    last_log_time = current_time

                time.sleep(0.5)  # 每 0.5 秒检查一次

            except Exception as e:
                print(f"\n[错误] 监控失败: {e}")
                time.sleep(0.5)

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
        token_id = position.get("token_id")

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
            # 止损触发：获取当前价格卖出
            try:
                prices = self.client.get_market_prices(self.config.market_id)
                exit_price = to_float_price(prices.get(token, self.config.stop_loss))
            except:
                exit_price = to_float_price(self.config.stop_loss)
        elif exit_reason == "TAKE_PROFIT":
            # 止盈触发：获取当前价格卖出
            try:
                prices = self.client.get_market_prices(self.config.market_id)
                exit_price = to_float_price(prices.get(token, self.config.take_profit))
            except:
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

        # 【关键修复】实际卖出持仓代币
        print(f"\n[平仓] 开始执行平仓操作...")
        print(f"  代币: {token}")
        print(f"  平仓价格: ${exit_price:.2f}")
        print(f"  卖出数量: {position_size} 股")
        
        if token_id:
            try:
                # 创建卖出订单（使用接近市价的价格）
                # 对于止损/止盈，我们以当前价格附近的价格卖出
                sell_price = int(exit_price * 100)  # 转换为美分
                sell_order = self.client.create_order(
                    token_id=token_id,
                    price=sell_price,
                    size=position_size,
                    side="SELL",
                    order_type="GTC",
                )
                
                if sell_order and sell_order.get("success") != False:
                    order_id = sell_order.get("orderID") or sell_order.get("order_id", "")
                    print(f"[平仓] ✓ 卖出订单已挂: {order_id[:20] if order_id else 'N/A'}...")
                    
                    # 等待订单成交（最多5秒）
                    start_wait = time.time()
                    while time.time() - start_wait < 5:
                        try:
                            order_status = self.client.get_order(order_id)
                            if order_status:
                                filled = (
                                    order_status.get("filled_size", 0) or
                                    order_status.get("size_filled", 0) or
                                    order_status.get("fills", 0) or
                                    0
                                )
                                if filled > 0:
                                    print(f"[平仓] ✓ 卖出订单已成交!")
                                    break
                        except:
                            pass
                        time.sleep(0.5)
                else:
                    print(f"[平仓] ✗ 卖出订单创建失败: {sell_order.get('errorMsg', 'Unknown') if sell_order else 'Empty'}")
            except Exception as e:
                print(f"[平仓] ✗ 卖出操作失败: {e}")

        # 平仓（更新余额和记录）
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

        # 【新增】交易结束后输出实时统计
        self._print_trade_summary(trade_record)

    def _print_trade_summary(self, last_trade: TradeRecord) -> None:
        """
        输出实时交易情况统计（每次交易结束后）
        
        Args:
            last_trade: 刚刚完成的交易记录
        """
        stats = self.trade_history.get_statistics()
        
        print("\n" + "=" * 60)
        print("📊 实时交易统计")
        print("=" * 60)
        
        # 本次交易信息
        print("【本次交易】")
        print(f"  代币: {last_trade.token}")
        print(f"  方向: 做多")
        print(f"  开仓价: ${last_trade.entry_price:.2f}")
        print(f"  平仓价: ${last_trade.exit_price:.2f}")
        print(f"  持仓量: {last_trade.position_size} 股")
        
        # 盈亏显示（带颜色）
        pnl = last_trade.pnl
        if pnl >= 0:
            print(f"  盈亏: ${pnl:+.2f} ✅")
        else:
            print(f"  盈亏: ${pnl:+.2f} ❌")
        
        # 退出原因
        exit_reason_map = {
            "STOP_LOSS": "止损触发",
            "TAKE_PROFIT": "止盈触发",
            "TIMEOUT": "周期到期",
            "FORCED_CLOSE": "强制平仓"
        }
        print(f"  退出原因: {exit_reason_map.get(last_trade.exit_reason, last_trade.exit_reason)}")
        print(f"  时间: {last_trade.timestamp}")
        
        print("-" * 60)
        
        # 累计统计
        print("【累计统计】")
        print(f"  总交易次数: {stats['total_trades']} 次")
        print(f"  盈利次数: {stats['win_trades']} 次 ✅")
        print(f"  亏损次数: {stats['loss_trades']} 次 ❌")
        
        # 胜率（带颜色）
        win_rate = stats['win_rate']
        if win_rate >= 50:
            print(f"  胜率: {win_rate:.2f}% ✅")
        else:
            print(f"  胜率: {win_rate:.2f}% ❌")
        
        # 总盈亏（带颜色）
        total_profit = stats['total_profit']
        if total_profit >= 0:
            print(f"  总盈亏: ${total_profit:+.2f} ✅")
        else:
            print(f"  总盈亏: ${total_profit:+.2f} ❌")
        
        print("-" * 60)
        
        # 账户余额
        print("【账户余额】")
        print(f"  交易前: ${last_trade.balance_before:.2f}")
        print(f"  交易后: ${last_trade.balance_after:.2f}")
        
        # 余额变化（带颜色）
        balance_change = last_trade.balance_after - last_trade.balance_before
        if balance_change >= 0:
            print(f"  变化: ${balance_change:+.2f} ✅")
        else:
            print(f"  变化: ${balance_change:+.2f} ❌")
        
        # 相对初始余额的变化
        if self.initial_balance > 0:
            total_return = ((last_trade.balance_after - self.initial_balance) / self.initial_balance) * 100
            if total_return >= 0:
                print(f"  总收益率: {total_return:+.2f}% ✅")
            else:
                print(f"  总收益率: {total_return:+.2f}% ❌")
        
        print("=" * 60)
        print()

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
