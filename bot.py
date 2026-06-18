import asyncio
import logging
import os
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from data_fetcher import fetch_data
from smc_logic import analyze

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.getenv("TELEGRAM_TOKEN", "8765599414:AAHZBS8xKvycdL97vfkTD9seFnkDvaKmjug")
CHAT_ID = os.getenv("CHAT_ID", "6400507534")
PORT = int(os.getenv("PORT", 8080))
SCAN_INTERVAL = 60 * 15  # every 15 minutes

SYMBOL_LABEL = "XAUUSD"

sent_signals = set()


# --- Health Check Web Server ---
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


# --- Signal Formatting ---
def format_signal(signal):
    direction = signal['direction']
    grade = signal['grade']
    structure = signal['structure']

    emoji = "🟢" if direction == "BUY" else "🔴"
    direction_emoji = "📈" if direction == "BUY" else "📉"

    grade_labels = {
        1: "L1 • Weak",
        2: "L2 • Moderate",
        3: "L3 • Good",
        4: "L4 • Strong",
        5: "L5 • Very Strong"
    }
    grade_label = grade_labels.get(grade, f"L{grade}")

    if direction == "BUY":
        risk = signal['entry'] - signal['sl']
        reward = signal['tp'] - signal['entry']
    else:
        risk = signal['sl'] - signal['entry']
        reward = signal['entry'] - signal['tp']

    rr = round(reward / risk, 2) if risk > 0 else 0

    msg = (
        f"{emoji} *{SYMBOL_LABEL} {direction} SIGNAL* {emoji}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{direction_emoji} *Direction:* {direction}\n"
        f"📊 *Structure:* {structure}\n"
        f"🏷 *Zone Grade:* {grade_label}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎯 *Entry:* `{signal['entry']}`\n"
        f"🛑 *Stop Loss:* `{signal['sl']}`\n"
        f"✅ *Take Profit:* `{signal['tp']}`\n"
        f"⚖️ *RR:* `1:{rr}`\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📦 *OB Zone:* `{signal['ob_bottom']} — {signal['ob_top']}`\n"
        f"📍 *Distance:* `{signal['distance_pct']}% from zone`\n"
        f"🕐 *Time:* {datetime.utcnow().strftime('%H:%M UTC')}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ _OlasmosFX SMC Bot_"
    )
    return msg


def signal_key(signal):
    return f"{signal['direction']}_{signal['entry']}_{signal['grade']}"


# --- Scan Logic ---
async def scan_and_send(bot: Bot):
    logger.info("Running scan...")

    df = fetch_data(symbol="XAU_USD", interval="M15", count=200)
    if df is None:
        logger.warning("No data fetched, skipping scan")
        return

    signals = analyze(df)

    if not signals:
        logger.info("No signals found this scan")
        return

    for signal in signals:
        key = signal_key(signal)
        if key in sent_signals:
            logger.info(f"Signal already sent: {key}")
            continue

        msg = format_signal(signal)
        try:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=msg,
                parse_mode='Markdown'
            )
            sent_signals.add(key)
            logger.info(f"Signal sent: {key}")

            if len(sent_signals) > 200:
                oldest = list(sent_signals)[:100]
                for k in oldest:
                    sent_signals.discard(k)

        except Exception as e:
            logger.error(f"Failed to send signal: {e}")


# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *OlasmosFX SMC Bot is live!*\n\n"
        "📡 Scanning XAUUSD on 15m timeframe\n"
        "🔍 Detecting: BOS, CHoCH, Order Blocks\n"
        "🏷 Grading zones: L1 to L5\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/scan - Force a manual scan now\n"
        "/status - Check bot status",
        parse_mode='Markdown'
    )


async def manual_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Running manual scan...")
    await scan_and_send(context.bot)
    await update.message.reply_text("✅ Scan complete.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ *Bot Status: ONLINE*\n"
        f"📊 Pair: XAUUSD\n"
        f"⏱ Timeframe: 15m\n"
        f"🔄 Scan interval: Every 15 minutes\n"
        f"📦 Signals tracked: {len(sent_signals)}",
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
    await app.bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(scan_loop(app.bot))


# --- Main ---
def main():
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    app = Application.builder()\
        .token(TOKEN)\
        .post_init(post_init)\
        .build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", manual_scan))
    app.add_handler(CommandHandler("status", status))

    logger.info("OlasmosFX SMC Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
