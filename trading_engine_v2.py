"""
实时交易引擎 V2
- 实时监控价格变动
- 达到买入价立即买入
- 达到止损止盈价格立即卖出
- 最小延迟，简化输出
- 性能优化：智能缓存、连接池
"""
import time
import math
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import requests

from config import TradingConfig
from polymarket_api import PolymarketClient

# 性能配置
PRICE_CACHE_TTL = 0.1        # 价格缓存有效期（秒）
MAIN_LOOP_INTERVAL = 0.02    # 主循环最小间隔（秒）


class RealtimeTrader:
    """实时交易引擎 - 简化版"""
    
    # 状态常量
    STATE_IDLE = "IDLE"                    # 空闲，等待买入机会
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
        self.real_market_id: Optional[str] = None  # 真实的市场 ID（用于价格查询）
        self.yes_token_id: Optional[str] = None
        self.no_token_id: Optional[str] = None
        self.event_end_time: float = 0
        
        # 上次价格检查时间
        self.last_price_check = 0
        
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
        # 1. 检查是否需要更新市场（只在周期结束或首次运行时）
        if self.current_event_id is None or time.time() >= self.event_end_time:
            if not self._check_market():
                print(f"\r{' '*60}\r[等待] 获取市场中...", end="", flush=True)
                time.sleep(1)
                return

        # 2. 获取实时价格
        prices = self._get_prices_fast()
        if not prices:
            time.sleep(0.05)
            return

        yes_price = prices.get("YES", 0.5)
        no_price = prices.get("NO", 0.5)

        # 3. 显示实时状态（每秒一次）
        now = time.time()
        remaining = max(0, int(self.event_end_time - now))
        if now - self.last_price_check >= 1.0:
            time_str = datetime.now().strftime("%H:%M:%S")
            if self.state == self.STATE_IDLE:
                status = "等待机会" if not self.has_traded_in_event else "已交易"
            elif self.state == self.STATE_HOLDING:
                status = f"持仓 {self.position['token']}" if self.position else "确认中"
            elif self.state == self.STATE_MONITORING_EXIT:
                status = f"持仓 {self.position['token']}" if self.position else "卖出中"
            else:
                status = self.state
            
            # 格式化剩余时间
            if remaining >= 60:
                mins, secs = divmod(remaining, 60)
                time_left = f"{mins}分{secs}秒"
            else:
                time_left = f"{remaining}秒"
            
            # 清除整行后打印新内容（避免残留字符）
            line = f"[{time_str}] {status} | YES={int(yes_price*100)}% NO={int(no_price*100)}% | 剩余{time_left}"
            print(f"\r{' '*60}\r{line}", end="", flush=True)
            self.last_price_check = now

        # 4. 根据状态执行
        if self.state == self.STATE_IDLE:
            self._handle_idle(yes_price, no_price)
        elif self.state == self.STATE_HOLDING:
            self._handle_holding(yes_price, no_price)
        elif self.state == self.STATE_MONITORING_EXIT:
            self._handle_monitoring_exit(yes_price, no_price)
        
        # 最小间隔，避免 CPU 占用过高
        time.sleep(0.05)
    
    # ==================== 状态处理 ====================
    
    def _handle_idle(self, yes_price: float, no_price: float) -> None:
        """空闲状态 - 检查是否可以买入"""
        # 如果当前事件已交易，跳过
        if self.has_traded_in_event:
            return
        
        # 如果需要检查持仓（买入失败后可能延迟成交）- 每5次循环检查一次
        if hasattr(self, '_need_check_position') and self._need_check_position:
            if not hasattr(self, '_position_check_counter'):
                self._position_check_counter = 0
            self._position_check_counter += 1
            
            if self._position_check_counter >= 5:  # 每5次循环检查一次
                self._position_check_counter = 0
                if self.yes_token_id:
                    yes_balance = self.client.get_token_balance(self.yes_token_id)
                    if yes_balance > 0:
                        self.position = {
                            "token": "YES",
                            "token_id": self.yes_token_id,
                            "size": yes_balance,
                            "entry_price": self.config.entry_price / 100.0,
                        }
                        self.has_traded_in_event = True
                        self.state = self.STATE_HOLDING
                        self._need_check_position = False
                        print(f"\n[发现] 已有 YES 持仓 {yes_balance:.2f}股")
                        return
                
                if self.no_token_id:
                    no_balance = self.client.get_token_balance(self.no_token_id)
                    if no_balance > 0:
                        self.position = {
                            "token": "NO",
                            "token_id": self.no_token_id,
                            "size": no_balance,
                            "entry_price": self.config.entry_price / 100.0,
                        }
                        self.has_traded_in_event = True
                        self.state = self.STATE_HOLDING
                        self._need_check_position = False
                        print(f"\n[发现] 已有 NO 持仓 {no_balance:.2f}股")
                        return
        
        # 检查是否在买入冷却中
        if hasattr(self, '_buy_cooldown') and time.time() < self._buy_cooldown:
            return  # 冷却中，继续等待
        
        # 剩余时间太少，跳过（等待新周期）
        remaining = self.event_end_time - time.time()
        if remaining < 30:
            return
        
        entry_price = self.config.entry_price / 100.0
        
        # 检查价格是否达到买入条件（达到买入价且不极端）
        can_buy_yes = yes_price >= entry_price and yes_price < 0.90
        can_buy_no = no_price >= entry_price and no_price < 0.90
        
        if not can_buy_yes and not can_buy_no:
            return  # 价格不满足条件，继续等待
        
        # 选择买入哪一方（选择价格更高的一方）
        if can_buy_yes and can_buy_no:
            token = "YES" if yes_price >= no_price else "NO"
            price = max(yes_price, no_price)
        elif can_buy_yes:
            token, price = "YES", yes_price
        else:
            token, price = "NO", no_price
        
        # 执行买入
        print(f"\n{'='*50}")
        print(f"[触发] {token} 达到买入价 {int(price*100)}%")
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
        token_id = self.position["token_id"]
        current_price = yes_price if token == "YES" else no_price
        
        # 定期检查实际持仓（每10次循环检查一次）
        if not hasattr(self, '_balance_check_counter'):
            self._balance_check_counter = 0
        self._balance_check_counter += 1
        
        if self._balance_check_counter >= 10:
            self._balance_check_counter = 0
            actual_balance = self.client.get_token_balance(token_id)
            if actual_balance <= 0:
                # 持仓已清空（可能被外部卖出），清除状态
                print(f"\n[检测] {token} 持仓已清空")
                self.position = None
                self.state = self.STATE_IDLE
                return
        
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
    
    def _get_best_ask(self, token: str) -> Optional[float]:
        """获取卖一价格（用于买入）"""
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        # 优先使用缓存的订单簿
        if hasattr(self, '_orderbook_cache'):
            cache_age = time.time() - self._orderbook_cache.get("time", 0)
            if cache_age < 0.5:  # 缓存 0.5 秒内有效
                book = self._orderbook_cache.get(token, {})
                asks = book.get("asks", [])
                if asks:
                    best_ask = float(asks[0].get("price", 0))
                    if best_ask > 1:
                        best_ask = best_ask / 100.0
                    return best_ask
        
        # 缓存过期，重新查询
        try:
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            resp = requests.get(url, timeout=1.0)
            if resp.status_code == 200:
                book = resp.json()
                asks = book.get("asks", [])
                if asks:
                    best_ask = float(asks[0].get("price", 0))
                    if best_ask > 1:
                        best_ask = best_ask / 100.0
                    return best_ask
        except:
            pass
        return None
    
    def _get_best_bid(self, token: str) -> Optional[float]:
        """获取买一价格（用于卖出）"""
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        # 优先使用缓存的订单簿
        if hasattr(self, '_orderbook_cache'):
            cache_age = time.time() - self._orderbook_cache.get("time", 0)
            if cache_age < 0.5:  # 缓存 0.5 秒内有效
                book = self._orderbook_cache.get(token, {})
                bids = book.get("bids", [])
                if bids:
                    best_bid = float(bids[0].get("price", 0))
                    if best_bid > 1:
                        best_bid = best_bid / 100.0
                    return best_bid
        
        # 缓存过期，重新查询
        try:
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            resp = requests.get(url, timeout=1.0)
            if resp.status_code == 200:
                book = resp.json()
                bids = book.get("bids", [])
                if bids:
                    best_bid = float(bids[0].get("price", 0))
                    if best_bid > 1:
                        best_bid = best_bid / 100.0
                    return best_bid
        except:
            pass
        return None
    
    def _execute_buy(self, token: str, price: float) -> None:
        """执行买入"""
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        # 检查是否在冷却中（防止频繁重试）
        if hasattr(self, '_buy_cooldown') and time.time() < self._buy_cooldown:
            return
        
        # 获取卖一价格（使用缓存优化）
        best_ask = self._get_best_ask(token)
        if best_ask is None:
            self._buy_cooldown = time.time() + 2
            return
        
        # 先查询最新余额
        try:
            latest_balance = self.client.get_balance()
            if latest_balance is not None:
                self.balance = latest_balance
        except:
            pass
        
        # 计算仓位（使用卖一价格）
        position_amount = self._calculate_position(best_ask)
        if position_amount <= 0:
            print(f"\n[跳过] 余额 ${self.balance:.2f} 不足以开仓")
            return
        
        shares = math.ceil(position_amount / best_ask)
        if shares < 5:
            shares = 5  # 最小5股
        
        buy_price = int(best_ask * 100)
        print(f"\n{'='*50}")
        print(f"[买入] {token} {shares}股 @ {buy_price}% (卖一价)")
        
        try:
            # 使用FOK订单立即成交
            order = self.client.create_order(
                token_id=token_id,
                price=buy_price,
                size=float(shares),
                side="BUY",
                order_type="FOK",
            )
            
            if order and order.get("success") != False:
                # 快速确认买入（最多5秒，每0.2秒检查一次）
                actual_balance = 0.0
                for _ in range(25):
                    time.sleep(0.2)
                    actual_balance = self.client.get_token_balance(token_id)
                    if actual_balance > 0:
                        break
                
                if actual_balance > 0:
                    self.position = {
                        "token": token,
                        "token_id": token_id,
                        "size": actual_balance,
                        "entry_price": best_ask,
                    }
                    self.has_traded_in_event = True
                    self.state = self.STATE_HOLDING
                    self._need_check_position = False
                    print(f"[确认] 买入成功 {actual_balance:.2f}股")
                    self._print_stats()
                else:
                    print("[等待] 买入未立即成交，继续监控...")
                    self._buy_cooldown = time.time() + 3
                    self._need_check_position = True
            else:
                error = order.get("errorMsg", "") if order else ""
                print(f"[失败] {error}")
                self._buy_cooldown = time.time() + 2
        except Exception as e:
            print(f"[错误] {e}")
            self._buy_cooldown = time.time() + 2
    
    def _execute_sell(self, reason: str, price: float) -> None:
        """执行卖出"""
        if not self.position:
            return
        
        token = self.position["token"]
        token_id = self.position["token_id"]
        entry_price = self.position["entry_price"]
        
        # 查询实际余额
        actual_balance = self.client.get_token_balance(token_id)
        
        # 如果实际余额为 0，清除持仓状态
        if actual_balance <= 0:
            print(f"\n[检测] {token} 持仓已清空")
            self.position = None
            self.state = self.STATE_IDLE
            return
        
        size = actual_balance
        
        # 获取买一价格（使用缓存优化）
        best_bid = self._get_best_bid(token)
        if best_bid is None:
            self._sell_cooldown = time.time() + 2
            return
        
        sell_price = int(best_bid * 100)
        order_amount = size * best_bid
        
        # 检查订单金额
        if order_amount < 1.0:
            self._sell_cooldown = time.time() + 3
            return
        
        # 检查冷却
        if hasattr(self, '_sell_cooldown') and time.time() < self._sell_cooldown:
            return
        
        print(f"\n[{reason}] 卖出 {token} {size:.2f}股 @ {sell_price}% (买一价)")
        
        try:
            # FOK订单立即成交
            order = self.client.create_order(
                token_id=token_id,
                price=sell_price,
                size=size,
                side="SELL",
                order_type="FOK",
            )
            
            if order and order.get("success") != False:
                # FOK成功即成交
                time.sleep(0.1)
                self._close_position(entry_price, best_bid, size, reason)
            else:
                error = order.get("errorMsg", "") if order else ""
                print(f"[失败] {error}")
                self._sell_cooldown = time.time() + 2
        except Exception as e:
            print(f"[错误] {e}")
            self._sell_cooldown = time.time() + 2
    
    def _handle_event_end(self) -> None:
        """处理事件结束"""
        if not self.position:
            self.state = self.STATE_IDLE
            return
        
        token = self.position["token"]
        token_id = self.position["token_id"]
        entry_price = self.position["entry_price"]
        
        # 查询实际余额
        actual_balance = self.client.get_token_balance(token_id)
        
        # 如果实际余额为 0，清除持仓状态
        if actual_balance <= 0:
            print(f"\n[检测] {token} 持仓已清空")
            self.position = None
            self.state = self.STATE_IDLE
            return
        
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
        
        # 清除持仓
        self.position = None
        self.state = self.STATE_IDLE
        
        # 显示统计
        self._print_stats(fetch_balance=True)
        print("=" * 50)
    
    def _print_stats(self, fetch_balance: bool = False) -> None:
        """打印统计信息"""
        # 只在需要时获取最新余额
        if fetch_balance:
            try:
                current_balance = self.client.get_balance() or self.balance
                self.balance = current_balance  # 更新缓存
            except:
                current_balance = self.balance
        else:
            current_balance = self.balance
        
        # 计算盈亏
        pnl = current_balance - self.initial_balance
        pnl_display = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        
        print(f"[统计] 初始: ${self.initial_balance:.2f} | 当前: ${current_balance:.2f} | 盈亏: {pnl_display}")
    
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
                # 显示获取失败信息
                print(f"\r{' '*60}\r[获取市场] slug={slug} 未找到", end="", flush=True)
                return False
            
            market_id = market.get("condition_id", "") or market.get("id", "")
            if not market_id:
                print(f"\r{' '*60}\r[获取市场] market_id 为空", end="", flush=True)
                return False
            
            # 检查是否是新事件
            if market_id != self.current_event_id:
                self.current_event_id = market_id
                self.market_id = market_id
                self.real_market_id = market.get("id", "")  # 保存真实的市场 ID
                
                # 新周期重置所有状态（原周期代币由平台自动结算）
                self.has_traded_in_event = False
                self.position = None
                self.state = self.STATE_IDLE
                
                # 获取token IDs
                token_ids = market.get("clobTokenIds", [])
                if isinstance(token_ids, str):
                    import json
                    token_ids = json.loads(token_ids)
                
                if len(token_ids) >= 2:
                    self.yes_token_id = token_ids[0]
                    self.no_token_id = token_ids[1]
                else:
                    print(f"[警告] token_ids 数量不足: {len(token_ids)}")
                
                # 获取结束时间
                end_ts = market.get("endDate") or market.get("end_date")
                
                # 计算下一个5分钟边界（备用）
                next_minute = period_minute + 5
                if next_minute >= 60:
                    next_period = period_start.replace(minute=0) + timedelta(hours=1)
                else:
                    next_period = period_start.replace(minute=next_minute)
                
                if end_ts:
                    # endDate 可能是毫秒时间戳或 ISO 字符串
                    if isinstance(end_ts, (int, float)):
                        self.event_end_time = float(end_ts) / 1000.0
                    elif isinstance(end_ts, str):
                        # ISO 格式字符串，如 "2024-01-01T12:00:00Z"
                        try:
                            # 尝试解析 ISO 格式
                            from datetime import datetime as dt
                            dt_obj = dt.fromisoformat(end_ts.replace('Z', '+00:00'))
                            self.event_end_time = dt_obj.timestamp()
                        except:
                            # 解析失败，使用备用计算
                            self.event_end_time = next_period.timestamp()
                else:
                    self.event_end_time = next_period.timestamp()
                
                remaining = max(0, int(self.event_end_time - time.time()))
                if remaining >= 60:
                    mins, secs = divmod(remaining, 60)
                    time_left = f"{mins}分{secs}秒"
                else:
                    time_left = f"{remaining}秒"
                print(f"\n{'='*50}")
                print(f"[新周期] market_id: {market_id[:20]}...")
                print(f"[新周期] 剩余: {time_left}")
                print(f"[新周期] YES token: {self.yes_token_id[:20] if self.yes_token_id else 'None'}...")
                print(f"[新周期] NO token: {self.no_token_id[:20] if self.no_token_id else 'None'}...")
                self._print_stats()
            
            return True
        except Exception as e:
            print(f"\r{' '*60}\r[错误] _check_market: {e}", end="", flush=True)
            return False
    
    def _get_prices_fast(self) -> Optional[Dict[str, float]]:
        """快速获取价格 - 并行获取YES/NO订单簿"""
        if not self.yes_token_id or not self.no_token_id:
            return None
        
        # 检查缓存（避免频繁请求）
        now = time.time()
        if hasattr(self, '_price_cache') and hasattr(self, '_price_cache_time') and now - self._price_cache_time < PRICE_CACHE_TTL:
            return self._price_cache
        
        # 复用 requests Session
        if not hasattr(self, '_price_session'):
            self._price_session = requests.Session()
            # 连接池优化
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=4,
                pool_maxsize=4,
                max_retries=0
            )
            self._price_session.mount('https://', adapter)
        
        # 直接串行获取（更稳定，避免并行问题）
        def fetch_orderbook(token_id: str) -> dict:
            try:
                url = f"https://clob.polymarket.com/book?token_id={token_id}"
                resp = self._price_session.get(url, timeout=1.0)
                if resp.status_code == 200:
                    book = resp.json()
                    asks = book.get("asks", [])
                    bids = book.get("bids", [])
                    
                    # 排序（API通常已排序，但确保正确性）
                    if asks:
                        asks = sorted(asks, key=lambda x: float(x.get("price", "999")))
                    if bids:
                        bids = sorted(bids, key=lambda x: float(x.get("price", "0")), reverse=True)
                    
                    return {"asks": asks, "bids": bids}
            except Exception as e:
                pass
            return {"asks": [], "bids": []}
        
        # 串行获取（更稳定）
        yes_book = fetch_orderbook(self.yes_token_id)
        no_book = fetch_orderbook(self.no_token_id)
        
        # 计算价格
        def calc_price(book: dict) -> float:
            asks = book.get("asks", [])
            bids = book.get("bids", [])
            
            if asks and bids:
                best_ask = float(asks[0].get("price", 0))
                best_bid = float(bids[0].get("price", 0))
                price = (best_ask + best_bid) / 2
            elif asks:
                price = float(asks[0].get("price", 0))
            elif bids:
                price = float(bids[0].get("price", 0))
            else:
                return 0
            
            # 转换为小数格式
            if price > 1:
                price = price / 100.0
            return price
        
        yes_price = calc_price(yes_book)
        no_price = calc_price(no_book)
        
        # 验证价格合理性：YES + NO 应该接近 1
        if yes_price > 0 and no_price > 0:
            total = yes_price + no_price
            if abs(total - 1.0) > 0.15:  # 偏差超过 15%
                no_price = 1.0 - yes_price
        elif yes_price > 0:
            no_price = 1.0 - yes_price
        elif no_price > 0:
            yes_price = 1.0 - no_price
        else:
            return None
        
        # 缓存结果（同时缓存订单簿用于买入/卖出）
        result = {"YES": yes_price, "NO": no_price}
        self._price_cache = result
        self._price_cache_time = now
        self._orderbook_cache = {
            "YES": yes_book,
            "NO": no_book,
            "time": now
        }
        
        return result
    
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
    
    def _calculate_position(self, price: float = 0.5) -> float:
        """计算开仓金额（考虑最小股数限制）
        
        Args:
            price: 当前价格（用于计算最小股数对应的实际金额）
        
        Returns:
            开仓金额（确保余额足够支付最小5股）
        """
        base = self.initial_balance / 12.0
        multiplier = 1
        power = 0
        while self.balance >= self.initial_balance * (3 ** power):
            multiplier = 2 ** power
            power += 1
        
        position_amount = base * multiplier
        
        # 考虑最小股数限制：最小5股
        # 实际最小订单金额 = 5 * price
        min_order_amount = 5 * price
        
        # 确保开仓金额足够支付最小股数
        position_amount = max(position_amount, min_order_amount, 1.0)
        
        # 确保余额足够
        if position_amount > self.balance:
            # 余额不足，使用余额的90%（留一点余地）
            position_amount = self.balance * 0.9
            # 但至少要能买最小股数
            if position_amount < min_order_amount:
                # 余额不足以买最小股数
                print(f"[警告] 余额 ${self.balance:.2f} 不足以支付最小订单 ${min_order_amount:.2f}")
                return 0
        
        return position_amount
    
    def stop(self) -> None:
        """停止交易"""
        self.is_running = False
