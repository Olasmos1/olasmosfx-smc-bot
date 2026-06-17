import pandas as pd
import numpy as np


def find_swing_points(df, lookback=5):
    """Find swing highs and lows"""
    highs = []
    lows = []

    for i in range(lookback, len(df) - lookback):
        # Swing High
        if df['high'].iloc[i] == df['high'].iloc[i - lookback:i + lookback + 1].max():
            highs.append((i, df['high'].iloc[i]))
        # Swing Low
        if df['low'].iloc[i] == df['low'].iloc[i - lookback:i + lookback + 1].min():
            lows.append((i, df['low'].iloc[i]))

    return highs, lows


def detect_bos_choch(df, lookback=5):
    """
    Detect BOS (Break of Structure) and CHoCH (Change of Character)
    Returns list of structure events
    """
    highs, lows = find_swing_points(df, lookback)
    events = []

    if len(highs) < 2 or len(lows) < 2:
        return events

    # Track last significant swing points
    last_swing_high = highs[-2][1] if len(highs) >= 2 else None
    last_swing_low = lows[-2][1] if len(lows) >= 2 else None
    prev_swing_high = highs[-3][1] if len(highs) >= 3 else None
    prev_swing_low = lows[-3][1] if len(lows) >= 3 else None

    current_close = df['close'].iloc[-1]
    current_high = df['high'].iloc[-1]
    current_low = df['low'].iloc[-1]

    # --- BOS Detection ---
    # Bullish BOS: price breaks above last swing high (trend continuation up)
    if last_swing_high and current_high > last_swing_high:
        if prev_swing_high and last_swing_high > prev_swing_high:
            # Higher highs = uptrend continuation = BOS
            events.append({
                'type': 'BOS',
                'direction': 'BULLISH',
                'level': last_swing_high,
                'index': len(df) - 1
            })
        else:
            # Breaking previous structure against trend = CHoCH
            events.append({
                'type': 'CHoCH',
                'direction': 'BULLISH',
                'level': last_swing_high,
                'index': len(df) - 1
            })

    # Bearish BOS: price breaks below last swing low
    if last_swing_low and current_low < last_swing_low:
        if prev_swing_low and last_swing_low < prev_swing_low:
            events.append({
                'type': 'BOS',
                'direction': 'BEARISH',
                'level': last_swing_low,
                'index': len(df) - 1
            })
        else:
            events.append({
                'type': 'CHoCH',
                'direction': 'BEARISH',
                'level': last_swing_low,
                'index': len(df) - 1
            })

    return events


def find_order_blocks(df, lookback=5):
    """
    Find Order Blocks:
    - Bullish OB: last bearish candle before a bullish BOS
    - Bearish OB: last bullish candle before a bearish BOS
    Returns list of active order blocks
    """
    highs, lows = find_swing_points(df, lookback)
    order_blocks = []

    if len(highs) < 2 or len(lows) < 2:
        return order_blocks

    # Look back through candles to find OBs
    scan_range = min(60, len(df) - 1)  # scan last 60 candles

    for i in range(len(df) - scan_range, len(df) - 3):
        candle = df.iloc[i]
        next_candles = df.iloc[i+1:i+6]

        is_bearish = candle['close'] < candle['open']
        is_bullish = candle['close'] > candle['open']

        # Bullish OB: bearish candle followed by strong bullish move up
        if is_bearish:
            future_high = next_candles['high'].max()
            candle_size = candle['open'] - candle['close']
            if future_high > candle['open'] + (candle_size * 1.5):
                ob = {
                    'type': 'BULLISH_OB',
                    'top': candle['open'],
                    'bottom': candle['close'],
                    'index': i,
                    'candle_size': candle_size,
                    'volume': candle.get('volume', 1),
                    'mitigated': False
                }
                order_blocks.append(ob)

        # Bearish OB: bullish candle followed by strong bearish move down
        if is_bullish:
            future_low = next_candles['low'].min()
            candle_size = candle['close'] - candle['open']
            if future_low < candle['open'] - (candle_size * 1.5):
                ob = {
                    'type': 'BEARISH_OB',
                    'top': candle['close'],
                    'bottom': candle['open'],
                    'index': i,
                    'candle_size': candle_size,
                    'volume': candle.get('volume', 1),
                    'mitigated': False
                }
                order_blocks.append(ob)

    # Filter out mitigated OBs
    current_price = df['close'].iloc[-1]
    active_obs = []

    for ob in order_blocks:
        if ob['type'] == 'BULLISH_OB':
            # Mitigated if price has closed below the OB bottom
            if current_price > ob['bottom'] * 0.998:
                active_obs.append(ob)
        elif ob['type'] == 'BEARISH_OB':
            # Mitigated if price has closed above the OB top
            if current_price < ob['top'] * 1.002:
                active_obs.append(ob)

    return active_obs


def grade_order_block(ob, df):
    """
    Grade OB from L1 to L5 based on:
    - Candle body size (relative to ATR)
    - How decisive the move away was
    - Number of times price has respected the zone
    """
    atr = calculate_atr(df, 14)
    candle_size = ob['candle_size']

    # Score based on candle size vs ATR
    size_ratio = candle_size / atr if atr > 0 else 0

    if size_ratio >= 2.0:
        grade = 5
    elif size_ratio >= 1.5:
        grade = 4
    elif size_ratio >= 1.0:
        grade = 3
    elif size_ratio >= 0.5:
        grade = 2
    else:
        grade = 1

    return grade


def calculate_atr(df, period=14):
    """Calculate Average True Range"""
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]

    return atr if not pd.isna(atr) else (high - low).mean()


def check_retest(ob, df):
    """
    Check if current price is retesting the order block zone
    Returns True if price is inside or touching the OB
    """
    current_price = df['close'].iloc[-1]
    current_low = df['low'].iloc[-1]
    current_high = df['high'].iloc[-1]

    buffer = calculate_atr(df, 14) * 0.3  # small buffer

    if ob['type'] == 'BULLISH_OB':
        # Price dipping into the bullish OB zone
        return current_low <= ob['top'] + buffer and current_price >= ob['bottom'] - buffer

    elif ob['type'] == 'BEARISH_OB':
        # Price pushing up into the bearish OB zone
        return current_high >= ob['bottom'] - buffer and current_price <= ob['top'] + buffer

    return False


def calculate_sl_tp(ob, df, direction):
    """
    Calculate SL and TP based on swing structure + ATR buffer
    """
    atr = calculate_atr(df, 14)
    highs, lows = find_swing_points(df, lookback=5)
    current_price = df['close'].iloc[-1]

    if direction == 'BUY':
        entry = ob['top']  # Enter at top of bullish OB
        sl = ob['bottom'] - (atr * 0.5)  # Below OB bottom + ATR buffer

        # TP = next swing high above entry
        next_highs = [h[1] for h in highs if h[1] > entry]
        if next_highs:
            tp = min(next_highs) - (atr * 0.3)
        else:
            tp = entry + (atr * 3)  # fallback: 3x ATR

    else:  # SELL
        entry = ob['bottom']  # Enter at bottom of bearish OB
        sl = ob['top'] + (atr * 0.5)  # Above OB top + ATR buffer

        # TP = next swing low below entry
        next_lows = [l[1] for l in lows if l[1] < entry]
        if next_lows:
            tp = max(next_lows) + (atr * 0.3)
        else:
            tp = entry - (atr * 3)  # fallback: 3x ATR

    return round(entry, 3), round(sl, 3), round(tp, 3)


def analyze(df):
    """
    Main analysis function.
    Returns list of signals ready to send.
    """
    signals = []

    if len(df) < 30:
        return signals

    # Detect market structure
    structure_events = detect_bos_choch(df)

    # Find active order blocks
    active_obs = find_order_blocks(df)

    for ob in active_obs:
        # Check if price is retesting this OB
        if not check_retest(ob, df):
            continue

        # Grade the OB
        grade = grade_order_block(ob, df)

        # Determine direction
        direction = 'BUY' if ob['type'] == 'BULLISH_OB' else 'SELL'

        # Get latest structure context
        structure_label = 'N/A'
        for event in reversed(structure_events):
            if (event['direction'] == 'BULLISH' and direction == 'BUY') or \
               (event['direction'] == 'BEARISH' and direction == 'SELL'):
                structure_label = event['type']
                break

        # Calculate SL/TP
        entry, sl, tp = calculate_sl_tp(ob, df, direction)

        # Distance from current price to zone
        current_price = df['close'].iloc[-1]
        distance_pct = abs(current_price - entry) / current_price * 100

        signals.append({
            'direction': direction,
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'grade': grade,
            'structure': structure_label,
            'distance_pct': round(distance_pct, 2),
            'ob_top': ob['top'],
            'ob_bottom': ob['bottom']
        })

    return signals
