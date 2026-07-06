# runner_v2.4.py
# QA Strategy v2.4 - RiskManager版 (修复数据不足)

from moomoo import * # type: ignore
import pandas as pd
import yaml
import time
from datetime import datetime, timedelta
from typing import Any, Optional, cast
from strategy_core import get_signal, generate_atr_trailing_stop_signal, calc_atr
from monitor import Monitor
from risk_manager import RiskManager

class MoomooStrategyRunner:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self.quote_ctx = OpenQuoteContext()
        self.monitor = Monitor(self.config)
        self.risk = RiskManager(self.config)
        self.positions = {code: 0 for code in self.config['symbols']}
        print(f"[{datetime.now()}] === QA Strategy v2.4 (RiskManager版) 启动 ===")

    def get_history(self, code: str) -> Optional[pd.DataFrame]:
        full_code = f"US.{code}" if not code.startswith("US.") else code
        end: str = datetime.now().strftime("%Y-%m-%d")
        start: str = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")  # 增加到400天
        ret, data, _ = self.quote_ctx.request_history_kline(full_code, start=start, end=end, ktype=KLType.K_DAY, max_count=300)

        if ret != RET_OK:
            print(f"❌ {code} K线获取失败: ret={ret}")
            return None

        if isinstance(data, pd.DataFrame):
            if data.empty:
                print(f"❌ {code} K线为空 DataFrame")
                return None
            df = data.copy()
        elif isinstance(data, (list, tuple)):
            if len(data) == 0:
                print(f"❌ {code} K线获取失败: data empty")
                return None
            df = pd.DataFrame(data)
        else:
            print(f"❌ {code} K线类型不支持: {type(data).__name__}")
            return None

        print(f"✅ {code} 获取 {len(df)} 条K线")
        return df

    def run_cycle(self):
        for code in self.config['symbols']:
            df = self.get_history(code)
            if df is None or len(df) < 200:
                continue

            current_price = df['close'].iloc[-1]
            ma_ok, ma_msg = self.risk.check_ma_protection(df, current_price)
            if not ma_ok:
                self.monitor.log(f"{code} {ma_msg}")
                continue

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
            time.sleep(30)
    except KeyboardInterrupt:
        print("策略停止")
    finally:
        runner.close()
