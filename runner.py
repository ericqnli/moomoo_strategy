# runner_v2.5.py
# moomoo 壳 + 国金 QMT Macd_V1 买卖规则
# Paper 默认。持仓状态写入 positions.json。

from moomoo import *
import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import yaml

from executor import Executor
from monitor import Monitor
from risk_manager import RiskManager
from strategy_core import decide, empty_pos_state


POS_FILE = "positions.json"


def _full_code(code):
    return code if "." in code and code.split(".", 1)[0] in ("US", "HK", "SH", "SZ") else f"US.{code}"


class MoomooStrategyRunner:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}
        local_path = "config.local.yaml"
        if os.path.isfile(local_path):
            with open(local_path, encoding="utf-8") as f:
                local = yaml.safe_load(f) or {}
            self.config.update(local)

        self.quote_ctx = OpenQuoteContext()
        self.monitor = Monitor(self.config)
        self.risk = RiskManager(self.config)
        self.executor = Executor(self.config)
        self.positions = self._load_positions()
        print(f"[{datetime.now()}] === moomoo + QMT规则 v2.5 启动 ===")

    def _load_positions(self):
        data = {}
        if os.path.isfile(POS_FILE):
            try:
                with open(POS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"读取 {POS_FILE} 失败: {e}")
        for code in self.config.get("symbols", []):
            if code not in data:
                data[code] = empty_pos_state()
        return data

    def _save_positions(self):
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.positions, f, ensure_ascii=False, indent=2)

    def get_history(self, code):
        full_code = _full_code(code)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        ret, data, _ = self.quote_ctx.request_history_kline(
            full_code,
            start=start,
            end=end,
            ktype=KLType.K_DAY,
            max_count=int(self.config.get("max_count", 300)),
        )
        if ret == RET_OK and data is not None and len(data) > 0:
            df = pd.DataFrame(data)
            print(f"✅ {code} 获取 {len(df)} 条K线")
            return df
        print(f"❌ {code} K线获取失败")
        return None

    def _buy_qty(self, price):
        amount = float(self.config.get("buy_amount", 5000))
        if price <= 0:
            return 0
        qty = int(amount / price)
        return max(qty, 1) if amount > 0 else 0

    def run_cycle(self):
        for code in self.config.get("symbols", []):
            df = self.get_history(code)
            if df is None or len(df) < 60:
                continue

            st = self.positions.setdefault(code, empty_pos_state())
            result = decide(df, st, self.config)
            extra = result.get("extra") or {}
            price = extra.get("price", float(df["close"].iloc[-1]))
            action = result["action"]
            reason = result["reason"]

            self.monitor.log(
                f"{code} | 价:{price:.2f} | {action} | {reason} | "
                f"ADX={extra.get('adx')} RSI={extra.get('rsi')} 仓={st.get('vol')}"
            )

            if action == "buy":
                qty = self._buy_qty(price)
                if qty <= 0:
                    continue
                ok = self.executor.place_order(code, qty, side="BUY")
                if ok:
                    old = float(st.get("vol") or 0)
                    if old > 0:
                        st["buy_price"] = (st["buy_price"] * old + price * qty) / (old + qty)
                        st["high_since_entry"] = max(float(st.get("high_since_entry") or 0), price)
                    else:
                        st["buy_price"] = price
                        st["bars_held"] = 0
                        st["high_since_entry"] = price
                        st["half_sold"] = False
                        st["entry_atr"] = extra.get("entry_atr") or 0.0
                        st["buy_time"] = datetime.now().strftime("%Y-%m-%d")
                    st["vol"] = old + qty
                    st["buy_count"] = int(st.get("buy_count") or 0) + 1
                    self.monitor.notify(
                        f"🟢 {code} 买入 {qty} @ {price:.2f} {reason}"
                    )

            elif action == "sell" and float(st.get("vol") or 0) > 0:
                sell_vol = int(float(st["vol"]) * float(result.get("ratio") or 1.0))
                sell_vol = max(sell_vol, 1) if result.get("ratio", 0) >= 1 else sell_vol
                if sell_vol <= 0:
                    continue
                sell_vol = min(sell_vol, int(st["vol"]))
                ok = self.executor.place_order(code, sell_vol, side="SELL")
                if ok:
                    st["vol"] = float(st["vol"]) - sell_vol
                    if result.get("ratio", 1) < 1:
                        st["half_sold"] = True
                    if st["vol"] <= 0:
                        self.positions[code] = empty_pos_state()
                    self.monitor.notify(
                        f"🔴 {code} 卖出 {sell_vol} @ {price:.2f} {reason}"
                    )

            self._save_positions()

    def close(self):
        self.executor.close()
        self.quote_ctx.close()


if __name__ == "__main__":
    runner = MoomooStrategyRunner()
    try:
        while True:
            runner.run_cycle()
            time.sleep(int(runner.config.get("sleep_seconds", 30)))
    except KeyboardInterrupt:
        print("策略停止")
    finally:
        runner.close()
