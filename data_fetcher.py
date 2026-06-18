import requests
import pandas as pd
import logging
import os

logger = logging.getLogger(__name__)

OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_BASE_URL = "https://api-fxpractice.oanda.com"  # Demo account



def fetch_data(symbol="XAU_USD", interval="M15", count=200):
    """
    Fetch OHLCV data from Oanda REST API.
    Returns cleaned DataFrame or None on failure.
    """
    try:
        headers = {
            "Authorization": f"Bearer {OANDA_API_KEY}",
            "Content-Type": "application/json"
        }

        params = {
            "granularity": interval,  # M15 = 15 minute candles
            "count": count,
            "price": "M"  # Midpoint candles
        }

        url = f"{OANDA_BASE_URL}/v3/instruments/{symbol}/candles"
        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code != 200:
            logger.error(f"Oanda API error {response.status_code}: {response.text}")
            return None

        data = response.json()
        candles = data.get("candles", [])

        if not candles:
            logger.warning(f"No candles returned for {symbol}")
            return None

        rows = []
        for c in candles:
            if not c.get("complete", True):
                continue  # skip incomplete current candle
            mid = c["mid"]
            rows.append({
                "time": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "volume": int(c.get("volume", 0))
            })

        if not rows:
            logger.warning("No complete candles found")
            return None

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"])
        df.set_index("time", inplace=True)
        df.dropna(inplace=True)

        logger.info(f"Fetched {len(df)} candles for {symbol} from Oanda")
        return df

    except Exception as e:
        logger.error(f"Error fetching data from Oanda: {e}")
        return None
