# V4 引擎代码逻辑审查报告

## ✅ 总体评价：逻辑正确，可以正常运行

---

## 一、核心逻辑流程

### 1.1 主循环状态机

```
启动
  ↓
初始化余额 → 失败 → 退出
  ↓
获取市场数据
  ↓
获取实时价格
  ↓
状态判断：
  ├─ STATE_IDLE → 检查买入条件
  ├─ STATE_HOLDING → 切换到监控卖出
  └─ STATE_MONITORING_EXIT → 检查止损止盈
```

**✅ 正确**：状态机逻辑清晰，转换正确。

---

### 1.2 买入逻辑（_handle_idle）

#### 触发条件
```python
can_buy_yes = yes_price >= entry_price and yes_price < 0.85
can_buy_no = no_price >= entry_price and no_price < 0.85
```

**✅ 正确**：
- 价格 ≥ 70%（达到买入价）
- 价格 < 85%（价格过高限制）
- 最后1分钟不买入（remaining < 60秒）

#### 买入执行
1. 强制刷新卖一价（best_ask）
2. 价格滑动检查
3. 查询最新余额
4. 计算仓位和股数
5. 发送FOK订单
6. 确认成交（最多等待5秒）

**✅ 正确**：逻辑完整，有冷却机制防止频繁重试。

---

### 1.3 卖出逻辑（_execute_sell）

#### 止损止盈条件
```python
# 止损：价格 <= 45%
if current_price <= 0.45:
    _execute_sell("STOP_LOSS", current_price)

# 止盈：价格 >= 95%
if current_price >= 0.95:
    _execute_sell("TAKE_PROFIT", current_price)
```

**✅ 正确**：全时段固定止损45%、止盈95%。

#### 卖出执行
1. 查询实际余额
2. 强制刷新买一价（best_bid）
3. 检查订单金额（≥ $1）
4. 发送FOK订单
5. 更新统计

**✅ 正确**：逻辑完整，有冷却机制。

---

## 二、关键功能检查

### 2.1 价格获取

**方法**：从订单簿获取中间价
```python
price = (best_ask + best_bid) / 2
```

**✅ 正确**：
- 使用卖一价和买一价的平均值
- 价格验证：YES + NO ≈ 1.00（偏差>15%时修正）
- 缓存机制：2秒有效期
- 后台刷新：主循环永不阻塞

---

### 2.2 仓位计算

**公式**：
```python
base = initial_balance / 12
multiplier = 2^n  (当 balance >= initial_balance * 3^n)
position_amount = base * multiplier
```

**示例**：
| 初始余额 | 当前余额 | 仓位 |
|---------|---------|------|
| $12 | $12 | $1 |
| $12 | $36 | $2 |
| $12 | $108 | $4 |

**✅ 正确**：
- 最小股数限制：5股
- 最小订单金额：$1
- 余额不足保护：使用余额的90%

---

### 2.3 市场数据获取

**逻辑**：
```python
# 计算当前事件slug
slug = f"btc-updown-5m-{period_ts}"

# 获取市场
market = client.get_market_by_slug(slug)

# 提取token_ids
token_ids = market.get("clobTokenIds", [])
```

**✅ 正确**：
- 使用动态slug匹配当前5分钟周期
- 正确提取YES/NO token IDs

---

### 2.4 事件结算处理

**逻辑**：
1. 事件结束时检查结算结果
2. 根据结果计算盈亏
3. 更新统计

**✅ 正确**：不尝试卖出已结算事件。

---

## 三、安全检查

### 3.1 防止重复买入

**✅ 正确**：
```python
# 方式1：状态机控制
if self.state == STATE_IDLE:
    # 只有IDLE状态才能买入

# 方式2：标志位控制
if self.has_traded_in_event:
    if self.position is not None:
        return  # 已有持仓，跳过
```

---

### 3.2 价格滑动检查

**✅ 正确**：
```python
# 买入前检查
if best_ask < entry_price:
    print("卖一价 < 买入价，跳过")
if best_ask >= 0.85:
    print("卖一价 >= 85%，跳过")
```

---

### 3.3 冷却机制

**✅ 正确**：
```python
# 买入冷却
self._buy_cooldown = time.time() + 2

# 卖出冷却
self._sell_cooldown = time.time() + 2
```

---

### 3.4 最小订单金额

**✅ 正确**：
```python
# 买入最小5股
if shares < 5:
    shares = 5

# 卖出最小$1
if order_amount < 1.0:
    return
```

---

## 四、潜在问题

### ⚠️ 4.1 确认提示会阻塞启动

**问题**：
```python
def _confirm_params(self) -> None:
    print("确认开始? (y/n): ", end="", flush=True)
    user_input = input().strip().lower()
```

**影响**：程序启动后会等待用户输入，无法自动运行。

**建议**：添加配置选项跳过确认。

---

### ⚠️ 4.2 仓位计算可能不准确

**问题**：
```python
while self.balance >= self.initial_balance * (3 ** power):
    multiplier = 2 ** power
    power += 1
```

**示例**：
- 初始余额 $12
- 当前余额 $36
- 计算：`3^0=1`, `3^1=3`
- 结果：`multiplier = 2^1 = 2` ✅

**✅ 正确**：逻辑无误。

---

## 五、总结

### ✅ 可以正常工作的功能

1. ✅ 价格获取（订单簿中间价）
2. ✅ 买入逻辑（价格范围70%-85%）
3. ✅ 卖出逻辑（止损45%、止盈95%）
4. ✅ 状态机控制（防止重复买入）
5. ✅ 仓位计算（动态倍增）
6. ✅ 最后1分钟限制（不买入）
7. ✅ 价格滑动检查
8. ✅ 冷却机制
9. ✅ 最小订单金额检查
10. ✅ 事件结算处理

### ⚠️ 需要注意的点

1. **启动确认**：程序启动后会等待用户输入 'y' 确认
2. **API凭证**：需要配置 PRIVATE_KEY 才能运行
3. **网络依赖**：依赖 Polymarket API 和 CLOB API

---

## 六、运行建议

1. **配置 .env 文件**
   ```env
   PRIVATE_KEY=你的私钥
   SIGNATURE_TYPE=2
   FUNDER_ADDRESS=0xDAe2545bB3063184ee7CEf9388cdcE1cB04e4bE4
   ```

2. **运行程序**
   ```bash
   python main_v4.py
   ```

3. **预期行为**
   - 程序启动后显示余额
   - 等待用户输入 'y' 确认
   - 开始监控价格
   - 满足条件时自动交易

---

## 七、结论

**✅ 程序逻辑正确，可以按预定逻辑工作。**

唯一的人工干预点是启动时的确认提示，这是安全设计，防止误启动。
