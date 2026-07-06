# runner_v2.3.py
# QA Strategy v2.3 - 使用独立 Monitor 模块 + Telegram

from moomoo import *
import pandas as pd
import yaml
import time
from datetime import datetime, timedelta
from strategy_core import get_signal, generate_atr_trailing_stop_signal, calc_atr
from monitor import Monitor

class MoomooStrategyRunner:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self.quote_ctx = OpenQuoteContext()
        self.monitor = Monitor(self.config)
        self.positions = {code: 0 for code in self.config['symbols']}
        print(f"[{datetime.now()}] === QA Strategy v2.3 (Monitor版) 启动 ===")
        print(f"品种: {self.config['symbols']} | 模式: {self.config.get('trade_mode', 'paper')}")

    def get_history(self, code):
        full_code = f"US.{code}" if not code.startswith("US.") else code
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        ret, data, _ = self.quote_ctx.request_history_kline(full_code, start=start, end=end, ktype=KLType.K_DAY, max_count=200)
        return pd.DataFrame(data) if ret == RET_OK else None

    def run_cycle(self):
        for code in self.config['symbols']:
            df = self.get_history(code)
            if df is None or len(df) < 100:
                continue
            current_price = df['close'].iloc[-1]
            signal = get_signal(df, current_price, 0, self.config)
            atr_signal = generate_atr_trailing_stop_signal(df, current_price, self.positions[code], self.config)

            if signal == 'buy' and self.positions[code] == 0:
                msg = f"🟢 {code} 买入信号！价格: {current_price:.2f}"
                self.monitor.send_telegram(msg)
                self.positions[code] = 50

            elif signal == 'sell' or atr_signal == 'atr_trailing_sell':
                if self.positions[code] > 0:
                    msg = f"🔴 {code} 卖出/止损！价格: {current_price:.2f}"
                    self.monitor.send_telegram(msg)
                    self.positions[code] = 0

            self.monitor.log(f"{code} | 价: {current_price:.2f} | 信号: {signal} | ATR: {atr_signal} | 持仓: {self.positions[code]}")

    def close(self):
        self.quote_ctx.close()

if __name__ == "__main__":
    runner = MoomooStrategyRunner()
    try:
        while True:
            runner.run_cycle()
            print("=== 一轮扫描结束，等待 30 秒 ===\n")
            time.sleep(30)
    except KeyboardInterrupt:
        print("策略停止")
    finally:
        runner.close()
