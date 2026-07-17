# strategy_core.py v2.6
# 四要素 + 档位：金/死叉为扳机；L1/L2/L3；背离近窗；买卖 level 可分
#
# 买: 必须金叉
#   L1: 金叉 + count(div近窗, rsi, vol, trend) >= 1
#   L2: 金叉 + RSI门槛 + count(div近窗, vol, trend) >= 1
#   L3: 金叉 + RSI门槛 + 近窗底背离 + count(vol, trend) >= 1
# 卖: 必须死叉（对称；trend 为价 < MA）
#
# 风控止损/止盈/MA200 不在本文件。

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd


# ===================== 指标工具 =====================

def calc_macd_histogram(df, fast=12, slow=26, signal=9):
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif - dea, dif, dea


def calc_rsi(df, period=14):
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calc_atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calc_ma(df, period=20):
    return df["close"].rolling(window=period).mean()


def calc_volume_avg(df, period=20):
    return df["volume"].rolling(window=period).mean()


# ===================== 局部极值 + 背离（单端点） =====================

def get_local_low_idx(low_arr):
    idx_list = []
    for i in range(1, len(low_arr) - 1):
        if low_arr[i] < low_arr[i - 1] and low_arr[i] < low_arr[i + 1]:
            idx_list.append(i)
    return idx_list


def get_local_high_idx(high_arr):
    idx_list = []
    for i in range(1, len(high_arr) - 1):
        if high_arr[i] > high_arr[i - 1] and high_arr[i] > high_arr[i + 1]:
            idx_list.append(i)
    return idx_list


def check_macd_bottom_divergence(low_arr, dif_arr, bar_num, min_gap):
    if len(low_arr) < bar_num:
        return False
    recent_low = low_arr[-bar_num:]
    recent_dif = dif_arr[-bar_num:]
    low_idx = get_local_low_idx(recent_low)
    if len(low_idx) < 2:
        return False
    idx1, idx2 = low_idx[-2], low_idx[-1]
    if idx2 - idx1 < min_gap:
        return False
    return recent_low[idx2] < recent_low[idx1] and recent_dif[idx2] > recent_dif[idx1]


def check_macd_top_divergence(high_arr, dif_arr, bar_num, min_gap):
    if len(high_arr) < bar_num:
        return False
    recent_high = high_arr[-bar_num:]
    recent_dif = dif_arr[-bar_num:]
    high_idx = get_local_high_idx(recent_high)
    if len(high_idx) < 2:
        return False
    idx1, idx2 = high_idx[-2], high_idx[-1]
    if idx2 - idx1 < min_gap:
        return False
    return recent_high[idx2] > recent_high[idx1] and recent_dif[idx2] < recent_dif[idx1]


def check_rsi_bottom_divergence(low_arr, rsi_arr, bar_num, min_gap):
    if len(low_arr) < bar_num:
        return False
    recent_low = low_arr[-bar_num:]
    recent_rsi = rsi_arr[-bar_num:]
    low_idx = get_local_low_idx(recent_low)
    if len(low_idx) < 2:
        return False
    idx1, idx2 = low_idx[-2], low_idx[-1]
    if idx2 - idx1 < min_gap:
        return False
    return recent_low[idx2] < recent_low[idx1] and recent_rsi[idx2] > recent_rsi[idx1]


def check_rsi_top_divergence(high_arr, rsi_arr, bar_num, min_gap):
    if len(high_arr) < bar_num:
        return False
    recent_high = high_arr[-bar_num:]
    recent_rsi = rsi_arr[-bar_num:]
    high_idx = get_local_high_idx(recent_high)
    if len(high_idx) < 2:
        return False
    idx1, idx2 = high_idx[-2], high_idx[-1]
    if idx2 - idx1 < min_gap:
        return False
    return recent_high[idx2] > recent_high[idx1] and recent_rsi[idx2] < recent_rsi[idx1]


def _combine_div_flags(
    macd_ok: bool,
    rsi_ok: bool,
    use_macd: bool,
    use_rsi: bool,
    require_both: bool,
) -> bool:
    flags: List[bool] = []
    if use_macd:
        flags.append(bool(macd_ok))
    if use_rsi:
        flags.append(bool(rsi_ok))
    if not flags:
        return False
    return all(flags) if require_both else any(flags)


def _near_window_divergence(
    *,
    is_bottom: bool,
    low_arr,
    high_arr,
    dif_arr,
    rsi_arr,
    bar_num: int,
    min_gap: int,
    active_bars: int,
    use_macd: bool,
    use_rsi: bool,
    require_both: bool,
) -> bool:
    """
    近窗背离：在最近 active_bars 个结束点上，任一端点满足背离即 True。
    不要求背离极值落在最后一根 K。
    """
    n = len(dif_arr)
    if n < bar_num + 2:
        return False
    active_bars = max(1, int(active_bars))
    # end 为切片终点（不含），从 n 递减到 n-active_bars+1
    min_end = max(bar_num + 2, n - active_bars + 1)
    for end in range(n, min_end - 1, -1):
        if is_bottom:
            macd_ok = (
                check_macd_bottom_divergence(low_arr[:end], dif_arr[:end], bar_num, min_gap)
                if use_macd
                else False
            )
            rsi_ok = (
                check_rsi_bottom_divergence(low_arr[:end], rsi_arr[:end], bar_num, min_gap)
                if use_rsi
                else False
            )
        else:
            macd_ok = (
                check_macd_top_divergence(high_arr[:end], dif_arr[:end], bar_num, min_gap)
                if use_macd
                else False
            )
            rsi_ok = (
                check_rsi_top_divergence(high_arr[:end], rsi_arr[:end], bar_num, min_gap)
                if use_rsi
                else False
            )
        if _combine_div_flags(macd_ok, rsi_ok, use_macd, use_rsi, require_both):
            return True
    return False


def _clamp_level(value: Any, default: int = 2) -> int:
    try:
        lv = int(value)
    except (TypeError, ValueError):
        lv = default
    return max(1, min(3, lv))


def _count_true(flags: Sequence[Tuple[str, bool]]) -> Tuple[int, List[str]]:
    hits = [name for name, ok in flags if ok]
    return len(hits), hits


# ===================== 档位信号 =====================

def evaluate_signal(
    df, current_price, atr, config
) -> Tuple[str, str, Dict[str, Any]]:
    """
    返回 (signal, detail, factors)
    signal: 'buy' | 'sell' | 'none'
    detail: 命中说明
    factors: 日志用因子快照（叉/背离/RSI/量/趋势等）
    """
    cfg: Dict[str, Any] = config or {}
    empty: Dict[str, Any] = {}
    lookback = int(cfg.get("div_lookback", cfg.get("diver_bar_count", 20)))
    active = int(cfg.get("div_active_bars", 10))
    min_len = max(100, lookback + active + 5)
    if len(df) < min_len:
        return "none", "data_short", empty

    _hist, dif, dea = calc_macd_histogram(df)
    rsi_series = calc_rsi(df, 14)
    rsi = float(rsi_series.iloc[-1])
    if pd.isna(rsi):
        return "none", "rsi_nan", empty

    vol_ma = calc_volume_avg(df, 20)
    vol_ma_last = float(vol_ma.iloc[-1]) if len(vol_ma) else 0.0
    current_vol = float(df["volume"].iloc[-1])
    vol_ratio = (current_vol / vol_ma_last) if vol_ma_last > 0 else 0.0
    volume_ok = vol_ratio >= float(cfg.get("vol_multiple", 1.3))

    ma_period = int(cfg.get("trend_ma_period", 20))
    ma20 = float(calc_ma(df, ma_period).iloc[-1])
    # 证据用的 trend（可被 enable_trend_filter 关掉）
    trend_up_raw = current_price > ma20
    trend_down_raw = current_price < ma20
    trend_up = trend_up_raw
    trend_down = trend_down_raw
    if not cfg.get("enable_trend_filter", True):
        trend_up = False
        trend_down = False

    bar_num = lookback
    min_gap = int(cfg.get("min_wave_gap", 4))
    use_macd = bool(cfg.get("use_macd_divergence", True))
    use_rsi = bool(cfg.get("use_rsi_divergence", True))
    require_both = bool(cfg.get("require_both_divergence", False))

    low_arr = df["low"].values
    high_arr = df["high"].values
    dif_arr = dif.values
    rsi_arr = rsi_series.values

    bottom_div = _near_window_divergence(
        is_bottom=True,
        low_arr=low_arr,
        high_arr=high_arr,
        dif_arr=dif_arr,
        rsi_arr=rsi_arr,
        bar_num=bar_num,
        min_gap=min_gap,
        active_bars=active,
        use_macd=use_macd,
        use_rsi=use_rsi,
        require_both=require_both,
    )
    top_div = _near_window_divergence(
        is_bottom=False,
        low_arr=low_arr,
        high_arr=high_arr,
        dif_arr=dif_arr,
        rsi_arr=rsi_arr,
        bar_num=bar_num,
        min_gap=min_gap,
        active_bars=active,
        use_macd=use_macd,
        use_rsi=use_rsi,
        require_both=require_both,
    )

    gold_cross = bool(dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2])
    dead_cross = bool(dif.iloc[-1] < dea.iloc[-1] and dif.iloc[-2] >= dea.iloc[-2])
    dif_last = float(dif.iloc[-1])
    dea_last = float(dea.iloc[-1])

    rsi_buy_max = float(cfg.get("rsi_buy_max", cfg.get("rsi_oversell", 50)))
    rsi_sell_min = float(cfg.get("rsi_sell_min", cfg.get("rsi_overbuy", 55)))
    rsi_buy_ok = rsi <= rsi_buy_max
    rsi_sell_ok = rsi >= rsi_sell_min

    buy_level = _clamp_level(cfg.get("buy_level", 2))
    sell_level = _clamp_level(cfg.get("sell_level", 2))

    buy_ok = False
    buy_hits: List[str] = []
    if gold_cross:
        buy_hits.append("gold")
        if buy_level == 1:
            n, extra = _count_true(
                [
                    ("div", bottom_div),
                    ("rsi", rsi_buy_ok),
                    ("vol", volume_ok),
                    ("trend", trend_up),
                ]
            )
            buy_ok = n >= 1
            buy_hits.extend(extra)
        elif buy_level == 2:
            if rsi_buy_ok:
                buy_hits.append("rsi")
                n, extra = _count_true(
                    [
                        ("div", bottom_div),
                        ("vol", volume_ok),
                        ("trend", trend_up),
                    ]
                )
                buy_ok = n >= 1
                buy_hits.extend(extra)
        else:  # L3
            if rsi_buy_ok and bottom_div:
                buy_hits.append("rsi")
                buy_hits.append("div")
                n, extra = _count_true(
                    [
                        ("vol", volume_ok),
                        ("trend", trend_up),
                    ]
                )
                buy_ok = n >= 1
                buy_hits.extend(extra)

    sell_ok = False
    sell_hits: List[str] = []
    if dead_cross:
        sell_hits.append("dead")
        if sell_level == 1:
            n, extra = _count_true(
                [
                    ("div", top_div),
                    ("rsi", rsi_sell_ok),
                    ("vol", volume_ok),
                    ("trend", trend_down),
                ]
            )
            sell_ok = n >= 1
            sell_hits.extend(extra)
        elif sell_level == 2:
            if rsi_sell_ok:
                sell_hits.append("rsi")
                n, extra = _count_true(
                    [
                        ("div", top_div),
                        ("vol", volume_ok),
                        ("trend", trend_down),
                    ]
                )
                sell_ok = n >= 1
                sell_hits.extend(extra)
        else:  # L3
            if rsi_sell_ok and top_div:
                sell_hits.append("rsi")
                sell_hits.append("div")
                n, extra = _count_true(
                    [
                        ("vol", volume_ok),
                        ("trend", trend_down),
                    ]
                )
                sell_ok = n >= 1
                sell_hits.extend(extra)

    if sell_ok:
        signal = "sell"
        hits = sell_hits
        detail = f"L{sell_level} " + "+".join(sell_hits) + f" rsi={rsi:.1f} vol={vol_ratio:.2f}"
    elif buy_ok:
        signal = "buy"
        hits = buy_hits
        detail = f"L{buy_level} " + "+".join(buy_hits) + f" rsi={rsi:.1f} vol={vol_ratio:.2f}"
    else:
        signal = "none"
        hits = []
        bits = []
        if gold_cross:
            bits.append("gold")
        if dead_cross:
            bits.append("dead")
        if bottom_div:
            bits.append("bdiv")
        if top_div:
            bits.append("tdiv")
        bits.append(f"rsi={rsi:.1f}")
        bits.append(f"vol={vol_ratio:.2f}")
        bits.append(f"bL{buy_level}/sL{sell_level}")
        detail = " ".join(bits)

    factors: Dict[str, Any] = {
        "gold": gold_cross,
        "dead": dead_cross,
        "div_b": bottom_div,
        "div_t": top_div,
        "rsi": round(rsi, 2),
        "rsi_buy_ok": rsi_buy_ok,
        "rsi_sell_ok": rsi_sell_ok,
        "rsi_buy_max": rsi_buy_max,
        "rsi_sell_min": rsi_sell_min,
        "vol_ratio": round(vol_ratio, 3),
        "vol_ok": volume_ok,
        "ma20": round(ma20, 4),
        "trend_up": trend_up_raw,
        "trend_down": trend_down_raw,
        "dif": round(dif_last, 6),
        "dea": round(dea_last, 6),
        "buy_level": buy_level,
        "sell_level": sell_level,
        "signal": signal,
        "hits": "+".join(hits) if hits else "-",
        "detail": detail,
    }
    return signal, detail, factors


def get_signal(df, current_price, atr, config):
    """兼容旧接口，只返回 'buy' | 'sell' | 'none'。"""
    signal, _detail, _factors = evaluate_signal(df, current_price, atr, config)
    return signal


# ===================== ATR 追踪止损 =====================

def generate_atr_trailing_stop_signal(df, current_price, current_pos, config):
    if current_pos <= 0 or len(df) < config.get("highest_window", 60):
        return "none"
    atr_series = calc_atr(df, config.get("atr_period", 14))
    if atr_series is None or atr_series.empty:
        return "none"
    try:
        atr = float(atr_series.iloc[-1])
    except (TypeError, ValueError):
        return "none"
    if pd.isna(atr) or atr <= 0:
        return "none"
    recent_high = df["high"].iloc[-config.get("highest_window", 60) :].max()
    trailing_stop = recent_high - config.get("atr_mult", 2.0) * atr
    if current_price < trailing_stop:
        return "atr_trailing_sell"
    return "none"


print("strategy_core v2.6 (cross trigger + L1/L2/L3 + near-window div) loaded")
