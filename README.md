# moomoo_strategy

**版本**：v2.6  
**壳**：moomoo OpenAPI（Paper）  
**买卖规则**：国金 QMT `Macd_V1`  
**通知**：企业微信（Telegram 代码保留但默认不启动）

## 运行
```
cd ~/moomoo_strategy
git pull
cp config.local.yaml.example config.local.yaml
# 填 wecom_webhook_url
python runner.py
```

需要本机已启动 moomoo OpenD。

## 股票池
- 固定：`config.yaml` 的 `symbols`（默认 BRK.B / SPY / QQQ）
- 自选：OpenD 拉 `watchlist_groups`（默认 `AI`、`太空`），按 `watchlist_markets` 过滤美股
- 启动拉一次，之后每 `watchlist_refresh_seconds` 秒刷新（默认 900 = 15 分钟）
- OpenD 失败时用本地 `watchlist_cache.json`，不写回 `config.yaml`
- 分组里删掉但仍有仓的标的会留在观察池里继续管仓

## 默认规则
买：MACD 连续绿柱缩短 +（震荡 KDJ 超卖金叉 / 趋势 RSI6 拐头）  
卖：顶背离 100% / 死叉 50%
