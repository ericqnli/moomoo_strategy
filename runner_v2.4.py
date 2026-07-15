# runner_v2.4.py
# QA Strategy v2.5 - Four-Element + RiskManager 版
from moomoo import OpenQuoteContext, KLType, RET_OK
import pandas as pd
import yaml
import time
import signal
from datetime import datetime, timedelta
from typing import Optional
from strategy_core import get_signal, generate_atr_trailing_stop_signal
from monitor import Monitor
from risk_manager import RiskManager

class GracefulStop(Exception):
    pass

def handle_shutdown_signal(signum, _frame):
    raise GracefulStop(f"收到信号 {signum}")

class MoomooStrategyRunner:
    def __init__(self, config_path="config.yaml"):
        with open(config_path, encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        self.quote_ctx = OpenQuoteContext()
        self.monitor = Monitor(self.config)
        self.risk = RiskManager(self.config)
        self.positions = {code: 0 for code in self.config['symbols']}
        self._closed = False

        print(f"[{datetime.now()}] === QA Strategy v2.5 (Four-Element + RiskManager) 启动 ===")
        self.monitor.send_telegram("✅ QA Strategy v2.5 (Four-Element) 已启动")

    def get_history(self, code: str) -> Optional[pd.DataFrame]:
        full_code = f"US.{code}" if not code.startswith("US.") else code
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        
        ret, data, _ = self.quote_ctx.request_history_kline(
            full_code, start=start, end=end, 
            ktype=KLType.K_DAY, max_count=300
        )
        
        if ret != RET_OK or data is None or len(data) == 0:
            print(f"❌ {code} K线获取失败: ret={ret}, data={data}")
            return None
        
        df = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
        print(f"✅ {code} 获取 {len(df)} 条K线")
        return df

    def run_cycle(self):
        for code in self.config['symbols']:
            df = self.get_history(code)
            if df is None or len(df) < 200:
                continue

            current_price = float(df['close'].iloc[-1])
            
            # 风控检查
            ma_ok, ma_msg = self.risk.check_ma_protection(df, current_price)
            if not ma_ok:
                self.monitor.log(f"{code} {ma_msg}")
                continue

            # 四要素信号
            signal_str = get_signal(df, current_price, 0, self.config)
            atr_signal = generate_atr_trailing_stop_signal(
                df, current_price, self.positions[code], self.config
            )

            if signal_str == 'buy' and self.positions[code] == 0:
                shares = self.risk.calculate_position_size(100000, current_price, 2)  # 可调整资金
                msg = f"🟢 买入 {code} | 价格: {current_price:.2f} | 信号: 四要素买入"
                self.monitor.send_telegram(msg)
                self.positions[code] = shares or 50

            elif signal_str == 'sell' or atr_signal == 'atr_trailing_sell':
                if self.positions[code] > 0:
                    signal_type = '四要素卖出' if signal_str == 'sell' else 'ATR止损'
                    msg = f"🔴 卖出 {code} | 价格: {current_price:.2f} | 信号: {signal_type}"
                    self.monitor.send_telegram(msg)
                    self.positions[code] = 0

            self.monitor.log(f"{code} | 价: {current_price:.2f} | 信号: {signal_str} | ATR: {atr_signal} | 持仓: {self.positions[code]}")

    def close(self):
        if self._closed:
            return
        try:
            self.quote_ctx.close()
        except Exception as e:
            print(f"关闭 QuoteContext 失败: {e}")
        finally:
            self._closed = True

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    
    runner: Optional[MoomooStrategyRunner] = None
    try:
        runner = MoomooStrategyRunner()
        while True:
            runner.run_cycle()
            time.sleep(30)
    except (KeyboardInterrupt, GracefulStop):
        print("策略停止中...")
        if runner:
            runner.monitor.send_telegram("🛑 QA Strategy v2.5 已停止")
    finally:
        if runner:
            runner.close()