import pandas as pd
import numpy as np


def calculate_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return atr if not pd.isna(atr) else (high - low).mean()


def find_swing_points(df, lookback=5):
    highs = []
    lows = []
    for i in range(lookback, len(df) - lookback):
        if df['high'].iloc[i] == df['high'].iloc[i - lookback:i + lookback + 1].max():
            highs.append((i, df['high'].iloc[i]))
        if df['low'].iloc[i] == df['low'].iloc[i - lookback:i + lookback + 1].min():
            lows.append((i, df['low'].iloc[i]))
    return highs, lows


def get_market_bias(df):
    """
    Determine overall market bias using:
    - EMA 50 vs EMA 200 (trend filter)
    - Recent swing structure (higher highs/lows = bullish, lower highs/lows = bearish)
    Returns: 'BULLISH', 'BEARISH', or 'NEUTRAL'
    """
    close = df['close']

    ema50 = close.ewm(span=50).mean().iloc[-1]
    ema200 = close.ewm(span=200).mean().iloc[-1]
    current_price = close.iloc[-1]

    # EMA trend
    if current_price > ema50 and ema50 > ema200:
        ema_bias = 'BULLISH'
    elif current_price < ema50 and ema50 < ema200:
        ema_bias = 'BEARISH'
    else:
        ema_bias = 'NEUTRAL'

    # Swing structure
    highs, lows = find_swing_points(df, lookback=5)
    structure_bias = 'NEUTRAL'

    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]  # higher high
        hl = lows[-1][1] > lows[-2][1]    # higher low
        lh = highs[-1][1] < highs[-2][1]  # lower high
        ll = lows[-1][1] < lows[-2][1]    # lower low

        if hh and hl:
            structure_bias = 'BULLISH'
        elif lh and ll:
            structure_bias = 'BEARISH'

    # Both must agree for a valid bias
    if ema_bias == 'BULLISH' and structure_bias == 'BULLISH':
        return 'BULLISH'
    elif ema_bias == 'BEARISH' and structure_bias == 'BEARISH':
        return 'BEARISH'
    elif ema_bias == structure_bias:
        return ema_bias
    else:
        return 'NEUTRAL'


def detect_structure(df):
    highs, lows = find_swing_points(df, lookback=5)
    events = []
    if len(highs) < 3 or len(lows) < 3:
        return events

    last_high = highs[-2][1]
    last_low = lows[-2][1]
    prev_high = highs[-3][1]
    prev_low = lows[-3][1]

    current_high = df['high'].iloc[-1]
    current_low = df['low'].iloc[-1]

    if current_high > last_high:
        event_type = 'BOS' if last_high > prev_high else 'CHoCH'
        events.append({'type': event_type, 'direction': 'BULLISH'})

    if current_low < last_low:
        event_type = 'BOS' if last_low < prev_low else 'CHoCH'
        events.append({'type': event_type, 'direction': 'BEARISH'})

    return events


def find_best_order_block(df, direction):
    """
    Find the single best OB that matches the required direction.
    - BULLISH bias → look for BULLISH OB (demand zone) only
    - BEARISH bias → look for BEARISH OB (supply zone) only
    """
    atr = calculate_atr(df, 14)
    scan_range = min(50, len(df) - 5)
    candidates = []

    for i in range(len(df) - scan_range, len(df) - 3):
        candle = df.iloc[i]
        next_candles = df.iloc[i+1:i+6]

        if direction == 'BUY':
            # Bearish candle that caused a strong bullish move
            if candle['close'] < candle['open']:
                candle_size = candle['open'] - candle['close']
                if candle_size < atr * 0.3:
                    continue
                future_high = next_candles['high'].max()
                if future_high > candle['open'] + (candle_size * 2.0):
                    candidates.append({
                        'type': 'BULLISH_OB',
                        'top': candle['open'],
                        'bottom': candle['close'],
                        'index': i,
                        'candle_size': candle_size,
                    })

        elif direction == 'SELL':
            # Bullish candle that caused a strong bearish move
            if candle['close'] > candle['open']:
                candle_size = candle['close'] - candle['open']
                if candle_size < atr * 0.3:
                    continue
                future_low = next_candles['low'].min()
                if future_low < candle['open'] - (candle_size * 2.0):
                    candidates.append({
                        'type': 'BEARISH_OB',
                        'top': candle['close'],
                        'bottom': candle['open'],
                        'index': i,
                        'candle_size': candle_size,
                    })

    if not candidates:
        return None

    # Return the most recent valid OB
    return candidates[-1]


def check_retest(ob, df):
    """Price must be actively touching the OB zone"""
    current_price = df['close'].iloc[-1]
    current_low = df['low'].iloc[-1]
    current_high = df['high'].iloc[-1]
    atr = calculate_atr(df, 14)
    buffer = atr * 0.2

    if ob['type'] == 'BULLISH_OB':
        return (current_low <= ob['top'] + buffer and
                current_price >= ob['bottom'] - buffer and
                current_price <= ob['top'] + (atr * 0.5))

    elif ob['type'] == 'BEARISH_OB':
        return (current_high >= ob['bottom'] - buffer and
                current_price <= ob['top'] + buffer and
                current_price >= ob['bottom'] - (atr * 0.5))

    return False


def grade_order_block(ob, df):
    atr = calculate_atr(df, 14)
    size_ratio = ob['candle_size'] / atr if atr > 0 else 0
    if size_ratio >= 2.0:
        return 5
    elif size_ratio >= 1.5:
        return 4
    elif size_ratio >= 1.0:
        return 3
    elif size_ratio >= 0.5:
        return 2
    return 1


def calculate_sl_tp(ob, df, direction):
    atr = calculate_atr(df, 14)
    highs, lows = find_swing_points(df, lookback=5)

    if direction == 'BUY':
        entry = ob['top']
        sl = ob['bottom'] - (atr * 0.5)
        next_highs = [h[1] for h in highs if h[1] > entry]
        tp = min(next_highs) - (atr * 0.3) if next_highs else entry + (atr * 3)
    else:
        entry = ob['bottom']
        sl = ob['top'] + (atr * 0.5)
        next_lows = [l[1] for l in lows if l[1] < entry]
        tp = max(next_lows) + (atr * 0.3) if next_lows else entry - (atr * 3)

    return round(entry, 3), round(sl, 3), round(tp, 3)


def analyze(df):
    """
    Main analysis:
    1. Get market bias (BULLISH/BEARISH/NEUTRAL)
    2. Only look for OB in bias direction
    3. Check if price is retesting that OB
    4. Return max ONE signal
    """
    if len(df) < 50:
        return []

    # Step 1: Market bias
    bias = get_market_bias(df)
    if bias == 'NEUTRAL':
        return []  # No trade in choppy market

    direction = 'BUY' if bias == 'BULLISH' else 'SELL'

    # Step 2: Find best OB in bias direction only
    ob = find_best_order_block(df, direction)
    if ob is None:
        return []

    # Step 3: Is price retesting this OB right now?
    if not check_retest(ob, df):
        return []

    # Step 4: Grade and build signal
    grade = grade_order_block(ob, df)
    structure_events = detect_structure(df)
    structure_label = 'N/A'
    for event in reversed(structure_events):
        if ((event['direction'] == 'BULLISH' and direction == 'BUY') or
                (event['direction'] == 'BEARISH' and direction == 'SELL')):
            structure_label = event['type']
            break

    entry, sl, tp = calculate_sl_tp(ob, df, direction)
    current_price = df['close'].iloc[-1]
    distance_pct = round(abs(current_price - entry) / current_price * 100, 2)

    return [{
        'direction': direction,
        'bias': bias,
        'entry': entry,
        'sl': sl,
        'tp': tp,
        'grade': grade,
        'structure': structure_label,
        'distance_pct': distance_pct,
        'ob_top': ob['top'],
        'ob_bottom': ob['bottom']
    }]
