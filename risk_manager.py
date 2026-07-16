# risk_manager.py v2.5
# 风控模块 - MA保护 + ATR止损 + 止盈 + 动态仓位

import os
from datetime import datetime

import pandas as pd


class RiskManager:
    def __init__(self, config):
        self.config = config
        self.max_position_pct = config.get('max_position_pct', 0.30)
        self.stop_loss_atr_multiple = config.get('stop_loss_atr_multiple', 2.0)
        self.take_profit_ratio = config.get('take_profit_ratio', 0.12)
        self.take_profit_fraction = float(config.get('take_profit_fraction', 1.0))

    def check_ma_protection(self, df, current_price):
        """200日 / 20日均线风控：仅作为新开仓门禁。"""
        if len(df) < 200:
            return False, "数据不足"

        ma20 = df['close'].rolling(window=20).mean().iloc[-1]
        ma200 = df['close'].rolling(window=200).mean().iloc[-1]

        if current_price > ma200 and ma20 > ma200:
            return True, f"MA保护通过 (价格:{current_price:.2f} > MA200)"
        return False, "MA保护失败 (价格低于长期趋势)"

    def calculate_position_size(self, cash, price, signal_strength=2):
        """动态仓位控制（可根据信号强度调整）。"""
        if price <= 0 or cash <= 0:
            return 0
        base_pct = self.max_position_pct
        if signal_strength == 1:
            base_pct = min(0.40, base_pct * 1.2)
        elif signal_strength == 3:
            base_pct = base_pct * 0.7
        max_shares = int(cash * base_pct / price)
        return max(0, max_shares)

    def stop_loss_price(self, entry_price, atr):
        """入场价 - N * ATR。"""
        if entry_price is None or entry_price <= 0:
            return None
        if pd.isna(atr) or atr is None or atr <= 0:
            return None
        return entry_price - self.stop_loss_atr_multiple * float(atr)

    def check_stop_loss(self, entry_price, current_price, atr):
        """ATR 动态止损：现价跌破 入场价 - N*ATR。"""
        stop = self.stop_loss_price(entry_price, atr)
        if stop is None:
            return False
        return current_price < stop

    def unrealized_return(self, entry_price, current_price):
        if not entry_price or entry_price <= 0:
            return None
        return (current_price - entry_price) / entry_price

    def check_take_profit(self, entry_price, current_price):
        """达到 take_profit_ratio 浮盈则触发止盈。"""
        ret = self.unrealized_return(entry_price, current_price)
        if ret is None:
            return False
        return ret >= self.take_profit_ratio

    def take_profit_shares(self, shares, partial_tp_done=False):
        """
        分批止盈股数。
        take_profit_fraction=1.0 全平；0.5 且未做过部分止盈则先平一半。
        若已做过部分止盈，再次触发则全平剩余。
        """
        if shares <= 0:
            return 0
        frac = self.take_profit_fraction
        if frac >= 1.0 or partial_tp_done:
            return shares
        qty = max(1, int(shares * frac))
        return min(shares, qty)

    def log_risk(self, message):
        os.makedirs("logs", exist_ok=True)
        path = f"logs/risk_{datetime.now().strftime('%Y-%m-%d')}.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] {message}\n")
        print(f"[Risk] {message}")
