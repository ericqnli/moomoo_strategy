# runner.py
# moomoo 壳。日线：MA5 金叉 MA10 买 1 份，死叉 MA10 卖 0.5 份，死叉 MA120 全卖。
# Paper 默认。持仓状态写入 positions.json。
# 信号只用已收盘的日 K，同一根 K 只成交一次。

from moomoo import *
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from monitor import Monitor
from risk_manager import RiskManager
from strategy_core import apply_buy, decide, empty_pos_state, normalize_pos_state, plan_sell


POS_FILE = "positions.json"
WATCHLIST_CACHE = "watchlist_cache.json"
MARKET_PREFIXES = ("US", "HK", "SH", "SZ")


def _full_code(code):
    return code if "." in code and code.split(".", 1)[0] in MARKET_PREFIXES else f"US.{code}"


def _market_of(code):
    full = _full_code(code)
    return full.split(".", 1)[0]


def _pool_code(code):
    """统一成池内代码：美股去掉 US. 前缀，与 config.yaml 一致。"""
    raw = str(code).strip()
    if not raw:
        return ""
    if raw.startswith("US."):
        return raw[3:]
    return raw


def _unique(seq):
    seen = set()
    out = []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _fmt(v, digits=2):
    try:
        if v is None:
            return "-"
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _indicator_line(extra, vol):
    return (
        f"MA5={_fmt(extra.get('ma5'))} MA10={_fmt(extra.get('ma10'))} "
        f"MA120={_fmt(extra.get('ma120'))} 仓={vol}股/{_fmt(extra.get('units'), 1)}份"
    )


def load_merged_config(config_path="config.yaml"):
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if os.path.isfile("config.local.yaml"):
        with open("config.local.yaml", encoding="utf-8") as f:
            config.update(yaml.safe_load(f) or {})
    return config


def next_us_close_at(config):
    tz = ZoneInfo(str(config.get("close_timezone") or "America/New_York"))
    raw = str(config.get("close_time") or "16:15")
    hh, mm = [int(x) for x in raw.split(":")[:2]]
    now = datetime.now(tz)
    today_close = now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    def weekday_close_on(day):
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)

    if now.weekday() < 5 and now < today_close:
        return today_close

    day = now.date() + timedelta(days=1)
    while True:
        cand = weekday_close_on(day)
        if cand.weekday() < 5:
            return cand
        day += timedelta(days=1)


def wait_for_close_if_needed(config):
    if str(config.get("run_schedule") or "loop").lower() != "close":
        return
    target = next_us_close_at(config)
    tz = target.tzinfo
    now = datetime.now(tz)
    left = (target - now).total_seconds()
    if left <= 0:
        print(f"[{datetime.now()}] 已过美股收盘检查点，立即执行")
        return
    print(
        f"[{datetime.now()}] 等待美股常规收盘后执行: {target.isoformat()} "
        f"（约 {left / 3600:.1f} 小时）"
    )
    while True:
        left = (target - datetime.now(tz)).total_seconds()
        if left <= 0:
            break
        time.sleep(min(left, 60))
    print(f"[{datetime.now()}] 到达收盘检查点，开始本轮")


class MoomooStrategyRunner:
    def __init__(self, config=None, config_path="config.yaml"):
        self.config = config or load_merged_config(config_path)
        self.quote_ctx = OpenQuoteContext()
        self.monitor = Monitor(self.config)
        self.risk = RiskManager(self.config)
        self.trade_mode = str(self.config.get("trade_mode") or "paper").strip().lower()
        print(f"[{datetime.now()}] 下单记录初始化完成 - 模式: {self.trade_mode}")
        self.positions = self._load_positions()
        self.watchlist_symbols = []
        self.universe = list(self._static_symbols())
        self._watchlist_synced_at = 0.0
        self.refresh_watchlist(force=True)
        print(f"[{datetime.now()}] === moomoo + QMT规则 v2.7 启动 ===")
        print(f"[{datetime.now()}] 股票池 {len(self._active_codes())} 只: {', '.join(self._active_codes())}")

    def _static_symbols(self):
        return _unique(_pool_code(c) for c in (self.config.get("symbols") or []))

    def _watchlist_groups(self):
        groups = self.config.get("watchlist_groups") or []
        return [str(g).strip() for g in groups if str(g).strip()]

    def _allowed_markets(self):
        raw = self.config.get("watchlist_markets")
        if not raw:
            return set()
        return {str(m).upper().strip() for m in raw if str(m).strip()}

    def _refresh_seconds(self):
        return max(60, int(self.config.get("watchlist_refresh_seconds", 900)))

    def _load_positions(self):
        data = {}
        if os.path.isfile(POS_FILE):
            try:
                with open(POS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                print(f"读取 {POS_FILE} 失败: {e}")
        for code, st in list(data.items()):
            if isinstance(st, dict):
                normalize_pos_state(st)
        for code in self._static_symbols():
            if code not in data:
                data[code] = empty_pos_state()
        return data

    def _save_positions(self):
        with open(POS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.positions, f, ensure_ascii=False, indent=2)

    def _load_watchlist_cache(self):
        if not os.path.isfile(WATCHLIST_CACHE):
            return []
        try:
            with open(WATCHLIST_CACHE, encoding="utf-8") as f:
                payload = json.load(f) or {}
            return [_pool_code(c) for c in (payload.get("watchlist") or [])]
        except Exception as e:
            print(f"读取 {WATCHLIST_CACHE} 失败: {e}")
            return []

    def _save_watchlist_cache(self, codes):
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "groups": self._watchlist_groups(),
            "watchlist": codes,
        }
        try:
            with open(WATCHLIST_CACHE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"写入 {WATCHLIST_CACHE} 失败: {e}")

    def _fetch_group_codes(self, group_name):
        ret, data = self.quote_ctx.get_user_security(group_name)
        if ret != RET_OK or data is None:
            raise RuntimeError(f"{group_name}: {data}")
        if getattr(data, "empty", True) or "code" not in getattr(data, "columns", []):
            print(f"⚠️ 自选分组空或不存在: {group_name}")
            return []
        allowed = self._allowed_markets()
        out = []
        for raw in data["code"].tolist():
            code = _pool_code(raw)
            if not code:
                continue
            if allowed and _market_of(raw) not in allowed:
                continue
            out.append(code)
        return out

    def refresh_watchlist(self, force=False):
        groups = self._watchlist_groups()
        now = time.time()
        if not force and (now - self._watchlist_synced_at) < self._refresh_seconds():
            return False
        if not groups:
            self.watchlist_symbols = []
            self.universe = list(self._static_symbols())
            self._watchlist_synced_at = now
            return True

        fetched = []
        errors = []
        for group in groups:
            try:
                codes = self._fetch_group_codes(group)
                fetched.extend(codes)
                print(f"[{datetime.now()}] 自选[{group}] {len(codes)} 只")
            except Exception as e:
                errors.append(f"{group}: {e}")
                print(f"❌ 拉取自选分组失败 {group}: {e}")

        if errors and not fetched:
            cached = self._load_watchlist_cache()
            self.watchlist_symbols = _unique(cached)
            source = "缓存"
        else:
            self.watchlist_symbols = _unique(fetched)
            self._save_watchlist_cache(self.watchlist_symbols)
            source = "OpenD" if not errors else "OpenD部分失败"

        merged = _unique(self._static_symbols() + self.watchlist_symbols)
        if merged != self.universe:
            print(
                f"[{datetime.now()}] 股票池更新({source}): "
                f"{len(self.universe)} -> {len(merged)}"
            )
        self.universe = merged
        for code in self.universe:
            self.positions.setdefault(code, empty_pos_state())
        self._watchlist_synced_at = now
        return True

    def _active_codes(self):
        codes = list(self.universe)
        extra = []
        for code, st in self.positions.items():
            if float((st or {}).get("vol") or 0) > 0 and code not in codes:
                extra.append(code)
        return codes + extra

    def get_history(self, code):
        full_code = _full_code(code)
        end = datetime.now().strftime("%Y-%m-%d")
        lookback = int(self.config.get("history_days", 520))
        start = (datetime.now() - timedelta(days=lookback)).strftime("%Y-%m-%d")
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

    def _completed_bars(self, df):
        """收盘前最后一根是未走完的当日 K，信号只用它前面的已收盘日线。"""
        if df is None or len(df) == 0 or "time_key" not in df.columns:
            return df
        tz = ZoneInfo(str(self.config.get("close_timezone") or "America/New_York"))
        now = datetime.now(tz)
        raw = str(self.config.get("close_time") or "16:15")
        hh, mm = [int(x) for x in raw.split(":")[:2]]
        close_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        last = str(df["time_key"].iloc[-1])[:10]
        if last == now.date().isoformat() and now < close_at:
            return df.iloc[:-1].reset_index(drop=True)
        return df

    def _log_order(self, code, quantity, side="BUY"):
        """Paper 只打印。Live 同样只打印，不向券商下单。"""
        full_code = code if str(code).startswith("US.") else f"US.{code}"
        if self.trade_mode == "paper":
            print(f"[{datetime.now()}] [PAPER ORDER] {side} {quantity} shares of {code}")
        else:
            print(f"[{datetime.now()}] [LIVE ORDER] {side} {quantity} {full_code}")
        return True

    def _buy_qty(self, price):
        amount = float(self.config.get("buy_amount", 5000))
        if price <= 0:
            return 0
        qty = int(amount / price)
        return max(qty, 1) if amount > 0 else 0

    def _close_report(self, stats):
        mode = str(self.config.get("trade_mode") or "paper")
        tz = ZoneInfo(str(self.config.get("close_timezone") or "America/New_York"))
        session = datetime.now(tz).strftime("%Y-%m-%d")
        lines = [
            f"收盘日线 {session}",
            f"模式 {mode} | 池 {stats['pool']} 只 | 买 {len(stats['buys'])} | 卖 {len(stats['sells'])} | 无信号 {stats['none']} | 失败 {stats['fail']}",
        ]
        if stats["buys"]:
            lines.append("买入")
            lines.extend(stats["buys"])
        if stats["sells"]:
            lines.append("卖出")
            lines.extend(stats["sells"])
        holds = []
        for code, st in self.positions.items():
            vol = float((st or {}).get("vol") or 0)
            if vol <= 0:
                continue
            holds.append(
                f"{code} {int(vol)}股 {_fmt(st.get('units'), 1)}份 成本{_fmt(st.get('buy_price'))}"
            )
        if holds:
            lines.append("持仓 " + "；".join(holds))
        else:
            lines.append("持仓 无")
        if not stats["buys"] and not stats["sells"]:
            lines.append("本轮无交易")
        return "\n".join(lines)

    def _send_close_report(self, stats):
        if self.config.get("enable_close_report", True) is False:
            return
        self.monitor.notify(self._close_report(stats))

    def run_cycle(self):
        self.refresh_watchlist(force=True)
        codes = self._active_codes()
        stats = {"pool": len(codes), "buys": [], "sells": [], "none": 0, "fail": 0}
        need = int(self.config.get("ma_slow", 120)) + 2
        for code in codes:
            raw = self.get_history(code)
            df = self._completed_bars(raw)
            if df is None or len(df) < need:
                stats["fail"] += 1
                continue

            st = normalize_pos_state(self.positions.setdefault(code, empty_pos_state()))
            self.positions[code] = st
            result = decide(df, st, self.config)
            extra = result.get("extra") or {}
            price = extra.get("price", float(df["close"].iloc[-1]))
            action = result["action"]
            reason = result["reason"]
            bar = str(extra.get("bar") or "")
            if action in ("buy", "sell") and bar and st.get("last_bar") == bar:
                action = "none"
                reason = "本根已处理"

            self.monitor.log(
                f"{code} | 价:{price:.2f} | {action} | {reason} | "
                f"{_indicator_line(extra, st.get('vol'))}"
            )

            if action == "buy":
                qty = self._buy_qty(price)
                if qty <= 0:
                    stats["none"] += 1
                    continue
                ok = self._log_order(code, qty, side="BUY")
                if ok:
                    apply_buy(st, qty, price, bar)
                    extra["units"] = st.get("units")
                    stats["buys"].append(
                        f"{code} {qty}股 @{_fmt(price)} {reason} 共{_fmt(st.get('units'), 1)}份"
                    )
                    self.monitor.notify(
                        f"🟢 {code} 买入 {qty} @ {price:.2f} {reason} 持有{_fmt(st.get('units'), 1)}份"
                    )
                else:
                    stats["fail"] += 1

            elif action == "sell" and float(st.get("vol") or 0) > 0:
                sell_vol, sold_state = plan_sell(st, result.get("units"), bar)
                if sell_vol <= 0:
                    stats["none"] += 1
                    continue
                ok = self._log_order(code, sell_vol, side="SELL")
                if ok:
                    st.clear()
                    st.update(sold_state)
                    extra["units"] = st.get("units")
                    stats["sells"].append(
                        f"{code} {sell_vol}股 @{_fmt(price)} {reason} 剩{_fmt(st.get('units'), 1)}份"
                    )
                    self.monitor.notify(
                        f"🔴 {code} 卖出 {sell_vol} @ {price:.2f} {reason} 剩余{_fmt(st.get('units'), 1)}份"
                    )
                else:
                    stats["fail"] += 1
            else:
                stats["none"] += 1

            self._save_positions()

        self._send_close_report(stats)

    def close(self):
        print(f"[{datetime.now()}] 下单记录已关闭")
        self.quote_ctx.close()


if __name__ == "__main__":
    cfg = load_merged_config()
    runner = None
    try:
        wait_for_close_if_needed(cfg)
        runner = MoomooStrategyRunner(config=cfg)
        runner.run_cycle()
        if str(cfg.get("run_schedule") or "loop").lower() == "loop":
            while True:
                time.sleep(int(cfg.get("sleep_seconds", 1800)))
                runner.run_cycle()
        else:
            print(f"[{datetime.now()}] close 模式本轮完成，退出")
    except KeyboardInterrupt:
        print("策略停止")
        sys.exit(130)
    finally:
        if runner is not None:
            runner.close()
