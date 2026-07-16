# moomoo_strategy 项目

**版本**：v2.5 (Four-Element + RiskManager + Executor)  
**目标**：MACD金死叉 + RSI过滤 + 顶底背离 + 放量 + 趋势过滤 + 风控 + paper 下单账本

## 核心策略（四要素）

- **MACD** 金叉/死叉
- **RSI** 超买超卖过滤
- **顶底背离**（MACD + RSI 双波段）
- **放量确认**
- **趋势过滤**（站上20日均线）

## 风控与执行

| 能力 | 说明 |
|------|------|
| MA 保护 | 仅限制**新开仓**（价>MA200 且 MA20>MA200） |
| ATR 入场止损 | 入场价 − `stop_loss_atr_multiple` × ATR |
| 止盈 | 浮盈 ≥ `take_profit_ratio`（可 `take_profit_fraction` 分批） |
| ATR 追踪止损 | 近 `highest_window` 高点 − `atr_mult` × ATR |
| 四要素卖出 | 顶背离 + RSI 超买 + 死叉 |
| 资金 | `initial_cash`，动态仓位 `max_position_pct` |
| 持仓落盘 | `positions_file`（默认 `state/positions.json`） |
| 下单 | `Executor`：`trade_mode: paper` 模拟成交 |

退出优先级：**入场ATR止损 → 止盈 → ATR追踪止损 → 四要素卖出**

## 文件结构

- `runner_v2.4.py`：主程序入口
- `strategy_core.py`：四要素信号 + ATR 追踪
- `risk_manager.py`：MA / 仓位 / 止损止盈
- `position_store.py`：现金与持仓持久化
- `executor.py`：paper / live 下单
- `monitor.py`：Telegram + 日志
- `env_util.py`：加载 `.env`
- `config.yaml`：策略参数（无密钥）
- `.env.example`：密钥模板

## 配置密钥

```bash
cp .env.example .env
# 编辑 .env，填入:
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=...
```

也可用系统环境变量（优先级高于 `.env` 写入时的“不覆盖已有变量”逻辑）。

> 若 token 曾提交到 git，建议在 BotFather 轮换 token。

## 如何运行

```bash
cd ~/projects/moomoo_strategy
source venv/bin/activate
python runner_v2.4.py
```

## 主要 config 字段

```yaml
initial_cash: 100000
positions_file: state/positions.json
max_position_pct: 0.30
stop_loss_atr_multiple: 2.0
take_profit_ratio: 0.12
take_profit_fraction: 1.0   # 0.5 = 分批先平一半
trade_mode: paper
poll_interval_sec: 30
```
