from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import os
import yfinance as yf

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Stock Analysis Bot\n\n"
        "Bot ishga tushdi ✅\n\n"
        "Ticker tekshirish uchun:\n"
        "/ticker BIYA"
    )


async def ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Foydalanish:\n/ticker BIYA"
        )
        return

    symbol = context.args[0].upper()

    try:
        stock = yf.Ticker(symbol)
        info = stock.info

        msg = f"""
📊 {symbol}

💵 Price: {info.get('currentPrice', 'N/A')}
📈 Change: {info.get('regularMarketChangePercent', 'N/A')}%

💰 Market Cap: {info.get('marketCap', 'N/A')}
🏦 Float: {info.get('floatShares', 'N/A')}

📊 Avg Volume: {info.get('averageVolume', 'N/A')}
🔥 Current Volume: {info.get('volume', 'N/A')}

🏢 Sector: {info.get('sector', 'N/A')}
🏭 Industry: {info.get('industry', 'N/A')}
🌍 Country: {info.get('country', 'N/A')}
👥 Employees: {info.get('fullTimeEmployees', 'N/A')}
🏢 Exchange: {info.get('exchange', 'N/A')}
"""

        await update.message.reply_text(msg)

    except Exception as e:
        await update.message.reply_text(f"❌ Xatolik:\n{e}")


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ticker", ticker))

    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
