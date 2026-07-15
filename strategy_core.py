# strategy_core.py v2.5 - Four-Element Strategy (MACD + RSI + Divergence + Volume + Trend)
import pandas as pd
import numpy as np

# ===================== 原有工具函数（保留） =====================
def calc_macd_histogram(df, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif - dea, dif, dea

def calc_rsi(df, period=14):
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def calc_ma(df, period=20):
    return df['close'].rolling(window=period).mean()

def calc_volume_avg(df, period=20):
    return df['volume'].rolling(window=period).mean()

# ===================== pt2.0 核心：局部极值 + 双波段背离 =====================
def get_local_low_idx(low_arr):
    idx_list = []
    for i in range(1, len(low_arr) - 1):
        if low_arr[i] < low_arr[i-1] and low_arr[i] < low_arr[i+1]:
            idx_list.append(i)
    return idx_list

def get_local_high_idx(high_arr):
    idx_list = []
    for i in range(1, len(high_arr) - 1):
        if high_arr[i] > high_arr[i-1] and high_arr[i] > high_arr[i+1]:
            idx_list.append(i)
    return idx_list

def check_macd_bottom_divergence(low_arr, dif_arr, bar_num, min_gap):
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
    recent_high = high_arr[-bar_num:]
    recent_rsi = rsi_arr[-bar_num:]
    high_idx = get_local_high_idx(recent_high)
    if len(high_idx) < 2:
        return False
    idx1, idx2 = high_idx[-2], high_idx[-1]
    if idx2 - idx1 < min_gap:
        return False
    return recent_high[idx2] > recent_high[idx1] and recent_rsi[idx2] < recent_rsi[idx1]

# ===================== 四要素信号生成 =====================
def get_signal(df, current_price, atr, config):
    cfg = config
    if len(df) < max(100, cfg.get('diver_bar_count', 20) + 10):
        return 'none'

    # 计算指标
    hist, dif, dea = calc_macd_histogram(df)
    rsi_series = calc_rsi(df, 14)
    rsi = rsi_series.iloc[-1]
    vol_ma = calc_volume_avg(df, 20)
    current_vol = df['volume'].iloc[-1]
    vol_ratio = current_vol / vol_ma.iloc[-1] if vol_ma.iloc[-1] > 0 else 0

    # 趋势过滤
    trend_ok = True
    if cfg.get('enable_trend_filter', True):
        ma_trend = calc_ma(df, cfg.get('trend_ma_period', 20)).iloc[-1]
        trend_ok = current_price > ma_trend

    # 背离检测
    bottom_div = False
    if cfg.get('use_macd_divergence', True):
        bottom_div = check_macd_bottom_divergence(
            df['low'].values, dif.values, 
            cfg.get('diver_bar_count', 20), 
            cfg.get('min_wave_gap', 4)
        )
    if cfg.get('use_rsi_divergence', True):
        bottom_div = bottom_div or check_rsi_bottom_divergence(
            df['low'].values, rsi_series.values, 
            cfg.get('diver_bar_count', 20), 
            cfg.get('min_wave_gap', 4)
        )

    # 金叉
    gold_cross = (dif.iloc[-1] > dea.iloc[-1]) and (dif.iloc[-2] <= dea.iloc[-2])

    # 放量
    volume_ok = vol_ratio >= cfg.get('vol_multiple', 1.5)

    # 四要素买入条件
    buy_signal = (trend_ok and 
                  bottom_div and 
                  (rsi <= cfg.get('rsi_oversell', 40)) and 
                  volume_ok and 
                  (gold_cross if cfg.get('require_golden_cross', False) else True))

    # 卖出条件（顶背离 + 超买 + 死叉）
    top_div = False
    if cfg.get('use_macd_divergence', True):
        top_div = check_macd_top_divergence(
            df['high'].values, dif.values, 
            cfg.get('diver_bar_count', 20), 
            cfg.get('min_wave_gap', 4)
        )
    if cfg.get('use_rsi_divergence', True):
        top_div = top_div or check_rsi_top_divergence(
            df['high'].values, rsi_series.values, 
            cfg.get('diver_bar_count', 20), 
            cfg.get('min_wave_gap', 4)
        )

    dead_cross = (dif.iloc[-1] < dea.iloc[-1]) and (dif.iloc[-2] >= dea.iloc[-2])
    sell_signal = top_div and (rsi >= cfg.get('rsi_overbuy', 60)) and dead_cross

    if buy_signal:
        return 'buy'
    elif sell_signal:
        return 'sell'
    else:
        return 'none'

# ===================== ATR 追踪止损（保留增强） =====================
def generate_atr_trailing_stop_signal(df, current_price, current_pos, config):
    if current_pos <= 0 or len(df) < config.get('highest_window', 60):
        return 'none'
    atr = calc_atr(df, config.get('atr_period', 14)).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return 'none'
    recent_high = df['high'].iloc[-config.get('highest_window', 60):].max()
    trailing_stop = recent_high - config.get('atr_mult', 2.0) * atr
    if current_price < trailing_stop:
        return 'atr_trailing_sell'
    return 'none'

print("strategy_core v2.5 (Four-Element + Divergence) loaded")