# runner_v2.4.py
# QA Strategy v2.5 - Four-Element + RiskManager + Executor + 持久化持仓
from moomoo import OpenQuoteContext, KLType, RET_OK
import pandas as pd
import yaml
import time
import signal
from datetime import datetime, timedelta
from typing import Optional

from strategy_core import (
    get_signal,
    generate_atr_trailing_stop_signal,
    calc_atr,
)
from monitor import Monitor
from risk_manager import RiskManager
from executor import Executor
from position_store import PositionStore
from env_util import load_dotenv


class GracefulStop(Exception):
    pass


def handle_shutdown_signal(signum, _frame):
    raise GracefulStop(f"收到信号 {signum}")


class MoomooStrategyRunner:
    def __init__(self, config_path="config.yaml"):
        load_dotenv(".env")
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f) or {}

        self.quote_ctx = OpenQuoteContext()
        self.monitor = Monitor(self.config)
        self.risk = RiskManager(self.config)
        self.executor = Executor(self.config)

        initial_cash = float(self.config.get("initial_cash", 100_000))
        state_path = self.config.get("positions_file", "state/positions.json")
        self.store = PositionStore(
            path=state_path,
            symbols=self.config["symbols"],
            initial_cash=initial_cash,
        )
        self.poll_interval = int(self.config.get("poll_interval_sec", 30))
        self._closed = False

        mode = self.config.get("trade_mode", "paper")
        print(
            f"[{datetime.now()}] === QA Strategy v2.5 启动 === "
            f"mode={mode} | {self.store.summary_line()}"
        )
        self.monitor.send_telegram(
            f"✅ QA Strategy v2.5 已启动 | mode={mode} | {self.store.summary_line()}"
        )

    def get_history(self, code: str) -> Optional[pd.DataFrame]:
        full_code = f"US.{code}" if not code.startswith("US.") else code
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

        ret, data, _ = self.quote_ctx.request_history_kline(
            full_code,
            start=start,
            end=end,
            ktype=KLType.K_DAY,
            max_count=int(self.config.get("max_count", 300)),
        )
        if ret != RET_OK or data is None:
            print(f"❌ {code} K线获取失败: ret={ret}, data={data}")
            return None

        if isinstance(data, (list, pd.DataFrame, tuple)) and len(data) == 0:
            print(f"❌ {code} K线获取失败: 数据为空")
            return None

        if isinstance(data, pd.DataFrame):
            df = data
        else:
            df = pd.DataFrame(data)  # type: ignore[arg-type]

        print(f"✅ {code} 获取 {len(df)} 条K线")
        return df

    def _current_atr(self, df: pd.DataFrame) -> Optional[float]:
        atr_series = calc_atr(df, self.config.get("atr_period", 14))
        if atr_series is None or atr_series.empty:
            return None
        try:
            atr = float(atr_series.iloc[-1])
        except (TypeError, ValueError):
            return None
        if pd.isna(atr) or atr <= 0:
            return None
        return atr

    def _execute_buy(self, code: str, shares: int, price: float, reason: str) -> bool:
        order = self.executor.place_order(code, shares, side="BUY", price=price)
        if not order.get("ok"):
            self.monitor.send_telegram(
                f"⚠️ 买入失败 {code} | {order.get('message', 'unknown')}"
            )
            return False
        fill = float(order.get("price") or price)
        if not self.store.open_long(code, shares, fill):
            self.monitor.log(f"{code} 账本开仓失败（现金不足? cash={self.store.cash:.2f}）")
            return False
        msg = (
            f"🟢 买入 {code} | {shares}股 @ {fill:.2f} | "
            f"原因: {reason} | {self.store.summary_line()}"
        )
        self.monitor.send_telegram(msg)
        self.risk.log_risk(msg)
        return True

    def _execute_sell(
        self,
        code: str,
        shares: int,
        price: float,
        reason: str,
        mark_partial_tp: bool = False,
    ) -> bool:
        held = self.store.shares(code)
        qty = min(shares, held)
        if qty <= 0:
            return False
        order = self.executor.place_order(code, qty, side="SELL", price=price)
        if not order.get("ok"):
            self.monitor.send_telegram(
                f"⚠️ 卖出失败 {code} | {order.get('message', 'unknown')}"
            )
            return False
        fill = float(order.get("price") or price)
        entry = self.store.entry_price(code)
        if not self.store.reduce_long(code, qty, fill, mark_partial_tp=mark_partial_tp):
            return False
        pnl = (fill - entry) * qty if entry > 0 else 0.0
        pnl_pct = ((fill - entry) / entry * 100) if entry > 0 else 0.0
        msg = (
            f"🔴 卖出 {code} | {qty}股 @ {fill:.2f} | 原因: {reason} | "
            f"PnL: {pnl:+.2f} ({pnl_pct:+.2f}%) | {self.store.summary_line()}"
        )
        self.monitor.send_telegram(msg)
        self.risk.log_risk(msg)
        return True

    def _handle_exits(
        self,
        code: str,
        df: pd.DataFrame,
        current_price: float,
        signal_str: str,
    ) -> bool:
        """
        处理持仓退出。返回 True 表示本轮已发生卖出（可跳过开仓）。
        优先级: ATR入场止损 > 止盈 > ATR追踪止损 > 四要素卖出
        """
        held = self.store.shares(code)
        if held <= 0:
            return False

        entry = self.store.entry_price(code)
        atr = self._current_atr(df)
        atr_trail = generate_atr_trailing_stop_signal(
            df, current_price, held, self.config
        )

        # 1) 入场价 ATR 止损
        if entry > 0 and atr is not None and self.risk.check_stop_loss(entry, current_price, atr):
            stop_px = self.risk.stop_loss_price(entry, atr)
            return self._execute_sell(
                code,
                held,
                current_price,
                f"ATR止损(入场{entry:.2f}, 止损线{stop_px:.2f})",
            )

        # 2) 止盈（支持分批）
        if entry > 0 and self.risk.check_take_profit(entry, current_price):
            partial_done = self.store.partial_tp_done(code)
            qty = self.risk.take_profit_shares(held, partial_tp_done=partial_done)
            ret = self.risk.unrealized_return(entry, current_price) or 0.0
            is_partial = qty < held
            return self._execute_sell(
                code,
                qty,
                current_price,
                f"止盈({ret * 100:.1f}%, {'分批' if is_partial else '全平'})",
                mark_partial_tp=is_partial,
            )

        # 3) ATR 追踪止损
        if atr_trail == "atr_trailing_sell":
            return self._execute_sell(code, held, current_price, "ATR追踪止损")

        # 4) 四要素卖出
        if signal_str == "sell":
            return self._execute_sell(code, held, current_price, "四要素卖出")

        return False

    def run_cycle(self):
        for code in self.config["symbols"]:
            df = self.get_history(code)
            if df is None or len(df) < 200:
                continue

            current_price = float(df["close"].iloc[-1])
            held = self.store.shares(code)

            # 有持仓时始终检查退出（不因 MA 保护而跳过止损/止盈）
            signal_str = get_signal(df, current_price, 0, self.config)
            atr_signal = generate_atr_trailing_stop_signal(
                df, current_price, held, self.config
            )

            sold = False
            if held > 0:
                sold = self._handle_exits(code, df, current_price, signal_str)
                held = self.store.shares(code)

            # 新开仓：MA 门禁
            ma_ok, ma_msg = self.risk.check_ma_protection(df, current_price)
            if not ma_ok:
                self.monitor.log(f"{code} {ma_msg} | 持仓: {held}")
                if held == 0:
                    continue

            if (
                not sold
                and signal_str == "buy"
                and held == 0
                and ma_ok
            ):
                strength = int(self.config.get("signal_level", 2))
                shares = self.risk.calculate_position_size(
                    self.store.cash, current_price, strength
                )
                fallback = int(self.config.get("initial_shares", 50))
                if shares <= 0 and self.store.cash >= current_price * fallback:
                    shares = fallback
                if shares <= 0:
                    self.monitor.log(
                        f"{code} 买信号但仓位为 0（现金不足 cash={self.store.cash:.2f}）"
                    )
                else:
                    self._execute_buy(code, shares, current_price, "四要素买入")

            held = self.store.shares(code)
            entry = self.store.entry_price(code)
            entry_info = f" 入场:{entry:.2f}" if held > 0 and entry > 0 else ""
            self.monitor.log(
                f"{code} | 价: {current_price:.2f} | 信号: {signal_str} | "
                f"ATR: {atr_signal} | 持仓: {held}{entry_info} | 现金: {self.store.cash:.2f}"
            )

    def close(self):
        if self._closed:
            return
        try:
            self.store.save()
        except Exception as e:
            print(f"保存持仓失败: {e}")
        try:
            self.executor.close()
        except Exception as e:
            print(f"关闭 Executor 失败: {e}")
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
            time.sleep(runner.poll_interval if runner else 30)
    except (KeyboardInterrupt, GracefulStop):
        print("策略停止中...")
        if runner:
            runner.monitor.send_telegram(
                f"🛑 QA Strategy v2.5 已停止 | {runner.store.summary_line()}"
            )
    finally:
        if runner:
            runner.close()
