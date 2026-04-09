# 交易逻辑问题诊断

## 问题1: has_traded_in_event 标记时机错误

### 当前逻辑（错误）
```python
# 第486-499行：如果没有订单成交
if not has_execution and not self.current_position:
    if self.pending_orders:
        print("[周期] 周期结束，取消未成交的订单...")
        self._cancel_pending_orders()
    else:
        print("[周期] 订单创建失败，跳过此周期，等待下一周期...")
    
    # 等待剩余时间（如果有）
    elapsed = time.time() - cycle_start
    remaining_time = max(0, cycle_duration - elapsed)
    if remaining_time > 0:
        time.sleep(remaining_time)
    return  # ← 这里return了

# 第501-503行：标记为已交易
if not self.has_traded_in_event:
    self.has_traded_in_event = True
```

### 问题分析
- 如果订单没有成交（has_execution=False），会在第499行return
- **不会执行第502-503行的标记逻辑**
- 所以这个逻辑实际上是：**只有订单成交后才会标记为已交易**

**结论**: 这个逻辑是正确的，不是问题所在。

## 问题2: 开仓价格问题

### 当前逻辑
```python
# place_dual_orders 第832-834行
entry_price = self.config.entry_price  # 75（美分单位）
entry_price_float = entry_price / 100.0 if entry_price > 1 else entry_price  # 0.75

# pending_orders 第907、915行
"price": entry_price,  # 75（美分单位）
```

### 问题分析
- pending_orders中存储的是 **entry_price = 75**（美分单位）
- 但实际下单时使用的是 **api_price = 0.75**（0-1格式）
- 成交后设置持仓时：
```python
# wait_for_execution 第998行
"entry_price": order_info["price"],  # 75（美分单位）
```

### 问题
**开仓价格存储的是美分单位（75），但是：**
1. 在监控持仓时，如何使用这个价格？
2. 在计算盈亏时，如何使用这个价格？

让我检查盈亏计算逻辑...

## 问题3: 同时持有YES和NO的可能性

### 可能的场景

**场景1**: 两个订单同时成交
- YES和NO订单在同一时刻成交
- wait_for_execution检测到YES成交，设置current_position=YES
- 然后调用_cancel_pending_orders()取消NO订单
- **但是NO订单已经成交了**，取消失败
- 结果：current_position只存储了YES，但实际上持有YES和NO

**场景2**: 周期结束时的并发问题
- 周期结束时，订单还在挂着
- 市场价格变动，两个订单都成交了
- 代码没有检测到（因为已经退出wait_for_execution）
- 结果：持有YES和NO，但没有current_position记录

**场景3**: 多个周期运行
- 有bug导致start()方法被多次调用
- 多个线程同时运行execute_trade_cycle
- 结果：混乱

让我检查是否有线程问题...
