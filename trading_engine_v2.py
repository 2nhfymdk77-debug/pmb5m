"""
实时交易引擎 V2
- 实时监控价格变动
- 达到买入价立即买入
- 达到止损止盈价格立即卖出
- 最小延迟，简化输出
"""
import time
import math
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import requests

from config import TradingConfig, TradeRecord, TradeHistory
from polymarket_api import PolymarketClient


class RealtimeTrader:
    """实时交易引擎 - 简化版"""
    
    # 状态常量
    STATE_IDLE = "IDLE"                    # 空闲，等待监控
    STATE_MONITORING_ENTRY = "MONITORING"  # 监控买入价格
    STATE_HOLDING = "HOLDING"              # 持仓中
    STATE_MONITORING_EXIT = "EXITING"      # 监控卖出价格
    
    def __init__(self, config: TradingConfig):
        self.config = config
        
        # 初始化API客户端
        self.client = PolymarketClient(
            private_key=config.private_key,
            api_key=config.api_key,
            api_secret=config.api_secret,
            passphrase=config.passphrase,
            chain_id=config.chain_id,
            signature_type=config.signature_type,
            funder_address=config.funder_address,
        )
        
        # 交易状态
        self.state = self.STATE_IDLE
        self.is_running = False
        self.balance = 0.0
        self.initial_balance = 0.0
        
        # 当前持仓
        self.position: Optional[Dict] = None  # {token, token_id, size, entry_price}
        
        # 当前市场
        self.market_id: Optional[str] = None
        self.yes_token_id: Optional[str] = None
        self.no_token_id: Optional[str] = None
        self.event_end_time: float = 0
        
        # 交易历史
        self.trade_history = TradeHistory()
        
        # 统计
        self.stats = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
        
        # 上次价格检查时间
        self.last_price_check = 0
        self.last_price = {"YES": 0.5, "NO": 0.5}
        
        # 当前事件ID（防止重复交易）
        self.current_event_id: Optional[str] = None
        self.has_traded_in_event = False
    
    # ==================== 核心交易逻辑 ====================
    
    def start(self) -> None:
        """启动实时交易"""
        print("\n" + "=" * 50)
        print("  实时交易引擎 V2")
        print("=" * 50)
        
        # 初始化余额
        if not self._init_balance():
            print("[错误] 无法获取余额，退出")
            return
        
        # 确认交易参数
        self._confirm_params()
        
        print("\n[启动] 开始实时监控...")
        print(f"  买入价: {int(self.config.entry_price)}%")
        print(f"  止损价: {int(self.config.stop_loss)}%")
        print(f"  止盈价: {int(self.config.take_profit)}%")
        print("-" * 50)
        
        self.is_running = True
        self.state = self.STATE_IDLE
        
        try:
            while self.is_running:
                self._main_loop()
                time.sleep(0.01)  # 最小间隔
        except KeyboardInterrupt:
            print("\n[停止] 用户中断")
        except Exception as e:
            print(f"\n[错误] {e}")
    
    def _main_loop(self) -> None:
        """主循环 - 根据状态执行不同逻辑"""
        # 1. 检查/更新市场
        if not self._check_market():
            time.sleep(1)
            return
        
        # 2. 获取实时价格
        prices = self._get_prices_fast()
        if not prices:
            time.sleep(0.1)
            return
        
        yes_price = prices.get("YES", 0.5)
        no_price = prices.get("NO", 0.5)
        
        # 3. 根据状态执行
        if self.state == self.STATE_IDLE:
            self._handle_idle(yes_price, no_price)
        elif self.state == self.STATE_MONITORING_ENTRY:
            self._handle_monitoring_entry(yes_price, no_price)
        elif self.state == self.STATE_HOLDING:
            self._handle_holding(yes_price, no_price)
        elif self.state == self.STATE_MONITORING_EXIT:
            self._handle_monitoring_exit(yes_price, no_price)
    
    # ==================== 状态处理 ====================
    
    def _handle_idle(self, yes_price: float, no_price: float) -> None:
        """空闲状态 - 检查是否可以开始监控"""
        # 如果当前事件已成功买入，跳过
        if self.has_traded_in_event:
            return
        
        # 检查价格是否达到买入条件
        entry_price = self.config.entry_price / 100.0
        
        # 价格已经达到买入价？
        if yes_price >= entry_price or no_price >= entry_price:
            self.state = self.STATE_MONITORING_ENTRY
            self._print_status("监控买入", yes_price, no_price)
    
    def _handle_monitoring_entry(self, yes_price: float, no_price: float) -> None:
        """监控买入价格 - 等待达到目标价"""
        entry_price = self.config.entry_price / 100.0
        
        # 输出状态（每秒一次）
        now = time.time()
        if now - self.last_price_check >= 1.0:
            self._print_status("等待买入", yes_price, no_price)
            self.last_price_check = now
        
        # 检查是否可以买入
        can_buy_yes = yes_price >= entry_price and yes_price < 0.90
        can_buy_no = no_price >= entry_price and no_price < 0.90
        
        if can_buy_yes or can_buy_no:
            # 选择买入哪一方
            if can_buy_yes and can_buy_no:
                token = "YES" if yes_price >= no_price else "NO"
                price = max(yes_price, no_price)
            elif can_buy_yes:
                token, price = "YES", yes_price
            else:
                token, price = "NO", no_price
            
            # 执行买入
            self._execute_buy(token, price)
    
    def _handle_holding(self, yes_price: float, no_price: float) -> None:
        """持仓状态 - 开始监控卖出"""
        self.state = self.STATE_MONITORING_EXIT
    
    def _handle_monitoring_exit(self, yes_price: float, no_price: float) -> None:
        """监控卖出价格 - 检查止损止盈"""
        if not self.position:
            self.state = self.STATE_IDLE
            return
        
        token = self.position["token"]
        current_price = yes_price if token == "YES" else no_price
        
        # 输出状态（每秒一次）
        now = time.time()
        if now - self.last_price_check >= 1.0:
            remaining = max(0, int(self.event_end_time - now))
            print(f"\r[持仓] {token} @ {int(current_price*100)}% | 剩余{remaining}s    ", end="", flush=True)
            self.last_price_check = now
        
        stop_loss = self.config.stop_loss / 100.0
        take_profit = self.config.take_profit / 100.0
        
        # 检查止损
        if current_price <= stop_loss:
            self._execute_sell("STOP_LOSS", current_price)
            return
        
        # 检查止盈
        if current_price >= take_profit:
            self._execute_sell("TAKE_PROFIT", current_price)
            return
        
        # 检查事件是否结束
        if time.time() >= self.event_end_time:
            self._handle_event_end()
    
    # ==================== 交易执行 ====================
    
    def _execute_buy(self, token: str, price: float) -> None:
        """执行买入"""
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        # 计算仓位
        position_amount = self._calculate_position()
        shares = math.ceil(position_amount / price)
        if shares < 5:
            shares = 5  # 最小5股
        
        # 显示买入前余额
        print(f"\n{'='*50}")
        print(f"[买入] {token} {shares}股 @ {int(price*100)}%")
        print(f"[余额] 当前: ${self.balance:.2f} | 开仓金额: ${position_amount:.2f}")
        
        try:
            # 使用FOK订单立即成交
            order = self.client.create_order(
                token_id=token_id,
                price=int(price * 100),
                size=float(shares),
                side="BUY",
                order_type="FOK",
            )
            
            if order and order.get("success") != False:
                # 等待余额更新（循环检查，最多5秒）
                actual_balance = 0.0
                for _ in range(10):
                    time.sleep(0.5)
                    actual_balance = self.client.get_token_balance(token_id)
                    if actual_balance > 0:
                        break
                
                if actual_balance > 0:
                    self.position = {
                        "token": token,
                        "token_id": token_id,
                        "size": actual_balance,
                        "entry_price": price,
                    }
                    # 成功买入后标记已交易，防止同一周期再次买入
                    self.has_traded_in_event = True
                    self.state = self.STATE_HOLDING
                    print(f"[确认] 买入成功 {actual_balance:.2f}股")
                    self._print_stats()
                else:
                    # 买入失败，可以重试
                    print("[失败] 买入后余额为0，等待重试...")
                    self.state = self.STATE_MONITORING_ENTRY
            else:
                # 买入失败，可以重试
                error = order.get("errorMsg", "Unknown") if order else "No response"
                print(f"[失败] {error}，等待重试...")
                self.state = self.STATE_MONITORING_ENTRY
        except Exception as e:
            # 异常，可以重试
            print(f"[错误] {e}，等待重试...")
            self.state = self.STATE_MONITORING_ENTRY
    
    def _execute_sell(self, reason: str, price: float) -> None:
        """执行卖出"""
        if not self.position:
            return
        
        token = self.position["token"]
        token_id = self.position["token_id"]
        size = self.position["size"]
        entry_price = self.position["entry_price"]
        
        # 查询实际余额
        actual_balance = self.client.get_token_balance(token_id)
        if actual_balance > 0:
            size = actual_balance
        
        # 卖出价格
        sell_price = max(1, int(price * 100) - 2)
        
        print(f"\n{'='*50}")
        print(f"[{reason}] 卖出 {token} {size:.2f}股 @ {sell_price}%")
        
        try:
            order = self.client.create_order(
                token_id=token_id,
                price=sell_price,
                size=size,
                side="SELL",
                order_type="GTC",
            )
            
            if order and order.get("success") != False:
                # 等待成交
                order_id = order.get("orderID") or order.get("order_id", "")
                actual_price = self._wait_for_fill(order_id, sell_price / 100.0)
                
                if actual_price > 0:
                    self._close_position(entry_price, actual_price, size, reason)
                else:
                    # 卖出失败，取消订单并继续监控
                    print("[失败] 卖出未成交，继续监控...")
                    try:
                        self.client.cancel_order(order_id)
                    except:
                        pass
                    # 保持在 MONITORING_EXIT 状态继续尝试
            else:
                # 卖出失败，继续监控
                error = order.get("errorMsg", "Unknown") if order else "No response"
                print(f"[失败] {error}，继续监控...")
                # 保持在 MONITORING_EXIT 状态
        except Exception as e:
            # 异常，继续监控
            print(f"[错误] {e}，继续监控...")
            # 保持在 MONITORING_EXIT 状态
    
    def _handle_event_end(self) -> None:
        """处理事件结束"""
        if not self.position:
            self.state = self.STATE_IDLE
            return
        
        token = self.position["token"]
        token_id = self.position["token_id"]
        size = self.position["size"]
        entry_price = self.position["entry_price"]
        
        # 查询实际余额
        actual_balance = self.client.get_token_balance(token_id)
        
        print(f"\n{'='*50}")
        print(f"[事件结束] 等待结算...")
        
        # 检查事件是否已结算
        event_result = self._get_event_result()
        
        if event_result:
            exit_price = 1.0 if event_result == token else 0.0
            result = "获胜 ✓" if exit_price == 1.0 else "失败 ✗"
            print(f"[结算] 结果: {event_result} | 持仓: {token} {result}")
            self._close_position(entry_price, exit_price, actual_balance, "SETTLED")
        else:
            # 尝试卖出
            prices = self._get_prices_fast()
            if prices:
                price = prices.get(token, 0.5)
                self._execute_sell("TIMEOUT", price)
    
    def _close_position(self, entry_price: float, exit_price: float, size: float, reason: str) -> None:
        """关闭持仓"""
        pnl = (exit_price - entry_price) * size
        pnl_display = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        
        # 获取更新后的余额
        old_balance = self.balance
        try:
            self.balance = self.client.get_balance() or self.balance
        except:
            pass
        balance_change = self.balance - old_balance
        
        # 盈亏结果
        result_icon = "✓" if pnl >= 0 else "✗"
        print(f"\n[结果] {result_icon} 盈亏: {pnl_display}")
        print(f"[余额] ${old_balance:.2f} → ${self.balance:.2f} ({f'+${balance_change:.2f}' if balance_change >= 0 else f'-${abs(balance_change):.2f}'})")
        
        # 更新统计
        self.stats["trades"] += 1
        if pnl >= 0:
            self.stats["wins"] += 1
        else:
            self.stats["losses"] += 1
        self.stats["total_pnl"] += pnl
        
        # 清除持仓
        self.position = None
        self.state = self.STATE_IDLE
        
        # 显示统计
        self._print_stats()
        print("=" * 50)
    
    def _print_stats(self) -> None:
        """打印统计信息"""
        trades = self.stats["trades"]
        wins = self.stats["wins"]
        total_pnl = self.stats["total_pnl"]
        win_rate = (wins / trades * 100) if trades > 0 else 0
        pnl_display = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        
        print(f"[统计] 交易: {trades}次 | 胜率: {win_rate:.0f}% ({wins}/{trades}) | 总盈亏: {pnl_display}")
    
    # ==================== 辅助方法 ====================
    
    def _init_balance(self) -> bool:
        """初始化余额"""
        for _ in range(3):
            try:
                balance = self.client.get_balance()
                if balance is not None:
                    self.balance = balance
                    self.initial_balance = balance
                    print(f"[余额] ${balance:.2f}")
                    return True
            except Exception as e:
                print(f"[重试] {e}")
                time.sleep(2)
        return False
    
    def _confirm_params(self) -> None:
        """确认交易参数"""
        print("\n[参数]")
        print(f"  余额: ${self.balance:.2f}")
        print(f"  买入: {int(self.config.entry_price)}% | 止损: {int(self.config.stop_loss)}% | 止盈: {int(self.config.take_profit)}%")
        print()
        
        while True:
            try:
                print("确认开始? (y/n): ", end="", flush=True)
                user_input = input().strip().lower()
                if user_input == 'y':
                    return
                elif user_input == 'n':
                    sys.exit(0)
            except:
                pass
    
    def _check_market(self) -> bool:
        """检查/更新市场信息"""
        # 计算当前事件slug
        edt = timezone(timedelta(hours=-4))
        now_edt = datetime.now(edt)
        minute = now_edt.minute
        period_minute = (minute // 5) * 5
        period_start = now_edt.replace(minute=period_minute, second=0, microsecond=0)
        period_ts = int(period_start.timestamp())
        slug = f"btc-updown-5m-{period_ts}"
        
        # 获取市场
        try:
            market = self.client.get_market_by_slug(slug)
            if not market:
                return False
            
            market_id = market.get("condition_id", "") or market.get("id", "")
            if not market_id:
                return False
            
            # 检查是否是新事件
            if market_id != self.current_event_id:
                self.current_event_id = market_id
                self.market_id = market_id
                
                # 获取token IDs（先获取，用于检查代币余额）
                token_ids = market.get("clobTokenIds", [])
                if isinstance(token_ids, str):
                    import json
                    token_ids = json.loads(token_ids)
                if len(token_ids) >= 2:
                    self.yes_token_id = token_ids[0]
                    self.no_token_id = token_ids[1]
                
                # 检查是否还持有代币（防止新周期丢失持仓）
                has_position = False
                if self.yes_token_id:
                    yes_balance = self.client.get_token_balance(self.yes_token_id)
                    if yes_balance > 0:
                        has_position = True
                        # 修复持仓信息
                        if not self.position:
                            self.position = {
                                "token": "YES",
                                "token_id": self.yes_token_id,
                                "size": yes_balance,
                                "entry_price": 0.5,  # 未知入场价
                            }
                if not has_position and self.no_token_id:
                    no_balance = self.client.get_token_balance(self.no_token_id)
                    if no_balance > 0:
                        has_position = True
                        if not self.position:
                            self.position = {
                                "token": "NO",
                                "token_id": self.no_token_id,
                                "size": no_balance,
                                "entry_price": 0.5,
                            }
                
                # 只有在不持有代币时才完全重置
                if not has_position:
                    self.has_traded_in_event = False
                    self.position = None
                    self.state = self.STATE_IDLE
                else:
                    # 持有代币，进入卖出监控状态
                    self.state = self.STATE_MONITORING_EXIT
                    print(f"\n[警告] 检测到持仓 {self.position['token']} {self.position['size']:.2f}股")
                
                # 获取结束时间
                end_ts = market.get("endDate")
                if end_ts:
                    self.event_end_time = float(end_ts) / 1000.0
                else:
                    # 计算下一个5分钟边界
                    next_minute = period_minute + 5
                    if next_minute >= 60:
                        next_period = period_start.replace(minute=0) + timedelta(hours=1)
                    else:
                        next_period = period_start.replace(minute=next_minute)
                    self.event_end_time = next_period.timestamp()
                
                remaining = max(0, int(self.event_end_time - time.time()))
                print(f"\n{'='*50}")
                print(f"[新周期] 剩余: {remaining}秒")
                self._print_stats()
            
            return True
        except Exception as e:
            return False
    
    def _get_prices_fast(self) -> Optional[Dict[str, float]]:
        """快速获取价格 - 使用并行请求"""
        if not self.yes_token_id or not self.no_token_id:
            return None
        
        try:
            # 使用快速价格获取方法
            return self.client.get_prices_fast(self.yes_token_id, self.no_token_id, timeout=3.0)
        except:
            return None
    
    def _get_event_result(self) -> Optional[str]:
        """获取事件结果"""
        try:
            market = self.client.get_market_by_id(self.market_id)
            if market:
                is_settled = market.get("is_settled") or market.get("closed") or market.get("resolved")
                if is_settled:
                    result = market.get("winning_outcome") or market.get("winner") or market.get("result")
                    if result:
                        result = str(result).upper()
                        if "YES" in result:
                            return "YES"
                        elif "NO" in result:
                            return "NO"
        except:
            pass
        return None
    
    def _calculate_position(self) -> float:
        """计算开仓金额"""
        base = self.initial_balance / 12.0
        multiplier = 1
        power = 0
        while self.balance >= self.initial_balance * (3 ** power):
            multiplier = 2 ** power
            power += 1
        return max(base * multiplier, 1.0)
    
    def _wait_for_fill(self, order_id: str, default_price: float, timeout: float = 10) -> float:
        """等待订单成交"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                status = self.client.get_order(order_id)
                if status:
                    filled = status.get("filled_size") or status.get("size_filled") or 0
                    if filled > 0:
                        price = status.get("price") or status.get("avg_price") or default_price
                        if isinstance(price, str):
                            price = float(price)
                        if price > 1:
                            price = price / 100.0
                        return price
            except:
                pass
            time.sleep(0.3)
        return 0
    
    def _print_status(self, action: str, yes: float, no: float) -> None:
        """打印状态"""
        now = datetime.now().strftime("%H:%M:%S")
        remaining = max(0, int(self.event_end_time - time.time()))
        print(f"\r[{now}] {action} | YES={int(yes*100)}% NO={int(no*100)}% | 剩余{remaining}s    ", end="", flush=True)
    
    def stop(self) -> None:
        """停止交易"""
        self.is_running = False
