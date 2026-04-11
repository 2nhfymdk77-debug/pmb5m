# Polymarket 自动交易系统 - Windows桌面版

## 功能特点

- **统一交易引擎**：整合V1/V2/V3/V4所有功能，通过参数配置切换
- **Excel风格界面**：简洁直观的表格化操作界面
- **实时状态监控**：价格、余额、盈亏一目了然
- **交易历史记录**：自动记录每笔交易

## 安装

### 1. 安装Python
下载并安装 Python 3.10+ : https://www.python.org/downloads/

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

## 运行

### 方式一：直接运行Python脚本
```bash
python app_gui.py
```

### 方式二：打包为EXE文件
```bash
# 安装打包工具
pip install pyinstaller

# 打包
pyinstaller build.spec

# 运行生成的EXE
dist/PolymarketTrader.exe
```

## 界面说明

### 策略配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 策略模式 | CYCLE=周期模式, SINGLE=每周期一次, CONTINUOUS=连续 | CONTINUOUS |
| 买入价格 | 价格达到此值时买入 | 70% |
| 止损价格 | 价格跌到此值时止损卖出 | 45% |
| 止盈价格 | 价格涨到此值时止盈卖出 | 95% |
| 买入限制 | 价格超过此值不买入，0=不限制 | 85% |
| 最后1分钟止损 | 最后1分钟止损价，0=使用固定止损 | 0 |
| 最后1分钟止盈 | 最后1分钟止盈价，0=不止盈 | 0 |

### 策略模式对比

| 模式 | 说明 | 对应版本 |
|------|------|---------|
| CYCLE | 每5分钟一个完整周期，双向挂单 | V1 |
| SINGLE | 每周期最多交易一次 | V2 |
| CONTINUOUS | 无持仓时可继续交易 | V3/V4 |

## API配置

在用户目录创建 `.polymarket-trader/config.json`:

```json
{
  "private_key": "你的私钥",
  "api_key": "你的API Key",
  "api_secret": "你的API Secret",
  "passphrase": "你的Passphrase"
}
```

或在项目目录创建 `.env` 文件:

```
PRIVATE_KEY=你的私钥
API_KEY=你的API Key
API_SECRET=你的API Secret
PASSPHRASE=你的Passphrase
```

## 项目文件

```
polymarket-trader/
├── app_gui.py                  # GUI主程序
├── trading_engine_unified.py   # 统一交易引擎
├── trading_engine.py           # V1交易引擎
├── trading_engine_v2.py        # V2交易引擎
├── trading_engine_v3.py        # V3交易引擎
├── trading_engine_v4.py        # V4交易引擎
├── polymarket_api.py           # API客户端
├── config.py                   # 配置管理
├── requirements.txt            # 依赖列表
├── build.spec                  # 打包配置
└── README_GUI.md               # 本文档
```

## 注意事项

1. 本程序需要真实的API凭证才能运行
2. 请确保账户有足够的余额
3. 建议先在测试环境验证策略
4. 交易有风险，投资需谨慎
