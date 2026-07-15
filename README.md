# moomoo_strategy 项目

**版本**：v2.5 (Four-Element + RiskManager版)  
**目标**：MACD金死叉 + RSI过滤 + 顶底背离 + 放量 + 趋势过滤 + 风控

## 核心策略（四要素）

- **MACD** 金叉/死叉
- **RSI** 超买超卖过滤
- **顶底背离**（MACD + RSI 双波段）
- **放量确认**
- **趋势过滤**（站上20日均线）

## 文件结构

- `runner_v2.4.py`：主程序入口（已集成 RiskManager）
- `strategy_core.py`：**核心信号逻辑**（四要素 + pt2.0 背离）
- `risk_manager.py`：风控模块（MA保护 + ATR止损 + 动态仓位）
- `monitor.py`：Telegram 通知 + 日志
- `config.yaml`：参数配置（含四要素开关）
- `executor.py`：下单执行（paper/live）

## 如何运行

```bash
cd ~/projects/moomoo_strategy
source venv/bin/activate
python runner_v2.4.py