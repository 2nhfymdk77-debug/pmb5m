#!/usr/bin/env python3
"""
Polymarket 自动交易 - V4策略
运行方式: python main_v4.py
"""
from config import TradingConfig
from trading_engine_v4 import RealtimeTrader


def main():
    """主函数"""
    print("=" * 60)
    print("Polymarket 自动交易系统 - V4策略")
    print("=" * 60)
    
    # 加载配置
    try:
        config = TradingConfig.load()
        print("✓ 配置已加载")
    except Exception as e:
        print(f"✗ 加载配置失败: {e}")
        print("\n请确保已配置 .env 文件，包含以下内容：")
        print("  PRIVATE_KEY=你的私钥")
        print("  API_KEY=你的API密钥")
        print("  API_SECRET=你的API密钥密文")
        print("  PASSPHRASE=你的API密码")
        return
    
    # 检查API密钥
    if not config.private_key or not config.api_key or not config.api_secret:
        print("✗ API密钥未配置")
        print("\n请在 .env 文件中配置API密钥")
        return
    
    # 创建交易引擎
    try:
        trader = RealtimeTrader(config)
        print("✓ 交易引擎已初始化")
    except Exception as e:
        print(f"✗ 初始化失败: {e}")
        return
    
    # 启动交易
    print("\n开始交易...")
    print("-" * 60)
    
    try:
        trader.start()
    except KeyboardInterrupt:
        print("\n\n用户中断，正在退出...")
    except Exception as e:
        print(f"\n\n错误: {e}")
    finally:
        trader.stop()
        print("\n交易已停止")


if __name__ == "__main__":
    main()
