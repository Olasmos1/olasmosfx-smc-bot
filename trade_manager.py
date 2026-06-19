"""
Trade Manager - ONE trade at a time.
No new signal until current trade hits TP or SL.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Only one active trade allowed at a time
active_trade = None


def has_open_trade():
    return active_trade is not None


def open_trade(signal):
    global active_trade
    active_trade = {
        'direction': signal['direction'],
        'entry': signal['entry'],
        'sl': signal['sl'],
        'tp': signal['tp'],
        'grade': signal['grade'],
        'structure': signal['structure'],
        'ob_top': signal['ob_top'],
        'ob_bottom': signal['ob_bottom'],
        'opened_at': datetime.now(timezone.utc),
    }
    logger.info(f"Trade opened: {signal['direction']} @ {signal['entry']}")


def check_trade_exit(current_price):
    """
    Check if active trade hit TP or SL.
    Returns closed trade dict with result, or None.
    """
    global active_trade
    if active_trade is None:
        return None

    direction = active_trade['direction']
    tp = active_trade['tp']
    sl = active_trade['sl']
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
        closed = dict(active_trade)
        closed['result'] = result
        closed['closed_price'] = current_price
        closed['closed_at'] = datetime.now(timezone.utc)
        active_trade = None
        logger.info(f"Trade closed: {result} at {current_price}")
        return closed

    return None


def get_active_trade():
    return active_trade
