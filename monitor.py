# monitor.py v1.1
# Telegram + 日志监控模块（优先环境变量 / .env，其次 config）

import os
from datetime import datetime

import requests

from env_util import env_first, load_dotenv


class Monitor:
    def __init__(self, config=None):
        config = config or {}
        load_dotenv(".env")

        # 优先级: 环境变量 > config（config 中不应再提交明文 token）
        self.telegram_bot_token = env_first(
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_TOKEN",
            default=str(config.get("telegram_bot_token") or ""),
        )
        self.telegram_chat_id = env_first(
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_CHAT",
            default=str(config.get("telegram_chat_id") or ""),
        )

        if not (self.telegram_bot_token and self.telegram_chat_id):
            print(
                "[Monitor] 未配置 Telegram（设置 .env 中 TELEGRAM_BOT_TOKEN / "
                "TELEGRAM_CHAT_ID，或环境变量）。将仅打印日志。"
            )

    def send_telegram(self, message):
        """发送 Telegram 消息；未配置时降级为日志。"""
        if not (self.telegram_bot_token and self.telegram_chat_id):
            print(f"[LOG] {message}")
            return
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            data = {
                "chat_id": self.telegram_chat_id,
                "text": f"[{datetime.now()}] {message}",
            }
            requests.post(url, data=data, timeout=5)
            print(f"[Telegram] {message}")
        except Exception as e:
            print(f"[Telegram 失败] {e}")

    def log(self, message):
        """普通日志"""
        print(f"[{datetime.now()}] {message}")
