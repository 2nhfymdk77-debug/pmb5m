"""
Polymarket API客户端
使用官方 py-clob-client
基于 Polymarket 官方文档优化：
- 使用官方推荐的 OrderArgs 和 OrderType
- 添加 get_tick_size 和 get_neg_risk
- 添加心跳机制
- 优化下单流程
"""
from typing import Optional, Dict, List, Any, Tuple
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType, PartialCreateOrderOptions
from py_clob_client.order_builder.constants import BUY, SELL
from pathlib import Path
import time
import requests
import threading
import functools
from typing import Callable, Any, Optional

# ==================== 价格辅助函数 ====================

def cents_to_float(cents: float) -> float:
    """美分格式转小数格式
    
    75 -> 0.75
    45 -> 0.45
    """
    if cents > 1:
        return cents / 100.0
    return cents

def float_to_cents(price: float) -> float:
    """小数格式转美分格式（用于下单）
    
    0.75 -> 75
    0.45 -> 45
    """
    if price <= 1:
        return price * 100.0
    return price



# === Utility Functions ===

def format_time_remaining(seconds: float) -> str:
    """格式化剩余时间为 MM:SS 格式
    
    Args:
        seconds: 剩余秒数
        
    Returns:
        格式化的字符串，如 "04:30" 或 "--:--"（如果为负数）
    """
    if seconds <= 0:
        return "--:--"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def format_price(price: float, to_cents: bool = True) -> str:
    """格式化价格
    
    Args:
        price: 价格（0-1 或 0-100）
        to_cents: 是否转换为美分格式（0.75 -> 75）
        
    Returns:
        格式化后的价格字符串
    """
    if to_cents and 0 <= price <= 1:
        price = price * 100
    return f"{price:.2f}"


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (requests.RequestException, ConnectionError, TimeoutError)
):
    """
    带指数退避的重试装饰器
    
    用于处理临时性错误，如网络超时、连接失败、429 Rate Limit 等
    
    Args:
        max_retries: 最大重试次数
        initial_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        backoff_factor: 退避因子
        exceptions: 需要重试的异常类型
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    # 检查是否是永久性错误，不需要重试
                    error_msg = str(e).lower()
                    if any(x in error_msg for x in ['401', '403', 'auth', 'unauthorized', 'invalid']):
                        # 认证错误不重试
                        raise
                    
                    if attempt < max_retries:
                        # 429 Rate Limit 特殊处理，等待稍长时间
                        if '429' in str(e) or 'rate' in error_msg:
                            delay = min(delay * 3, max_delay)  # Rate limit 时延长
                        
                        time.sleep(delay)
                        delay = min(delay * backoff_factor, max_delay)
                    else:
                        # 最后一次尝试失败
                        pass
            
            # 所有重试都失败
            if last_exception:
                raise last_exception
            return None
                
        return wrapper
    return decorator


class HeartbeatManager:
    """心跳管理器 - 保持会话活跃
    
    根据 Polymarket 文档：
    - 如果在 10 秒内（带 5 秒缓冲）未收到有效心跳，所有未完成订单将被取消
    - 需要在每个请求中包含最新的 heartbeat_id
    """
    
    def __init__(self, client: ClobClient):
        self.client = client
        self.heartbeat_id = None  # 初始为 None，表示需要创建新心跳
        self._running = False
        self._thread = None
        self._interval = 5  # 每 5 秒发送一次心跳
        self._initialized = False  # 是否已完成首次心跳初始化
    
    def start(self) -> None:
        """启动心跳"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()
        print("[OK] 心跳管理器已启动（每 5 秒发送一次）")
    
    def stop(self) -> None:
        """停止心跳"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        print("[OK] 心跳管理器已停止")
    
    def _heartbeat_loop(self) -> None:
        """心跳循环"""
        consecutive_failures = 0
        max_failures = 3  # 连续失败3次后减少日志频率
        first_run = True
        has_valid_id = False  # 是否已获得有效心跳ID
        
        while self._running:
            try:
                # 第一次尝试：使用 None 或空字符串创建心跳
                # 后续使用获取到的心跳 ID
                hb_id = self.heartbeat_id if self.heartbeat_id else ""
                
                resp = self.client.post_heartbeat(hb_id)
                if resp and "heartbeat_id" in resp:
                    old_id = self.heartbeat_id
                    self.heartbeat_id = resp["heartbeat_id"]
                    consecutive_failures = 0  # 重置失败计数
                    first_run = False
                    self._initialized = True
                    if not has_valid_id:
                        print("[OK] 心跳ID已获取，会话保持活跃")
                        has_valid_id = True
                time.sleep(self._interval)
            except Exception as e:
                consecutive_failures += 1
                error_msg = str(e)
                
                # 检查是否是 Invalid Heartbeat ID 错误
                if "Invalid Heartbeat ID" in error_msg or "invalid" in error_msg.lower():
                    # 心跳 ID 无效，重置为 None 重新创建
                    self.heartbeat_id = None
                    if consecutive_failures <= max_failures and not has_valid_id:
                        print(f"[*] 等待获取有效心跳ID（首次下单后将正常）")
                elif first_run and ("401" in error_msg or "Unauthorized" in error_msg):
                    if consecutive_failures <= max_failures:
                        print(f"[!] 心跳认证失败，请检查 API 凭证")
                elif consecutive_failures <= max_failures and has_valid_id:
                    print(f"[X] 心跳发送失败: {e}")
                    
                time.sleep(self._interval)  # 即使失败也继续尝试


class RateLimiter:
    """API 速率限制器（线程安全，支持突发）

    官方速率限制：
    - 一般限制：15,000 req / 10s
    - POST /order：峰值 500/s，持续 60/s
    - DELETE /order：峰值 300/s，持续 50/s
    """

    def __init__(self):
        self.requests = {}  # {endpoint: [timestamps]}
        self.lock = threading.Lock()  # 线程安全
        self._suppress_logs = False  # 静默模式

    def suppress_logs(self, suppress: bool = True) -> None:
        """设置是否静默模式（减少日志输出）"""
        self._suppress_logs = suppress

    def wait_if_needed(self, endpoint: str, limit: int, window: int = 10) -> None:
        """
        如果需要，等待以遵守速率限制

        Args:
            endpoint: API 端点
            limit: 时间窗口内的最大请求数
            window: 时间窗口（秒）
        """
        with self.lock:
            now = time.time()

            if endpoint not in self.requests:
                self.requests[endpoint] = []

            # 清理过期的请求
            cutoff = now - window
            self.requests[endpoint] = [
                ts for ts in self.requests[endpoint] if ts > cutoff
            ]

            # 检查是否需要等待
            if len(self.requests[endpoint]) >= limit:
                oldest = min(self.requests[endpoint])
                wait_time = (oldest + window) - now + 0.05  # 额外 50ms 缓冲
                if wait_time > 0:
                    if not self._suppress_logs:
                        print(f"[限速] 等待 {wait_time:.2f}s")
                    time.sleep(wait_time)
                    # 清理过期的请求
                    now = time.time()
                    cutoff = now - window
                    self.requests[endpoint] = [
                        ts for ts in self.requests[endpoint] if ts > cutoff
                    ]

            # 记录这次请求
            self.requests[endpoint].append(now)


class TTLCache:
    """TTL 缓存 - 带过期时间的内存缓存
    
    用于缓存市场数据、价格等会变化但不需要实时更新的数据
    """
    
    def __init__(self, default_ttl: int = 60):
        """
        Args:
            default_ttl: 默认过期时间（秒）
        """
        self._cache = {}  # {key: (value, expires_at)}
        self._lock = threading.Lock()
        self.default_ttl = default_ttl
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        with self._lock:
            if key in self._cache:
                value, expires_at = self._cache[key]
                if time.time() < expires_at:
                    return value
                else:
                    # 过期，删除
                    del self._cache[key]
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值"""
        with self._lock:
            expires_at = time.time() + (ttl if ttl is not None else self.default_ttl)
            self._cache[key] = (value, expires_at)
    
    def delete(self, key: str) -> None:
        """删除缓存"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
    
    def cleanup(self) -> int:
        """清理过期缓存，返回清理数量"""
        with self._lock:
            now = time.time()
            expired = [k for k, (_, exp) in self._cache.items() if now >= exp]
            for k in expired:
                del self._cache[k]
            return len(expired)


class PolymarketClient:
    """Polymarket API客户端（基于官方py-clob-client）

    身份验证说明：
    - 公开 API（市场、价格、订单簿）: 无需身份验证
    - 私有 API（交易、余额）: 需要 L1（私钥）+ L2（API 凭证）

    性能优化：
    - TTL缓存：市场详情 5分钟，价格 1秒
    - 速率限制：自动遵守 API 速率限制
    - 重试机制：指数退避处理临时错误
    - 线程安全：支持多线程调用
    """

    # Gamma API 基础 URL
    GAMMA_API_BASE = "https://gamma-api.polymarket.com"

    # 速率限制配置（官方文档）
    RATE_LIMITS = {
        "get_markets": (300, 10),      # Gamma API
        "get_midpoints": (1500, 10),   # CLOB API
        "get_orderbook": (1500, 10),   # CLOB API
        "create_order": (500, 1),      # 峰值 500/s，持续 60/s
        "cancel_order": (300, 10),      # 峰值 300/s，持续 50/s
    }

    # 缓存 TTL 配置（秒）
    CACHE_TTL = {
        "market_details": 300,   # 市场详情 5分钟
        "token_ids": 300,        # 代币ID 5分钟
        "prices": 1,             # 价格 1秒（实时更新）
        "orderbook": 1,           # 订单簿 1秒
    }

    def __init__(
        self,
        private_key: str = "",
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        chain_id: int = POLYGON,
        signature_type: int = 2,  # GNOSIS_SAFE (最常见)
        funder_address: str = "",
    ):
        """
        初始化API客户端

        Args:
            private_key: 钱包私钥（L1 身份验证）
            api_key: API 密钥（L2 身份验证）
            api_secret: API 密钥（L2 身份验证）
            passphrase: API 口令（L2 身份验证）
            chain_id: 链ID（默认为Polygon 137）
            signature_type: 签名类型
                - 0: EOA（标准以太坊钱包）
                - 1: POLY_PROXY（Magic Link 代理钱包）
                - 2: GNOSIS_SAFE（多签代理钱包，最常见）
            funder_address: 资金地址（代理钱包地址）
        """
        self.private_key = private_key
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder_address = funder_address

        # TTL 缓存（带过期时间）
        self.token_ids_cache = TTLCache(default_ttl=self.CACHE_TTL["token_ids"])
        self.market_details_cache = TTLCache(default_ttl=self.CACHE_TTL["market_details"])
        self.prices_cache = TTLCache(default_ttl=self.CACHE_TTL["prices"])
        self.orderbook_cache = TTLCache(default_ttl=self.CACHE_TTL["orderbook"])

        # 速率限制器（静默模式减少日志）
        self.rate_limiter = RateLimiter()
        self.rate_limiter.suppress_logs(True)
        
        # 心跳管理器
        self.heartbeat_manager: Optional[HeartbeatManager] = None
        
        # API 调用统计
        self._api_stats = {
            "calls": 0,
            "errors": 0,
            "cache_hits": 0,
            "last_reset": time.time()
        }
        self._stats_lock = threading.Lock()

        # 初始化官方客户端
        self.client: Optional[ClobClient] = None
        self.api_credentials: Optional[Dict[str, str]] = None

        try:
            # 公开 API 初始化（无需私钥）
            if not private_key:
                self.client = ClobClient(
                    host="https://clob.polymarket.com",
                    chain_id=chain_id,
                )
                print("[ 客户端初始化成功（公开 API 模式）")
                return

            # 私有 API 初始化（需要私钥和凭证）
            client_args = {
                "host": "https://clob.polymarket.com",
                "chain_id": chain_id,
                "key": private_key,
            }

            # 准备 API 凭证（L2 身份验证）
            # 只有当凭证都是非空字符串时才使用
            if api_key and api_secret and passphrase:
                # 验证凭证不是占位符或旧值
                if len(api_key) > 10 and len(api_secret) > 10 and len(passphrase) > 5:
                    self.api_credentials = ApiCreds(
                        api_key=api_key,
                        api_secret=api_secret,
                        api_passphrase=passphrase
                    )
                    client_args["creds"] = self.api_credentials
                    print("[*] 使用现有 API 凭证")
                else:
                    print("[!] 现有凭证无效，将尝试自动创建...")

            # 添加签名类型
            if signature_type:
                client_args["signature_type"] = signature_type

            # 添加 funder 地址
            if funder_address:
                client_args["funder"] = funder_address

            self.client = ClobClient(**client_args)
            print(f"[OK] 客户端初始化成功（私有 API 模式, 签名类型: {signature_type})")
            
            # 启动心跳管理器（保持会话活跃）
            self.heartbeat_manager = HeartbeatManager(self.client)
            self.heartbeat_manager.start()

            # 总是尝试自动创建 API 凭证（如果私钥有效）
            if self.private_key:
                print("[!] 正在尝试自动创建 API 凭证...")
                if self.create_api_credentials():
                    print("[OK] API 凭证创建成功")
                    # 凭证创建成功后，重新初始化 CLOBClient（使用新凭证）
                    print("[*] 重新初始化客户端，使用新凭证...")
                    self._reinit_client_with_credentials()
                    print("[OK] 客户端重新初始化完成")
                else:
                    print("[!] API 凭证创建失败，将尝试使用提供的凭证")

        except Exception as e:
            print(f"[X] 初始化客户端失败: {e}")
            import traceback
            traceback.print_exc()

    # ==================== 市场数据方法 ====================

    def get_tick_size(self, token_id: str) -> str:
        """
        获取代币的最小价格变动单位
        
        根据 Polymarket 官方文档：
        - 每个订单都需要指定 tickSize
        - 常见值：0.1 (1位小数), 0.01 (2位小数), 0.001 (3位小数), 0.0001 (4位小数)
        
        Args:
            token_id: 代币ID
            
        Returns:
            tick_size 字符串（如 "0.01"）
        """
        if not self.client or not token_id:
            return "0.01"  # 默认值
        
        try:
            tick_size = self.client.get_tick_size(token_id)
            if tick_size:
                return str(tick_size)
        except Exception as e:
            print(f"获取 tick_size 失败: {e}")
        
        return "0.01"  # 默认值
    
    def get_neg_risk(self, token_id: str) -> bool:
        """
        获取代币的 neg_risk 标志
        
        根据 Polymarket 官方文档：
        - 多结果事件（3个及以上结果）使用 Neg Risk CTF Exchange
        - 需要传递 negRisk: true
        
        Args:
            token_id: 代币ID
            
        Returns:
            True 如果是 neg_risk 市场，否则 False
        """
        if not self.client or not token_id:
            return False
        
        try:
            is_neg_risk = self.client.get_neg_risk(token_id)
            return bool(is_neg_risk)
        except Exception as e:
            print(f"获取 neg_risk 失败: {e}")
            return False
    
    def get_market_options(self, token_id: str) -> Dict[str, Any]:
        """
        获取市场的完整交易选项（tick_size 和 neg_risk）
        
        Args:
            token_id: 代币ID
            
        Returns:
            {"tick_size": "0.01", "neg_risk": False}
        """
        return {
            "tick_size": self.get_tick_size(token_id),
            "neg_risk": self.get_neg_risk(token_id)
        }

    def get_markets(
        self, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """获取市场列表（优化：应用速率限制）"""
        if not self.client:
            return []

        try:
            # 应用速率限制
            rate_limit, window = self.RATE_LIMITS.get("get_markets", (300, 10))
            self.rate_limiter.wait_if_needed("get_markets", rate_limit, window)

            response = self.client.get_markets()
            return response.get("data", [])
        except Exception as e:
            print(f"获取市场列表失败: {e}")
            return []

    def get_tradable_markets(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取可交易市场（使用Gamma API获取活跃事件）"""
        if not self.client:
            return []
        try:
            import requests
            # 使用 Gamma API 获取活跃事件（包含 slug）
            url = "https://gamma-api.polymarket.com/events"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                events = response.json()
                print(f"[*] 获取到 {len(events)} 个活跃事件")
                # 事件包含 markets 数组，我们需要提取市场信息
                result = []
                for event in events:
                    markets = event.get("markets", [])
                    for market in markets:
                        # 将事件信息添加到市场信息中
                        market_copy = market.copy()
                        market_copy["slug"] = event.get("slug", "")
                        market_copy["event_title"] = event.get("title", "")
                        result.append(market_copy)
                return result
            else:
                print(f"获取活跃事件失败: {response.status_code} - {response.text[:200]}")
                return []
        except Exception as e:
            print(f"获取可交易市场失败: {e}")
            return []

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """通过 slug 获取市场详情（官方推荐方式）"""
        try:
            import requests
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            headers = {"Accept": "application/json"}
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                events = response.json()
                if events and len(events) > 0:
                    event = events[0]
                    markets = event.get("markets", [])
                    if markets:
                        market = markets[0]
                        market["slug"] = event.get("slug", "")
                        market["event_title"] = event.get("title", "")
                        return market
                print(f"[!] 未找到市场: {slug}")
                return None
            else:
                print(f"获取市场失败: {response.status_code}")
                return None
        except Exception as e:
            print(f"获取市场详情失败: {e}")
            return None

    def get_btc_5min_markets(self, limit: int = 10) -> List[Dict[str, Any]]:
        """专门获取BTC 5分钟预测市场"""
        try:
            import requests
            url = "https://gamma-api.polymarket.com/events"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit
            }
            headers = {"Accept": "application/json"}
            response = requests.get(url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                events = response.json()
                print(f"[*] 获取到 {len(events)} 个活跃事件")
                
                # 调试：打印前10个事件的slug
                print(f"[调试] 前10个事件slug:")
                for i, event in enumerate(events[:10]):
                    slug = event.get("slug", "")
                    title = event.get("title", "")[:40]
                    print(f"    {i+1}. {slug} - {title}")
                
                # 查找是否有任何包含 btc 或 updown 的 slug
                btc_slugs = [e.get("slug", "") for e in events if "btc" in e.get("slug", "").lower() or "updown" in e.get("slug", "").lower()]
                if btc_slugs:
                    print(f"[调试] 包含btc/updown的slug: {btc_slugs}")
                
                result = []
                for event in events:
                    slug = event.get("slug", "").lower()
                    # 匹配 btc-updown-5m-xxx 格式
                    if "btc-updown-5m" in slug:
                        markets = event.get("markets", [])
                        for market in markets:
                            market_copy = market.copy()
                            market_copy["slug"] = event.get("slug", "")
                            market_copy["event_title"] = event.get("title", "")
                            result.append(market_copy)
                        if len(result) >= limit:
                            break
                print(f"[*] 匹配到 {len(result)} 个BTC 5分钟市场")
                return result
            else:
                print(f"获取BTC市场失败: {response.status_code} - {response.text[:200]}")
                return []
        except Exception as e:
            print(f"获取BTC市场失败: {e}")
            return []

    def get_crypto_markets(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取加密货币市场（专门用于BTC 5分钟预测）"""
        if not self.client:
            return []
        try:
            import requests
            # 首先获取加密标签
            tags_response = requests.get(
                "https://gamma-api.polymarket.com/tags",
                headers={"Accept": "application/json"},
                timeout=10
            )
            crypto_tag_id = None
            
            if tags_response.status_code == 200:
                tags = tags_response.json()
                # 查找加密相关标签
                for tag in tags:
                    tag_name = tag.get("name", "").lower()
                    if "crypto" in tag_name or "bitcoin" in tag_name:
                        crypto_tag_id = tag.get("id")
                        print(f"[*] 找到加密标签: {tag.get('name')}, ID: {crypto_tag_id}")
                        break
            
            # 使用默认参数获取事件
            url = "https://gamma-api.polymarket.com/events"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit
            }
            
            if crypto_tag_id:
                params["tag_id"] = crypto_tag_id
            
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                events = response.json()
                print(f"[*] 获取到 {len(events)} 个事件")
                
                # 提取市场信息
                result = []
                for event in events:
                    markets = event.get("markets", [])
                    for market in markets:
                        market_copy = market.copy()
                        market_copy["slug"] = event.get("slug", "")
                        market_copy["event_title"] = event.get("title", "")
                        result.append(market_copy)
                return result
            else:
                print(f"获取加密事件失败: {response.status_code}")
                return []
        except Exception as e:
            print(f"获取加密市场失败: {e}")
            return []

    # ==================== 价格方法 ====================

    def get_midpoints(
        self, token_ids: List[str]
    ) -> Dict[str, float]:
        """
        批量获取中间价（优化：应用速率限制）

        Args:
            token_ids: 代币ID列表

        Returns:
            {token_id: price}
        """
        if not self.client or not token_ids:
            return {}

        try:
            # 应用速率限制
            rate_limit, window = self.RATE_LIMITS.get("get_midpoints", (1500, 10))
            self.rate_limiter.wait_if_needed("get_midpoints", rate_limit, window)

            from py_clob_client.clob_types import BookParams

            # 创建 BookParams 列表（官方 SDK 需要）
            book_params = [BookParams(token_id=tid, side="0") for tid in token_ids]

            response = self.client.get_midpoints(book_params)
            midpoints = response.get("midpoints", {})
            
            # 转换价格格式：如果是 0-100 格式，转换为 0-1 格式
            converted = {}
            for token_id, price in midpoints.items():
                if price is not None:
                    # 如果价格大于 1，认为是美分格式（0-100），转换为小数格式（0-1）
                    if price > 1:
                        converted[token_id] = price / 100
                    else:
                        converted[token_id] = price
                else:
                    converted[token_id] = 0.0
            
            return converted
        except Exception as e:
            print(f"获取中间价失败: {e}")
            return {}

    def get_market_prices(
        self, market_id: str
    ) -> Dict[str, float]:
        """
        获取市场价格（统一返回 0-1 小数格式）

        Args:
            market_id: 市场ID

        Returns:
            {"YES": price, "NO": price}  # 价格范围 0.0 - 1.0
        """
        token_ids = self.get_token_ids(market_id)
        if not token_ids:
            return {}

        yes_token_id = token_ids.get("YES")
        no_token_id = token_ids.get("NO")

        if not yes_token_id or not no_token_id:
            return {}

        # 方法1: 尝试使用 get_midpoints
        try:
            midpoints = self.get_midpoints([yes_token_id, no_token_id])
            
            if midpoints:
                # 直接用 token_id 作为 key 获取
                yes_price = midpoints.get(yes_token_id)
                no_price = midpoints.get(no_token_id)
                
                # 如果找不到，尝试用数值索引 0 和 1
                if yes_price is None:
                    yes_price = midpoints.get(0)
                if no_price is None:
                    no_price = midpoints.get(1)
                
                if yes_price is not None and no_price is not None:
                    yes_price = float(yes_price)
                    no_price = float(no_price)
                    # 确保价格是 0-1 格式
                    yes_price = cents_to_float(yes_price)
                    no_price = cents_to_float(no_price)
                    return {"YES": yes_price, "NO": no_price}
        except Exception as e:
            print(f"get_midpoints 失败: {e}")

        # 方法2: 尝试使用 get_orderbook 获取价格
        try:
            yes_orderbook = self.get_orderbook(yes_token_id)
            no_orderbook = self.get_orderbook(no_token_id)
            
            yes_bids = yes_orderbook.get("bids", [])
            yes_asks = yes_orderbook.get("asks", [])
            no_bids = no_orderbook.get("bids", [])
            no_asks = no_orderbook.get("asks", [])
            
            if yes_bids and yes_asks:
                # 买一和卖一的中间价
                yes_price = (float(yes_bids[0].get("price", 0)) + float(yes_asks[0].get("price", 0))) / 2
                # 确保是 0-1 格式
                yes_price = cents_to_float(yes_price)
                
                # NO 价格应该从 NO 的订单簿获取
                if no_bids and no_asks:
                    no_price = (float(no_bids[0].get("price", 0)) + float(no_asks[0].get("price", 0))) / 2
                    no_price = cents_to_float(no_price)
                else:
                    # 如果没有 NO 订单簿数据，使用 1 - YES
                    no_price = 1.0 - yes_price
                
                if yes_price > 0:
                    return {"YES": yes_price, "NO": no_price}
        except Exception as e:
            print(f"get_orderbook 获取价格失败: {e}")

        # 方法3: 直接从市场数据获取
        try:
            market = self.get_market_by_id(market_id)
            if market:
                # 尝试从市场数据中获取价格
                outcome_prices = market.get("outcomePrices", [])
                if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                    yes_price = float(outcome_prices[0]) if outcome_prices[0] else 0
                    no_price = float(outcome_prices[1]) if outcome_prices[1] else 0
                    # Gamma API 返回的是百分比（如 75 表示 75%），转换为小数
                    yes_price = cents_to_float(yes_price)
                    no_price = cents_to_float(no_price)
                    if yes_price > 0:
                        return {"YES": yes_price, "NO": no_price}
                
                # 尝试其他可能的价格字段
                for field in ["yes_price", "no_price", "price", "current_price"]:
                    if field in market and market[field]:
                        price = float(market[field])
                        price = cents_to_float(price)
                        if price > 0:
                            yes_price = price
                            no_price = 1.0 - price
                            return {"YES": yes_price, "NO": no_price}
        except Exception as e:
            print(f"市场数据获取价格失败: {e}")

        return {}

    # ==================== 订单簿方法 ====================

    def get_market_orderbook(
        self, market_id: str
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """
        获取市场订单簿

        Args:
            market_id: 市场ID

        Returns:
            {"YES": {"bids": [...], "asks": [...]}, "NO": {...}}
        """
        token_ids = self.get_token_ids(market_id)
        if not token_ids:
            return {}

        yes_token_id = token_ids.get("YES")
        no_token_id = token_ids.get("NO")

        if not yes_token_id or not no_token_id:
            return {}

        return {
            "YES": self.get_orderbook(yes_token_id),
            "NO": self.get_orderbook(no_token_id),
        }

    def get_orderbook(
        self, token_id: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        获取订单簿（优化：应用速率限制）

        Args:
            token_id: 代币ID

        Returns:
            {"bids": [...], "asks": [...]}
        """
        if not self.client or not token_id:
            return {"bids": [], "asks": []}

        try:
            # 应用速率限制
            rate_limit, window = self.RATE_LIMITS.get("get_orderbook", (1500, 10))
            self.rate_limiter.wait_if_needed("get_orderbook", rate_limit, window)

            response = self.client.get_order_book(token_id)

            # OrderBookSummary 是对象，不是字典
            bids_data = response.bids if hasattr(response, 'bids') else []
            asks_data = response.asks if hasattr(response, 'asks') else []

            # 转换 OrderSummary 为字典，并处理字符串类型的 price 和 size
            bids = [
                {
                    "price": float(bid.price) if bid.price else 0.0,
                    "size": float(bid.size) if bid.size else 0.0,
                }
                for bid in bids_data
            ] if bids_data else []

            asks = [
                {
                    "price": float(ask.price) if ask.price else 0.0,
                    "size": float(ask.size) if ask.size else 0.0,
                }
                for ask in asks_data
            ] if asks_data else []
            
            # 统一转换价格格式（如果是 0-100 格式，转换为 0-1 格式）
            def convert_price(price):
                if price > 1:
                    return price / 100
                return price
            
            bids_converted = [
                {"price": convert_price(bid["price"]), "size": bid["size"]}
                for bid in bids
            ]
            
            asks_converted = [
                {"price": convert_price(ask["price"]), "size": ask["size"]}
                for ask in asks
            ]

            return {"bids": bids_converted, "asks": asks_converted}

        except Exception as e:
            print(f"获取订单簿失败: {e}")
            return {"bids": [], "asks": []}

    # ==================== 身份验证方法 ====================

    def create_api_credentials(self) -> Optional[Dict[str, str]]:
        """
        创建或派生 API 凭证（L1 身份验证）

        使用私钥创建 API 凭证，用于 L2 身份验证。
        如果凭证已存在，则派生现有凭证。

        Returns:
            {
                "apiKey": "uuid",
                "secret": "base64_encoded_secret",
                "passphrase": "random_string"
            }
            或 None（如果失败）
        """
        if not self.client or not self.private_key:
            print("[X] 需要私钥才能创建 API 凭证")
            return None

        try:
            print(f"[*] 私钥长度: {len(self.private_key)}")
            print(f"[*] 客户端状态: {self.client is not None}")
            print(f"[*] 正在调用 create_or_derive_api_creds()...")
            
            creds_obj = self.client.create_or_derive_api_creds()
            print(f"[*] 返回类型: {type(creds_obj)}")
            print(f"[*] 返回内容: {creds_obj}")
            
            if creds_obj is None:
                print("[X] create_or_derive_api_creds() 返回 None")
                return None
            
            # ApiCreds 对象的属性访问（注意：属性名是 api_key, api_secret, api_passphrase）
            try:
                self.api_key = creds_obj.api_key
                self.api_secret = creds_obj.api_secret
                self.passphrase = creds_obj.api_passphrase
                print(f"[*] 提取凭证成功: api_key={self.api_key[:10]}...")
            except AttributeError as e:
                print(f"[X] ApiCreds 对象缺少属性: {e}")
                return None

            # 创建 ApiCreds 对象供 SDK 使用
            credentials = ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.passphrase
            )

            # 保存凭证（用于 .env 文件，使用 SDK 期望的键名）
            env_credentials = {
                "key": self.api_key,
                "secret": self.api_secret,
                "passphrase": self.passphrase
            }

            # 保存凭证
            self.api_credentials = credentials

            print(f"[OK] API 凭证创建/派生成功")
            print(f"  API Key: {self.api_key[:20]}...")
            print(f"  Secret: {self.api_secret[:20]}...")
            print(f"  Passphrase: {self.passphrase[:10]}...")

            # 保存到 .env 文件
            self._save_credentials_to_env(env_credentials)

            # 返回凭证信息
            return credentials

        except Exception as e:
            print(f"[X] 创建 API 凭证失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _save_credentials_to_env(self, credentials: Dict[str, str]) -> bool:
        """保存 API 凭证到 .env 文件"""
        try:
            env_path = Path.cwd() / ".env"
            
            # 读取现有 .env 内容（如果存在）
            existing_lines = []
            if env_path.exists():
                with open(env_path, 'r', encoding='utf-8') as f:
                    existing_lines = f.readlines()
            
            # 更新或添加凭证
            lines_to_write = []
            api_key_line = f"API_KEY={credentials.get('key', '')}\n"
            api_secret_line = f"API_SECRET={credentials.get('secret', '')}\n"
            passphrase_line = f"PASSPHRASE={credentials.get('passphrase', '')}\n"
            
            api_key_added = False
            api_secret_added = False
            passphrase_added = False
            
            for line in existing_lines:
                if line.startswith("API_KEY="):
                    lines_to_write.append(api_key_line)
                    api_key_added = True
                elif line.startswith("API_SECRET="):
                    lines_to_write.append(api_secret_line)
                    api_secret_added = True
                elif line.startswith("PASSPHRASE="):
                    lines_to_write.append(passphrase_line)
                    passphrase_added = True
                else:
                    lines_to_write.append(line)
            
            # 添加缺失的凭证行
            if not api_key_added:
                lines_to_write.append(api_key_line)
            if not api_secret_added:
                lines_to_write.append(api_secret_line)
            if not passphrase_added:
                lines_to_write.append(passphrase_line)
            
            # 写回文件
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(lines_to_write)

            print(f"[OK] 凭证已保存到 .env 文件")
            print(f"    API_KEY: {credentials.get('key', '')[:10]}...")
            print(f"    API_SECRET: {credentials.get('secret', '')[:10]}...")
            return True

        except Exception as e:
            print(f"[X] 保存凭证失败: {e}")
            return False

    def _reinit_client_with_credentials(self) -> bool:
        """使用新创建的凭证重新初始化 CLOBClient"""
        try:
            # 停止旧的心跳管理器
            if self.heartbeat_manager:
                self.heartbeat_manager.stop()
                print("[*] 旧心跳管理器已停止")

            # 重新初始化客户端（使用已创建的凭证）
            print(f"[*] 重新初始化客户端，凭证类型: {type(self.api_credentials)}")
            print(f"[*] 凭证内容: {self.api_credentials}")
            
            if self.api_credentials is None:
                print("[X] 凭证为 None，无法重新初始化客户端")
                return False
            
            self.client = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=137,  # Polygon
                key=self.private_key,
                creds=self.api_credentials,  # 使用新创建的凭证
                signature_type=2  # L2 签名
            )
            
            # 验证客户端是否正确初始化
            print(f"[*] 新客户端的 creds: {self.client.creds}")
            print("[OK] CLOBClient 重新初始化成功")

            # 重新启动心跳管理器
            self.heartbeat_manager = HeartbeatManager(self.client)
            self.heartbeat_manager.start()
            print("[OK] 新心跳管理器已启动")

            return True

        except Exception as e:
            print(f"[X] 重新初始化客户端失败: {e}")
            import traceback
            traceback.print_exc()
            return False


    # ==================== 授权管理方法 ====================
    
    def check_and_initialize_allowance(self) -> Dict[str, Any]:
        """
        检查并初始化授权
        
        根据 Polymarket 官方文档：
        - 下单前必须授权 Exchange 合约使用你的资产
        - BUY 订单需要 USDC.e 授权额度 >= 花费金额
        
        Returns:
            {"balance": float, "allowance": float, "initialized": bool}
        """
        if not self.client:
            return {"balance": 0.0, "allowance": 0.0, "initialized": False, "error": "Client not initialized"}
        
        result = {
            "balance": 0.0,
            "allowance": 0.0,
            "initialized": False,
            "error": None
        }
        
        try:
            # 1. 检查当前授权状态
            print("[*] 检查授权状态...")
            try:
                # 绕过 SDK 的 bug：必须传入 BalanceAllowanceParams 对象，且需要指定 asset_type
                from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                allowance_info = self.client.get_balance_allowance(params)
                if allowance_info and isinstance(allowance_info, dict):
                    result["balance"] = float(allowance_info.get("balance", 0) or 0)
                    result["allowance"] = float(allowance_info.get("allowance", 0) or 0)
                    print(f"[*] 当前余额: ${result['balance']:.2f}")
                    print(f"[*] 当前授权额度: ${result['allowance']:.2f}")
                    # 即使 API 调用成功，也标记为可能需要初始化
                    if result["allowance"] > 0:
                        result["initialized"] = True
                else:
                    print(f"[!] 获取授权信息返回空，继续尝试初始化...")
            except Exception as e:
                error_str = str(e)
                print(f"[!] get_balance_allowance() 失败: {error_str}")
                # API 调用失败时，继续尝试初始化
                result["error"] = error_str
            
            # 2. 尝试更新授权（如果需要）
            needs_update = result["allowance"] < result["balance"] or result["allowance"] == 0
            if needs_update:
                print("[*] 尝试更新授权...")
                try:
                    update_result = self.client.update_balance_allowance()
                    if update_result and isinstance(update_result, dict):
                        success = update_result.get("success", False)
                        new_allowance = update_result.get("value", "")
                        
                        if success or new_allowance:
                            # 检查是否设置了无限授权
                            if "115792089237316195423570985008687907853269984665640564039457584007913129639935" in str(new_allowance):
                                print("[OK] 已设置无限授权额度")
                                result["allowance"] = float("inf")
                                result["initialized"] = True
                            else:
                                result["allowance"] = float(new_allowance) if new_allowance else float("inf")
                                result["initialized"] = True
                            print(f"[OK] 授权更新成功")
                        else:
                            print(f"[!] 授权更新返回无效结果，但继续运行")
                            result["initialized"] = True  # 继续尝试
                    else:
                        print(f"[!] 授权更新返回空，但继续运行")
                        result["initialized"] = True  # 继续尝试
                except Exception as e:
                    print(f"[!] update_balance_allowance() 失败: {e}")
                    print(f"[!] 将尝试直接进行交易...")
                    result["error"] = str(e)
                    result["initialized"] = True  # 继续尝试，不阻塞用户
            else:
                print("[OK] 授权状态正常")
                result["initialized"] = True
                
            # 3. 再次检查余额确认
            if result["initialized"]:
                try:
                    # 绕过 SDK 的 bug：必须指定 asset_type
                    from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
                    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                    resp = self.client.get_balance_allowance(params)
                    if resp:
                        if isinstance(resp, dict):
                            balance = resp.get("balance", 0)
                        else:
                            balance = resp
                        result["balance"] = float(balance)
                        print(f"[OK] 最终余额: ${result['balance']:.2f}")
                except Exception:
                    pass
                    
        except Exception as e:
            print(f"[X] 授权检查失败: {e}")
            result["error"] = str(e)
        
        return result

    def has_api_credentials(self) -> bool:
        """检查是否有完整的 API 凭证"""
        return all([
            self.api_key,
            self.api_secret,
            self.passphrase
        ])

    def get_wallet_address(self) -> Optional[str]:
        """
        获取钱包地址

        Returns:
            钱包地址字符串或 None
        """
        if not self.client or not self.private_key:
            return None

        try:
            address = self.client.get_address()
            return address
        except Exception as e:
            print(f"[X] 获取钱包地址失败: {e}")
            return None

    # ==================== 交易方法 ====================

    def get_balance(self) -> float:
        """获取账户余额"""
        if not self.client:
            print("[!] get_balance: client 未初始化")
            return 0.0

        # 方法1: 使用 get_balance_allowance（SDK 推荐方式）
        try:
            print("[*] get_balance: 尝试 get_balance_allowance...")
            
            # 绕过 SDK 的 bug：必须传入 BalanceAllowanceParams 对象，不能是 None
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            
            resp = self.client.get_balance_allowance(params)
            print(f"[*] get_balance: 响应类型 = {type(resp)}")
            print(f"[*] get_balance: 原始内容 = {resp}")
            
            # 如果 balance 值很大，可能是以更小单位返回（如 wei）
            # USDC 通常是 6 位小数，所以需要除以 10^6
            if resp and isinstance(resp, dict):
                raw_balance = resp.get("balance", 0)
                print(f"[*] get_balance: 原始余额值 = {raw_balance}")
            
            if resp is None:
                print("[!] get_balance: 响应为 None")
            elif isinstance(resp, dict):
                raw_balance = resp.get("balance", 0) or 0
                
                # 处理 allowances 字典（取第一个值）
                allowances = resp.get("allowances", {})
                if isinstance(allowances, dict) and allowances:
                    raw_allowance = list(allowances.values())[0]
                else:
                    raw_allowance = resp.get("allowance", 0) or 0
                
                # 转换余额（balance 可能是字符串，需要先转为数字）
                # USDC 通常是 6 位小数，如果余额 > 10000，除以 10^6
                try:
                    balance = float(str(raw_balance).strip('"'))
                except (ValueError, TypeError):
                    balance = 0.0
                    
                if balance > 10000:
                    balance = balance / 1000000  # 转换为 USDC
                
                try:
                    allowance = float(str(raw_allowance).strip('"'))
                except (ValueError, TypeError):
                    allowance = 0.0
                    
                if allowance > 10000 and allowance != float("inf"):
                    allowance = allowance / 1000000
                
                # 如果授权额度是天文数字，认为是无限授权
                if allowance > 1e10:
                    allowance_display = "无限"
                else:
                    allowance_display = f"{allowance}"
                
                print(f"[*] get_balance: balance={balance}, allowance={allowance_display}")
                return balance
            elif isinstance(resp, (float, int)):
                return float(resp)
            else:
                print(f"[!] get_balance: 未知响应类型 {type(resp)}")
        except Exception as e:
            print(f"[!] get_balance: get_balance_allowance 失败: {e}")

        return 0.0

    def _get_balance_direct(self) -> float:
        """方法1: 直接获取余额（保留兼容性）"""
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            resp = self.client.get_balance_allowance(params)
            if resp and isinstance(resp, (dict, float, int)):
                if isinstance(resp, dict):
                    return float(resp.get("balance", 0) or 0)
                return float(resp)
        except Exception as e:
            print(f"[!] _get_balance_direct 失败: {e}")
        return None

    def _get_balance_from_allowance(self) -> float:
        """方法2: 从 allowance 获取余额"""
        # SDK 没有单独的方法，保持兼容
        return None

    def _get_usdc_balance(self) -> float:
        """方法3: 获取 USDC 余额"""
        # SDK 没有这个方法，保持兼容
        return None

    def _get_wallet_balance(self) -> float:
        """方法4: 获取钱包余额"""
        try:
            # SDK 可能没有这个方法，尝试使用 get_address 获取钱包地址
            address = self.client.get_address()
            print(f"[*] 钱包地址: {address}")
            # 注意：钱包余额需要通过其他 API 获取，这里返回 None
        except Exception as e:
            print(f"[!] _get_wallet_balance 失败: {e}")
        return None

    def create_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,  # "BUY" or "SELL"
        order_type: str = "GTC",  # GTC, GTD, FOK, FAK
        expiration: int = None,  # GTD 订单的过期时间戳
    ) -> Dict[str, Any]:
        """
        创建并提交订单（官方推荐方式）
        
        根据 Polymarket 官方文档：
        - 使用 create_and_post_order() 一步完成创建、签名和提交
        - GTC: Good Till Cancelled - 挂单直到成交或取消（默认）
        - GTD: Good Till Date - 到指定时间自动过期
        - FOK: Fill Or Kill - 全部成交或立即取消
        - FAK: Fill And Kill - 成交可成交的部分，取消剩余
        
        Args:
            token_id: 代币ID
            price: 价格（支持两种格式：整数如75 或小数如0.75）
            size: 数量（股数）
            side: 方向 (BUY/SELL)
            order_type: 订单类型 (GTC/GTD/FOK/FAK)
            expiration: GTD 订单的过期时间戳（Unix 时间戳 + 60秒缓冲）

        Returns:
            订单响应，包含 orderID, status 等字段
        """
        if not self.client:
            return {"success": False, "errorMsg": "Client not initialized"}

        try:
            # 价格格式转换：Polymarket API 使用小数格式（0.75），不是整数（75）
            if price > 1:
                api_price = price / 100.0
            else:
                api_price = price

            # 获取市场的 tick_size 和 neg_risk
            tick_size = self.get_tick_size(token_id)
            neg_risk = self.get_neg_risk(token_id)
            
            # 限价单：使用官方推荐的 OrderArgs（直接作为位置参数）
            # OrderArgs 签名: (token_id, price, size, side, fee_rate_bps=0, nonce=0, expiration=0, taker='0x...')
            # GTD 订单需要设置 expiration 参数
            expiration_time = expiration if order_type == "GTD" and expiration else 0
            args = OrderArgs(
                token_id=token_id,
                price=api_price,
                size=size,
                side=side.upper(),
                expiration=expiration_time,
            )
            
            # 使用 PartialCreateOrderOptions 对象传递 tick_size 和 neg_risk
            order_options = PartialCreateOrderOptions(
                tick_size=tick_size,
                neg_risk=neg_risk,
            )
            response = self.client.create_and_post_order(
                args,
                options=order_options,
            )

            # 解析响应
            if response:
                order_id = response.get("orderID") or response.get("order_id", "")
                status = response.get("status", "unknown")
                success = response.get("success", True) and response.get("errorMsg", "") == ""
                
                print(f"[OK] 订单创建成功: ID={order_id[:20]}..., status={status}")
                return {
                    "success": success,
                    "orderID": order_id,
                    "status": status,
                    "errorMsg": response.get("errorMsg", ""),
                    "filled": 0,
                }
            else:
                return {"success": False, "errorMsg": "Empty response"}

        except Exception as e:
            print(f"[X] 创建订单失败: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "errorMsg": str(e)}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """取消订单"""
        if not self.client or not order_id:
            return {"success": False, "errorMsg": "Invalid order ID"}

        try:
            response = self.client.cancel(order_id)
            success = response.get("success", False) if response else False
            if success:
                print(f"[OK] 订单已取消: {order_id[:20]}...")
            else:
                print(f"[X] 取消订单失败: {response.get('errorMsg', 'Unknown error')}")
            return response or {"success": False}
        except Exception as e:
            print(f"[X] 取消订单失败: {e}")
            return {"success": False, "errorMsg": str(e)}

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """获取未成交订单"""
        if not self.client:
            return []

        try:
            response = self.client.get_orders()
            return response.get("orders", [])
        except Exception as e:
            print(f"获取未成交订单失败: {e}")
            return []
    
    def get_pending_orders_count(self) -> int:
        """获取未成交订单数量（快速检查）"""
        return len(self.get_open_orders())

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个订单状态
        
        Args:
            order_id: 订单ID
            
        Returns:
            订单信息字典，包含 filled_size 等字段
        """
        if not self.client or not order_id:
            return None

        try:
            response = self.client.get_order(order_id)
            return response
        except Exception as e:
            print(f"获取订单状态失败: {e}")
            return None

    # ==================== 便捷方法 ====================

    def get_token_ids(self, market_id: str) -> Dict[str, str]:
        """
        获取市场的代币ID（带 TTL 缓存）

        Args:
            market_id: 市场ID

        Returns:
            {"YES": "xxx", "NO": "xxx"}
        """
        # 检查 TTL 缓存
        cached = self.token_ids_cache.get(market_id)
        if cached is not None:
            self._record_cache_hit()
            return cached

        # 获取市场详情
        market = self.get_market_by_id(market_id)
        if not market:
            return {}

        # 获取 token IDs，处理不同的数据格式
        token_ids_raw = market.get("clobTokenIds", {}) or market.get("tokens", {})
        token_ids = {}

        if isinstance(token_ids_raw, dict):
            token_ids = token_ids_raw
        elif isinstance(token_ids_raw, str):
            try:
                import json
                parsed = json.loads(token_ids_raw)
                if isinstance(parsed, dict):
                    token_ids = parsed
                elif isinstance(parsed, list) and len(parsed) >= 2:
                    token_ids = {"YES": parsed[0], "NO": parsed[1]}
            except:
                pass
        elif isinstance(token_ids_raw, list) and len(token_ids_raw) >= 2:
            token_ids = {"YES": token_ids_raw[0], "NO": token_ids_raw[1]}

        # 验证
        if isinstance(token_ids, dict) and "YES" in token_ids and "NO" in token_ids:
            self.token_ids_cache.set(market_id, token_ids)
            return token_ids

        return {}

    def clear_cache(self) -> None:
        """清除所有缓存"""
        self.token_ids_cache.clear()
        self.market_details_cache.clear()
        self.prices_cache.clear()
        self.orderbook_cache.clear()

    def get_api_stats(self) -> Dict[str, Any]:
        """获取 API 调用统计"""
        with self._stats_lock:
            stats = self._api_stats.copy()
            stats["uptime"] = time.time() - stats["last_reset"]
            return stats

    def _record_api_call(self, success: bool = True) -> None:
        """记录 API 调用"""
        with self._stats_lock:
            self._api_stats["calls"] += 1
            if not success:
                self._api_stats["errors"] += 1

    def _record_cache_hit(self) -> None:
        """记录缓存命中"""
        with self._stats_lock:
            self._api_stats["cache_hits"] += 1

    def get_market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        """通过ID获取市场（带 TTL 缓存）

        使用 Gamma API 直接查询，避免获取全部市场列表
        """
        # 检查 TTL 缓存
        cached = self.market_details_cache.get(market_id)
        if cached is not None:
            self._record_cache_hit()
            return cached

        # 应用速率限制
        limit, window = self.RATE_LIMITS.get("get_markets", (300, 10))
        self.rate_limiter.wait_if_needed("get_markets", limit, window)

        try:
            # 使用 Gamma API 直接查询
            url = f"{self.GAMMA_API_BASE}/markets?condition_id={market_id}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            self._record_api_call(True)

            data = response.json()

            # 处理响应格式
            if isinstance(data, list):
                markets = data
            elif isinstance(data, dict) and "data" in data:
                markets = data["data"]
            else:
                return None

            if markets and isinstance(markets, list):
                market = markets[0]
                
                # 处理代币信息字段
                if "clobTokenIds" not in market or not market["clobTokenIds"]:
                    for field in ["token", "tokens", "clobTokenId"]:
                        if field in market and market[field]:
                            if isinstance(market[field], dict):
                                market["clobTokenIds"] = market[field]
                                break
                            elif isinstance(market[field], str):
                                try:
                                    import json
                                    market["clobTokenIds"] = json.loads(market[field])
                                    break
                                except:
                                    pass
                
                # 缓存结果
                self.market_details_cache.set(market_id, market)
                return market

            return None

        except Exception as e:
            self._record_api_call(False)
            return None
    
    def health_check(self) -> Dict[str, Any]:
        """健康检查 - 检查客户端和服务状态
        
        Returns:
            {"status": "ok" | "degraded" | "error", "details": {...}}
        """
        result = {
            "status": "ok",
            "client_initialized": self.client is not None,
            "rate_limiter": {},
            "api_stats": {},
            "errors": []
        }
        
        # 检查速率限制器
        if hasattr(self, 'rate_limiter') and self.rate_limiter:
            result["rate_limiter"]["active"] = True
        
        # 获取 API 统计
        result["api_stats"] = self.get_api_stats()
        
        # 检查错误率
        stats = result["api_stats"]
        if stats["calls"] > 0:
            error_rate = stats["errors"] / stats["calls"]
            if error_rate > 0.1:  # 超过10%错误率
                result["status"] = "degraded"
                result["errors"].append(f"High error rate: {error_rate:.1%}")
        
        # 如果客户端未初始化，状态为错误
        if not result["client_initialized"]:
            result["status"] = "error"
            result["errors"].append("Client not initialized")
        
        return result
