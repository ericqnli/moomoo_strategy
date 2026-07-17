# moomoo_strategy 项目

**版本**：v2.5 (Four-Element + RiskManager + Executor)  
**目标**：MACD金死叉 + RSI过滤 + 顶底背离 + 放量 + 趋势过滤 + 风控 + paper 下单账本

## 核心策略（金/死叉扳机 + L1/L2/L3）

- **扳机（必须）**：买 = MACD 金叉；卖 = MACD 死叉
- **证据**：近窗背离 `div`、RSI、放量 `vol`、趋势 MA20 `trend`
- **档位**（`buy_level` / `sell_level` 可分开，默认 2）：
  - L1：叉 + 任一证据
  - L2：叉 + RSI 门槛 +（div|vol|trend）任一
  - L3：叉 + RSI 门槛 + 近窗背离 +（vol|trend）任一
- **背离**：近窗有效（`div_active_bars`），不要求与交叉同一根；MACD∨RSI（可 `require_both_divergence`）
- **仓位**：`position_level`（与信号档位独立）

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
| 下单 | `Executor`：`paper` 本地模拟；`live` 接 moomoo（默认 SIMULATE） |

退出优先级：**入场ATR止损 → 止盈 → ATR追踪止损 → 四要素卖出**

## 文件结构

- `runner_v2.4.py`：主程序入口
- `strategy_core.py`：四要素信号 + ATR 追踪
- `risk_manager.py`：MA / 仓位 / 止损止盈
- `position_store.py`：现金与持仓持久化
- `universe.py`：股票来源（fixed / dynamic / watchlist / account）
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

### 下单模式 `trade_mode`

| 模式 | 行为 |
|------|------|
| **paper**（默认） | 本地假成交，不动券商账户 |
| **live** | moomoo `OpenSecTradeContext.place_order` |

Live 安全层：

1. 默认 `trade_mode: paper`  
2. `live.trd_env: SIMULATE`（官方模拟盘，先跑这个）  
3. `live.allow_real: false` —— 真金须同时 `trd_env: REAL` **且** `allow_real: true`  
4. REAL 需 `.env` 中 `TRADE_UNLOCK_PWD`

```yaml
trade_mode: live
live:
  trd_env: SIMULATE      # 先模拟盘
  allow_real: false
  trd_market: US
```

真金（务必确认后）：

```yaml
trade_mode: live
live:
  trd_env: REAL
  allow_real: true
```

```bash
# .env（勿提交 git；密码留空则无法 unlock，REAL 下单会失败）
TRADE_UNLOCK_PWD=
# 或 TRADE_UNLOCK_PWD_MD5=
```

`trade_mode: paper` 或 `trd_env: SIMULATE` 时不读交易密码，**.env 里密码为空无影响**。  
仅 `REAL` 且真正下单时才需要填对密码。

OpenD 登录、交易权限、API 下单权限需已开通。Live 成交后本地 `positions.json` 仍会记账；与券商持仓对账建议人工抽查（尚未自动 sync）。

## 日志

| 文件 | 内容 |
|------|------|
| `logs/runner_YYYY-MM-DD.log` | `[DETAIL]` 四要素/趋势/ATR 快照；`[EVENT]` 成交与异常 |
| `logs/risk_YYYY-MM-DD.log` | 成交风控记录（保留） |
| `logs/.detail_stamp.json` | daily/interval 去重戳 |

```yaml
logging:
  detail_mode: daily    # 默认：每票每个自然日 1 条 DETAIL（股票多也可）
  # detail_mode: interval / every_cycle / off
  always_log_events: true
  console: signal       # 终端少刷；detail 看文件
```

成交与止损等 **不受 daily 限制**，会即时写 `[EVENT]`。策略轮询仍是 `poll_interval_sec`（可盘中买卖）。

## 回测 `backtester.py`

日线回测，复用与实盘相同的 `evaluate_signal` + 风控；**收盘价成交**（非 30s 盘中）。

| 项 | 说明 |
|----|------|
| 区间 | `backtest.start` / `end`，可 CLI 覆盖 |
| 股票池 | `backtest.symbols` 自定义 |
| 手续费 | 买卖各边 `fee_rate: 0.0001`（万分之一） |
| 基准 | `benchmark: ".SPX"`（S&P 500，接口 `US..SPX`） |

```bash
# 需 OpenD
python backtester.py
python backtester.py --start 2022-01-01 --end 2025-06-30 --symbols SPY,QQQ
```

输出：`logs/backtest_equity_*.csv`、`logs/backtest_trades_*.csv`，终端打印收益/回撤/相对 .SPX 超额。

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

## 股票来源 `universe.mode`

| mode | 说明 | 依赖 |
|------|------|------|
| `fixed` | 固定列表 `universe.symbols`（默认） | 无 |
| `dynamic` | 基本面条件选股（市值/PE/行业等，**无技术信号**） | OpenD 行情 |
| `watchlist` | 账号自选股分组 `get_user_security` | OpenD 行情 + 登录 |
| `account` | 账号持仓 `position_list_query` | OpenD 交易 + 登录 |

切换示例：把 `config.yaml` 里 `universe.mode` 改成 `watchlist` / `account` / `dynamic` 即可。  
`include_held: true` 时会始终并入本地已有持仓，避免池子变化后漏掉止损/止盈。

### `dynamic`：基本面池（与技术信号分层）

| 层 | 职责 | 示例 |
|----|------|------|
| **universe.dynamic** | 基本面候选池 | 市值、PE_TTM、PB、行业 plate、最低价 |
| **strategy_core** | 技术买卖信号 | MACD / RSI / 背离 / 放量 / 趋势 |

当前默认粗筛：`CUR_PRICE 10~1000` + `MARKET_VAL ≥ 约100亿` + `PE_TTM 5~35`，按市值降序取前 40。

```yaml
universe:
  mode: dynamic
  refresh_sec: 86400   # 基本面建议日更
  dynamic:
    market: US
    plate_code: null   # 或行业板块代码；多行业用 plate_codes
    num: 40
    filters:
      - { type: simple, field: CUR_PRICE, min: 10, max: 1000 }
      - { type: simple, field: MARKET_VAL, min: 10000000000, sort: DESCEND }
      - { type: simple, field: PE_TTM, min: 5, max: 35 }
```

- `type: simple`：报价/估值（`MARKET_VAL` `PE_TTM` `PB_RATE` …）
- `type: financial`：财报字段，需 `quarter`（如 `ANNUAL`）
- 市值等字段**单位以 OpenD 实测为准**，上线前请校准