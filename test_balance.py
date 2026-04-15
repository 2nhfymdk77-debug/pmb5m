#!/usr/bin/env python3
"""
测试余额获取功能
"""
import sys
from pathlib import Path

# 确保能找到本地模块
sys.path.insert(0, str(Path(__file__).parent))

from config import TradingConfig
from polymarket_api import PolymarketClient


def test_balance():
    """测试获取余额"""
    print("=" * 60)
    print("Polymarket 余额测试")
    print("=" * 60)
    
    # 1. 加载配置
    print("\n[1] 加载配置...")
    try:
        config = TradingConfig.load()
        print("✓ 配置加载成功")
    except Exception as e:
        print(f"✗ 配置加载失败: {e}")
        print("\n请确保已配置 .env 文件中的以下字段：")
        print("  - PRIVATE_KEY=你的私钥")
        print("  - API_KEY=你的API密钥")
        print("  - API_SECRET=你的API密钥密文")
        print("  - PASSPHRASE=你的API密码")
        return False
    
    # 2. 检查必要字段
    print("\n[2] 检查配置字段...")
    required_fields = {
        "private_key": "PRIVATE_KEY",
        "api_key": "API_KEY",
        "api_secret": "API_SECRET",
        "passphrase": "PASSPHRASE",
    }
    
    missing = []
    for field, name in required_fields.items():
        value = getattr(config, field, None)
        if value:
            # 显示部分内容（隐藏敏感信息）
            if len(str(value)) > 10:
                display = f"{str(value)[:6]}...{str(value)[-4:]}"
            else:
                display = "***"
            print(f"  ✓ {name}: {display}")
        else:
            print(f"  ✗ {name}: 未设置")
            missing.append(name)
    
    if missing:
        print(f"\n✗ 缺少必要字段: {', '.join(missing)}")
        print("\n请在 .env 文件中填写这些字段")
        return False
    
    # 3. 初始化客户端
    print("\n[3] 初始化API客户端...")
    try:
        client = PolymarketClient(
            private_key=config.private_key,
            api_key=config.api_key,
            api_secret=config.api_secret,
            passphrase=config.passphrase,
            chain_id=config.chain_id,
            signature_type=config.signature_type,
            funder_address=config.funder_address,
        )
        print("✓ 客户端初始化成功")
    except Exception as e:
        print(f"✗ 客户端初始化失败: {e}")
        return False
    
    # 4. 获取余额
    print("\n[4] 获取账户余额...")
    try:
        balance = client.get_balance()
        if balance is not None and balance > 0:
            print(f"✓ 余额获取成功: ${balance:.2f} USDC")
            return True
        elif balance is not None and balance == 0:
            print(f"⚠ 余额为 $0.00")
            print("  请确保账户有USDC余额")
            return False
        else:
            print(f"✗ 余额获取失败")
            return False
    except Exception as e:
        print(f"✗ 获取余额失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_balance()
    print("\n" + "=" * 60)
    if success:
        print("测试结果: ✓ 成功 - 可以正常获取余额")
    else:
        print("测试结果: ✗ 失败 - 请检查配置")
    print("=" * 60)
    sys.exit(0 if success else 1)
