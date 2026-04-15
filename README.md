# Polymarket 自动交易系统 - V4策略

基于 Python 的 Polymarket 平台 5 分钟比特币预测自动交易系统。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API 密钥

创建 `.env` 文件，填写以下内容：

```env
PRIVATE_KEY=你的私钥
API_KEY=你的API密钥
API_SECRET=你的API密钥密文
PASSPHRASE=你的API密码
```

### 3. 运行程序

```bash
python main_v4.py
```

## V4 策略参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 买入价格 | 70% | 价格达到 70% 时买入 |
| 止损价格 | 45% | 价格跌到 45% 时止损 |
| 止盈价格 | 97% | 价格涨到 97% 时止盈 |
| 策略模式 | 连续 | 持续监控，无持仓时继续交易 |

## 项目结构

```
├── main_v4.py              # 主程序入口
├── trading_engine_v4.py    # V4交易引擎
├── polymarket_api.py       # API客户端
├── config.py               # 配置管理
├── requirements.txt        # 依赖列表
└── .env                    # API密钥配置
```

## 交易流程

1. 实时监控市场价格
2. 当价格 ≤ 70% 时买入
3. 设置止损 45%、止盈 97%
4. 监控价格触发止损或止盈
5. 卖出后计算盈亏
6. 继续下一轮交易

## 注意事项

⚠️ **风险提示**：
- 本程序为自动交易系统，存在资金损失风险
- 请确保账户有足够余额
- 建议先在测试环境验证策略

## 技术栈

- Python 3.12
- py-clob-client (Polymarket官方SDK)
- requests
- python-dotenv
