"""测试仓位计算逻辑"""

def calculate_position(
    balance: float,
    initial_balance: float,
    position_divisor: float = 12.0,
    position_multiplier_threshold: float = 3.0,
    max_position_multiplier: float = 8.0,
    min_position_amount: float = 1.0,
) -> float:
    """计算仓位"""
    if balance < min_position_amount:
        return 0
    
    # 基础仓位 = 初始余额 / position_divisor
    base_amount = initial_balance / position_divisor
    
    # 仓位倍数计算
    multiplier = 1.0
    threshold = initial_balance
    
    # 当余额达到 threshold * position_multiplier_threshold 时，仓位翻倍
    while balance >= threshold * position_multiplier_threshold:
        multiplier *= 2
        threshold *= position_multiplier_threshold
        
        # 限制最大倍数
        if multiplier > max_position_multiplier:
            multiplier = max_position_multiplier
            break
    
    position_amount = base_amount * multiplier
    
    # 确保仓位至少是最小开仓金额
    if position_amount < min_position_amount:
        # 如果余额足够，使用最小开仓金额
        if balance >= min_position_amount:
            position_amount = min_position_amount
        else:
            return 0
    
    # 确保仓位不超过当前余额
    if position_amount > balance:
        position_amount = balance
    
    return position_amount


def test_position_calculation():
    """测试仓位计算"""
    print("=" * 60)
    print("仓位计算测试")
    print("=" * 60)
    
    # 测试1: 默认参数
    print("\n【测试1: 默认参数】")
    print("参数: position_divisor=12, threshold=3, max_multiplier=8, min_amount=1")
    initial = 12.0
    balances = [12, 36, 108, 324, 972, 2916]
    
    for balance in balances:
        position = calculate_position(balance, initial)
        print(f"余额=${balance:.0f} → 仓位=${position:.2f}")
    
    # 测试2: 调整基础仓位比例
    print("\n【测试2: 基础仓位比例调整】")
    print("参数: position_divisor=20 (更保守)")
    initial = 12.0
    for balance in balances:
        position = calculate_position(balance, initial, position_divisor=20)
        print(f"余额=${balance:.0f} → 仓位=${position:.2f}")
    
    # 测试3: 调整倍增阈值
    print("\n【测试3: 倍增阈值调整】")
    print("参数: threshold=2 (更快翻倍)")
    initial = 12.0
    for balance in balances:
        position = calculate_position(balance, initial, position_multiplier_threshold=2)
        print(f"余额=${balance:.0f} → 仓位=${position:.2f}")
    
    # 测试4: 限制最大倍数
    print("\n【测试4: 最大倍数限制】")
    print("参数: max_multiplier=4 (限制最大4倍)")
    initial = 12.0
    for balance in balances:
        position = calculate_position(balance, initial, max_position_multiplier=4)
        print(f"余额=${balance:.0f} → 仓位=${position:.2f}")
    
    # 测试5: 最小开仓金额
    print("\n【测试5: 最小开仓金额】")
    print("参数: min_amount=5 (最小$5)")
    initial = 12.0
    small_balances = [1, 5, 10, 12]
    for balance in small_balances:
        position = calculate_position(balance, initial, min_position_amount=5)
        print(f"余额=${balance:.0f} → 仓位=${position:.2f}")
    
    print("\n" + "=" * 60)


if __name__ == "__main__":
    test_position_calculation()
