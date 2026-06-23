import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Bot

from data_fetcher import fetch_data
from smc_logic import analyze, get_market_bias
from trade_manager import has_open_trade, open_trade, check_trade_exit, get_active_trade

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "8765599414:AAHZBS8xKvycdL97vfkTD9seFnkDvaKmjug")
CHAT_ID = os.getenv("CHAT_ID", "6400507534")
PORT = int(os.getenv("PORT", 8080))
SCAN_INTERVAL = 60 * 15
SYMBOL_LABEL = "XAUUSD"

last_daily_day = -1


# --- Health Server ---
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OlasmosFX SMC Bot is running!')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def run_health_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthHandler)
    logger.info(f"Health server running on port {PORT}")
    server.serve_forever()


# --- Formatting ---
def format_signal(signal):
    direction = signal['direction']
    grade = signal['grade']
    emoji = "🟢" if direction == "BUY" else "🔴"
    direction_emoji = "📈" if direction == "BUY" else "📉"
    grade_labels = {
        1: "L1 • Weak", 2: "L2 • Moderate", 3: "L3 • Good",
        4: "L4 • Strong", 5: "L5 • Very Strong"
    }
    grade_label = grade_labels.get(grade, f"L{grade}")

    if direction == "BUY":
        risk = signal['entry'] - signal['sl']
        reward = signal['tp'] - signal['entry']
    else:
        risk = signal['sl'] - signal['entry']
        reward = signal['entry'] - signal['tp']

    rr = round(reward / risk, 2) if risk > 0 else 0

    return (
        f"{emoji} *{SYMBOL_LABEL} {direction} SIGNAL* {emoji}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{direction_emoji} *Direction:* {direction}\n"
        f"📊 *Structure:* {signal['structure']}\n"
        f"📉 *Bias:* {signal['bias']}\n"
        f"🏷 *Zone Grade:* {grade_label}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* `{signal['entry']}`\n"
        f"🛑 *Stop Loss:* `{signal['sl']}`\n"
        f"✅ *Take Profit:* `{signal['tp']}`\n"
        f"⚖️ *RR:* `1:{rr}`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📦 *OB Zone:* `{signal['ob_bottom']} — {signal['ob_top']}`\n"
        f"🕐 *Time:* {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ _OlasmosFX SMC Bot_"
    )


def format_exit(trade):
    result = trade['result']
    emoji = "💰" if result == 'TP' else "🛑"
    color = "🟢" if result == 'TP' else "🔴"
    result_text = "TAKE PROFIT HIT ✅" if result == 'TP' else "STOP LOSS HIT ❌"

    duration = datetime.now(timezone.utc) - trade['opened_at']
    hours, rem = divmod(int(duration.total_seconds()), 3600)
    mins = rem // 60
    dur_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"

    return (
        f"{emoji} *{SYMBOL_LABEL} {result_text}* {emoji}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{color} *Result:* {result}\n"
        f"📊 *Direction was:* {trade['direction']}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* `{trade['entry']}`\n"
        f"🛑 *SL:* `{trade['sl']}`\n"
        f"✅ *TP:* `{trade['tp']}`\n"
        f"📍 *Closed at:* `{trade['closed_price']}`\n"
        f"⏱ *Duration:* {dur_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ _OlasmosFX SMC Bot_"
    )


# --- Daily Update ---
async def maybe_send_daily_update(bot: Bot, bias: str):
    global last_daily_day
    now = datetime.now(timezone.utc)
    if now.hour == 7 and last_daily_day != now.day:
        last_daily_day = now.day
        trade = get_active_trade()
        trade_info = (
            f"📂 Open trade: {trade['direction']} @ {trade['entry']}"
            if trade else "📭 No open trade"
        )
        bias_emoji = "📈" if bias == "BULLISH" else "📉" if bias == "BEARISH" else "➡️"
        msg = (
            f"🌅 *OlasmosFX Daily Update*\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📅 {now.strftime('%A, %d %B %Y')}\n"
            f"{bias_emoji} *Market Bias:* {bias}\n"
            f"{trade_info}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"✅ Bot is active and scanning\n"
            f"⚡ _OlasmosFX SMC Bot_"
        )
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        logger.info("Daily update sent")


# --- Main Scan ---
async def scan_and_send(bot: Bot):
    logger.info("Running scan...")

    df = fetch_data(symbol="XAU_USD", interval="M15", count=200)
    if df is None:
        logger.warning("No data fetched")
        return

    current_price = df['close'].iloc[-1]
    bias = get_market_bias(df)

    # Daily update
    try:
        await maybe_send_daily_update(bot, bias)
    except Exception as e:
        logger.error(f"Daily update error: {e}")

    # Step 1: Check open trade
    if has_open_trade():
        closed = check_trade_exit(current_price)
        if closed:
            try:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=format_exit(closed),
                    parse_mode='Markdown'
                )
                logger.info(f"Exit alert sent: {closed['result']}")
            except Exception as e:
                logger.error(f"Exit alert error: {e}")
        else:
            logger.info("Trade still open — skipping signal scan")
            return

    # Step 2: Scan for new signal
    signals = analyze(df)
    if not signals:
        logger.info(f"No signal — bias is {bias}")
        return

    signal = signals[0]
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=format_signal(signal),
            parse_mode='Markdown'
        )
        open_trade(signal)
        logger.info(f"Signal sent: {signal['direction']} @ {signal['entry']}")
    except Exception as e:
        logger.error(f"Signal send error: {e}")


# --- Scan Loop ---
async def run_bot():
    bot = Bot(token=TOKEN)

    # Clear any existing webhook first
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook cleared — running in push-only mode")
    except Exception as e:
        logger.error(f"Webhook clear error: {e}")

    # Send startup message
    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=(
                "✅ *OlasmosFX SMC Bot Started*\n"
                "📡 Scanning XAUUSD every 15 minutes\n"
                "⚡ _OlasmosFX SMC Bot_"
            ),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Startup message error: {e}")

    # Main scan loop — no polling, just push
    while True:
        try:
            await scan_and_send(bot)
        except Exception as e:
            logger.error(f"Scan error: {e}")
        await asyncio.sleep(SCAN_INTERVAL)


def main():
    # Start health server
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    logger.info("OlasmosFX SMC Bot starting in push-only mode...")
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
