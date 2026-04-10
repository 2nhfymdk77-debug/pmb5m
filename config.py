"""
配置管理模块
"""
import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, Any, Optional

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 导入 dotenv 用于加载 .env 文件
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

# 配置文件路径
CONFIG_DIR = Path.home() / ".polymarket-trader"
CONFIG_FILE = CONFIG_DIR / "config.json"

# .env 文件路径（当前目录）
ENV_FILE = Path.cwd() / ".env"

# 确保目录存在
CONFIG_DIR.mkdir(exist_ok=True)


def load_env_variables() -> Dict[str, str]:
    """从 .env 文件加载环境变量"""
    env_vars = {}

    # 如果可用，使用 dotenv 加载
    if DOTENV_AVAILABLE and ENV_FILE.exists():
        load_dotenv(ENV_FILE)
        print(f"[OK] 已加载 .env 文件: {ENV_FILE}", flush=True)

    # 定义环境变量到配置字段的映射
    env_mapping = {
        "PRIVATE_KEY": "private_key",
        "API_KEY": "api_key",
        "API_SECRET": "api_secret",
        "PASSPHRASE": "passphrase",
        "FUNDER_ADDRESS": "funder_address",
        "MARKET_SLUG": "market_slug",
        "ENTRY_PRICE": "entry_price",
        "STOP_LOSS": "stop_loss",
        "TAKE_PROFIT": "take_profit",
        "INITIAL_BALANCE": "initial_balance",
        "INITIAL_POSITION": "initial_position",
        "TRADE_CYCLE_MINUTES": "trade_cycle_minutes",
        "CHAIN_ID": "chain_id",
        "SIGNATURE_TYPE": "signature_type",
    }

    # 从环境变量读取值
    for env_key, config_key in env_mapping.items():
        value = os.getenv(env_key)
        if value:
            # 类型转换
            if config_key in ["entry_price", "stop_loss", "take_profit", "initial_balance"]:
                env_vars[config_key] = float(value)
            elif config_key in ["trade_cycle_minutes", "chain_id", "signature_type"]:
                env_vars[config_key] = int(value)
            else:
                env_vars[config_key] = value

    return env_vars


class ConfigValidationError(Exception):
    """配置验证错误"""
    pass


def validate_private_key(private_key: str) -> bool:
    """验证私钥格式"""
    if not private_key:
        return True
    pattern = r'^(0x)?[a-fA-F0-9]{64}$'
    return bool(re.match(pattern, private_key))


def validate_price(price: float, name: str = "价格") -> None:
    """验证价格范围"""
    if not (0 < price <= 100):
        raise ConfigValidationError(f"{name}必须在 0-100 之间，当前: {price}")


@dataclass
class TradingConfig:
    """交易配置类"""

    # API 凭证
    private_key: str = ""
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    funder_address: str = ""
    
    # 交易参数
    entry_price: float = 75.0        # 买入价格（百分比）
    stop_loss: float = 45.0          # 止损价格（百分比）
    take_profit: float = 95.0        # 止盈价格（百分比）
    initial_balance: float = 12.0    # 初始余额（用于仓位计算）
    initial_position: float = 1.0    # 初始开仓金额
    trade_cycle_minutes: int = 5     # 交易周期（分钟）
    
    # 网络参数
    chain_id: int = 137              # Polygon Mainnet
    signature_type: int = 2          # EOA钱包
    
    # 市场参数
    market_slug: str = ""            # 市场标识
    
    def __post_init__(self):
        """初始化后验证"""
        # 从环境变量加载
        env_vars = load_env_variables()
        for key, value in env_vars.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)
        
        # 从配置文件加载
        self._load_from_file()
        
        # 验证
        self._validate()
    
    def _validate(self) -> None:
        """验证配置"""
        validate_price(self.entry_price, "买入价")
        validate_price(self.stop_loss, "止损价")
        validate_price(self.take_profit, "止盈价")
        
        if not validate_private_key(self.private_key):
            raise ConfigValidationError("私钥格式无效")
    
    def _load_from_file(self) -> None:
        """从文件加载配置"""
        if not CONFIG_FILE.exists():
            return
        
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key, value in data.items():
                    if hasattr(self, key) and value is not None:
                        setattr(self, key, value)
        except Exception:
            pass
    
    def save(self) -> None:
        """保存配置到文件"""
        data = {}
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            # 跳过敏感字段
            if field_name in ["private_key", "api_key", "api_secret", "passphrase"]:
                continue
            data[field_name] = value
        
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    
    def to_dict(self, hide_sensitive: bool = True) -> dict:
        """转换为字典"""
        config_dict = asdict(self)
        if hide_sensitive:
            for field in ["private_key", "api_key", "api_secret", "passphrase"]:
                if config_dict.get(field):
                    config_dict[field] = f"***HIDDEN*** ({len(config_dict[field])} chars)"
        return config_dict
    
    def is_configured_for_trading(self) -> bool:
        """检查是否已配置用于真实交易"""
        return all([
            self.private_key,
            self.api_key,
            self.api_secret,
            self.passphrase,
        ])
