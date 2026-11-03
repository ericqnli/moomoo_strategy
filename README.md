# moomoo_strategy

**壳**：moomoo OpenAPI（Paper）  
**买卖规则**：MA5 上穿 MA10 买 1 份，下穿 MA10 卖 0.5 份，下穿 MA120 全部卖出  
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

## 调度
- 默认 `run_schedule: close`：等到美东 `16:15`（工作日）再连 OpenD、跑一轮日线信号并退出
- 周末自动顺延到下一个工作日收盘；未做美股假日早收表
- 收盘后才启动：立刻跑一轮就退
- 改回循环：`run_schedule: loop`，间隔用 `sleep_seconds`

## 通知
- `paper` / `live` 都发企业微信
- 收盘每轮一定发汇总（无交易也发）；有买卖再单发一条
- webhook 写在 `config.local.yaml` 的 `wecom_webhook_url`

## 股票池
- 固定：`config.yaml` 的 `symbols`（默认 BRK.B / SPY / QQQ）
- 自选：OpenD 拉 `watchlist_groups`（默认 `AI`、`太空`），按 `watchlist_markets` 过滤美股
- close 模式下每轮强制拉一次自选
- OpenD 失败时用本地 `watchlist_cache.json`，不写回 `config.yaml`
- 分组里删掉但仍有仓的标的会留在观察池里继续管仓

## 默认规则
信号只用已收盘日 K，同一根 K 只成交一次。一份 = `buy_amount` 买入的股数。
- MA5 上穿 MA10：买 1 份。已有仓也会再买一份
- MA5 下穿 MA10：卖 0.5 份（先进先出，不是把总仓位砍半）
- MA5 下穿 MA120：全部卖出。和短周期死叉同一天时只清仓一次
