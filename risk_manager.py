# risk_manager.py v2.5
# 风控模块 - 支持四要素策略 + ATR止损 + 动态仓位·

import pandas as pd
from datetime import datetime

class RiskManager:
    def __init__(self, config):
        self.config = config
        self.max_position_pct = config.get('max_position_pct', 0.30)

    def check_ma_protection(self, df, current_price):
        """200日 / 20日均线风控（保留原有逻辑）"""
        if len(df) < 200:
            return False, "数据不足"

        ma20 = df['close'].rolling(window=20).mean().iloc[-1]
        ma200 = df['close'].rolling(window=200).mean().iloc[-1]

        if current_price > ma200 and ma20 > ma200:
            return True, f"MA保护通过 (价格:{current_price:.2f} > MA200)"
        else:
            return False, f"MA保护失败 (价格低于长期趋势)"

    def calculate_position_size(self, cash, price, signal_strength=2):
        """动态仓位控制（可根据信号强度调整）"""
        base_pct = self.max_position_pct
        if signal_strength == 1:
            base_pct = min(0.40, base_pct * 1.2)
        elif signal_strength == 3:
            base_pct = base_pct * 0.7
        max_shares = int(cash * base_pct / price)
        return max(0, max_shares)

    def check_stop_loss(self, entry_price, current_price, atr):
        """ATR动态止损"""
        if pd.isna(atr) or atr <= 0:
            return False
        stop_price = entry_price - self.config.get('stop_loss_atr_multiple', 2.0) * atr
        return current_price < stop_price

    def log_risk(self, message):
        with open(f"logs/risk_{datetime.now().strftime('%Y-%m-%d')}.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {message}\n")
        print(f"[Risk] {message}")