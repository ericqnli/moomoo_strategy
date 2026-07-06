# monitor.py v1.0
# Telegram + 日志监控模块

import requests
from datetime import datetime

class Monitor:
    def __init__(self, config):
        self.telegram_bot_token = config.get('telegram_bot_token', '')
        self.telegram_chat_id = config.get('telegram_chat_id', '')

    def send_telegram(self, message):
        """发送 Telegram 消息"""
        if not (self.telegram_bot_token and self.telegram_chat_id):
            print(f"[LOG] {message}")
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            data = {"chat_id": self.telegram_chat_id, "text": f"[{datetime.now()}] {message}"}
            requests.post(url, data=data, timeout=5)
            print(f"[Telegram] {message}")
        except Exception as e:
            print(f"[Telegram 失败] {e}")

    def log(self, message):
        """普通日志"""
        print(f"[{datetime.now()}] {message}")
