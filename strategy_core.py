# strategy_core.py
# 日线均线：MA5 上穿 MA10 买 1 份，下穿 MA10 卖 0.5 份，下穿 MA120 全部卖出。
# 一份 = 一次 buy_amount 买入的股数。半份按先进先出从各笔里扣。

import math

import pandas as pd


def empty_pos_state():
    return {
        "vol": 0,
        "units": 0.0,
        "lots": [],
        "buy_price": 0.0,
        "buy_count": 0,
        "buy_time": "",
        "last_bar": "",
    }


def normalize_pos_state(st):
    if not isinstance(st, dict):
        st = empty_pos_state()
    base = empty_pos_state()
    for key, value in base.items():
        st.setdefault(key, value)
    vol = float(st.get("vol") or 0)
    if vol > 0 and not st.get("lots"):
        st["lots"] = [{
            "units": 1.0,
            "shares": vol,
            "price": float(st.get("buy_price") or 0),
        }]
        st["units"] = 1.0
    elif vol <= 0:
        st["lots"] = []
        st["units"] = 0.0
        st["vol"] = 0
    return st


def _bar_date(value):
    text = str(value or "").strip()
    return text[:10]


def _sma_pair(close, window):
    ma = close.rolling(window).mean()
    curr = ma.iloc[-1]
    prev = ma.iloc[-2]
    if pd.isna(curr) or pd.isna(prev):
        return None, None
    return float(prev), float(curr)


def _crossed_up(prev_fast, fast, prev_slow, slow):
    return prev_fast <= prev_slow and fast > slow


def _crossed_down(prev_fast, fast, prev_slow, slow):
    return prev_fast >= prev_slow and fast < slow


def decide(df, pos_state, config):
    fast_n = int(config.get("ma_fast", 5))
    mid_n = int(config.get("ma_mid", 10))
    slow_n = int(config.get("ma_slow", 120))
    need = slow_n + 2
    if df is None or len(df) < need or "close" not in df.columns:
        return {"action": "none", "units": 0.0, "reason": "数据不足", "extra": {}}

    close = df["close"].astype(float)
    prev_fast, fast = _sma_pair(close, fast_n)
    prev_mid, mid = _sma_pair(close, mid_n)
    prev_slow, slow = _sma_pair(close, slow_n)
    price = float(close.iloc[-1])
    bar = ""
    if "time_key" in df.columns:
        bar = _bar_date(df["time_key"].iloc[-1])

    extra = {
        "price": price,
        "bar": bar,
        "ma5": fast,
        "ma10": mid,
        "ma120": slow,
        "units": float((pos_state or {}).get("units") or 0),
    }
    if None in (prev_fast, fast, prev_mid, mid, prev_slow, slow):
        return {"action": "none", "units": 0.0, "reason": "均线不足", "extra": extra}

    st = pos_state or empty_pos_state()
    held = float(st.get("vol") or 0) > 0 and float(st.get("units") or 0) > 0
    golden = _crossed_up(prev_fast, fast, prev_mid, mid)
    death_mid = _crossed_down(prev_fast, fast, prev_mid, mid)
    death_slow = _crossed_down(prev_fast, fast, prev_slow, slow)

    if held and death_slow:
        extra["units"] = float(st.get("units") or 0)
        return {
            "action": "sell",
            "units": extra["units"],
            "reason": "MA5死叉MA120",
            "extra": extra,
        }
    if held and death_mid:
        sell_units = min(0.5, float(st.get("units") or 0))
        return {
            "action": "sell",
            "units": sell_units,
            "reason": "MA5死叉MA10",
            "extra": extra,
        }
    if golden and not death_slow:
        return {"action": "buy", "units": 1.0, "reason": "MA5金叉MA10", "extra": extra}

    return {"action": "none", "units": 0.0, "reason": "无信号", "extra": extra}


def _sync_avg(st):
    vol = 0.0
    cost = 0.0
    units = 0.0
    for lot in st.get("lots") or []:
        shares = float(lot.get("shares") or 0)
        vol += shares
        cost += shares * float(lot.get("price") or 0)
        units += float(lot.get("units") or 0)
    st["vol"] = vol
    st["units"] = units
    st["buy_price"] = (cost / vol) if vol > 0 else 0.0


def apply_buy(st, shares, price, bar):
    shares = int(shares)
    if shares <= 0:
        return 0
    st.setdefault("lots", [])
    st["lots"].append({"units": 1.0, "shares": shares, "price": float(price)})
    if not st.get("buy_time"):
        st["buy_time"] = bar
    st["buy_count"] = int(st.get("buy_count") or 0) + 1
    st["last_bar"] = bar
    _sync_avg(st)
    return shares


def _take_share_count(raw):
    if raw <= 0:
        return 0
    shares = int(math.floor(raw + 0.5))
    if shares < 1 and raw >= 0.5:
        return 1
    return shares


def apply_sell(st, units, bar):
    """按份数先进先出卖出。返回股数。units 大于等于当前份数时清空。"""
    lots = list(st.get("lots") or [])
    held_units = float(st.get("units") or 0)
    if held_units <= 0 or not lots:
        return 0
    if units is None or float(units) >= held_units - 1e-9:
        shares = _take_share_count(sum(float(lot.get("shares") or 0) for lot in lots))
        st["lots"] = []
        st["vol"] = 0
        st["units"] = 0.0
        st["buy_price"] = 0.0
        st["buy_count"] = 0
        st["buy_time"] = ""
        st["last_bar"] = bar
        return shares

    left = float(units)
    sold = 0
    kept = []
    for lot in lots:
        lot_units = float(lot.get("units") or 0)
        lot_shares = float(lot.get("shares") or 0)
        if left <= 1e-12 or lot_units <= 0:
            if lot_shares > 0 and lot_units > 0:
                kept.append(lot)
            continue
        take_units = min(lot_units, left)
        raw = lot_shares * (take_units / lot_units)
        take_shares = min(_take_share_count(raw), int(round(lot_shares)))
        sold += take_shares
        remain_shares = lot_shares - take_shares
        remain_units = lot_units - take_units
        left -= take_units
        if remain_units > 1e-9 and remain_shares > 0:
            kept.append({
                "units": remain_units,
                "shares": remain_shares,
                "price": lot.get("price"),
            })
    st["lots"] = kept
    st["last_bar"] = bar
    _sync_avg(st)
    if float(st.get("vol") or 0) <= 0:
        st["lots"] = []
        st["vol"] = 0
        st["units"] = 0.0
        st["buy_price"] = 0.0
        st["buy_count"] = 0
        st["buy_time"] = ""
    return sold


def plan_sell(st, units, bar):
    """算出卖出股数和卖出后的仓位，不改原持仓。"""
    shadow = {
        "lots": [dict(lot) for lot in (st.get("lots") or [])],
        "vol": st.get("vol") or 0,
        "units": st.get("units") or 0,
        "buy_price": st.get("buy_price") or 0,
        "buy_count": st.get("buy_count") or 0,
        "buy_time": st.get("buy_time") or "",
        "last_bar": st.get("last_bar") or "",
    }
    shares = apply_sell(shadow, units, bar)
    return shares, shadow
