"""
Polymarket 实时交易程序
- V2: 每周期最多交易一次
- V3: 同一周期内无持仓时可继续交易
"""
import sys
from config import TradingConfig


def main():
    print("\n" + "=" * 50)
    print("  Polymarket 实时交易")
    print("=" * 50)
    
    # 选择版本
    print("\n选择交易策略版本:")
    print("  1. V2 - 每周期最多交易一次")
    print("  2. V3 - 同一周期内无持仓时可继续交易")
    print()
    
    version = None
    while version is None:
        try:
            choice = input("请选择 (1/2): ").strip()
            if choice == '1':
                version = 'V2'
            elif choice == '2':
                version = 'V3'
            else:
                print("无效输入，请输入 1 或 2")
        except:
            print("无效输入，请输入 1 或 2")
    
    print(f"\n[版本] {version}")
    
    # 加载配置
    config = TradingConfig.load()
    
    # 创建对应版本的交易引擎
    try:
        if version == 'V2':
            from trading_engine_v2 import RealtimeTrader
        else:
            from trading_engine_v3 import RealtimeTrader
        trader = RealtimeTrader(config)
    except Exception as e:
        print(f"[错误] 初始化失败: {e}")
        sys.exit(1)
    
    # 启动交易
    trader.start()


if __name__ == "__main__":
    main()
