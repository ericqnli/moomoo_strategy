# moomoo_strategy 项目

**版本**：v2.4 (RiskManager版)
**目标**：ATR + MA + Volume Filter + 200/20日均线风控

## 文件结构
- runner_v2.4.py : 主程序入口
- strategy_core.py : 信号逻辑 (MACD/RSI/Volume/ATR)
- risk_manager.py : 风控 (均线保护 + 仓位)
- monitor.py : Telegram / 日志
- executor.py : 下单执行 (paper/live)
- config.yaml : 参数配置

## 如何运行
1. cd ~/moomoo_strategy
2. python runner_v2.4.py

## 配置
在 config.yaml 中设置 Telegram Token 和 Chat ID

## 版本历史
- v2.4 : 接入 RiskManager (200/20 MA保护)
- v2.3 : 独立 Monitor + Telegram
- v2.2 : 完整信号逻辑

## 注意
- 美股代码自动加 US. 前缀
- 目前为 Paper 模式 (安全)
