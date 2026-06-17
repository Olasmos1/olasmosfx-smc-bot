import yfinance as yf
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def fetch_data(symbol="XAUUSD=X", interval="15m", period="5d"):
    """
    Fetch OHLCV data from Yahoo Finance.
    Returns cleaned DataFrame or None on failure.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(interval=interval, period=period)

        if df.empty:
            logger.warning(f"No data returned for {symbol}")
            return None

        # Normalize column names
        df.columns = [c.lower() for c in df.columns]
        df = df[['open', 'high', 'low', 'close', 'volume']]
        df.dropna(inplace=True)

        logger.info(f"Fetched {len(df)} candles for {symbol}")
        return df

    except Exception as e:
        logger.error(f"Error fetching data for {symbol}: {e}")
        return None
