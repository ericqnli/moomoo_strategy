# moomoo_strategy

**版本**：v2.5  
**壳**：moomoo OpenAPI（Paper）  
**买卖规则**：国金 QMT `Macd_V1`（绿柱缩短 + ADX 切 KDJ/RSI，顶背离全平，死叉半仓）

QMT 仓库保持独立，互不 import。

## 文件
- `runner_v2.4.py`：入口（v2.5 逻辑）
- `strategy_core.py`：QMT 买卖决策
- `strategy_core_v24_macd_flip.py`：旧版柱翻转策略备份
- `risk_manager.py`：仓位辅助
- `monitor.py`：Telegram / 日志
- `executor.py`：paper / live
- `config.yaml`：公开参数
- `config.local.yaml`：Token（不提交）

## 运行
```
cd ~/moomoo_strategy
cp config.local.yaml.example config.local.yaml   # 填 Telegram
python runner_v2.4.py
```

需要本机已启动 moomoo OpenD。

## 默认有效规则
买：MACD 连续绿柱缩短 +（震荡 KDJ 超卖金叉 / 趋势 RSI6 拐头）  
卖：顶背离 100% / 死叉 50%  
止损止盈开关默认关，与当前 QMT 一致。

## 注意
- 美股代码自动加 `US.`
- `trade_mode: paper`
- 公开仓库不要再放 Telegram token
