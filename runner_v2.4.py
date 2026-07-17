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
    evaluate_signal,
    generate_atr_trailing_stop_signal,
    calc_atr,
)
from monitor import Monitor
from risk_manager import RiskManager
from executor import Executor
from position_store import PositionStore
from universe import UniverseProvider, to_full_code
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
        self.universe = UniverseProvider(self.config, self.quote_ctx)

        # 启动时解析一次股票列表（fixed 即时可用；远程源可能稍后 refresh）
        bootstrap = self.universe.get_symbols(force=True)
        initial_cash = float(self.config.get("initial_cash", 100_000))
        state_path = self.config.get("positions_file", "state/positions.json")
        self.store = PositionStore(
            path=state_path,
            symbols=bootstrap,
            initial_cash=initial_cash,
        )
        self.poll_interval = int(self.config.get("poll_interval_sec", 30))
        self._closed = False

        mode = self.config.get("trade_mode", "paper")
        buy_lv = self.config.get("buy_level", 2)
        sell_lv = self.config.get("sell_level", 2)
        start_msg = (
            f"=== QA Strategy v2.6 启动 === mode={mode} "
            f"buy_level={buy_lv} sell_level={sell_lv} | "
            f"universe={self.universe.summary()} | "
            f"symbols={bootstrap} | {self.store.summary_line()}"
        )
        print(f"[{datetime.now()}] {start_msg}")
        self.monitor.log_event(start_msg)
        self.monitor.send_telegram(
            f"✅ QA Strategy v2.6 已启动 | mode={mode} | "
            f"bL={buy_lv}/sL={sell_lv} | "
            f"universe={self.universe.mode} n={len(bootstrap)} | {self.store.summary_line()}"
        )

    def get_history(self, code: str) -> Optional[pd.DataFrame]:
        u_cfg = self.config.get("universe") if isinstance(self.config.get("universe"), dict) else {}
        default_mkt = str(u_cfg.get("default_market") or "US") # type: ignore
        full_code = to_full_code(code, default_market=default_mkt)
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
            self.monitor.log_event(f"❌ {code} K线获取失败: ret={ret}, data={data}")
            return None

        if isinstance(data, (list, pd.DataFrame, tuple)) and len(data) == 0:
            self.monitor.log_event(f"❌ {code} K线获取失败: 数据为空")
            return None

        if isinstance(data, pd.DataFrame):
            df = data
        else:
            df = pd.DataFrame(data)  # type: ignore[arg-type]

        # 成功拉K：仅 detail 模式打控制台，避免 30s 刷屏
        if getattr(self.monitor, "console_mode", "signal") in ("detail", "all"):
            print(f"[{datetime.now()}] ✅ {code} 获取 {len(df)} 条K线")
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
            msg = f"⚠️ 买入失败 {code} | {order.get('message', 'unknown')}"
            self.monitor.log_event(msg)
            self.monitor.send_telegram(msg)
            return False
        fill = float(order.get("price") or price)
        if not self.store.open_long(code, shares, fill):
            self.monitor.log_event(
                f"{code} 账本开仓失败（现金不足? cash={self.store.cash:.2f}）"
            )
            return False
        msg = (
            f"🟢 买入 {code} | {shares}股 @ {fill:.2f} | "
            f"原因: {reason} | {self.store.summary_line()}"
        )
        self.monitor.log_event(msg)
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
            msg = f"⚠️ 卖出失败 {code} | {order.get('message', 'unknown')}"
            self.monitor.log_event(msg)
            self.monitor.send_telegram(msg)
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
        self.monitor.log_event(msg)
        self.monitor.send_telegram(msg)
        self.risk.log_risk(msg)
        return True

    def _risk_levels(
        self, df: pd.DataFrame, current_price: float, held: int, entry: float
    ):
        """ATR / 止损 / 追踪 / 止盈价，供 DETAIL 日志。"""
        atr = self._current_atr(df)
        stop_entry = None
        trail_stop = None
        tp_px = None
        unreal_pct = None
        if atr is not None and entry > 0:
            stop_entry = self.risk.stop_loss_price(entry, atr)
        if held > 0 and atr is not None and len(df) >= int(self.config.get("highest_window", 60)):
            recent_high = df["high"].iloc[-int(self.config.get("highest_window", 60)) :].max()
            trail_stop = float(recent_high) - float(self.config.get("atr_mult", 2.0)) * atr
        if entry > 0:
            tp_ratio = float(self.config.get("take_profit_ratio", 0.12))
            tp_px = entry * (1.0 + tp_ratio)
            unreal_pct = (current_price - entry) / entry * 100.0
        ma200 = None
        if len(df) >= 200:
            try:
                ma200 = float(df["close"].rolling(window=200).mean().iloc[-1])
            except (TypeError, ValueError):
                ma200 = None
        return atr, stop_entry, trail_stop, tp_px, unreal_pct, ma200

    def _handle_exits(
        self,
        code: str,
        df: pd.DataFrame,
        current_price: float,
        signal_str: str,
        signal_detail: str = "",
    ) -> bool:
        """
        处理持仓退出。返回 True 表示本轮已发生卖出（可跳过开仓）。
        优先级: ATR入场止损 > 止盈 > ATR追踪止损 > 信号卖出
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

        # 4) 信号卖出（死叉档位）
        if signal_str == "sell":
            reason = f"信号卖出({signal_detail})" if signal_detail else "信号卖出"
            return self._execute_sell(code, held, current_price, reason)

        return False

    def run_cycle(self):
        symbols = self.universe.get_symbols(held_codes=self.store.held_codes())
        for code in symbols:
            self.store.ensure_symbol(code)
            df = self.get_history(code)
            if df is None or len(df) < 200:
                continue

            current_price = float(df["close"].iloc[-1])
            held = self.store.shares(code)
            entry = self.store.entry_price(code)

            signal_str, signal_detail, factors = evaluate_signal(
                df, current_price, 0, self.config
            )
            atr_signal = generate_atr_trailing_stop_signal(
                df, current_price, held, self.config
            )
            atr, stop_entry, trail_stop, tp_px, unreal_pct, ma200 = self._risk_levels(
                df, current_price, held, entry
            )

            action = "hold"
            reason = signal_detail or ""

            sold = False
            if held > 0:
                sold = self._handle_exits(
                    code, df, current_price, signal_str, signal_detail
                )
                if sold:
                    action = "sell"
                    reason = signal_detail or "exit"
                held = self.store.shares(code)
                entry = self.store.entry_price(code)

            # 新开仓：MA 门禁
            ma_ok, ma_msg = self.risk.check_ma_protection(df, current_price)
            if not ma_ok and held == 0 and signal_str == "buy":
                action = "block_ma"
                reason = ma_msg
                self.monitor.log_event(f"{code} {ma_msg} | 买信号被MA门禁拦截")
            elif not ma_ok and held == 0:
                # 无信号时不刷 event，只进 detail
                pass

            if (
                not sold
                and signal_str == "buy"
                and held == 0
                and ma_ok
            ):
                strength = int(
                    self.config.get(
                        "position_level",
                        self.config.get("signal_level", 2),
                    )
                )
                shares = self.risk.calculate_position_size(
                    self.store.cash, current_price, strength
                )
                fallback = int(self.config.get("initial_shares", 50))
                if shares <= 0 and self.store.cash >= current_price * fallback:
                    shares = fallback
                if shares <= 0:
                    action = "block_cash"
                    reason = f"现金不足 cash={self.store.cash:.2f}"
                    self.monitor.log_event(
                        f"{code} 买信号但仓位为 0（{reason}）"
                    )
                else:
                    ok = self._execute_buy(
                        code,
                        shares,
                        current_price,
                        f"信号买入({signal_detail})",
                    )
                    if ok:
                        action = "buy"
                        reason = signal_detail

            held = self.store.shares(code)
            entry = self.store.entry_price(code)
            entry_info = f" 入场:{entry:.2f}" if held > 0 and entry > 0 else ""

            # 控制台精简一行（signal 模式）
            console_line = (
                f"{code} | 价: {current_price:.2f} | 信号: {signal_str} "
                f"[{signal_detail}] | ATR: {atr_signal} | "
                f"持仓: {held}{entry_info} | 现金: {self.store.cash:.2f}"
            )
            show_console = (
                self.monitor.console_mode in ("detail", "all")
                or signal_str in ("buy", "sell")
                or action in ("buy", "sell", "block_ma", "block_cash")
            )
            if show_console and self.monitor.console_mode != "quiet":
                # 仅打印，不重复写「每轮全量」到文件（detail 另写）
                print(f"[{datetime.now()}] {console_line}")

            # 因子 DETAIL：默认每天每票一次；成交/拦截 force 再写一条
            force_detail = action in ("buy", "sell", "block_ma", "block_cash")
            snap = self.monitor.format_factor_snapshot(
                code,
                current_price,
                held,
                entry,
                self.store.cash,
                factors or {},
                ma200=ma200,
                ma_gate=ma_ok,
                ma_msg=ma_msg if not ma_ok else "",
                atr=atr,
                stop_entry=stop_entry,
                trail_stop=trail_stop,
                tp_px=tp_px,
                atr_trail=str(atr_signal),
                unreal_pct=unreal_pct,
                action=action,
                reason=reason,
            )
            self.monitor.log_detail(
                code,
                snap,
                force=force_detail,
                is_none_signal=(signal_str == "none" and action == "hold"),
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
            self.universe.close()
        except Exception as e:
            print(f"关闭 Universe 失败: {e}")
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
            runner.monitor.log_event(
                f"策略停止 | {runner.store.summary_line()}"
            )
            runner.monitor.send_telegram(
                f"🛑 QA Strategy v2.6 已停止 | {runner.store.summary_line()}"
            )
    finally:
        if runner:
            runner.close()
