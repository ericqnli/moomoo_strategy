# test_telegram.py - 测试 Telegram 通知
import yaml
from monitor import Monitor
from datetime import datetime

print(f"[{datetime.now()}] 开始测试 Telegram 配置...")

with open("config.yaml", encoding='utf-8') as f:
    config = yaml.safe_load(f)

monitor = Monitor(config)
monitor.send_telegram("🧪 这是一条测试消息 - 如果你收到，说明 Telegram 已打通！")

print("✅ 测试脚本执行完成，请检查 Telegram")