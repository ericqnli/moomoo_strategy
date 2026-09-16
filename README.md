# moomoo_strategy

**版本**：v2.5  
**壳**：moomoo OpenAPI（Paper）  
**买卖规则**：国金 QMT `Macd_V1`  
**通知**：企业微信（Telegram 代码保留但默认不启动）

## 运行
```
cd ~/moomoo_strategy
git pull
cp config.local.yaml.example config.local.yaml
# 填 wecom_webhook_url
python runner_v2.4.py
```

需要本机已启动 moomoo OpenD。

## 默认规则
买：MACD 连续绿柱缩短 +（震荡 KDJ 超卖金叉 / 趋势 RSI6 拐头）  
卖：顶背离 100% / 死叉 50%
