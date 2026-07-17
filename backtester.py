# backtester.py
# 日线回测：复用 strategy_core + risk_manager
# - 区间 / 股票池可配置
# - 买卖手续费默认万分之一 (0.0001)
# - 基准默认 .SPX（S&P 500 指数，行情代码 US..SPX）
#
# 用法:
#   python backtester.py
#   python backtester.py --start 2022-01-01 --end 2025-12-31 --symbols SPY,QQQ,BRK.B
#   python backtester.py --config config.yaml

from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

from moomoo import KLType, OpenQuoteContext, RET_OK

from risk_manager import RiskManager
from strategy_core import calc_atr, evaluate_signal, generate_atr_trailing_stop_signal


# moomoo 指数常见代码
_BENCHMARK_QUOTE = {
    ".SPX": "US..SPX",
    "SPX": "US..SPX",
    "US..SPX": "US..SPX",
    ".IXIC": "US..IXIC",
    "IXIC": "US..IXIC",
}


def to_quote_code(code: str, default_market: str = "US") -> str:
    s = str(code).strip()
    if not s:
        return s
    key = s.upper() if not s.startswith(".") else s
    if s in _BENCHMARK_QUOTE:
        return _BENCHMARK_QUOTE[s]
    if key in _BENCHMARK_QUOTE:
        return _BENCHMARK_QUOTE[key]
    if s.startswith(".") and s.upper() in {".SPX", ".IXIC", ".DJI"}:
        return _BENCHMARK_QUOTE.get(s.upper(), f"US.{s}")
    upper = s.upper()
    if upper.startswith("US.."):
        return upper
    if "." in upper and upper.split(".", 1)[0] in {
        "US", "HK", "SH", "SZ", "SG", "JP", "AU", "CA",
    }:
        return upper
    # BRK.B 等
    bare = upper
    if upper.startswith("US."):
        bare = upper[3:]
    return f"{default_market.upper()}.{bare}"


def normalize_symbol(code: str) -> str:
    """策略内部标的名（与 runner 一致倾向裸代码）。"""
    s = str(code).strip()
    if s in (".SPX", "US..SPX", "SPX"):
        return ".SPX"
    upper = s.upper()
    if upper.startswith("US.."):
        return "." + upper.split("..", 1)[-1]
    if upper.startswith("US."):
        return upper[3:]
    return s


@dataclass
class Position:
    shares: int = 0
    entry_price: float = 0.0
    entry_date: str = ""
    partial_tp_done: bool = False


@dataclass
class Trade:
    date: str
    code: str
    side: str
    shares: int
    price: float
    fee: float
    reason: str
    cash_after: float


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame
    trades: List[Trade]
    stats: Dict[str, Any]
    config_snapshot: Dict[str, Any] = field(default_factory=dict)


class Backtester:
    def __init__(self, config: Dict[str, Any], quote_ctx: Optional[OpenQuoteContext] = None):
        self.config = config or {}
        bt = self.config.get("backtest") if isinstance(self.config.get("backtest"), dict) else {}
        self.bt = bt

        self.start = str(bt.get("start") or "2023-01-01")
        self.end = str(bt.get("end") or datetime.now().strftime("%Y-%m-%d"))
        self.symbols = [
            normalize_symbol(s)
            for s in (bt.get("symbols") or self._fallback_symbols())
            if str(s).strip()
        ]
        self.fee_rate = float(bt.get("fee_rate", 0.0001))  # 万分之一
        self.benchmark = str(bt.get("benchmark") or ".SPX")
        self.initial_cash = float(
            bt.get("initial_cash", self.config.get("initial_cash", 100_000))
        )
        self.warmup_days = int(bt.get("warmup_calendar_days", 450))
        self.default_market = str(
            (self.config.get("universe") or {}).get("default_market")
            if isinstance(self.config.get("universe"), dict)
            else "US"
        ) or "US"

        self.risk = RiskManager(self.config)
        self._owns_ctx = quote_ctx is None
        self.quote_ctx = quote_ctx or OpenQuoteContext()

        self.cash = self.initial_cash
        self.positions: Dict[str, Position] = {c: Position() for c in self.symbols}
        self.trades: List[Trade] = []
        self._data: Dict[str, pd.DataFrame] = {}
        self._bench: Optional[pd.DataFrame] = None

    def _fallback_symbols(self) -> List[str]:
        u = self.config.get("universe")
        if isinstance(u, dict) and u.get("symbols"):
            return list(u["symbols"])
        return list(self.config.get("symbols") or ["SPY", "QQQ"])

    def close(self) -> None:
        if self._owns_ctx and self.quote_ctx is not None:
            try:
                self.quote_ctx.close()
            except Exception:
                pass

    # ---------- data ----------

    def _fetch_kline(self, code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        full = to_quote_code(code, self.default_market)
        max_count = int(self.bt.get("max_count") or self.config.get("max_count") or 1000)
        frames: List[pd.DataFrame] = []
        page_key = None
        for _ in range(50):  # 分页上限
            kwargs = dict(
                code=full,
                start=start,
                end=end,
                ktype=KLType.K_DAY,
                max_count=max_count,
            )
            # 兼容不同 SDK 签名
            try:
                if page_key is None:
                    ret, data, page_key = self.quote_ctx.request_history_kline(**kwargs)
                else:
                    ret, data, page_key = self.quote_ctx.request_history_kline(
                        **kwargs, page_req_key=page_key
                    )
            except TypeError:
                ret, data, page_key = self.quote_ctx.request_history_kline(
                    full, start=start, end=end, ktype=KLType.K_DAY, max_count=max_count
                )
                page_key = None

            if ret != RET_OK or data is None:
                if not frames:
                    print(f"[Backtest] K线失败 {code} ({full}): {data}")
                    return None
                break
            if isinstance(data, pd.DataFrame):
                part = data.copy()
            else:
                part = pd.DataFrame(data)
            if part.empty:
                break
            frames.append(part)
            if not page_key:
                break

        if not frames:
            print(f"[Backtest] K线为空 {code} ({full})")
            return None
        df = pd.concat(frames, ignore_index=True)
        # 统一日期列
        if "time_key" in df.columns:
            df["date"] = pd.to_datetime(df["time_key"]).dt.strftime("%Y-%m-%d")
        elif "time" in df.columns:
            df["date"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d")
        else:
            df = df.reset_index()
            for col in ("time_key", "time", "date"):
                if col in df.columns:
                    df["date"] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
                    break
        if "date" not in df.columns:
            print(f"[Backtest] 无法解析日期列 {code}: cols={list(df.columns)}")
            return None
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                print(f"[Backtest] 缺列 {col} @ {code}")
                return None
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("date").drop_duplicates("date")
        df = df.reset_index(drop=True)
        print(
            f"[Backtest] {code} 加载 {len(df)} 根日K "
            f"({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})"
        )
        return df

    def load_data(self) -> None:
        start_dt = datetime.strptime(self.start, "%Y-%m-%d") - timedelta(days=self.warmup_days)
        fetch_start = start_dt.strftime("%Y-%m-%d")
        fetch_end = self.end

        self._data = {}
        for sym in self.symbols:
            df = self._fetch_kline(sym, fetch_start, fetch_end)
            if df is not None and len(df) > 0:
                self._data[sym] = df
            else:
                print(f"[Backtest] 警告: 跳过无数据标的 {sym}")

        if not self._data:
            raise RuntimeError("没有任何标的K线，请检查 OpenD / 股票代码 / 日期")

        bench_code = self.benchmark
        self._bench = self._fetch_kline(bench_code, fetch_start, fetch_end)
        if self._bench is None:
            print(f"[Backtest] 警告: 基准 {bench_code} 加载失败，将跳过相对基准对比")

    # ---------- portfolio helpers ----------

    def _fee(self, notional: float) -> float:
        return abs(notional) * self.fee_rate

    def _buy(self, date: str, code: str, shares: int, price: float, reason: str) -> bool:
        if shares <= 0 or price <= 0:
            return False
        notional = shares * price
        fee = self._fee(notional)
        cost = notional + fee
        if cost > self.cash + 1e-9:
            return False
        self.cash -= cost
        self.positions[code] = Position(
            shares=shares,
            entry_price=price,
            entry_date=date,
            partial_tp_done=False,
        )
        self.trades.append(
            Trade(date, code, "BUY", shares, price, fee, reason, self.cash)
        )
        return True

    def _sell(
        self,
        date: str,
        code: str,
        shares: int,
        price: float,
        reason: str,
        mark_partial_tp: bool = False,
    ) -> bool:
        pos = self.positions.get(code) or Position()
        held = pos.shares
        qty = min(shares, held)
        if qty <= 0 or price <= 0:
            return False
        notional = qty * price
        fee = self._fee(notional)
        self.cash += notional - fee
        remain = held - qty
        if remain <= 0:
            self.positions[code] = Position()
        else:
            self.positions[code] = Position(
                shares=remain,
                entry_price=pos.entry_price,
                entry_date=pos.entry_date,
                partial_tp_done=True if mark_partial_tp else pos.partial_tp_done,
            )
        self.trades.append(
            Trade(date, code, "SELL", qty, price, fee, reason, self.cash)
        )
        return True

    def _equity(self, date: str, px_map: Dict[str, float]) -> float:
        eq = self.cash
        for code, pos in self.positions.items():
            if pos.shares > 0:
                px = px_map.get(code)
                if px is None:
                    # 停牌等：用入场价兜底
                    px = pos.entry_price
                eq += pos.shares * px
        return eq

    def _slice_until(self, df: pd.DataFrame, date: str) -> Optional[pd.DataFrame]:
        sub = df[df["date"] <= date]
        if len(sub) < 200:
            return None
        return sub.reset_index(drop=True)

    def _price_on(self, df: pd.DataFrame, date: str) -> Optional[float]:
        row = df[df["date"] == date]
        if row.empty:
            return None
        return float(row["close"].iloc[-1])

    # ---------- main loop ----------

    def run(self) -> BacktestResult:
        self.load_data()
        self.cash = self.initial_cash
        self.positions = {c: Position() for c in self._data.keys()}
        self.trades = []

        # 交易日历：所有标的日期的并集，落在 [start, end]
        all_dates = set()
        for df in self._data.values():
            all_dates.update(df["date"].tolist())
        dates = sorted(d for d in all_dates if self.start <= d <= self.end)
        if not dates:
            raise RuntimeError(f"区间 {self.start}~{self.end} 无交易日数据")

        equity_rows = []
        strength = int(
            self.config.get("position_level", self.config.get("signal_level", 2))
        )
        fallback_shares = int(self.config.get("initial_shares", 50))

        for date in dates:
            px_map: Dict[str, float] = {}
            for code, df in self._data.items():
                px = self._price_on(df, date)
                if px is not None:
                    px_map[code] = px

            # 先处理持仓退出，再新开仓（与 runner 一致：逐标的）
            for code, df_full in self._data.items():
                if code not in px_map:
                    continue
                df = self._slice_until(df_full, date)
                if df is None:
                    continue
                price = float(df["close"].iloc[-1])
                pos = self.positions[code]
                held = pos.shares

                signal, detail, _fac = evaluate_signal(df, price, 0, self.config)
                atr_series = calc_atr(df, self.config.get("atr_period", 14))
                atr = None
                if atr_series is not None and not atr_series.empty:
                    try:
                        atr = float(atr_series.iloc[-1])
                        if pd.isna(atr) or atr <= 0:
                            atr = None
                    except (TypeError, ValueError):
                        atr = None

                atr_trail = generate_atr_trailing_stop_signal(
                    df, price, held, self.config
                )

                sold = False
                if held > 0:
                    entry = pos.entry_price
                    # 1) ATR 止损
                    if (
                        entry > 0
                        and atr is not None
                        and self.risk.check_stop_loss(entry, price, atr)
                    ):
                        stop_px = self.risk.stop_loss_price(entry, atr)
                        self._sell(
                            date,
                            code,
                            held,
                            price,
                            f"ATR止损(入场{entry:.2f},线{stop_px:.2f})",
                        )
                        sold = True
                    # 2) 止盈
                    elif entry > 0 and self.risk.check_take_profit(entry, price):
                        qty = self.risk.take_profit_shares(
                            held, partial_tp_done=pos.partial_tp_done
                        )
                        is_partial = qty < held
                        ret = self.risk.unrealized_return(entry, price) or 0.0
                        self._sell(
                            date,
                            code,
                            qty,
                            price,
                            f"止盈({ret*100:.1f}%)",
                            mark_partial_tp=is_partial,
                        )
                        sold = True
                    # 3) 追踪
                    elif atr_trail == "atr_trailing_sell":
                        self._sell(date, code, held, price, "ATR追踪止损")
                        sold = True
                    # 4) 信号卖
                    elif signal == "sell":
                        self._sell(date, code, held, price, f"信号卖({detail})")
                        sold = True

                held = self.positions[code].shares
                if sold or held > 0:
                    continue

                # 新开仓
                if signal != "buy":
                    continue
                ma_ok, _ma_msg = self.risk.check_ma_protection(df, price)
                if not ma_ok:
                    continue

                shares = self.risk.calculate_position_size(self.cash, price, strength)
                # 费用预留
                if shares > 0:
                    while shares > 0 and shares * price * (1 + self.fee_rate) > self.cash + 1e-9:
                        shares -= 1
                if shares <= 0 and self.cash >= price * fallback_shares * (1 + self.fee_rate):
                    shares = fallback_shares
                    while shares > 0 and shares * price * (1 + self.fee_rate) > self.cash + 1e-9:
                        shares -= 1
                if shares > 0:
                    self._buy(date, code, shares, price, f"信号买({detail})")

            eq = self._equity(date, px_map)
            bench_px = None
            if self._bench is not None:
                b = self._price_on(self._bench, date)
                bench_px = b
            equity_rows.append(
                {
                    "date": date,
                    "equity": eq,
                    "cash": self.cash,
                    "benchmark_px": bench_px,
                }
            )

        eq_df = pd.DataFrame(equity_rows)
        stats = self._compute_stats(eq_df)
        return BacktestResult(
            equity_curve=eq_df,
            trades=self.trades,
            stats=stats,
            config_snapshot={
                "start": self.start,
                "end": self.end,
                "symbols": list(self._data.keys()),
                "fee_rate": self.fee_rate,
                "benchmark": self.benchmark,
                "initial_cash": self.initial_cash,
                "buy_level": self.config.get("buy_level"),
                "sell_level": self.config.get("sell_level"),
            },
        )

    def _compute_stats(self, eq_df: pd.DataFrame) -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        if eq_df.empty:
            return stats

        eq0 = float(eq_df["equity"].iloc[0])
        eq1 = float(eq_df["equity"].iloc[-1])
        stats["start_equity"] = eq0
        stats["end_equity"] = eq1
        stats["total_return"] = (eq1 / eq0 - 1.0) if eq0 > 0 else 0.0

        # 年化（按交易日）
        n = max(len(eq_df) - 1, 1)
        years = n / 252.0
        if years > 0 and eq0 > 0 and eq1 > 0:
            stats["ann_return"] = (eq1 / eq0) ** (1.0 / years) - 1.0
        else:
            stats["ann_return"] = 0.0

        # 最大回撤
        peak = eq_df["equity"].cummax()
        dd = eq_df["equity"] / peak - 1.0
        stats["max_drawdown"] = float(dd.min()) if len(dd) else 0.0

        # 基准
        if "benchmark_px" in eq_df.columns and eq_df["benchmark_px"].notna().any():
            b = eq_df.dropna(subset=["benchmark_px"])
            if len(b) >= 2:
                b0 = float(b["benchmark_px"].iloc[0])
                b1 = float(b["benchmark_px"].iloc[-1])
                stats["benchmark_return"] = (b1 / b0 - 1.0) if b0 > 0 else 0.0
                stats["excess_return"] = stats["total_return"] - stats["benchmark_return"]
            else:
                stats["benchmark_return"] = None
                stats["excess_return"] = None
        else:
            stats["benchmark_return"] = None
            stats["excess_return"] = None

        # 交易统计（完整 round-trip 简化：按卖出计）
        sells = [t for t in self.trades if t.side == "SELL"]
        buys = [t for t in self.trades if t.side == "BUY"]
        stats["n_buys"] = len(buys)
        stats["n_sells"] = len(sells)
        stats["n_trades"] = len(self.trades)
        total_fees = sum(t.fee for t in self.trades)
        stats["total_fees"] = total_fees

        # 粗算胜率：配对 FIFO
        wins = 0
        losses = 0
        lots: Dict[str, List[Tuple[int, float]]] = {}
        for t in self.trades:
            if t.side == "BUY":
                lots.setdefault(t.code, []).append((t.shares, t.price))
            else:
                remain = t.shares
                queue = lots.get(t.code, [])
                while remain > 0 and queue:
                    sh, ep = queue[0]
                    use = min(sh, remain)
                    pnl = (t.price - ep) * use
                    # 粗略：忽略费用拆分
                    if pnl >= 0:
                        wins += 1
                    else:
                        losses += 1
                    if use >= sh:
                        queue.pop(0)
                    else:
                        queue[0] = (sh - use, ep)
                    remain -= use
                lots[t.code] = queue
        closed = wins + losses
        stats["win_rate"] = (wins / closed) if closed else None
        stats["fee_rate"] = self.fee_rate
        return stats


def print_report(result: BacktestResult) -> None:
    s = result.stats
    snap = result.config_snapshot
    print("\n========== 回测报告 ==========")
    print(f"区间: {snap.get('start')} ~ {snap.get('end')}")
    print(f"股票池: {snap.get('symbols')}")
    print(f"手续费: {snap.get('fee_rate')} (买卖各边)")
    print(f"基准: {snap.get('benchmark')}")
    print(f"初始资金: {snap.get('initial_cash')}")
    print(f"buy_level/sell_level: {snap.get('buy_level')}/{snap.get('sell_level')}")
    print("---------- 绩效 ----------")
    print(f"期末权益: {s.get('end_equity', 0):.2f}")
    print(f"总收益率: {100 * (s.get('total_return') or 0):.2f}%")
    print(f"年化收益: {100 * (s.get('ann_return') or 0):.2f}%")
    print(f"最大回撤: {100 * (s.get('max_drawdown') or 0):.2f}%")
    br = s.get("benchmark_return")
    if br is not None:
        print(f"基准收益(.SPX): {100 * br:.2f}%")
        print(f"超额收益: {100 * (s.get('excess_return') or 0):.2f}%")
    else:
        print("基准收益: N/A（未加载）")
    print(f"买入/卖出次数: {s.get('n_buys')}/{s.get('n_sells')}")
    wr = s.get("win_rate")
    print(f"胜率(粗): {100 * wr:.1f}%" if wr is not None else "胜率(粗): N/A")
    print(f"总手续费: {s.get('total_fees', 0):.2f}")
    print("---------- 最近成交 ----------")
    for t in result.trades[-15:]:
        print(
            f"  {t.date} {t.side:4s} {t.code:8s} {t.shares:5d} @ {t.price:.2f} "
            f"fee={t.fee:.2f} | {t.reason}"
        )
    if len(result.trades) > 15:
        print(f"  ... 共 {len(result.trades)} 笔")
    print("==============================\n")


def save_outputs(result: BacktestResult, out_dir: str = "logs") -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eq_path = os.path.join(out_dir, f"backtest_equity_{stamp}.csv")
    tr_path = os.path.join(out_dir, f"backtest_trades_{stamp}.csv")
    result.equity_curve.to_csv(eq_path, index=False)
    with open(tr_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["date", "code", "side", "shares", "price", "fee", "reason", "cash_after"]
        )
        for t in result.trades:
            w.writerow(
                [
                    t.date,
                    t.code,
                    t.side,
                    t.shares,
                    f"{t.price:.6f}",
                    f"{t.fee:.6f}",
                    t.reason,
                    f"{t.cash_after:.4f}",
                ]
            )
    print(f"[Backtest] 权益曲线: {eq_path}")
    print(f"[Backtest] 成交明细: {tr_path}")
    return eq_path, tr_path


def load_config(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def apply_cli_overrides(cfg: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    bt = dict(cfg.get("backtest") or {})
    if args.start:
        bt["start"] = args.start
    if args.end:
        bt["end"] = args.end
    if args.symbols:
        bt["symbols"] = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.fee_rate is not None:
        bt["fee_rate"] = args.fee_rate
    if args.benchmark:
        bt["benchmark"] = args.benchmark
    if args.cash is not None:
        bt["initial_cash"] = args.cash
    cfg = dict(cfg)
    cfg["backtest"] = bt
    return cfg


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="QA Strategy 日线回测")
    p.add_argument("--config", default="config.yaml", help="配置文件")
    p.add_argument("--start", default=None, help="回测开始 YYYY-MM-DD")
    p.add_argument("--end", default=None, help="回测结束 YYYY-MM-DD")
    p.add_argument("--symbols", default=None, help="逗号分隔股票池，如 SPY,QQQ,BRK.B")
    p.add_argument(
        "--fee-rate",
        type=float,
        default=None,
        dest="fee_rate",
        help="单边手续费率，默认用 config（万分之一=0.0001）",
    )
    p.add_argument("--benchmark", default=None, help="基准，默认 .SPX")
    p.add_argument("--cash", type=float, default=None, help="初始资金")
    p.add_argument("--out-dir", default="logs", help="输出目录")
    args = p.parse_args(argv)

    cfg = load_config(args.config)
    cfg = apply_cli_overrides(cfg, args)

    bt = cfg.get("backtest") or {}
    print(
        f"[Backtest] start={bt.get('start')} end={bt.get('end')} "
        f"symbols={bt.get('symbols')} fee={bt.get('fee_rate', 0.0001)} "
        f"benchmark={bt.get('benchmark', '.SPX')}"
    )

    engine = Backtester(cfg)
    try:
        result = engine.run()
    finally:
        engine.close()

    print_report(result)
    save_outputs(result, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
