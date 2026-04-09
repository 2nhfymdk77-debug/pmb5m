# Polymarket Auto Trader - 项目文档

## 项目概览

这是一个基于 Python 的 Polymarket 平台自动交易系统，用于 5 分钟比特币预测。项目支持真实 API 模式，带有详细的实时交易仪表盘。

**技术栈**：Python 3.12 + py-clob-client + requests

## 项目结构

```
polymarket-trader/
├── config.py                    # 配置管理模块
├── polymarket_api.py            # Polymarket API 客户端
├── trading_engine.py            # 交易引擎（核心逻辑）
├── ui_display.py                # 界面显示模块
├── main.py                      # 主程序入口
├── requirements.txt             # 依赖列表
├── .env.example                 # 环境变量示例
├── AGENTS.md                    # 本规范文档
├── README_PYTHON.md             # 使用文档
└── README.md                    # 项目根说明
```

## 核心功能模块

### 1. 配置管理 (config.py)

**职责**：
- 管理交易配置
- 配置持久化
- 交易历史管理

**核心类**：
- `TradingConfig` - 交易配置类
- `TradeRecord` - 交易记录类
- `TradeHistory` - 交易历史管理类

**配置字段**：
```python
initial_balance: float   # 初始余额（用于仓位计算）
entry_price: float       # 开仓价格（美分单位）
initial_position: float  # 初始开仓金额
stop_loss: float         # 止损价格（美分单位）
take_profit: float       # 止盈价格（美分单位）
trade_cycle_minutes: int # 交易周期（分钟）
private_key: str         # 钱包私钥（必需）
api_key: str             # API密钥（必需）
api_secret: str          # API密钥（必需）
passphrase: str          # API密码（必需）
```

**重要提示**：
- **真实交易必须提供完整的API凭证**：private_key, api_key, api_secret, passphrase
- 所有交易都使用真实API和真实数据

### 2. API 客户端 (polymarket_api.py)

**职责**：
- 封装 Polymarket API
- 实现两级身份验证（L1 + L2）
- 智能缓存机制
- 数据格式转换

**核心类：PolymarketClient**

**关键方法**：
- `get_markets()` - 获取市场列表
- `get_market_prices(market_id)` - 获取市场价格
- `get_market_orderbook(market_id)` - 获取订单簿
- `create_order(...)` - 创建订单
- `cancel_order(order_id)` - 取消订单
- `get_balance()` - 获取余额
- `create_api_credentials()` - 创建 API 凭证

**身份验证**：
- L1（私钥）：创建 API 凭证、本地签名
- L2（API 凭证）：交易操作、余额查询

### 3. 交易引擎 (trading_engine.py)

**职责**：
- 实现交易策略
- 执行交易循环
- 管理持仓和订单
- 监控止损止盈

**核心类：TradingEngine**

**核心方法**：
- `start()` - 开始交易循环
- `stop()` - 停止交易
- `execute_trade_cycle()` - 执行一个交易周期
- `fetch_market_data()` - 获取市场数据
- `place_dual_orders(position_size)` - 挂双向限价单
- `wait_for_execution(position_size)` - 等待成交
- `monitor_position()` - 监控持仓
- `get_event_result()` - 获取事件结果（从API获取）
- `show_dashboard()` - 显示实时仪表盘

**交易策略**：
```
每 5 分钟一个周期：
1. 获取市场数据（价格、订单簿）
2. 从Polymarket读取余额
3. 计算开仓金额
4. 同时挂 YES 买单和 NO 买单 @ 75（两个不同的代币）
5. 真实API等待成交
6. 监控订单成交状态
7. 取消未成交的一侧
8. 监控价格触发（止损 45，止盈 95）
9. 如果到期（5分钟）未触发，按照事件结果结算
   - 从 Polymarket API 获取事件结果
   - 持仓代币获胜：平仓价 = 100
   - 持仓代币失败：平仓价 = 0
10. 平仓并计算盈亏
11. 记录交易
```

### 4. 界面显示 (ui_display.py)

**职责**：
- 实时交易仪表盘（现代化设计）
- 数据格式化和美化
- 状态显示和提示

**核心类**：
- `Colors` - ANSI 颜色管理（16种颜色）
- `Icons` - Unicode 图标管理（30+图标）
- `BoxStyle` - 边框样式（4种样式）
- `ModernFormatter` - 现代格式化工具
- `Box` - 边框框组件
- `ProgressBar` - 进度条组件
- `Table` - 表格组件
- `Card` - 卡片组件
- `TradingDashboard` - 交易仪表盘
- `RealTimeDisplay` - 实时显示控制器

**显示面板**（现代化设计）：
- 市场信息卡片（YES/NO 价格、订单簿、价差、更新时间）
- 账户信息卡片（余额、盈亏、杠杆）
- 交易统计卡片（总交易、胜率、总盈亏）
- 当前持仓卡片（持仓类型、盈亏）
- 挂单状态卡片（挂单列表）
- 交易历史表格（最近交易）
- 系统状态卡片（运行状态、API状态）

**特性**：
- 🎨 丰富的颜色支持（16种颜色 + 样式）
- 🖼️ 现代边框样式（单线、双线、圆角、粗边）
- 🔤 优雅的字体和格式化
- 📐 智能两列布局
- ⚡ 实时数据更新
- 📈 动态效果和动画

详细文档：[UI_DISPLAY_GUIDE.md](./UI_DISPLAY_GUIDE.md)

### 5. 主程序 (main.py)

**职责**：
- 用户交互界面
- 参数配置
- 启动交易引擎

## 交易策略逻辑

### Polymarket 机制说明

**重要**：Polymarket 的 YES 和 NO 是**两个不同的代币**，不是同一代币的多空对冲！

- **YES 代币**：表示"同意"某个预测
- **NO 代币**：表示"不同意"某个预测

**关键关系**：YES 价格 + NO 价格 = 100

### 核心策略

**同时买入 YES 和 NO @ 75**，随机选择一侧成交，然后监控止损止盈

**注意**：
- 所有操作都是**做多**，没有做空
- YES 和 NO 是两个不同的代币

### 仓位计算

**Polymarket 下单参数说明**：
- `size` 参数是**股数**，不是金额
- 订单金额 = 价格 × size
- 最小订单金额 = $1

```python
基础开仓金额 = 初始余额 / 12

当 余额 ≥ 初始余额 × 3^n 时
  开仓金额 = 基础金额 × 2^n

示例：初始余额 $12
- 余额 $12   → 开仓 $1  (基础)
- 余额 $36   → 开仓 $2  (12×3=36, 翻倍)
- 余额 $108  → 开仓 $4  (36×3=108, 再翻倍)
- 余额 $324  → 开仓 $8  (108×3=324, 再翻倍)

下单时计算股数：
- 股数 = ceil(开仓金额 / 价格)
- 示例：开仓金额 $1，价格 0.75 → 股数 = ceil(1/0.75) = 2股
- 实际订单金额 = 0.75 × 2 = $1.5 ≥ $1 ✓

最小订单金额：$1
```

### 盈亏计算

**统一逻辑**（只有做多）：

```python
盈亏 = (平仓价 - 开仓价) × 开仓金额 / 开仓价
```

### 止损止盈

**统一的止损止盈逻辑**（无论 YES 还是 NO）：

- **止损**：价格 ≤ 45
- **止盈**：价格 ≥ 95

### 到期结算

**重要**：如果在 5 分钟交易周期内没有触发止损或止盈，系统将按照事件结果结算：

**事件结果机制**：
- Polymarket 的每个预测事件最终会有一个结果（YES 或 NO）
- 如果事件结果为 YES，YES 代币价值 = 100，NO 代币价值 = 0
- 如果事件结果为 NO，YES 代币价值 = 0，NO 代币价值 = 100

**到期结算逻辑**：
```python
# 从 Polymarket API 获取事件结果
event_result = get_event_result()  # 返回 "YES" 或 "NO"

# 根据持仓代币和事件结果计算平仓价
if event_result == token:
    # 持仓的代币获胜
    exit_price = 100
else:
    # 持仓的代币失败
    exit_price = 0

# 计算盈亏
profit = (exit_price - entry_price) * position_size / entry_price
```

**示例**：
- 场景 1：持仓 YES @ 75，事件结果 = YES → 平仓价 = 100，盈利 = (100-75) × 10 / 75 = $3.33
- 场景 2：持仓 YES @ 75，事件结果 = NO → 平仓价 = 0，亏损 = (0-75) × 10 / 75 = -$10.00

**注意事项**：
- 从 Polymarket API 获取结算结果（需要事件已结算）
- 如果无法获取事件结果，使用当前市场价格作为平仓价

## 数据流

```
用户输入
    ↓
配置管理 (config.py)
    ↓
交易引擎 (trading_engine.py)
    ↓
API 客户端 (polymarket_api.py)
    ↓
Polymarket API
    ↓
界面显示 (ui_display.py)
    ↓
用户界面
```

## 关键文件说明

### config.py
- 管理所有交易配置
- 配置持久化到 `~/.polymarket-trader/config.json`
- 交易历史存储到 `~/.polymarket-trader/trade_history.json`
- 日志存储到 `~/.polymarket-trader/logs/`

### polymarket_api.py
- 使用官方 py-clob-client SDK
- 支持公开 API（无需身份验证）
- 支持私有 API（需要 L1 + L2 身份验证）
- 自动处理类型转换和数据解析

### trading_engine.py
- 核心交易逻辑
- 实时仪表盘更新
- 持仓和订单管理
- 日志记录

### ui_display.py
- 格式化显示工具
- 实时交易仪表盘
- 状态图标和颜色

## 开发指南

### 1. 常见修改任务

#### 修改交易参数
- 涉及文件：`config.py`（默认值）、`main.py`（用户输入）
- 示例：修改初始开仓金额为 20

#### 修改交易策略
- 涉及文件：`trading_engine.py`（`execute_trade_cycle`）
- 建议：在修改前更新策略文档

#### 添加新的 API 方法
- 涉及文件：`polymarket_api.py`
- 需要实现认证签名（如果需要）

#### 自定义界面显示
- 涉及文件：`ui_display.py`
- 可以添加新的面板或修改现有面板

### 2. 调试

```bash
# 查看日志
cat ~/.polymarket-trader/logs/trading.log

# 查看配置
cat ~/.polymarket-trader/config.json

# 查看交易历史
cat ~/.polymarket-trader/trade_history.json
```

### 3. 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 运行程序
python main.py

# 选择模式
1. 开始交易
2. 修改参数
3. 配置API凭证
4. 查看交易历史
5. 重新选择模式
6. 退出
```

## 安全性与性能

### 安全性
- 使用 HTTPS 协议
- 私钥不提交到版本控制
- API 凭证加密存储
- 支持 API 凭证自动创建

### 性能优化（重要）
- **速率限制器**：自动遵守 Polymarket API 速率限制
  - Gamma API：300 req / 10s
  - CLOB API：1,500 req / 10s
  - 交易 API：3,500 req / 10s（突发）
- **智能缓存**：永久缓存不变数据（tokenIDs、市场详情）
  - token_ids_cache：避免重复获取代币 ID
  - market_details_cache：避免重复获取市场详情
- **优化查询**：直接查询单个市场，而非获取全部市场列表
  - 从获取 1000+ 条记录 → 获取 1 条记录
  - 减少 99.9% 的数据传输量
- **批量请求**：使用批量 API 减少调用次数
- **API 统计**：实时监控 API 使用情况和缓存命中率

## 常见问题排查

### 应用无法启动
1. 检查 Python 版本（≥ 3.12）
2. 重新安装依赖
3. 查看日志文件

### 交易逻辑错误
1. 检查 `calculate_position_size()` 计算
2. 检查 `execute_order()` 盈亏计算
3. 使用日志调试关键变量

### API 连接失败
1. 检查网络连接
2. 检查 API 凭证
3. 查看错误日志

### 界面显示异常
1. 检查 `ui_display.py` 格式化
2. 检查数据类型转换
3. 查看控制台错误

## 未来扩展

### 待实现功能
- [ ] WebSocket 实时数据推送
- [ ] 更多技术指标（MA、RSI）
- [ ] 策略回测功能
- [ ] 交易通知（声音/弹窗）
- [ ] 数据导出（CSV/Excel）

### 优化方向
- [ ] 添加单元测试
- [ ] 优化界面响应速度
- [ ] 支持多市场交易
- [ ] 云端配置同步

## 相关资源

- [Polymarket API 官方文档](https://docs.polymarket.com/)
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)
- [README_PYTHON.md](./README_PYTHON.md) - 使用文档

## 更新日志

### 2025-04-08 - 极速交易优化
- **关键优化**：
  1. **成交后立即取消另一侧**：删除等待1秒的逻辑，成交后立即调用 `_cancel_single_order()` 取消另一侧订单
  2. **成交后立即设置止损止盈**：在 `wait_for_execution()` 中成交后立即调用止损止盈设置，不等到监控阶段
  3. **检查间隔优化**：从1秒改为0.5秒，更快检测成交（理论上响应时间减少50%）
- **新增方法**：
  - `_cancel_single_order()`：专门用于成交后快速取消单个订单
- **性能提升**：
  - 成交到取消另一侧：从 ~1秒 → <0.1秒
  - 成交到止损止盈设置：从 ~2秒 → <0.5秒
  - 订单检测延迟：从 1秒 → 0.5秒

### 2025-04-08 - 修复到期结算逻辑
- **问题**：TIMEOUT 时按照当前价格计算盈亏，而不是按照事件结果
- **解决方案**：
  - 新增 `get_event_result()` 方法，从 Polymarket API 获取事件结果
  - 修改到期结算逻辑，根据事件结果计算平仓价（获胜 = 100，失败 = 0）
  - 在真实模式下从 Polymarket API 获取结算结果
- **影响**：确保到期结算按照事件实际结果计算，更符合 Polymarket 机制

### 2025-04-08 - 删除模拟模式
- **变更**：移除所有模拟交易逻辑，只保留真实交易模式
- **影响**：
  - 简化代码逻辑，减少维护成本
  - 强制使用真实API，确保测试结果可靠
  - 需要配置完整的API凭证（private_key, api_key, api_secret, passphrase）
