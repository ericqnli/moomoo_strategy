import pandas as pd
import numpy as np

def calc_macd_histogram(df, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif - dea

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

def calc_ma(df, period=200):
    return df['close'].rolling(window=period).mean()

def calc_volume_avg(df, period=20):
    return df['volume'].rolling(window=period).mean()

def check_protection_layer(df, current_price):
    if len(df) < 200:
        return False
    ma20 = calc_ma(df, 20).iloc[-1]
    ma200 = calc_ma(df, 200).iloc[-1]
    vol_avg = calc_volume_avg(df, 20).iloc[-1]
    current_vol = df['volume'].iloc[-1] if 'volume' in df.columns else 0
    return (current_price > ma200 and ma20 > ma200) and (current_vol > vol_avg * 0.5)

def get_signal(df, current_price, atr, config):
    if len(df) < 100:
        return 'none'

    hist = calc_macd_histogram(df)
    prev_hist = hist.iloc[-2]
    curr_hist = hist.iloc[-1]
    rsi = calc_rsi(df, config.get('rsi_period', 14)).iloc[-1]

    if not check_protection_layer(df, current_price):
        return 'none'

    current_vol = df['volume'].iloc[-1]
    vol_avg_5 = df['volume'].rolling(window=5).mean().iloc[-1]

    if prev_hist <= 0 and curr_hist > 0:
        volume_ok_buy = current_vol > vol_avg_5 * config.get('volume_confirm_mult_buy', 0.8)
        if rsi < config.get('rsi_max_for_buy', 46) and volume_ok_buy:
            return 'buy'

    if prev_hist >= 0 and curr_hist < 0:
        volume_ok_sell = current_vol > vol_avg_5 * config.get('volume_confirm_mult_sell', 1.5)
        if rsi > config.get('rsi_min_for_sell', 68) and volume_ok_sell:
            return 'sell'

    return 'none'

def generate_atr_trailing_stop_signal(df, current_price, current_pos, config):
    if current_pos <= 0 or len(df) < config.get('highest_window', 60):
        return 'none'
    atr = calc_atr(df, config.get('atr_period', 14)).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return 'none'
    recent_high = df['high'].iloc[-config.get('highest_window', 60):].max()
    trailing_stop = recent_high - config.get('atr_mult', 3.0) * atr
    if current_price < trailing_stop:
        return 'atr_trailing_sell'
    return 'none'

print("strategy_core loaded")
