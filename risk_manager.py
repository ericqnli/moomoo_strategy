# risk_manager.py v1.1
# 风控模块 - 200日 / 20日均线保护 + 仓位控制

import pandas as pd
from datetime import datetime

class RiskManager:
    def __init__(self, config):
        self.config = config
        self.max_position_pct = config.get('max_position_pct', 0.35)

    def check_ma_protection(self, df, current_price):
        """200日 / 20日均线风控（趋势过滤）"""
        if len(df) < 200:
            return False, "数据不足"

        ma20 = df['close'].rolling(window=20).mean().iloc[-1]
        ma200 = df['close'].rolling(window=200).mean().iloc[-1]

        if current_price > ma200 and ma20 > ma200:
            return True, f"MA保护通过 (价格:{current_price:.2f} > MA200, MA20>MA200)"
        else:
            return False, f"MA保护失败 (价格低于长期趋势)"

    def calculate_position_size(self, cash, price):
        """仓位控制"""
        max_shares = int(cash * self.max_position_pct / price)
        return max(0, max_shares)

    def log_risk(self, message):
        """风险日志"""
        with open(f"logs/risk_{datetime.now().strftime('%Y-%m-%d')}.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {message}\n")
        print(f"[Risk] {message}")
