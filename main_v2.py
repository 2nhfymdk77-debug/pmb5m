"""
Polymarket 实时交易程序 V2
- 实时监控价格
- 立即触发交易
- 简化输出
"""
import sys
from config import TradingConfig
from trading_engine_v2 import RealtimeTrader


def main():
    print("\n" + "=" * 50)
    print("  Polymarket 实时交易 V2")
    print("=" * 50)
    
    # 加载配置
    config = TradingConfig()
    
    # 创建交易引擎
    try:
        trader = RealtimeTrader(config)
    except Exception as e:
        print(f"[错误] 初始化失败: {e}")
        sys.exit(1)
    
    # 启动交易
    trader.start()


if __name__ == "__main__":
    main()
