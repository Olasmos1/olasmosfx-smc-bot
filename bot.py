import asyncio
import logging
import os
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from data_fetcher import fetch_data
from smc_logic import analyze
from trade_manager import has_open_trade, open_trade, check_trade_exit, get_active_trade

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "8765599414:AAHZBS8xKvycdL97vfkTD9seFnkDvaKmjug")
CHAT_ID = os.getenv("CHAT_ID", "6400507534")
PORT = int(os.getenv("PORT", 8080))
RENDER_URL = os.getenv("RENDER_URL", "https://olasmosfx-smc-bot.onrender.com")
SCAN_INTERVAL = 60 * 15
SYMBOL_LABEL = "XAUUSD"


# --- Health + Webhook Server ---
class BotHandler(BaseHTTPRequestHandler):
    app = None  # will be set after app is built

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
    server = HTTPServer(('0.0.0.0', PORT), BotHandler)
    logger.info(f"Health server running on port {PORT}")
    server.serve_forever()


# --- Formatting ---
def format_signal(signal):
    direction = signal['direction']
    grade = signal['grade']
    emoji = "🟢" if direction == "BUY" else "🔴"
    direction_emoji = "📈" if direction == "BUY" else "📉"
    grade_labels = {1: "L1 • Weak", 2: "L2 • Moderate", 3: "L3 • Good",
                    4: "L4 • Strong", 5: "L5 • Very Strong"}
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


# --- Main Scan ---
async def scan_and_send(bot: Bot):
    logger.info("Running scan...")

    df = fetch_data(symbol="XAU_USD", interval="M15", count=200)
    if df is None:
        logger.warning("No data fetched")
        return

    current_price = df['close'].iloc[-1]

    # Step 1: Check if open trade hit TP/SL
    if has_open_trade():
        closed = check_trade_exit(current_price)
        if closed:
            msg = format_exit(closed)
            try:
                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
                logger.info(f"Exit alert sent: {closed['result']}")
            except Exception as e:
                logger.error(f"Failed to send exit alert: {e}")
        else:
            logger.info("Trade still open — skipping new signal scan")
            return

    # Step 2: No open trade — scan for new signal
    signals = analyze(df)

    if not signals:
        logger.info("No signal found this scan")
        return

    signal = signals[0]
    msg = format_signal(signal)

    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
        open_trade(signal)
        logger.info(f"Signal sent: {signal['direction']} @ {signal['entry']}")
    except Exception as e:
        logger.error(f"Failed to send signal: {e}")


# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *OlasmosFX SMC Bot is live!*\n\n"
        "📡 Scanning XAUUSD on 15m timeframe\n"
        "🔍 One trade at a time — bias-filtered\n"
        "🏷 Grading zones: L1 to L5\n\n"
        "/start — This message\n"
        "/scan — Force manual scan\n"
        "/trade — View open trade\n"
        "/status — Bot status",
        parse_mode='Markdown'
    )


async def manual_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Running manual scan...")
    await scan_and_send(context.bot)
    await update.message.reply_text("✅ Done.")


async def trade_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade = get_active_trade()
    if not trade:
        await update.message.reply_text("📭 No open trade currently.")
        return
    opened = trade['opened_at'].strftime('%H:%M UTC')
    emoji = "📈" if trade['direction'] == 'BUY' else "📉"
    await update.message.reply_text(
        f"📂 *Open Trade:*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{emoji} *{trade['direction']}* @ `{trade['entry']}`\n"
        f"🛑 SL: `{trade['sl']}`\n"
        f"✅ TP: `{trade['tp']}`\n"
        f"🕐 Opened: {opened}",
        parse_mode='Markdown'
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trade_status = "1 open trade" if has_open_trade() else "No open trades"
    await update.message.reply_text(
        f"✅ *Bot Status: ONLINE*\n"
        f"📊 Pair: XAUUSD | TF: 15m\n"
        f"📂 Trades: {trade_status}",
        parse_mode='Markdown'
    )


# --- Scan Loop ---
async def scan_loop(bot: Bot):
    await asyncio.sleep(15)
    while True:
        try:
            await scan_and_send(bot)
        except Exception as e:
            logger.error(f"Scan loop error: {e}")
        await asyncio.sleep(SCAN_INTERVAL)


async def post_init(app: Application):
    # Delete any existing webhook and clear pending updates
    await app.bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook cleared")
    asyncio.create_task(scan_loop(app.bot))


def main():
    # Start health server in background
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    app = Application.builder()\
        .token(TOKEN)\
        .post_init(post_init)\
        .build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", manual_scan))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("trade", trade_cmd))

    logger.info("OlasmosFX SMC Bot starting...")

    # Use polling with long timeout to prevent conflicts
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        pool_timeout=30,
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        close_loop=False
    )


if __name__ == "__main__":
    main()
