"""
配置管理模块
提供配置验证、持久化和管理功能
"""
import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field, MISSING
from pathlib import Path
from typing import List, Dict, Any, Optional

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
LOG_DIR = CONFIG_DIR / "logs"

# .env 文件路径（当前目录）
ENV_FILE = Path.cwd() / ".env"

# 确保目录存在
CONFIG_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


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
        "LOG_LEVEL": "log_level",
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
            elif config_key == "log_level":
                env_vars[config_key] = value.upper()
            else:
                env_vars[config_key] = value

    return env_vars



class ConfigValidationError(Exception):
    """配置验证错误"""
    pass


def validate_private_key(private_key: str) -> bool:
    """验证私钥格式"""
    if not private_key:
        return True  # 空私钥允许（公开 API 模式）
    # Ethereum 私钥格式：64 个十六进制字符（可选 0x 前缀）
    pattern = r'^(0x)?[a-fA-F0-9]{64}$'
    return bool(re.match(pattern, private_key))


def validate_price(price: float, name: str = "价格") -> None:
    """验证价格参数"""
    if not isinstance(price, (int, float)):
        raise ConfigValidationError(f"{name}必须是数字")
    if price < 0:
        raise ConfigValidationError(f"{name}不能为负数")
    if price > 100:  # Polymarket 价格范围 0-100
        raise ConfigValidationError(f"{name}不能超过 100 美分")


def validate_position_size(size: float, name: str = "仓位大小") -> None:
    """验证仓位大小"""
    if not isinstance(size, (int, float)):
        raise ConfigValidationError(f"{name}必须是数字")
    if size <= 0:
        raise ConfigValidationError(f"{name}必须大于 0")
    if size > 10000:  # 合理上限
        raise ConfigValidationError(f"{name}过大（最大 10000 美元）")


@dataclass
class TradingConfig:
    """交易配置类（支持验证）"""

    # 交易参数
    initial_balance: float = 120.0
    current_price: float = 75.0
    leverage: int = 1
    entry_price: float = 75.0
    stop_loss: float = 45.0
    take_profit: float = 95.0
    trade_cycle_minutes: int = 5

    # API凭证（必需）
    private_key: str = ""  # 钱包私钥（L1 身份验证，官方SDK需要）
    api_key: str = ""  # API 密钥（L2 身份验证）
    api_secret: str = ""  # API 密钥（L2 身份验证）
    passphrase: str = ""  # API 口令（L2 身份验证）
    signature_type: int = 0  # 签名类型: 0=EOA(普通钱包), 1=POLY_PROXY, 2=GNOSIS_SAFE
    funder_address: str = ""  # 资金地址（代理钱包地址，从 Polymarket.com 获取）
    chain_id: int = 137  # Polygon链ID

    # 市场配置（留空则自动查找活跃市场）
    market_id: str = ""  # 市场ID（程序会自动查找活跃的5分钟市场）
    market_slug: str = "bitcoin-price-in-5-minutes"

    # 日志设置
    log_level: str = "INFO"
    log_to_file: bool = True

    def __post_init__(self):
        """初始化后验证"""
        self.validate()

    def validate(self) -> None:
        """验证配置参数"""
        errors = []

        # 验证私钥格式
        if not validate_private_key(self.private_key):
            errors.append("私钥格式错误（应为 0x 开头的 64 位十六进制字符串）")

        # 验证价格参数
        try:
            validate_price(self.entry_price, "开仓价格")
            validate_price(self.stop_loss, "止损价格")
            validate_price(self.take_profit, "止盈价格")
            validate_price(self.current_price, "当前价格")
        except ConfigValidationError as e:
            errors.append(str(e))

        # 验证价格逻辑
        if self.stop_loss >= self.entry_price:
            errors.append("止损价格必须小于开仓价格")
        if self.take_profit <= self.entry_price:
            errors.append("止盈价格必须大于开仓价格")

        # 验证余额
        try:
            validate_position_size(self.initial_balance, "初始余额")
        except ConfigValidationError as e:
            errors.append(str(e))

        # 验证杠杆
        if self.leverage < 1 or self.leverage > 100:
            errors.append("杠杆必须在 1-100 之间")

        # 验证交易周期
        if self.trade_cycle_minutes < 1 or self.trade_cycle_minutes > 60:
            errors.append("交易周期必须在 1-60 分钟之间")

        # 验证签名类型
        if self.signature_type not in [0, 1, 2]:
            errors.append("签名类型必须是 0(EOA)、1(POLY_PROXY) 或 2(GNOSIS_SAFE)")

        # 验证日志级别
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level not in valid_log_levels:
            errors.append(f"日志级别必须是 {valid_log_levels} 之一")

        # 验证 API 凭证完整性
        # 真实交易必须提供私钥
        if not self.private_key:
            errors.append("真实交易必须提供私钥（private_key）")
        # API 凭证可以为空，程序会自动创建
        # 只要提供了私钥，就允许通过验证

        if errors:
            raise ConfigValidationError("配置验证失败：\n" + "\n".join(f"  - {e}" for e in errors))

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TradingConfig":
        """从字典创建实例"""
        # 过滤掉不存在的字段，避免报错
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)

    def save(self) -> None:
        """保存配置到文件"""
        try:
            # 保存前先验证
            self.validate()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ConfigValidationError(f"保存配置失败: {e}")

    @classmethod
    def load(cls) -> "TradingConfig":
        """从文件加载配置，并从 .env 文件加载环境变量"""
        try:
            # 1. 先从 .env 文件加载环境变量
            env_vars = load_env_variables()
            
            print(f"[配置] 配置文件路径: {CONFIG_FILE}", flush=True)

            # 2. 加载配置文件
            if not CONFIG_FILE.exists():
                print(f"[配置] 配置文件不存在，创建默认配置", flush=True)
                # 创建一个包含所有默认值的字典
                data = {}
                for field in cls.__dataclass_fields__.values():
                    # 使用字段的默认值
                    if field.default is not MISSING:
                        data[field.name] = field.default
                    elif field.default_factory is not MISSING:
                        data[field.name] = field.default_factory()
                    else:
                        data[field.name] = ""
                
                # 应用环境变量（覆盖默认值）
                for key, value in env_vars.items():
                    if value is not None:
                        data[key] = value
                
                # 从字典创建配置（此时已包含环境变量）
                config = cls.from_dict(data)
                config.save()
                print(f"[配置] 已创建默认配置文件", flush=True)
                return config

            print(f"[配置] 加载现有配置文件", flush=True)
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # 3. 先合并环境变量到数据中（在创建实例之前）
                for key, value in env_vars.items():
                    if value is not None:  # 只应用非空值
                        data[key] = value
                
                # 4. 再从字典创建实例（此时已包含环境变量）
                config = cls.from_dict(data)
                
                print(f"[配置] 配置加载完成: entry_price={config.entry_price}", flush=True)
                return config
        except json.JSONDecodeError as e:
            raise ConfigValidationError(f"配置文件格式错误: {e}")
        except Exception as e:
            raise ConfigValidationError(f"加载配置失败: {e}")

    def update(self, **kwargs) -> None:
        """更新配置"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        # 更新后验证并保存
        self.validate()
        self.save()

    def get_safe_config(self) -> Dict[str, Any]:
        """获取安全的配置（隐藏敏感信息）"""
        config_dict = self.to_dict()
        # 隐藏敏感信息
        sensitive_fields = ["private_key", "api_secret", "passphrase"]
        for field in sensitive_fields:
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
