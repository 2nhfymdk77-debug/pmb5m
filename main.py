"""
Polymarket自动交易程序 - 主入口
真实交易模式
"""
import sys
import argparse

from config import TradingConfig
from trading_engine import TradingEngine
from polymarket_api import PolymarketClient


def print_banner():
    """打印欢迎横幅"""
    banner = """
╔════════════════════════════════════════════════════════════╗
║                                                              ║
║          Polymarket 自动交易系统 (Python版本)                ║
║                                                              ║
║          真实交易模式                                         ║
║                                                              ║
╚════════════════════════════════════════════════════════════╝
    """
    print(banner, flush=True)


def print_config(config: TradingConfig):
    """打印当前配置"""
    print("\n" + "=" * 60)
    print("当前配置:")
    print("=" * 60)

    print(f"  模式: 真实交易")
    print(f"  初始余额: ${config.initial_balance}")
    # 转换价格为 0-1 格式显示
    entry_display = config.entry_price / 100.0 if config.entry_price > 1 else config.entry_price
    stop_loss_display = config.stop_loss / 100.0 if config.stop_loss > 1 else config.stop_loss
    take_profit_display = config.take_profit / 100.0 if config.take_profit > 1 else config.take_profit
    print(f"  开仓价格: ${entry_display:.2f}")
    print(f"  止损价格: ${stop_loss_display:.2f}")
    print(f"  止盈价格: ${take_profit_display:.2f}")
    print(f"  交易周期: {config.trade_cycle_minutes} 分钟")
    print(f"  仓位计算: 基础=初始余额/12, 余额≥初始×3^n → 开仓=基础×2^n")
    print(f"\n  身份验证配置:")
    if config.private_key:
        print(f"    Private Key: {config.private_key[:10]}...{config.private_key[-10:]}")
    if config.api_key:
        print(f"    API Key: {config.api_key[:10]}...{config.api_key[-10:]}")
    if config.api_secret:
        print(f"    API Secret: {config.api_secret[:10]}...{config.api_secret[-10:]}")
    if config.passphrase:
        print(f"    Passphrase: {config.passphrase[:10]}...{config.passphrase[-10:]}")
    print(f"    Signature Type: {config.signature_type} ({get_signature_type_name(config.signature_type)})")
    if config.funder_address:
        print(f"    Funder Address: {config.funder_address[:10]}...{config.funder_address[-10:]}")

    print("=" * 60 + "\n")


def get_signature_type_name(signature_type: int) -> str:
    """获取签名类型名称"""
    types = {
        0: "EOA",
        1: "POLY_PROXY",
        2: "GNOSIS_SAFE"
    }
    return types.get(signature_type, "UNKNOWN")


def print_menu():
    """打印交互菜单"""
    print("\n" + "=" * 60)
    print("交互菜单:")
    print("=" * 60)
    print("  1. 开始交易")
    print("  2. 修改参数")
    print("  3. 配置API凭证")
    print("  4. 查看交易历史")
    print("  5. 退出")
    print("=" * 60)


def modify_parameters(config: TradingConfig) -> None:
    """修改交易参数"""
    print("\n" + "=" * 60)
    print("修改交易参数:")
    print("=" * 60)

    try:
        entry_price = float(input(f"  开仓价格 (当前: {config.entry_price}): ") or config.entry_price)
        stop_loss = float(input(f"  止损价格 (当前: {config.stop_loss}): ") or config.stop_loss)
        take_profit = float(input(f"  止盈价格 (当前: {config.take_profit}): ") or config.take_profit)
        trade_cycle = int(input(f"  交易周期分钟 (当前: {config.trade_cycle_minutes}): ") or config.trade_cycle_minutes)

        config.update(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trade_cycle_minutes=trade_cycle,
        )

        print("\n[ 参数已更新!")

    except ValueError as e:
        print(f"\n[X] 输入错误: {e}")


def configure_api(config: TradingConfig, client: PolymarketClient = None) -> None:
    """配置API凭证"""
    print("\n" + "=" * 60)
    print("配置API凭证:")
    print("=" * 60)
    print("  输入为空则保持当前值")
    print("  真实交易模式需要配置 Private Key")
    print("  API 凭证（Key/Secret/Passphrase）用于 L2 身份验证\n")

    try:
        # 清除凭证选项
        if client:
            print("  [r/R] 清除旧凭证并重新创建")
            print()
        
        # L1 身份验证 - 私钥
        private_key_input = input(f"  Private Key (钱包私钥): ")
        if private_key_input.lower() == 'r':
            # 清除旧凭证并重新创建
            if client and client.clear_api_credentials():
                print("\n  [OK] 已清除旧凭证，请在下方输入新的私钥")
            private_key_input = input(f"  Private Key (钱包私钥): ")
        
        if private_key_input:
            config.private_key = private_key_input

        # L2 身份验证 - API 凭证
        print("\n  L2 身份验证（API 凭证）:")
        print("  提示: 如果没有 API 凭证，可以留空，程序会尝试自动创建")

        api_key = input(f"  API Key (当前: {config.api_key[:10] if config.api_key else '(未设置)'}...): ") or config.api_key
        api_secret = input(f"  API Secret (当前: {config.api_secret[:10] if config.api_secret else '(未设置)'}...): ") or config.api_secret
        passphrase = input(f"  Passphrase (当前: {config.passphrase[:10] if config.passphrase else '(未设置)'}...): ") or config.passphrase

        # 签名类型和 funder 地址
        print("\n  高级配置:")

        signature_type_input = input(f"  Signature Type (当前: {config.signature_type}, 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE): ")
        if signature_type_input:
            config.signature_type = int(signature_type_input)

        funder_address = input(f"  Funder Address (当前: {config.funder_address[:10] if config.funder_address else '(未设置)'}...): ") or config.funder_address

        config.update(
            private_key=config.private_key,
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            signature_type=config.signature_type,
            funder_address=funder_address,
        )

        print("\n[ API凭证已更新!")

    except Exception as e:
        print(f"\n[X] 配置错误: {e}")


def view_trade_history(engine: TradingEngine) -> None:
    """查看交易历史"""
    print("\n" + "=" * 60)
    print("交易历史:")
    print("=" * 60)

    records = engine.trade_history.get_all()

    if not records:
        print("  暂无交易记录")
        return

    print(f"{'时间':<20} {'类型':<6} {'开仓':<8} {'平仓':<8} {'盈亏':<8}")
    print("-" * 60)

    for record in records[-10:]:  # 显示最近10条
        print(
            f"{record.timestamp:<20} "
            f"{record.type:<6} "
            f"{record.entry_price:<8.2f} "
            f"{record.exit_price:<8.2f} "
            f"{record.pnl:+<8.2f}"
        )

    stats = engine.trade_history.get_statistics()
    print("\n统计:")
    print(f"  总交易: {stats['total_trades']}")
    print(f"  盈利: {stats['win_trades']}")
    print(f"  亏损: {stats['loss_trades']}")
    print(f"  总盈亏: ${stats['total_profit']:+.2f}")
    print(f"  胜率: {stats['win_rate']:.2f}%")


def run_interactive_mode(config: TradingConfig) -> None:
    """运行交互模式"""
    # 直接进入主菜单（真实交易模式）
    while True:
        print_config(config)
        print_menu()

        choice = input("\n请选择操作 (1-5): ").strip()

        if choice == "1":
            # 开始交易
            engine = TradingEngine(config)
            engine.start()
            break

        elif choice == "2":
            # 修改参数
            modify_parameters(config)

        elif choice == "3":
            # 配置API
            configure_api(config)

        elif choice == "4":
            # 查看历史（需要先创建引擎）
            engine = TradingEngine(config)
            view_trade_history(engine)

        elif choice == "5":
            # 退出
            print("\n再见!")
            sys.exit(0)

        else:
            print("\n[X] 无效选择，请重新输入")


def run_direct_mode(config: TradingConfig) -> None:
    """直接运行模式（真实交易）"""
    engine = TradingEngine(config)
    engine.start()


def main():
    """主函数"""
    # 强制输出立即显示
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    
    parser = argparse.ArgumentParser(description="Polymarket自动交易系统 - 真实交易模式")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="直接运行模式（使用默认配置，跳过交互菜单）"
    )

    args = parser.parse_args()

    # 打印横幅
    print_banner()
    sys.stdout.flush()

    # 加载配置
    config = TradingConfig.load()
    sys.stdout.flush()

    # 选择运行模式
    if args.direct:
        run_direct_mode(config)
    else:
        run_interactive_mode(config)


if __name__ == "__main__":
    main()
