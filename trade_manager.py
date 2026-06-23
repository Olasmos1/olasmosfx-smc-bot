"""
Trade Manager - ONE trade at a time.
Saves state to file so it survives bot restarts.
"""
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STATE_FILE = "trade_state.json"


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                # Convert opened_at back to datetime
                if data and 'opened_at' in data:
                    data['opened_at'] = datetime.fromisoformat(data['opened_at'])
                return data
        except Exception as e:
            logger.error(f"Failed to load trade state: {e}")
    return None


def _save_state(trade):
    try:
        data = dict(trade)
        # Convert datetime to string for JSON
        if 'opened_at' in data:
            data['opened_at'] = data['opened_at'].isoformat()
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save trade state: {e}")


def _clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


def has_open_trade():
    return _load_state() is not None


def open_trade(signal):
    trade = {
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
    _save_state(trade)
    logger.info(f"Trade opened and saved: {signal['direction']} @ {signal['entry']}")


def check_trade_exit(current_price):
    trade = _load_state()
    if trade is None:
        return None

    direction = trade['direction']
    tp = trade['tp']
    sl = trade['sl']
    result = None

    if direction == 'BUY':
        if current_price >= tp:
            result = 'TP'
        elif current_price <= sl:
            result = 'SL'
    else:
        if current_price <= tp:
            result = 'TP'
        elif current_price >= sl:
            result = 'SL'

    if result:
        closed = dict(trade)
        closed['result'] = result
        closed['closed_price'] = current_price
        closed['closed_at'] = datetime.now(timezone.utc)
        _clear_state()
        logger.info(f"Trade closed: {result} at {current_price}")
        return closed

    return None


def get_active_trade():
    return _load_state()
