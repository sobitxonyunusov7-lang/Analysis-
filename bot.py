async def ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Foydalanish:\n/ticker BIYA")
        return

    symbol = context.args[0].upper()

    try:
        stock = yf.Ticker(symbol)
        info = stock.info

        msg = f"""📊 {symbol}

💵 Price: {info.get('currentPrice', 'N/A')}
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
        await update.message.reply_text(f"Xatolik:\n{e}")
