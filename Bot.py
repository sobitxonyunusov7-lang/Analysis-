from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import os

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
        await update.message.reply_text("Foydalanish:\n/ticker BIYA")
        return

    symbol = context.args[0].upper()

    await update.message.reply_text(
        f"🔍 {symbol} qidirilmoqda...\n\n"
        "Bu yerga keyingi bosqichda:\n"
        "💵 Price\n"
        "🏦 Float\n"
        "💰 Market Cap\n"
        "📰 News\n"
        "🔄 Reverse Split\n"
        "⚠️ Delisting\n"
        "va boshqa ma'lumotlar chiqadi."
    )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ticker", ticker))

    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
