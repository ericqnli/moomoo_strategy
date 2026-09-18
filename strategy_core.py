# strategy_core.py
# QMT Macd_V1 买卖规则（独立实现，不依赖 QMT / talib）
# 买：MACD 绿柱缩短 + ADX 切换 KDJ/RSI 超卖反转
# 卖：止损/止盈/移动止盈/时间止损（默认关）→ 顶背离全平 → 死叉半仓

import numpy as np
import pandas as pd


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def calc_macd(close, fast=12, slow=26, signal=9):
    dif = _ema(close, fast) - _ema(close, slow)
    dea = _ema(dif, signal)
    hist = dif - dea
    return dif, dea, hist


def calc_rsi(close, period=6):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_kdj(high, low, close, n=9, m1=3, m2=3):
    lowest = low.rolling(n).min()
    highest = high.rolling(n).max()
    rsv = (close - lowest) / (highest - lowest).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def calc_atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calc_adx(high, low, close, period=14):
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr = calc_atr(high, low, close, period)
    plus_di = 100 * pd.Series(plus_dm, index=close.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False
    ).mean() / atr
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx


def detect_macd_top_divergence(close, dif, lookback=30, peak_order=3):
    if len(close) < lookback + 5 or len(dif) < lookback + 5:
        return False
    c = np.asarray(close[-lookback:], dtype=float)
    d = np.asarray(dif[-lookback:], dtype=float)
    if np.any(np.isnan(c)) or np.any(np.isnan(d)):
        return False
    peaks = []
    for i in range(peak_order, len(c) - peak_order):
        if c[i] == np.max(c[i - peak_order : i + peak_order + 1]):
            peaks.append(i)
    if len(peaks) < 2:
        return False
    p1, p2 = peaks[-2], peaks[-1]
    return bool(c[p2] > c[p1] * 1.001 and d[p2] < d[p1] * 0.995)


def empty_pos_state():
    return {
        "vol": 0,
        "buy_price": 0.0,
        "buy_count": 0,
        "high_since_entry": 0.0,
        "half_sold": False,
        "bars_held": 0,
        "entry_atr": 0.0,
        "buy_time": "",
    }


def decide(df, pos_state, config):
    need = 60
    if df is None or len(df) < need:
        return {"action": "none", "ratio": 0.0, "reason": "数据不足", "extra": {}}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float) if "volume" in df.columns else None

    fast = int(config.get("macd_fast", 12))
    slow = int(config.get("macd_slow", 26))
    signal = int(config.get("macd_signal", 9))
    rsi_period = int(config.get("rsi_period", 6))
    kdj_n = int(config.get("kdj_n", 9))
    kdj_m1 = int(config.get("kdj_m1", 3))
    kdj_m2 = int(config.get("kdj_m2", 3))
    adx_period = int(config.get("adx_period", 14))
    atr_period = int(config.get("atr_period", 14))

    dif, dea, hist = calc_macd(close, fast, slow, signal)
    rsi = calc_rsi(close, rsi_period)
    k, d, j = calc_kdj(high, low, close, kdj_n, kdj_m1, kdj_m2)
    adx = calc_adx(high, low, close, adx_period)
    atr = calc_atr(high, low, close, atr_period)

    curr_close = float(close.iloc[-1])
    vals = {
        "curr_dif": dif.iloc[-1],
        "curr_dea": dea.iloc[-1],
        "prev_dif": dif.iloc[-2],
        "prev_dea": dea.iloc[-2],
        "curr_hist": hist.iloc[-1],
        "prev_hist": hist.iloc[-2],
        "curr_rsi": rsi.iloc[-1],
        "prev_rsi": rsi.iloc[-2],
        "curr_k": k.iloc[-1],
        "prev_k": k.iloc[-2],
        "curr_d": d.iloc[-1],
        "prev_d": d.iloc[-2],
        "curr_j": j.iloc[-1],
        "prev_j": j.iloc[-2],
        "curr_adx": adx.iloc[-1],
        "curr_atr": atr.iloc[-1],
    }
    if any(pd.isna(v) for v in [
        vals["curr_dif"], vals["curr_dea"], vals["curr_hist"], vals["prev_hist"],
        vals["curr_rsi"], vals["curr_k"], vals["curr_d"],
    ]):
        return {"action": "none", "ratio": 0.0, "reason": "指标NaN", "extra": {}}

    curr_hist = float(vals["curr_hist"])
    prev_hist = float(vals["prev_hist"])
    curr_rsi = float(vals["curr_rsi"])
    prev_rsi = float(vals["prev_rsi"]) if not pd.isna(vals["prev_rsi"]) else None
    curr_k = float(vals["curr_k"])
    prev_k = float(vals["prev_k"]) if not pd.isna(vals["prev_k"]) else None
    curr_d = float(vals["curr_d"])
    prev_d = float(vals["prev_d"]) if not pd.isna(vals["prev_d"]) else None
    curr_j = float(vals["curr_j"]) if not pd.isna(vals["curr_j"]) else None
    prev_j = float(vals["prev_j"]) if not pd.isna(vals["prev_j"]) else None
    curr_adx = float(vals["curr_adx"]) if not pd.isna(vals["curr_adx"]) else None
    curr_atr = float(vals["curr_atr"]) if not pd.isna(vals["curr_atr"]) else None
    curr_dif = float(vals["curr_dif"])
    curr_dea = float(vals["curr_dea"])
    prev_dif = float(vals["prev_dif"]) if not pd.isna(vals["prev_dif"]) else None
    prev_dea = float(vals["prev_dea"]) if not pd.isna(vals["prev_dea"]) else None

    vol_ok = True
    vol_ratio = None
    if config.get("use_volume_filter", False) and volume is not None:
        ma_p = int(config.get("volume_ma_period", 20))
        vol_ma = volume.rolling(ma_p).mean().iloc[-1]
        if not pd.isna(vol_ma) and vol_ma > 0:
            vol_ratio = float(volume.iloc[-1] / vol_ma)
            vol_ok = vol_ratio >= float(config.get("volume_mult", 1.2))

    regime = "mid"
    if curr_adx is not None:
        if curr_adx > float(config.get("adx_trend", 25)):
            regime = "trend"
        elif curr_adx < float(config.get("adx_range", 20)):
            regime = "range"

    macd_green_shrinking = prev_hist < 0 and curr_hist < 0 and curr_hist > prev_hist
    kdj_golden = prev_k is not None and prev_d is not None and prev_k <= prev_d and curr_k > curr_d
    k_oversold = float(config.get("kdj_k_oversold", 30))
    j_oversold = float(config.get("kdj_j_oversold", 20))
    kdj_recent_oversold = (
        curr_k < k_oversold
        or (prev_k is not None and prev_k < k_oversold)
        or (curr_j is not None and curr_j < j_oversold)
        or (prev_j is not None and prev_j < j_oversold)
    )
    kdj_ok = kdj_golden and kdj_recent_oversold
    rsi_ok = curr_rsi <= float(config.get("rsi_oversold", 35)) and prev_rsi is not None and curr_rsi > prev_rsi

    mode = config.get("regime_mode", "auto")
    if mode == "kdj":
        entry_trigger, trigger_by = kdj_ok, "KDJ"
    elif mode == "rsi":
        entry_trigger, trigger_by = rsi_ok, "RSI"
    elif mode == "both":
        entry_trigger = kdj_ok or rsi_ok
        trigger_by = "KDJ" if kdj_ok else ("RSI" if rsi_ok else "")
    elif regime == "range":
        entry_trigger, trigger_by = kdj_ok, "KDJ(震荡)"
    elif regime == "trend":
        entry_trigger, trigger_by = rsi_ok, "RSI(趋势)"
    else:
        entry_trigger, trigger_by = kdj_ok and rsi_ok, "KDJ|RSI(中间)"

    extra = {
        "price": curr_close,
        "hist": curr_hist,
        "prev_hist": prev_hist,
        "rsi": curr_rsi,
        "k": curr_k,
        "j": curr_j,
        "adx": curr_adx,
        "regime": regime,
        "atr": curr_atr,
        "vol_ratio": vol_ratio,
        "trigger_by": trigger_by,
    }

    st = pos_state or empty_pos_state()
    has_pos = float(st.get("vol", 0) or 0) > 0

    if has_pos:
        st["high_since_entry"] = max(float(st.get("high_since_entry") or 0), curr_close)
        st["bars_held"] = int(st.get("bars_held") or 0) + 1
        buy_price = float(st.get("buy_price") or 0)
        pnl = (curr_close - buy_price) / buy_price if buy_price > 0 else None
        high_since = float(st.get("high_since_entry") or 0)
        entry_atr = float(st.get("entry_atr") or 0)
        sell_reason = None
        sell_ratio = 0.0
        stop_mode = config.get("stop_mode", "pct")

        if config.get("use_stop_loss", False) and pnl is not None:
            if stop_mode == "atr" and entry_atr > 0:
                if curr_close <= buy_price - float(config.get("stop_atr_mult", 2.0)) * entry_atr:
                    sell_reason = "ATR止损"
                    sell_ratio = 1.0
            elif pnl <= -float(config.get("stop_loss_pct", 0.08)):
                sell_reason = "百分比止损"
                sell_ratio = 1.0

        if sell_reason is None and config.get("use_take_profit", False) and pnl is not None:
            if stop_mode == "atr" and entry_atr > 0:
                if curr_close >= buy_price + float(config.get("take_atr_mult", 3.5)) * entry_atr:
                    sell_reason = "ATR止盈"
                    sell_ratio = 1.0
            elif pnl >= float(config.get("take_profit_pct", 0.18)):
                sell_reason = "百分比止盈"
                sell_ratio = 1.0

        if sell_reason is None and config.get("use_trailing", False) and high_since > 0:
            drawdown = (high_since - curr_close) / high_since
            if drawdown >= float(config.get("trail_pct", 0.08)):
                sell_reason = f"移动止盈(回撤{drawdown * 100:.2f}%)"
                sell_ratio = 1.0

        if sell_reason is None and detect_macd_top_divergence(
            close.values,
            dif.values,
            lookback=int(config.get("div_lookback", 30)),
            peak_order=int(config.get("div_peak_order", 3)),
        ):
            sell_reason = "MACD顶背离"
            sell_ratio = 1.0

        if sell_reason is None and not st.get("half_sold", False):
            death = (
                prev_dif is not None
                and prev_dea is not None
                and prev_dif > prev_dea
                and curr_dif < curr_dea
            )
            if death:
                sell_reason = "MACD死叉"
                sell_ratio = float(config.get("death_cross_sell_ratio", 0.5))

        if sell_reason is None and config.get("use_time_stop", False):
            if int(st.get("bars_held") or 0) >= int(config.get("max_hold_days", 30)):
                sell_reason = f"时间止损(持仓{st['bars_held']}天)"
                sell_ratio = 1.0

        extra["pnl"] = pnl
        if sell_reason and sell_ratio > 0:
            return {"action": "sell", "ratio": sell_ratio, "reason": sell_reason, "extra": extra}

    can_buy = (not has_pos) or (
        config.get("enable_repeat_buy", True)
        and int(st.get("buy_count") or 0) < int(config.get("max_buy_count", 3))
    )
    if can_buy and macd_green_shrinking and entry_trigger and vol_ok:
        reason = f"绿柱缩短+{trigger_by}"
        extra["entry_atr"] = curr_atr
        return {"action": "buy", "ratio": 1.0, "reason": reason, "extra": extra}

    return {"action": "none", "ratio": 0.0, "reason": "无信号", "extra": extra}
