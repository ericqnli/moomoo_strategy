# monitor.py v1.1
# 主通道：企业微信群机器人
# Telegram 代码保留但默认不启动

import json
from datetime import datetime
from urllib.request import Request, urlopen


class Monitor:
    def __init__(self, config):
        self.wecom_webhook_url = (config.get("wecom_webhook_url") or "").strip()
        self.enable_telegram = bool(config.get("enable_telegram", False))
        self.telegram_bot_token = (config.get("telegram_bot_token") or "").strip()
        self.telegram_chat_id = str(config.get("telegram_chat_id") or "").strip()

    def notify(self, message):
        """买卖信号通知：只走企业微信。"""
        self.log(message)
        if not self.wecom_webhook_url:
            print("[WeCom] 未配置 wecom_webhook_url，仅打印")
            return
        try:
            payload = json.dumps(
                {"msgtype": "text", "text": {"content": f"[moomoo-paper]\n{message}"}},
                ensure_ascii=False,
            ).encode("utf-8")
            req = Request(
                self.wecom_webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            if not isinstance(body, dict) or body.get("errcode") != 0:
                raise RuntimeError(body)
            print(f"[WeCom] 已发送")
        except Exception as e:
            print(f"[WeCom 失败] {e}")

    def send_telegram(self, message):
        """备份通道，默认不调用。enable_telegram=true 才发。"""
        if not self.enable_telegram:
            return
        if not (self.telegram_bot_token and self.telegram_chat_id):
            return
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            data = {"chat_id": self.telegram_chat_id, "text": f"[{datetime.now()}] {message}"}
            requests.post(url, data=data, timeout=5)
        except Exception as e:
            print(f"[Telegram 失败] {e}")

    def log(self, message):
        print(f"[{datetime.now()}] {message}")
