"""
Trade Manager - tracks open trades, prevents duplicates,
monitors TP/SL hits and sends alerts.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Active trades: key -> trade dict
active_trades = {}


def trade_key(signal):
    """Unique key per trade based on direction + entry zone"""
    entry = round(signal['entry'], 1)  # round to 1dp to group nearby entries
    return f"{signal['direction']}_{entry}"


def is_duplicate(signal):
    """Return True if this signal is already an active trade"""
    key = trade_key(signal)
    return key in active_trades


def add_trade(signal):
    """Register a new trade"""
    key = trade_key(signal)
    active_trades[key] = {
        'direction': signal['direction'],
        'entry': signal['entry'],
        'sl': signal['sl'],
        'tp': signal['tp'],
        'grade': signal['grade'],
        'structure': signal['structure'],
        'ob_top': signal['ob_top'],
        'ob_bottom': signal['ob_bottom'],
        'opened_at': datetime.now(timezone.utc),
        'key': key
    }
    logger.info(f"Trade added: {key}")


def check_trade_exits(current_price):
    """
    Check all active trades against current price.
    Returns list of closed trades with result (TP/SL hit).
    """
    closed = []

    for key, trade in list(active_trades.items()):
        direction = trade['direction']
        tp = trade['tp']
        sl = trade['sl']
        result = None

        if direction == 'BUY':
            if current_price >= tp:
                result = 'TP'
            elif current_price <= sl:
                result = 'SL'
        else:  # SELL
            if current_price <= tp:
                result = 'TP'
            elif current_price >= sl:
                result = 'SL'

        if result:
            trade['result'] = result
            trade['closed_price'] = current_price
            trade['closed_at'] = datetime.now(timezone.utc)
            closed.append(trade)
            del active_trades[key]
            logger.info(f"Trade closed: {key} → {result} at {current_price}")

    return closed


def active_trade_count():
    return len(active_trades)


def get_active_trades():
    return list(active_trades.values())
