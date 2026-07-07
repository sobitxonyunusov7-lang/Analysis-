from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import os
import yfinance as yf
from datetime import datetime

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Stock Analysis Bot\n\n"
        "Ticker yuborish:\n"
        "#BIYA\n"
        "yoki\n"
        "/ticker BIYA"
    )


async def ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Misol:\n#BIYA\nyoki\n/ticker BIYA")
        return

    symbol = context.args[0].upper()

    try:
        stock = yf.Ticker(symbol)
        info = stock.info

        avg_volume = info.get("averageVolume", 0)
        current_volume = info.get("volume", 0)

        if avg_volume:
            rvol = round(current_volume / avg_volume, 2)
        else:
            rvol = "N/A"

        earnings = info.get("earningsTimestamp")
        if earnings:
            earnings = datetime.fromtimestamp(earnings).strftime("%Y-%m-%d")
        else:
            earnings = "N/A"

        msg = f"""
📊 {symbol}

💵 Price: {info.get('currentPrice','N/A')}
📈 Change: {info.get('regularMarketChangePercent','N/A')}%

💰 Market Cap: {info.get('marketCap','N/A')}
🏦 Float: {info.get('floatShares','N/A')}

📊 Avg Volume: {avg_volume}
🔥 Current Volume: {current_volume}
📈 Relative Volume: {rvol}

📅 Earnings: {earnings}

🏢 Sector: {info.get('sector','N/A')}
🏭 Industry: {info.get('industry','N/A')}
🌍 Country: {info.get('country','N/A')}
👥 Employees: {info.get('fullTimeEmployees','N/A')}
🏢 Exchange: {info.get('exchange','N/A')}
"""

        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def hashtag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()

    if text.startswith("#"):
        context.args = [text[1:]]
        await ticker(update, context)


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ticker", ticker))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, hashtag))

    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
