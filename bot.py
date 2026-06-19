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
from trade_manager import is_duplicate, add_trade, check_trade_exits, get_active_trades, active_trade_count

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


# --- Health Check Server ---
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
        f"🕐 *Time:* {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ _OlasmosFX SMC Bot_"
    )
    return msg


def format_exit_alert(trade):
    result = trade['result']
    direction = trade['direction']

    if result == 'TP':
        emoji = "💰"
        result_text = "TAKE PROFIT HIT"
        color = "🟢"
    else:
        emoji = "🛑"
        result_text = "STOP LOSS HIT"
        color = "🔴"

    duration = datetime.now(timezone.utc) - trade['opened_at']
    hours, remainder = divmod(int(duration.total_seconds()), 3600)
    minutes = remainder // 60
    duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

    msg = (
        f"{emoji} *{SYMBOL_LABEL} {result_text}* {emoji}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{color} *Result:* {result}\n"
        f"📊 *Direction was:* {direction}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎯 *Entry was:* `{trade['entry']}`\n"
        f"🛑 *Stop Loss:* `{trade['sl']}`\n"
        f"✅ *Take Profit:* `{trade['tp']}`\n"
        f"📍 *Closed at:* `{trade['closed_price']}`\n"
        f"⏱ *Duration:* {duration_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ _OlasmosFX SMC Bot_"
    )
    return msg


# --- Scan Logic ---
async def scan_and_send(bot: Bot):
    logger.info("Running scan...")

    df = fetch_data(symbol="XAU_USD", interval="M15", count=200)
    if df is None:
        logger.warning("No data fetched, skipping scan")
        return

    current_price = df['close'].iloc[-1]

    # 1. Check if any open trades hit TP or SL
    closed_trades = check_trade_exits(current_price)
    for trade in closed_trades:
        msg = format_exit_alert(trade)
        try:
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
            logger.info(f"Exit alert sent: {trade['key']} → {trade['result']}")
        except Exception as e:
            logger.error(f"Failed to send exit alert: {e}")

    # 2. Scan for new signals
    signals = analyze(df)

    if not signals:
        logger.info("No new signals found")
        return

    for signal in signals:
        # Skip if already an active trade
        if is_duplicate(signal):
            logger.info(f"Duplicate skipped: {signal['direction']} @ {signal['entry']}")
            continue

        msg = format_signal(signal)
        try:
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
            add_trade(signal)
            logger.info(f"Signal sent and trade opened: {signal['direction']} @ {signal['entry']}")
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
        "/scan - Force a manual scan\n"
        "/trades - View open trades\n"
        "/status - Bot status",
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
        f"📂 Open trades: {active_trade_count()}",
        parse_mode='Markdown'
    )


async def trades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = get_active_trades()
    if not trades:
        await update.message.reply_text("📭 No open trades currently.")
        return

    msg = "📂 *Open Trades:*\n━━━━━━━━━━━━━━━━\n"
    for t in trades:
        opened = t['opened_at'].strftime('%H:%M UTC')
        msg += (
            f"{'📈' if t['direction'] == 'BUY' else '📉'} *{t['direction']}* @ `{t['entry']}`\n"
            f"   SL: `{t['sl']}` | TP: `{t['tp']}`\n"
            f"   Opened: {opened}\n\n"
        )
    await update.message.reply_text(msg, parse_mode='Markdown')


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
    app.add_handler(CommandHandler("trades", trades_cmd))

    logger.info("OlasmosFX SMC Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
