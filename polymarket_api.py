"""
Polymarket API客户端
使用官方 py-clob-client SDK

官方文档参考：
- GitHub: https://github.com/Polymarket/py-clob-client
- API: https://clob.polymarket.com

关键配置说明：
1. signature_type (签名类型):
   - 0: EOA (标准钱包，私钥直接对应地址)
   - 1: POLY_PROXY (Magic Link 代理钱包)
   - 2: GNOSIS_SAFE (Safe 多签钱包，需要设置 funder)

2. funder (资金地址):
   - Safe 钱包必填！填写 Safe 钱包地址（存有资金的地址）
   - 普通钱包可以留空

3. 余额查询：
   - Safe 钱包：查询 funder 地址的余额
   - 普通钱包：查询私钥对应地址的余额
"""
from typing import Optional, Dict, List, Any, Tuple
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import (
    ApiCreds, 
    OrderArgs, 
    OrderType, 
    PartialCreateOrderOptions,
    BalanceAllowanceParams,
    AssetType,
)
from py_clob_client.order_builder.constants import BUY, SELL
from pathlib import Path
import time
import requests
import threading
import functools
from typing import Callable, Any, Optional
import json


# ==================== 价格辅助函数 ====================

def cents_to_float(cents: float) -> float:
    """美分格式转小数格式 (75 -> 0.75)"""
    if cents > 1:
        return cents / 100.0
    return cents


def float_to_cents(price: float) -> float:
    """小数格式转美分格式 (0.75 -> 75)"""
    if price <= 1:
        return price * 100.0
    return price


def format_time_remaining(seconds: float) -> str:
    """格式化剩余时间为 MM:SS 格式"""
    if seconds <= 0:
        return "--:--"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def format_price(price: float, to_cents: bool = True) -> str:
    """格式化价格"""
    if to_cents and 0 <= price <= 1:
        price = price * 100
    return f"{price:.2f}"


# ==================== 缓存和速率限制 ====================

class TTLCache:
    """带过期时间的缓存"""
    def __init__(self, default_ttl: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._default_ttl = default_ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                value, expiry = self._cache[key]
                if time.time() < expiry:
                    return value
                del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl: int = None) -> None:
        with self._lock:
            expiry = time.time() + (ttl or self._default_ttl)
            self._cache[key] = (value, expiry)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class RateLimiter:
    """速率限制器"""
    def __init__(self):
        self._calls: Dict[str, List[float]] = {}
        self._lock = threading.Lock()
        self._suppress_logs = False

    def suppress_logs(self, suppress: bool) -> None:
        self._suppress_logs = suppress

    def wait_if_needed(self, name: str, limit: int, window: int) -> None:
        """等待直到可以进行下一次调用"""
        with self._lock:
            now = time.time()
            if name not in self._calls:
                self._calls[name] = []

            # 清理过期记录
            self._calls[name] = [t for t in self._calls[name] if now - t < window]

            # 如果达到限制，等待
            if len(self._calls[name]) >= limit:
                wait_time = window - (now - self._calls[name][0])
                if wait_time > 0 and not self._suppress_logs:
                    print(f"[速率限制] {name} 等待 {wait_time:.1f} 秒")
                time.sleep(wait_time)

            # 记录本次调用
            self._calls[name].append(now)


# ==================== 心跳管理器 ====================

class HeartbeatManager:
    """心跳管理器 - 保持会话活跃"""
    def __init__(self, client: ClobClient, interval: int = 30):
        self.client = client
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_id: Optional[str] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                if self.client:
                    self._heartbeat_id = self.client.post_heartbeat(self._heartbeat_id)
            except Exception:
                pass
            time.sleep(self.interval)


# ==================== Polymarket 客户端 ====================

class PolymarketClient:
    """
    Polymarket API 客户端
    
    官方 SDK: py-clob-client
    文档: https://github.com/Polymarket/py-clob-client
    
    关键参数说明:
    - signature_type: 签名类型
        0 = EOA (普通钱包)
        2 = GNOSIS_SAFE (Safe 多签钱包)
    - funder: 资金地址 (Safe 钱包必填)
    """

    GAMMA_API_BASE = "https://gamma-api.polymarket.com"
    CLOB_API_BASE = "https://clob.polymarket.com"

    RATE_LIMITS = {
        "get_markets": (300, 10),     # 300 req / 10s
        "get_prices": (1500, 10),      # 1500 req / 10s
        "orders": (3500, 10),          # 3500 req / 10s (burst)
    }

    CACHE_TTL = {
        "market_details": 300,
        "token_ids": 300,
        "prices": 1,
        "orderbook": 1,
    }

    def __init__(
        self,
        private_key: str = "",
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        chain_id: int = POLYGON,
        signature_type: int = 0,
        funder_address: str = "",
    ):
        """
        初始化 API 客户端

        Args:
            private_key: 钱包私钥（签名密钥）
            api_key: API 密钥（L2 身份验证）
            api_secret: API 密钥
            passphrase: API 口令
            chain_id: 链 ID（默认 Polygon 137）
            signature_type: 签名类型
                0 = EOA (普通钱包)
                2 = GNOSIS_SAFE (Safe 多签钱包)
            funder_address: 资金地址（Safe 钱包必填！）
        """
        # 处理私钥格式
        self._raw_private_key = private_key
        if private_key and private_key.startswith("0x"):
            self._raw_private_key = private_key[2:]
        
        self.private_key = self._raw_private_key
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder_address = funder_address

        # 缓存
        self.token_ids_cache = TTLCache(default_ttl=self.CACHE_TTL["token_ids"])
        self.market_details_cache = TTLCache(default_ttl=self.CACHE_TTL["market_details"])
        self.prices_cache = TTLCache(default_ttl=self.CACHE_TTL["prices"])
        self.orderbook_cache = TTLCache(default_ttl=self.CACHE_TTL["orderbook"])

        # 速率限制器
        self.rate_limiter = RateLimiter()
        self.rate_limiter.suppress_logs(True)

        # 心跳管理器
        self.heartbeat_manager: Optional[HeartbeatManager] = None

        # API 统计
        self._api_stats = {"calls": 0, "errors": 0, "cache_hits": 0}
        self._stats_lock = threading.Lock()

        # 客户端
        self.client: Optional[ClobClient] = None
        self.api_credentials: Optional[ApiCreds] = None

        # 打印配置诊断
        self._print_config_diagnosis()

        # 初始化客户端
        self._init_client()

    def _print_config_diagnosis(self) -> None:
        """打印配置诊断信息"""
        from eth_account import Account
        
        print("\n" + "=" * 60)
        print("[配置诊断]")
        print("=" * 60)

        # 解析私钥地址
        signer_address = None
        if self.private_key:
            try:
                acct = Account.from_key(self.private_key)
                signer_address = acct.address
                print(f"[*] 签名密钥地址: {signer_address}")
            except Exception as e:
                print(f"[!] 私钥解析失败: {e}")
        else:
            print("[!] 私钥未设置")

        # 打印签名类型
        type_names = {0: "EOA (普通钱包)", 1: "POLY_PROXY", 2: "GNOSIS_SAFE (Safe 多签钱包)"}
        print(f"[*] 签名类型: {self.signature_type} - {type_names.get(self.signature_type, '未知')}")

        # 打印资金地址
        if self.funder_address:
            print(f"[*] 资金地址: {self.funder_address}")
        else:
            print("[*] 资金地址: 未设置")

        # 关键检查
        print("\n[配置检查]")
        if self.signature_type == 2:
            if not self.funder_address:
                print("[X] 错误: Safe 钱包必须设置 FUNDER_ADDRESS!")
                print("    请在 .env 中设置: FUNDER_ADDRESS=0x你的Safe钱包地址")
            elif signer_address and self.funder_address.lower() == signer_address.lower():
                print("[!] 警告: 资金地址与签名密钥地址相同")
                print("    Safe 钱包的资金地址应该与签名密钥地址不同")
            else:
                print("[OK] Safe 钱包配置正确")
        elif self.signature_type == 0:
            if self.funder_address and signer_address and self.funder_address.lower() != signer_address.lower():
                print("[!] 提示: 普通钱包通常不需要设置 FUNDER_ADDRESS")
                print(f"    私钥地址: {signer_address}")
                print(f"    资金地址: {self.funder_address}")
            else:
                print("[OK] EOA 钱包配置正确")

        # API 凭证检查
        print(f"\n[*] API_KEY: {'已设置' if self.api_key else '未设置'}")
        print(f"[*] API_SECRET: {'已设置' if self.api_secret else '未设置'}")
        print(f"[*] PASSPHRASE: {'已设置' if self.passphrase else '未设置'}")

        print("=" * 60 + "\n")

    def _init_client(self) -> None:
        """初始化 ClobClient"""
        try:
            # 公开 API 模式
            if not self.private_key:
                self.client = ClobClient(
                    host=self.CLOB_API_BASE,
                    chain_id=self.chain_id,
                )
                print("[OK] 客户端初始化成功（公开 API 模式）")
                return

            # 构建客户端参数
            client_args = {
                "host": self.CLOB_API_BASE,
                "chain_id": self.chain_id,
                "key": self.private_key,
            }

            # 检查凭证有效性
            has_valid_creds = bool(
                self.api_key and len(self.api_key) > 10 and
                self.api_secret and len(self.api_secret) > 10 and
                self.passphrase and len(self.passphrase) > 5
            )

            if has_valid_creds:
                self.api_credentials = ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.passphrase
                )
                client_args["creds"] = self.api_credentials
                print("[*] 使用 .env 中的 API 凭证")
            else:
                print("[!] .env 中凭证无效或未设置，将自动创建")

            # 设置签名类型和资金地址
            if self.signature_type is not None:
                client_args["signature_type"] = self.signature_type
            if self.funder_address:
                client_args["funder"] = self.funder_address

            # 创建客户端
            self.client = ClobClient(**client_args)
            print(f"[OK] 客户端初始化成功（签名类型: {self.signature_type}）")

            # 启动心跳
            self.heartbeat_manager = HeartbeatManager(self.client)
            self.heartbeat_manager.start()

            # 如果没有有效凭证，自动创建
            if not has_valid_creds and self.private_key:
                print("[*] 正在自动创建 API 凭证...")
                if self._create_api_credentials():
                    print("[OK] API 凭证创建成功，已保存到 .env")
                else:
                    print("[!] API 凭证创建失败")

        except Exception as e:
            print(f"[X] 客户端初始化失败: {e}")
            import traceback
            traceback.print_exc()

    def _create_api_credentials(self) -> bool:
        """创建 API 凭证"""
        if not self.client or not self.private_key:
            return False

        try:
            creds_obj = self.client.create_or_derive_api_creds()
            
            if creds_obj is None:
                print("[X] create_or_derive_api_creds() 返回 None")
                return False

            # 提取凭证
            self.api_key = creds_obj.api_key
            self.api_secret = creds_obj.api_secret
            self.passphrase = creds_obj.api_passphrase
            self.api_credentials = creds_obj

            print(f"[OK] API 凭证创建成功")
            print(f"    API_KEY: {self.api_key[:20]}...")
            print(f"    API_SECRET: {self.api_secret[:20]}...")

            # 保存到 .env
            self._save_credentials_to_env({
                "key": self.api_key,
                "secret": self.api_secret,
                "passphrase": self.passphrase
            })

            # 重新初始化客户端
            self._reinit_client_with_credentials()
            return True

        except Exception as e:
            print(f"[X] 创建 API 凭证失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _reinit_client_with_credentials(self) -> bool:
        """使用新凭证重新初始化客户端"""
        try:
            # 停止旧心跳
            if self.heartbeat_manager:
                self.heartbeat_manager.stop()

            # 创建新客户端
            self.client = ClobClient(
                host=self.CLOB_API_BASE,
                chain_id=self.chain_id,
                key=self.private_key,
                creds=self.api_credentials,
                signature_type=self.signature_type,
                funder=self.funder_address,
            )

            # 重新启动心跳
            self.heartbeat_manager = HeartbeatManager(self.client)
            self.heartbeat_manager.start()

            print("[OK] 客户端重新初始化成功")
            return True

        except Exception as e:
            print(f"[X] 重新初始化失败: {e}")
            return False

    def _save_credentials_to_env(self, credentials: Dict[str, str]) -> bool:
        """保存凭证到 .env 文件"""
        try:
            env_path = Path.cwd() / ".env"
            
            # 读取现有内容
            existing_lines = []
            if env_path.exists():
                with open(env_path, 'r', encoding='utf-8') as f:
                    existing_lines = f.readlines()

            # 更新凭证
            lines_to_write = []
            updated = {"API_KEY": False, "API_SECRET": False, "PASSPHRASE": False}
            
            for line in existing_lines:
                if line.startswith("API_KEY="):
                    lines_to_write.append(f"API_KEY={credentials.get('key', '')}\n")
                    updated["API_KEY"] = True
                elif line.startswith("API_SECRET="):
                    lines_to_write.append(f"API_SECRET={credentials.get('secret', '')}\n")
                    updated["API_SECRET"] = True
                elif line.startswith("PASSPHRASE="):
                    lines_to_write.append(f"PASSPHRASE={credentials.get('passphrase', '')}\n")
                    updated["PASSPHRASE"] = True
                else:
                    lines_to_write.append(line)

            # 添加缺失的凭证
            for key, is_updated in updated.items():
                if not is_updated:
                    lines_to_write.append(f"{key}={credentials.get(key.lower(), '')}\n")

            # 写入文件
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(lines_to_write)

            print(f"[OK] 凭证已保存到 .env")
            return True

        except Exception as e:
            print(f"[X] 保存凭证失败: {e}")
            return False

    # ==================== 市场数据方法 ====================

    def get_tick_size(self, token_id: str) -> str:
        """获取最小价格变动单位"""
        if not self.client or not token_id:
            return "0.01"
        try:
            return str(self.client.get_tick_size(token_id))
        except Exception:
            return "0.01"

    def get_neg_risk(self, token_id: str) -> bool:
        """获取 neg_risk 标志"""
        if not self.client or not token_id:
            return False
        try:
            return bool(self.client.get_neg_risk(token_id))
        except Exception:
            return False

    def get_market_options(self, token_id: str) -> Dict[str, Any]:
        """获取市场交易选项"""
        return {
            "tick_size": self.get_tick_size(token_id),
            "neg_risk": self.get_neg_risk(token_id)
        }

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """通过 slug 获取市场"""
        try:
            url = f"{self.GAMMA_API_BASE}/markets"
            params = {"slug": slug}
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    market = data[0]
                    market["slug"] = slug
                    return market
            return None
        except Exception as e:
            print(f"[!] get_market_by_slug 失败: {e}")
            return None

    def get_market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        """通过 ID 获取市场"""
        cached = self.market_details_cache.get(market_id)
        if cached:
            return cached

        try:
            # 方式1: 直接通过市场ID查询
            url = f"{self.GAMMA_API_BASE}/markets/{market_id}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                market = response.json()
                self.market_details_cache.set(market_id, market)
                return market

            # 方式2: 通过 condition_id 查询
            url = f"{self.GAMMA_API_BASE}/markets?condition_id={market_id}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and len(data) > 0:
                    market = data[0]
                    self.market_details_cache.set(market_id, market)
                    return market

            return None

        except Exception as e:
            print(f"[!] get_market_by_id 失败: {e}")
            return None

    def get_tradable_markets(self, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        """获取可交易的市场列表
        
        Args:
            limit: 返回的市场数量限制
            
        Returns:
            市场列表，失败返回 None
        """
        try:
            url = f"{self.GAMMA_API_BASE}/markets"
            params = {
                "limit": limit,
                "active": "true"  # 只获取活跃市场
            }
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                markets = response.json()
                if isinstance(markets, list):
                    return markets
            return None
        except Exception as e:
            print(f"[!] get_tradable_markets 失败: {e}")
            return None

    def get_token_ids(self, market_id: str) -> Dict[str, str]:
        """获取市场的代币 ID"""
        cached = self.token_ids_cache.get(market_id)
        if cached:
            return cached

        market = self.get_market_by_id(market_id)
        if not market:
            return {}

        token_ids_raw = market.get("clobTokenIds", [])
        token_ids = {}

        if isinstance(token_ids_raw, list) and len(token_ids_raw) >= 2:
            token_ids = {"YES": token_ids_raw[0], "NO": token_ids_raw[1]}
        elif isinstance(token_ids_raw, str):
            try:
                parsed = json.loads(token_ids_raw)
                if isinstance(parsed, list) and len(parsed) >= 2:
                    token_ids = {"YES": parsed[0], "NO": parsed[1]}
            except:
                pass

        if token_ids:
            self.token_ids_cache.set(market_id, token_ids)
        
        return token_ids

    def get_market_prices(self, market_id: str, debug: bool = False, yes_token_id: str = None, no_token_id: str = None, max_retries: int = 3) -> Optional[Dict[str, float]]:
        """获取市场价格（使用 CLOB API 获取实时价格）
        
        Args:
            market_id: 市场ID
            debug: 是否输出调试信息
            yes_token_id: YES 代币 ID（可选，用于直接查询）
            no_token_id: NO 代币 ID（可选，用于直接查询）
            max_retries: 最大重试次数（默认3次）
        """
        last_error = None
        
        for retry in range(max_retries):
            try:
                # 如果没有提供 token_id，先获取
                if not yes_token_id or not no_token_id:
                    token_ids = self.get_token_ids(market_id)
                    yes_token_id = token_ids.get("YES")
                    no_token_id = token_ids.get("NO")
                
                if not yes_token_id or not no_token_id:
                    if debug:
                        print(f"[调试] 无法获取 token_ids")
                    return None
                
                # 使用 CLOB API 获取实时价格（从订单簿）
                clob_base = "https://clob.polymarket.com"
                
                # 获取 YES 代币的订单簿
                yes_url = f"{clob_base}/book?token_id={yes_token_id}"
                no_url = f"{clob_base}/book?token_id={no_token_id}"
                
                if debug and retry == 0:
                    print(f"[调试] CLOB API - YES URL: {yes_url}")
                
                # 获取订单簿（增加超时）
                yes_resp = requests.get(yes_url, timeout=10)
                no_resp = requests.get(no_url, timeout=10)
                
                if yes_resp.status_code != 200 or no_resp.status_code != 200:
                    if debug:
                        print(f"[调试] CLOB API 响应失败: YES={yes_resp.status_code}, NO={no_resp.status_code}")
                    return None
                
                yes_book = yes_resp.json()
                no_book = no_resp.json()
                
                # 输出原始订单簿数据样本（用于诊断）
                if debug and retry == 0:
                    print(f"[调试] YES 订单簿原始数据样本: {str(yes_book)[:200]}...")
                    print(f"[调试] NO 订单簿原始数据样本: {str(no_book)[:200]}...")
                
                # 获取中间价
                yes_price = 0.5
                no_price = 0.5
                
                # YES 价格
                yes_asks = yes_book.get("asks", [])
                yes_bids = yes_book.get("bids", [])
                
                # 对订单簿排序：asks 按价格升序，bids 按价格降序
                if yes_asks:
                    yes_asks = sorted(yes_asks, key=lambda x: float(x.get("price", "999")))
                if yes_bids:
                    yes_bids = sorted(yes_bids, key=lambda x: float(x.get("price", "0")), reverse=True)
                
                # 检测价格格式：如果价格 > 1，说明是百分比格式（需要除以100）
                price_format = "decimal"  # 默认是小数格式
                if yes_asks and float(yes_asks[0].get("price", "0")) > 1:
                    price_format = "percent"
                    if debug and retry == 0:
                        print(f"[调试] 检测到价格格式为百分比格式（价格 > 1）")
                
                if debug:
                    print(f"[调试] YES 订单簿: asks={len(yes_asks)}, bids={len(yes_bids)}")
                    # 输出前3档订单簿详情
                    if len(yes_asks) > 0:
                        print(f"[调试] YES 卖单前3档:")
                        for i, ask in enumerate(yes_asks[:3]):
                            print(f"[调试]   {i+1}. price={ask.get('price')}, size={ask.get('size')}")
                    if len(yes_bids) > 0:
                        print(f"[调试] YES 买单前3档:")
                        for i, bid in enumerate(yes_bids[:3]):
                            print(f"[调试]   {i+1}. price={bid.get('price')}, size={bid.get('size')}")
                
                if yes_asks and len(yes_asks) > 0 and yes_bids and len(yes_bids) > 0:
                    best_ask = float(yes_asks[0].get("price", "0.5"))
                    best_bid = float(yes_bids[0].get("price", "0.5"))
                    yes_price_raw = (best_ask + best_bid) / 2
                    if debug:
                        print(f"[调试] YES 中间价: ({best_ask} + {best_bid}) / 2 = {yes_price_raw}")
                elif yes_asks and len(yes_asks) > 0:
                    yes_price_raw = float(yes_asks[0].get("price", "0.5"))
                    if debug:
                        print(f"[调试] YES 只有卖方，使用最低卖价: {yes_price_raw}")
                elif yes_bids and len(yes_bids) > 0:
                    yes_price_raw = float(yes_bids[0].get("price", "0.5"))
                    if debug:
                        print(f"[调试] YES 只有买方，使用最高买价: {yes_price_raw}")
                else:
                    yes_price_raw = 0.5
                    if debug:
                        print(f"[调试] YES 订单簿为空，使用默认价格: 0.5")
                
                if yes_price_raw > 1:
                    yes_price = yes_price_raw / 100.0
                else:
                    yes_price = yes_price_raw
                
                # NO 价格
                no_asks = no_book.get("asks", [])
                no_bids = no_book.get("bids", [])
                
                # 对订单簿排序：asks 按价格升序，bids 按价格降序
                if no_asks:
                    no_asks = sorted(no_asks, key=lambda x: float(x.get("price", "999")))
                if no_bids:
                    no_bids = sorted(no_bids, key=lambda x: float(x.get("price", "0")), reverse=True)
                
                if debug:
                    print(f"[调试] NO 订单簿: asks={len(no_asks)}, bids={len(no_bids)}")
                    # 输出前3档订单簿详情
                    if len(no_asks) > 0:
                        print(f"[调试] NO 卖单前3档:")
                        for i, ask in enumerate(no_asks[:3]):
                            print(f"[调试]   {i+1}. price={ask.get('price')}, size={ask.get('size')}")
                    if len(no_bids) > 0:
                        print(f"[调试] NO 买单前3档:")
                        for i, bid in enumerate(no_bids[:3]):
                            print(f"[调试]   {i+1}. price={bid.get('price')}, size={bid.get('size')}")
                
                if no_asks and len(no_asks) > 0 and no_bids and len(no_bids) > 0:
                    best_ask = float(no_asks[0].get("price", "0.5"))
                    best_bid = float(no_bids[0].get("price", "0.5"))
                    no_price_raw = (best_ask + best_bid) / 2
                    if debug:
                        print(f"[调试] NO 中间价: ({best_ask} + {best_bid}) / 2 = {no_price_raw}")
                elif no_asks and len(no_asks) > 0:
                    no_price_raw = float(no_asks[0].get("price", "0.5"))
                    if debug:
                        print(f"[调试] NO 只有卖方，使用最低卖价: {no_price_raw}")
                elif no_bids and len(no_bids) > 0:
                    no_price_raw = float(no_bids[0].get("price", "0.5"))
                    if debug:
                        print(f"[调试] NO 只有买方，使用最高买价: {no_price_raw}")
                else:
                    no_price_raw = 0.5
                    if debug:
                        print(f"[调试] NO 订单簿为空，使用默认价格: 0.5")
                
                if no_price_raw > 1:
                    no_price = no_price_raw / 100.0
                else:
                    no_price = no_price_raw
                
                # 价格修正逻辑
                yes_has_complete_book = len(yes_asks) > 0 and len(yes_bids) > 0
                no_has_complete_book = len(no_asks) > 0 and len(no_bids) > 0
                
                if debug:
                    print(f"[调试] 订单簿完整性: YES={yes_has_complete_book}, NO={no_has_complete_book}")
                
                if yes_has_complete_book and no_has_complete_book:
                    pass
                elif yes_has_complete_book and not no_has_complete_book:
                    no_price = 1.0 - yes_price
                    if debug:
                        print(f"[调试] NO 订单簿不完整，使用 1 - YES 价格: {no_price:.4f}")
                elif no_has_complete_book and not yes_has_complete_book:
                    yes_price = 1.0 - no_price
                    if debug:
                        print(f"[调试] YES 订单簿不完整，使用 1 - NO 价格: {yes_price:.4f}")
                else:
                    price_sum = yes_price + no_price
                    if abs(price_sum - 1.0) > 0.15:
                        if debug:
                            print(f"[调试] 价格总和异常: {price_sum:.4f}，使用默认值 0.5")
                        yes_price = min(yes_price, 0.5)
                        no_price = min(no_price, 0.5)
                
                # 最终验证
                if yes_price > 1.0:
                    yes_price = 1.0
                if no_price > 1.0:
                    no_price = 1.0
                if yes_price < 0:
                    yes_price = 0.01
                if no_price < 0:
                    no_price = 0.01
                
                if debug:
                    print(f"[调试] 最终价格（修正后）:")
                    print(f"[调试] YES: {yes_price:.4f} ({int(yes_price*100)}%)")
                    print(f"[调试] NO: {no_price:.4f} ({int(no_price*100)}%)")
                    print(f"[调试] YES + NO = {yes_price + no_price:.4f} (正常应接近1.00)")
                    
                    if yes_asks and len(yes_asks) > 0:
                        print(f"[调试] YES 卖一价: {float(yes_asks[0].get('price', 0)):.4f}")
                    if yes_bids and len(yes_bids) > 0:
                        print(f"[调试] YES 买一价: {float(yes_bids[0].get('price', 0)):.4f}")
                    if no_asks and len(no_asks) > 0:
                        print(f"[调试] NO 卖一价: {float(no_asks[0].get('price', 0)):.4f}")
                    if no_bids and len(no_bids) > 0:
                        print(f"[调试] NO 买一价: {float(no_bids[0].get('price', 0)):.4f}")
                
                return {"YES": yes_price, "NO": no_price}
                
            except requests.exceptions.ConnectionError as e:
                last_error = e
                if retry < max_retries - 1:
                    wait_time = (retry + 1) * 2
                    print(f"[网络] 连接错误，{wait_time}秒后重试 ({retry + 1}/{max_retries}): {e}")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"[错误] 获取价格失败（已重试{max_retries}次）: {e}")
                    return None
                    
            except requests.exceptions.Timeout as e:
                last_error = e
                if retry < max_retries - 1:
                    wait_time = (retry + 1) * 2
                    print(f"[网络] 请求超时，{wait_time}秒后重试 ({retry + 1}/{max_retries}): {e}")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"[错误] 获取价格超时（已重试{max_retries}次）: {e}")
                    return None
                    
            except Exception as e:
                print(f"[!] 获取价格失败: {e}")
                import traceback
                traceback.print_exc()
                return None
        
        return None
    # ==================== 余额方法 ====================

    def check_and_initialize_allowance(self) -> Dict[str, Any]:
        """检查并初始化授权状态"""
        result = {
            "balance": 0.0,
            "allowance": 0.0,
            "initialized": False,
            "error": None
        }
        
        if not self.client:
            result["error"] = "Client not initialized"
            print("[!] Client 未初始化", flush=True)
            return result

        try:
            print("[*] 正在检查授权状态...", flush=True)
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            allowance_info = self.client.get_balance_allowance(params)
            
            print(f"[*] 授权响应已收到", flush=True)
            
            if allowance_info and isinstance(allowance_info, dict):
                # 获取余额
                raw_balance = allowance_info.get("balance", 0) or 0
                try:
                    balance = float(str(raw_balance).strip('"'))
                except:
                    balance = 0.0
                
                # USDC 余额以微单位返回，需要转换
                if balance > 10000:
                    balance = balance / 1000000
                
                result["balance"] = balance
                
                # 获取授权额度
                allowances = allowance_info.get("allowances", {})
                if isinstance(allowances, dict) and allowances:
                    raw_allowance = list(allowances.values())[0]
                    try:
                        allowance = float(str(raw_allowance))
                    except:
                        allowance = 0.0
                    
                    # 检查是否是无限授权
                    if allowance > 1e50:
                        result["allowance"] = float("inf")
                        print(f"[*] 授权额度: 无限", flush=True)
                    elif allowance > 10000:
                        result["allowance"] = allowance / 1000000
                        print(f"[*] 授权额度: ${result['allowance']:.2f}", flush=True)
                    else:
                        result["allowance"] = allowance
                
                print(f"[*] 当前余额: ${balance:.2f}", flush=True)
                
                # 如果余额 > 0，认为已初始化
                if balance > 0:
                    result["initialized"] = True
                    
        except Exception as e:
            print(f"[!] 检查授权失败: {e}", flush=True)
            result["error"] = str(e)
            result["initialized"] = True  # 继续运行，不阻塞

        return result

    def get_balance(self) -> float:
        """获取账户余额（USDC）"""
        if not self.client:
            return 0.0

        try:
            # 使用 SDK 查询余额
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            resp = self.client.get_balance_allowance(params)
            
            print(f"[*] 余额查询响应: {resp}")

            if resp and isinstance(resp, dict):
                balance = float(resp.get("balance", 0) or 0)
                
                # USDC 余额以微单位返回，需要转换
                if balance > 10000:
                    balance = balance / 1000000
                
                print(f"[*] SDK 返回余额: ${balance:.2f}")
                return balance

        except Exception as e:
            print(f"[!] SDK 余额查询失败: {e}")

        return 0.0
    
    def get_token_balance(self, token_id: str) -> float:
        """获取代币余额（YES/NO 代币）
        
        Args:
            token_id: 代币 ID
            
        Returns:
            代币余额（股数）- 已从微单位转换
        """
        if not self.client:
            return 0.0

        try:
            # 查询特定代币余额（传入 token_id）
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id
            )
            resp = self.client.get_balance_allowance(params)
            
            if resp and isinstance(resp, dict):
                # 余额可能是字典格式 {token_id: balance} 或直接数值
                balance = resp.get("balance", 0)
                
                token_balance = 0.0
                if isinstance(balance, dict):
                    # 字典格式：{token_id: balance}
                    token_balance = float(balance.get(token_id, 0))
                elif isinstance(balance, (int, float, str)):
                    # 直接返回数值
                    token_balance = float(balance)
                
                # 代币余额以微单位返回，需要转换（与 USDC 余额相同）
                # 1 股 = 10^6 微单位
                if token_balance > 10000:
                    token_balance = token_balance / 1000000
                
                return token_balance
                
            print(f"[!] 无法获取代币余额: {token_id[:20]}...")
            
        except Exception as e:
            print(f"[!] 代币余额查询失败: {e}")
        
        return 0.0

    # ==================== 订单方法 ====================

    def create_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
        expiration: int = None,
    ) -> Dict[str, Any]:
        """
        创建并提交订单

        Args:
            token_id: 代币 ID
            price: 价格 (0-1 或 0-100)
            size: 数量
            side: 方向 (BUY/SELL)
            order_type: 订单类型 (GTC/GTD)
            expiration: GTD 订单过期时间戳

        Returns:
            订单响应（包含实际的size）
        """
        if not self.client:
            return {"success": False, "errorMsg": "Client not initialized"}

        try:
            # 价格格式转换
            if price > 1:
                api_price = price / 100.0
            else:
                api_price = price

            # 获取市场参数
            tick_size = self.get_tick_size(token_id)
            neg_risk = self.get_neg_risk(token_id)

            # 记录原始size
            original_size = size
            actual_size = size

            print(f"\n[下单] 参数:")
            print(f"  token_id: {token_id[:20]}...")
            print(f"  price: {api_price}")
            print(f"  size: {size}")
            print(f"  side: {side.upper()}")
            print(f"  order_type: {order_type} (限价单)")
            print(f"  tick_size: {tick_size}")
            print(f"  neg_risk: {neg_risk}")

            # 构建订单参数
            expiration_time = expiration if order_type == "GTD" and expiration else 0
            args = OrderArgs(
                token_id=token_id,
                price=api_price,
                size=size,
                side=side.upper(),
                expiration=expiration_time,
            )

            options = PartialCreateOrderOptions(
                tick_size=tick_size,
                neg_risk=neg_risk,
            )

            # 创建签名订单
            signed_order = self.client.create_order(args, options=options)
            print(f"[*] 订单已签名")

            # 确定订单类型
            order_type_enum = OrderType.GTC
            if order_type == "GTD":
                order_type_enum = OrderType.GTD

            # 提交订单（带重试和最小股数处理）
            max_retries = 3
            response = None
            
            for attempt in range(max_retries):
                try:
                    response = self.client.post_order(signed_order, orderType=order_type_enum)
                    if response and response.get("success") != False:
                        break
                    error_msg = response.get("errorMsg", "") if response else ""
                    if "service not ready" in str(error_msg).lower() and attempt < max_retries - 1:
                        print(f"[*] 服务未就绪，等待重试...")
                        time.sleep(2)
                        continue
                    break
                except Exception as e:
                    error_str = str(e)
                    
                    # 检查是否是最小股数错误
                    if "lower than the minimum" in error_str:
                        import re
                        match = re.search(r'minimum:\s*(\d+)', error_str)
                        if match:
                            min_size = int(match.group(1))
                            print(f"[!] 股数 {size} 小于最小值 {min_size}，调整后重试...")
                            actual_size = float(min_size)  # 更新实际size
                            
                            # 使用最小股数重新创建订单
                            args = OrderArgs(
                                token_id=token_id,
                                price=api_price,
                                size=float(min_size),
                                side=side.upper(),
                                expiration=expiration_time,
                            )
                            signed_order = self.client.create_order(args, options=options)
                            print(f"[*] 订单已重新签名 (size={min_size})")
                            
                            # 重试提交
                            response = self.client.post_order(signed_order, orderType=order_type_enum)
                            if response and response.get("success") != False:
                                break
                    
                    if "service not ready" in error_str.lower() and attempt < max_retries - 1:
                        print(f"[*] 服务未就绪，等待重试...")
                        time.sleep(2)
                        continue
                    raise

            # 解析响应
            if response:
                order_id = response.get("orderID") or response.get("order_id", "")
                success = response.get("success", True) and not response.get("errorMsg")
                
                if success:
                    print(f"[OK] 订单创建成功: {order_id[:20]}...")
                    print(f"[OK] 实际下单股数: {actual_size} (原始: {original_size})")
                else:
                    print(f"[X] 订单失败: {response.get('errorMsg', 'Unknown')}")
                
                return {
                    "success": success,
                    "orderID": order_id,
                    "errorMsg": response.get("errorMsg", ""),
                    "actual_size": actual_size,  # 返回实际下单的size
                }
            else:
                return {"success": False, "errorMsg": "Empty response", "actual_size": actual_size}

        except Exception as e:
            print(f"[X] 创建订单失败: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "errorMsg": str(e), "actual_size": size}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """取消订单"""
        if not self.client or not order_id:
            return {"success": False, "errorMsg": "Invalid order ID"}

        try:
            response = self.client.cancel(order_id)
            success = response.get("success", False) if response else False
            if success:
                print(f"[OK] 订单已取消: {order_id[:20]}...")
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
        except Exception:
            return []

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """获取订单状态"""
        if not self.client or not order_id:
            return None
        try:
            return self.client.get_order(order_id)
        except Exception:
            return None

    # ==================== 工具方法 ====================

    def health_check(self) -> bool:
        """健康检查"""
        if not self.client:
            return False
        try:
            self.client.get_ok()
            return True
        except Exception:
            return False

    def get_server_time(self) -> Optional[str]:
        """获取服务器时间"""
        if not self.client:
            return None
        try:
            return self.client.get_server_time()
        except Exception:
            return None

    def clear_cache(self) -> None:
        """清除所有缓存"""
        self.token_ids_cache.clear()
        self.market_details_cache.clear()
        self.prices_cache.clear()
        self.orderbook_cache.clear()

    def close(self) -> None:
        """关闭客户端"""
        if self.heartbeat_manager:
            self.heartbeat_manager.stop()
