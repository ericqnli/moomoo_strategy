# monitor.py v1.2
# 主通道：企业微信群机器人
# Telegram 代码保留但默认不启动

import json
from datetime import datetime
from urllib.request import Request, urlopen


class Monitor:
    def __init__(self, config):
        self.trade_mode = str(config.get("trade_mode") or "paper").strip().lower()
        self.wecom_webhook_url = (config.get("wecom_webhook_url") or "").strip()
        self.enable_telegram = bool(config.get("enable_telegram", False))
        self.telegram_bot_token = (config.get("telegram_bot_token") or "").strip()
        self.telegram_chat_id = str(config.get("telegram_chat_id") or "").strip()

    def _prefix(self):
        return f"[moomoo-{self.trade_mode}]"

    def notify(self, message):
        """企业微信推送，paper / live 都发。"""
        self.log(message)
        if not self.wecom_webhook_url:
            print("[WeCom] 未配置 wecom_webhook_url，仅打印")
            return False
        text = f"{self._prefix()}\n{message}"
        if len(text.encode("utf-8")) > 2000:
            raw = text.encode("utf-8")[:2000]
            text = raw.decode("utf-8", errors="ignore") + "\n…(截断)"
        try:
            payload = json.dumps(
                {"msgtype": "text", "text": {"content": text}},
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
            print("[WeCom] 已发送")
            return True
        except Exception as e:
            print(f"[WeCom 失败] {e}")
            return False

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
