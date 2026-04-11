"""
统一交易引擎
整合所有版本功能，支持通过参数配置切换策略

支持的功能选项：
1. 策略模式：
   - CYCLE: 周期模式（V1）
   - SINGLE: 每周期最多一次（V2）
   - CONTINUOUS: 无持仓可继续（V3/V4）

2. 买入价格（entry_price）：默认70%

3. 止损价格（stop_loss）：默认45%

4. 止盈价格（take_profit）：默认95%

5. 买入限制（buy_limit）：价格超过此值跳过，默认85%，0表示不限制

6. 最后1分钟策略：
   - last_minute_stop_loss: 最后1分钟止损价（None表示使用固定止损）
   - last_minute_take_profit: 最后1分钟止盈价（None表示不止盈）
"""
import time
import math
import sys
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Callable
import requests

from config import TradingConfig
from polymarket_api import PolymarketClient

# 性能配置
PRICE_CACHE_TTL = 2.0
MAIN_LOOP_INTERVAL = 0.05
REQUEST_TIMEOUT = 3.0


class UnifiedTrader:
    """统一交易引擎 - 支持多策略配置"""
    
    # 策略模式常量
    MODE_CYCLE = "CYCLE"           # 周期模式（V1）
    MODE_SINGLE = "SINGLE"         # 每周期最多一次（V2）
    MODE_CONTINUOUS = "CONTINUOUS" # 无持仓可继续（V3/V4）
    
    # 状态常量
    STATE_IDLE = "IDLE"
    STATE_HOLDING = "HOLDING"
    STATE_MONITORING_EXIT = "EXITING"
    
    def __init__(
        self,
        config: TradingConfig,
        # 策略参数
        mode: str = "CONTINUOUS",
        entry_price: float = 70.0,
        stop_loss: float = 45.0,
        take_profit: float = 95.0,
        buy_limit: float = 85.0,
        last_minute_stop_loss: Optional[float] = None,
        last_minute_take_profit: Optional[float] = None,
        # 回调函数（用于GUI更新）
        on_status_update: Optional[Callable] = None,
        on_trade_update: Optional[Callable] = None,
        on_log: Optional[Callable] = None,
    ):
        self.config = config
        
        # 策略参数
        self.mode = mode
        self.entry_price = entry_price / 100.0
        self.stop_loss = stop_loss / 100.0
        self.take_profit = take_profit / 100.0
        self.buy_limit = buy_limit / 100.0 if buy_limit > 0 else 1.0
        self.last_minute_stop_loss = last_minute_stop_loss / 100.0 if last_minute_stop_loss else None
        self.last_minute_take_profit = last_minute_take_profit / 100.0 if last_minute_take_profit else None
        
        # 回调函数
        self.on_status_update = on_status_update
        self.on_trade_update = on_trade_update
        self.on_log = on_log
        
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
        self.position: Optional[Dict] = None
        
        # 当前市场
        self.market_id: Optional[str] = None
        self.yes_token_id: Optional[str] = None
        self.no_token_id: Optional[str] = None
        self.event_end_time: float = 0
        
        # 交易统计
        self.total_trades = 0
        self.win_trades = 0
        self.total_pnl = 0.0
        
        # V2模式：每周期最多一次
        self.has_traded_in_event = False
        self.current_event_id: Optional[str] = None
        
        # 缓存
        self._price_cache: Optional[Dict[str, float]] = None
        self._price_cache_time: float = 0
        self._market_cache: Optional[Dict] = None
        self._market_cache_time: float = 0
        self._background_thread: Optional[threading.Thread] = None
        
        # 冷却
        self._buy_cooldown: float = 0
        self._sell_cooldown: float = 0
        self._need_check_position = False
        self._position_check_counter = 0
        self._balance_check_counter = 0
        
    def log(self, message: str, level: str = "INFO"):
        """输出日志"""
        if self.on_log:
            self.on_log(f"[{level}] {message}")
        else:
            print(f"[{level}] {message}")
    
    def update_status(self, status: str, yes_price: float = 0, no_price: float = 0, remaining: float = 0):
        """更新状态（用于GUI）"""
        if self.on_status_update:
            self.on_status_update(status, yes_price, no_price, remaining, self.balance, self.total_pnl)
    
    def update_trade(self, trade_info: Dict):
        """更新交易信息（用于GUI）"""
        if self.on_trade_update:
            self.on_trade_update(trade_info)
    
    def start(self) -> None:
        """启动交易"""
        if self.is_running:
            return
        
        # 初始化余额
        if not self._init_balance():
            self.log("无法获取余额，退出", "ERROR")
            return
        
        self.log(f"策略模式: {self.mode}")
        self.log(f"买入价格: {int(self.entry_price * 100)}%")
        self.log(f"止损价格: {int(self.stop_loss * 100)}%")
        self.log(f"止盈价格: {int(self.take_profit * 100)}%")
        self.log(f"买入限制: {int(self.buy_limit * 100)}%" if self.buy_limit < 1 else "买入限制: 无")
        
        self.is_running = True
        self.state = self.STATE_IDLE
        
        try:
            while self.is_running:
                self._main_loop()
                time.sleep(MAIN_LOOP_INTERVAL)
        except KeyboardInterrupt:
            self.log("用户中断")
        except Exception as e:
            self.log(f"错误: {e}", "ERROR")
    
    def stop(self) -> None:
        """停止交易"""
        self.is_running = False
        self.log("交易已停止")
    
    def _init_balance(self) -> bool:
        """初始化余额"""
        for _ in range(3):
            try:
                balance = self.client.get_balance()
                if balance is not None:
                    self.balance = balance
                    self.initial_balance = balance
                    self.log(f"余额: ${balance:.2f}")
                    return True
            except Exception as e:
                self.log(f"重试: {e}")
                time.sleep(2)
        return False
    
    def _main_loop(self) -> None:
        """主循环"""
        try:
            # 获取市场数据
            market_data = self._fetch_market_data()
            if not market_data:
                # 获取失败时也要更新状态
                status = self._get_status_text()
                self.update_status(status, 0, 0, 0)
                time.sleep(1)
                return
            
            yes_price = market_data.get("yes_price", 0)
            no_price = market_data.get("no_price", 0)
            remaining = market_data.get("remaining", 0)
            
            # 更新状态（价格转为百分比）
            status = self._get_status_text()
            self.update_status(status, yes_price * 100, no_price * 100, remaining)
            
            # 状态机
            if self.state == self.STATE_IDLE:
                self._handle_idle(yes_price, no_price, remaining, market_data)
            elif self.state == self.STATE_HOLDING:
                self._handle_holding(yes_price, no_price)
            elif self.state == self.STATE_MONITORING_EXIT:
                self._handle_monitoring_exit(yes_price, no_price, remaining)
                
        except Exception as e:
            self.log(f"循环错误: {e}", "ERROR")
            # 出错时也更新状态
            self.update_status("错误", 0, 0, 0)
            time.sleep(1)
    
    def _get_status_text(self) -> str:
        """获取状态文本"""
        if self.state == self.STATE_IDLE:
            if self.mode == self.MODE_SINGLE and self.has_traded_in_event:
                return "已交易"
            return "等待机会"
        elif self.state == self.STATE_HOLDING:
            return f"持仓 {self.position['token']}" if self.position else "确认中"
        elif self.state == self.STATE_MONITORING_EXIT:
            return "监控卖出"
        return "未知"
    
    def _handle_idle(self, yes_price: float, no_price: float, remaining: float, market_data: Dict) -> None:
        """空闲状态"""
        # V2模式：每周期最多一次
        if self.mode == self.MODE_SINGLE and self.has_traded_in_event:
            return
        
        # 最后60秒不买入
        if remaining <= 60:
            return
        
        # 检查是否需要检查持仓
        if self._need_check_position:
            self._check_delayed_position()
        
        # 检查买入条件
        if yes_price >= self.entry_price and yes_price < self.buy_limit:
            self._execute_buy("YES", yes_price)
        elif no_price >= self.entry_price and no_price < self.buy_limit:
            self._execute_buy("NO", no_price)
    
    def _handle_holding(self, yes_price: float, no_price: float) -> None:
        """持仓状态"""
        self.state = self.STATE_MONITORING_EXIT
    
    def _handle_monitoring_exit(self, yes_price: float, no_price: float, remaining: float) -> None:
        """监控卖出"""
        if not self.position:
            self.state = self.STATE_IDLE
            return
        
        token = self.position["token"]
        token_id = self.position["token_id"]
        entry_price = self.position["entry_price"]
        current_price = yes_price if token == "YES" else no_price
        
        # 定期检查持仓
        self._balance_check_counter += 1
        if self._balance_check_counter >= 10:
            self._balance_check_counter = 0
            actual_balance = self.client.get_token_balance(token_id)
            if actual_balance <= 0:
                self.log(f"{token} 持仓已清空")
                self._clear_position()
                return
        
        # 止损检查
        stop_loss = self.stop_loss
        if remaining <= 60 and self.last_minute_stop_loss:
            stop_loss = self.last_minute_stop_loss
        
        if current_price <= stop_loss:
            self._execute_sell("STOP_LOSS", current_price)
            return
        
        # 止盈检查
        take_profit = self.take_profit
        if remaining <= 60:
            if self.last_minute_take_profit:
                take_profit = self.last_minute_take_profit
            else:
                take_profit = None  # 最后1分钟不止盈
        
        if take_profit and current_price >= take_profit:
            self._execute_sell("TAKE_PROFIT", current_price)
            return
        
        # 事件结束
        if time.time() >= self.event_end_time:
            self._handle_event_end()
    
    def _execute_buy(self, token: str, price: float) -> None:
        """执行买入"""
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        if time.time() < self._buy_cooldown:
            return
        
        # 获取最新卖一价
        best_ask = self._get_best_ask(token, use_cache=False)
        if best_ask is None:
            self._buy_cooldown = time.time() + 2
            return
        
        # 价格检查
        if best_ask < self.entry_price:
            return
        if best_ask >= self.buy_limit:
            self.log(f"跳过 {token} 价格 {int(best_ask*100)}% >= {int(self.buy_limit*100)}%")
            return
        
        # 计算仓位
        position_amount = self._calculate_position(best_ask)
        if position_amount <= 0:
            self.log(f"余额 ${self.balance:.2f} 不足以开仓")
            return
        
        shares = max(5, math.ceil(position_amount / best_ask))
        buy_price = int(best_ask * 100)
        
        self.log(f"买入 {token} {shares}股 @ {buy_price}%")
        
        try:
            order = self.client.create_order(
                token_id=token_id,
                price=buy_price,
                size=float(shares),
                side="BUY",
                order_type="FOK",
            )
            
            if order and order.get("success") != False:
                # 等待成交
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
                    self.log(f"确认买入成功 {actual_balance:.2f}股")
                else:
                    self.log("买入未立即成交，继续监控...")
                    self._buy_cooldown = time.time() + 3
                    self._need_check_position = True
            else:
                error = order.get("errorMsg", "") if order else ""
                self.log(f"买入失败: {error}")
                self._buy_cooldown = time.time() + 2
        except Exception as e:
            self.log(f"买入错误: {e}")
            self._buy_cooldown = time.time() + 2
    
    def _execute_sell(self, reason: str, price: float) -> None:
        """执行卖出"""
        if not self.position:
            return
        
        if time.time() < self._sell_cooldown:
            return
        
        token = self.position["token"]
        token_id = self.position["token_id"]
        entry_price = self.position["entry_price"]
        
        actual_balance = self.client.get_token_balance(token_id)
        if actual_balance <= 0:
            self._clear_position()
            return
        
        size = actual_balance
        best_bid = self._get_best_bid(token, use_cache=False)
        if best_bid is None:
            self._sell_cooldown = time.time() + 2
            return
        
        sell_price = int(best_bid * 100)
        order_amount = size * best_bid
        
        if order_amount < 1.0:
            self._sell_cooldown = time.time() + 3
            return
        
        self.log(f"{reason} 卖出 {token} {size:.2f}股 @ {sell_price}%")
        
        try:
            order = self.client.create_order(
                token_id=token_id,
                price=sell_price,
                size=size,
                side="SELL",
                order_type="FOK",
            )
            
            if order and order.get("success") != False:
                time.sleep(0.1)
                self._close_position(entry_price, best_bid, size, reason)
            else:
                error = order.get("errorMsg", "") if order else ""
                self.log(f"卖出失败: {error}")
                self._sell_cooldown = time.time() + 2
        except Exception as e:
            self.log(f"卖出错误: {e}")
            self._sell_cooldown = time.time() + 2
    
    def _close_position(self, entry_price: float, exit_price: float, size: float, reason: str) -> None:
        """平仓"""
        pnl = (exit_price - entry_price) * size
        pnl_display = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        
        self.total_trades += 1
        if pnl >= 0:
            self.win_trades += 1
        self.total_pnl += pnl
        
        # 更新余额
        try:
            new_balance = self.client.get_balance()
            if new_balance:
                self.balance = new_balance
        except:
            pass
        
        self.log(f"结果: {pnl_display} ({reason})")
        
        # 更新交易信息
        self.update_trade({
            "token": self.position["token"] if self.position else "",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size": size,
            "pnl": pnl,
            "reason": reason,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
        
        self._clear_position()
    
    def _clear_position(self) -> None:
        """清除持仓状态"""
        self.position = None
        
        # 根据模式决定是否重置
        if self.mode == self.MODE_CONTINUOUS:
            self.has_traded_in_event = False
            self.log("持仓清空，继续监控")
        
        self.state = self.STATE_IDLE
    
    def _handle_event_end(self) -> None:
        """处理事件结束"""
        if not self.position:
            self.state = self.STATE_IDLE
            return
        
        token = self.position["token"]
        token_id = self.position["token_id"]
        entry_price = self.position["entry_price"]
        
        actual_balance = self.client.get_token_balance(token_id)
        if actual_balance <= 0:
            self._clear_position()
            return
        
        self.log("事件结束，等待结算...")
        
        # 等待结算
        for _ in range(30):
            time.sleep(1)
            balance = self.client.get_token_balance(token_id)
            if balance <= 0:
                break
        
        # 获取结算结果
        try:
            market = self.client.get_market_by_id(self.market_id)
            if market:
                outcome = market.get("resolvedOutcome", "")
                if outcome == "Yes":
                    exit_price = 1.0 if token == "YES" else 0.0
                else:
                    exit_price = 0.0 if token == "YES" else 1.0
                
                self._close_position(entry_price, exit_price, actual_balance, "SETTLED")
                return
        except:
            pass
        
        self.log("无法获取结算结果")
        self._clear_position()
    
    def _check_delayed_position(self) -> None:
        """检查延迟成交的持仓"""
        self._position_check_counter += 1
        if self._position_check_counter < 5:
            return
        
        self._position_check_counter = 0
        
        if self.yes_token_id:
            yes_balance = self.client.get_token_balance(self.yes_token_id)
            if yes_balance > 0:
                self.position = {
                    "token": "YES",
                    "token_id": self.yes_token_id,
                    "size": yes_balance,
                    "entry_price": self.entry_price,
                }
                self.has_traded_in_event = True
                self.state = self.STATE_HOLDING
                self._need_check_position = False
                self.log(f"发现 YES 持仓 {yes_balance:.2f}股")
                return
        
        if self.no_token_id:
            no_balance = self.client.get_token_balance(self.no_token_id)
            if no_balance > 0:
                self.position = {
                    "token": "NO",
                    "token_id": self.no_token_id,
                    "size": no_balance,
                    "entry_price": self.entry_price,
                }
                self.has_traded_in_event = True
                self.state = self.STATE_HOLDING
                self._need_check_position = False
                self.log(f"发现 NO 持仓 {no_balance:.2f}股")
    
    def _calculate_position(self, price: float) -> float:
        """计算仓位"""
        if self.balance < 1:
            return 0
        
        base_amount = self.initial_balance / 12
        multiplier = 1
        
        threshold = self.initial_balance
        while self.balance >= threshold * 3:
            multiplier *= 2
            threshold *= 3
        
        return base_amount * multiplier
    
    def _fetch_market_data(self) -> Optional[Dict]:
        """获取市场数据"""
        try:
            # 获取5分钟BTC市场
            url = "https://gamma-api.polymarket.com/markets"
            params = {
                "slug": "5-minute-btc-price-direction",
                "closed": "false",
            }
            
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                self.log(f"API返回错误: {resp.status_code}")
                return None
            
            markets = resp.json()
            if not markets:
                self.log("未找到活跃市场")
                return None
            
            market = markets[0]
            self.market_id = market.get("conditionId", "")
            
            # 解析代币ID
            tokens = market.get("tokens", [])
            for token in tokens:
                outcome = token.get("outcome", "")
                token_id = token.get("token_id", "")
                if outcome == "Yes":
                    self.yes_token_id = token_id
                elif outcome == "No":
                    self.no_token_id = token_id
            
            # 获取价格
            yes_price, no_price = self._get_prices()
            
            # 如果价格都为0，记录警告
            if yes_price == 0 and no_price == 0:
                self.log("价格获取失败，使用默认值")
                yes_price, no_price = 0.5, 0.5
            
            # 计算剩余时间
            end_date = market.get("end_date_iso")
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    self.event_end_time = end_dt.timestamp()
                    remaining = max(0, self.event_end_time - time.time())
                except:
                    remaining = 300
            else:
                remaining = 300
            
            # V2模式：检测新周期
            if self.mode == self.MODE_SINGLE:
                if self.current_event_id != self.market_id:
                    self.current_event_id = self.market_id
                    self.has_traded_in_event = False
            
            return {
                "yes_price": yes_price,
                "no_price": no_price,
                "remaining": remaining,
            }
            
        except Exception as e:
            self.log(f"获取市场数据失败: {e}")
            return None
    
    def _get_prices(self) -> tuple:
        """获取价格"""
        yes_price = 0.0
        no_price = 0.0
        
        try:
            if self.yes_token_id:
                yes_price = self._get_mid_price(self.yes_token_id)
            if self.no_token_id:
                no_price = self._get_mid_price(self.no_token_id)
        except:
            pass
        
        return yes_price, no_price
    
    def _get_mid_price(self, token_id: str) -> float:
        """获取中间价"""
        try:
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                book = resp.json()
                asks = book.get("asks", [])
                bids = book.get("bids", [])
                
                if asks:
                    asks = sorted(asks, key=lambda x: float(x.get("price", "999")))
                    best_ask = float(asks[0].get("price", 0))
                    if best_ask > 1:
                        best_ask /= 100
                else:
                    best_ask = 0.5
                
                if bids:
                    bids = sorted(bids, key=lambda x: float(x.get("price", "0")), reverse=True)
                    best_bid = float(bids[0].get("price", 0))
                    if best_bid > 1:
                        best_bid /= 100
                else:
                    best_bid = 0.5
                
                return (best_ask + best_bid) / 2
        except:
            pass
        return 0.5
    
    def _get_best_ask(self, token: str, use_cache: bool = True) -> Optional[float]:
        """获取卖一价"""
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        try:
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                book = resp.json()
                asks = book.get("asks", [])
                if asks:
                    asks = sorted(asks, key=lambda x: float(x.get("price", "999")))
                    best_ask = float(asks[0].get("price", 0))
                    if best_ask > 1:
                        best_ask /= 100
                    return best_ask
        except:
            pass
        return None
    
    def _get_best_bid(self, token: str, use_cache: bool = True) -> Optional[float]:
        """获取买一价"""
        token_id = self.yes_token_id if token == "YES" else self.no_token_id
        
        try:
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                book = resp.json()
                bids = book.get("bids", [])
                if bids:
                    bids = sorted(bids, key=lambda x: float(x.get("price", "0")), reverse=True)
                    best_bid = float(bids[0].get("price", 0))
                    if best_bid > 1:
                        best_bid /= 100
                    return best_bid
        except:
            pass
        return None
