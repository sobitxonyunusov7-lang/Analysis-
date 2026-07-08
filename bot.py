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

# Xavf so'zlari — yangiliklar sarlavhalarida qidiriladi
RISK_KEYWORDS = {
    "delisting": ["delisting", "delist", "noncompliance", "non-compliance", "minimum bid"],
    "reverse_split": ["reverse split", "reverse stock split"],
    "split": [" stock split", "forward split"],
    "offering": ["offering", "registered direct", "private placement", "atm offering", "shelf registration"],
    "dilution": ["dilution", "dilutive", "shares outstanding increase"],
}


def fmt_num(value, suffix=""):
    """Katta sonlarni chiroyli formatlash (masalan 7277056 -> 7.28M)"""
    if value in (None, "N/A"):
        return "N/A"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B{suffix}"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M{suffix}"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}K{suffix}"
    return f"{value}{suffix}"


def fmt_pct(value):
    if value in (None, "N/A"):
        return "N/A"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def get_news_flags_and_events(stock):
    """Yangiliklarni skanerlab, xavf bayroqlari va so'nggi voqealar ro'yxatini qaytaradi"""
    flags = {key: False for key in RISK_KEYWORDS}
    events = []

    try:
        news_items = stock.news or []
    except Exception:
        news_items = []

    for item in news_items[:15]:
        # yfinance versiyalariga qarab strukturasi farq qilishi mumkin
        content = item.get("content", item)
        title = (content.get("title") or "").strip()
        if not title:
            continue

        title_lower = title.lower()
        for key, keywords in RISK_KEYWORDS.items():
            if any(kw in title_lower for kw in keywords):
                flags[key] = True

        if len(events) < 5:
            events.append(title)

    return flags, events


def get_sec_filings(stock):
    """Eng so'nggi SEC filinglarni turlari bo'yicha qaytaradi"""
    try:
        filings = stock.sec_filings
    except Exception:
        filings = None

    if not filings:
        return "Topilmadi"

    lines = []
    for f in filings[:8]:
        ftype = f.get("type", "N/A")
        date = f.get("date", "")
        lines.append(f"• {ftype} ({date})")

    return "\n".join(lines) if lines else "Topilmadi"


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
    await update.message.reply_text(f"⏳ {symbol} tekshirilyapti...")

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

        # --- Xavf bayroqlari va yangiliklar ---
        flags, events = get_news_flags_and_events(stock)

        def flag_icon(key):
            return "🔴 Bor" if flags[key] else "🟢 Yo'q"

        # Dilution risk — bir nechta belgiga qarab baholash
        dilution_score = sum([
            flags["offering"], flags["dilution"], flags["reverse_split"], flags["delisting"]
        ])
        if dilution_score >= 2:
            dilution_risk = "🔴 Yuqori"
        elif dilution_score == 1:
            dilution_risk = "🟡 O'rta"
        else:
            dilution_risk = "🟢 Past"

        # --- Umumiy risk ---
        market_cap = info.get("marketCap") or 0
        if dilution_score >= 2 or (market_cap and market_cap < 50_000_000):
            overall_risk = "🔴 Yuqori"
        elif dilution_score == 1 or (market_cap and market_cap < 300_000_000):
            overall_risk = "🟡 O'rta"
        else:
            overall_risk = "🟢 Past"

        events_text = "\n".join(f"• {e}" for e in events) if events else "• Ma'lumot topilmadi"
        sec_filings_text = get_sec_filings(stock)

        msg = f"""📊 {symbol}

💵 Price: {info.get('currentPrice', 'N/A')}
📈 Change: {info.get('regularMarketChangePercent', 'N/A')}%
💰 Market Cap: {fmt_num(info.get('marketCap'))}
🏦 Float: {fmt_num(info.get('floatShares'))}
📊 Avg Volume: {fmt_num(avg_volume)}
🔥 Current Volume: {fmt_num(current_volume)}
📈 Relative Volume: {rvol}

⚠️ Delisting: {flag_icon('delisting')}
🔄 Reverse Split: {flag_icon('reverse_split')}
✂️ Split: {flag_icon('split')}
💵 Offering: {flag_icon('offering')}
📉 Dilution Risk: {dilution_risk}

📅 Earnings: {earnings}

🏢 Sector: {info.get('sector', 'N/A')}
🏭 Industry: {info.get('industry', 'N/A')}
🌍 Country: {info.get('country', 'N/A')}
👥 Employees: {info.get('fullTimeEmployees', 'N/A')}
🏢 Exchange: {info.get('exchange', 'N/A')}

📰 Oxirgi muhim voqealar:
{events_text}

📂 SEC Filings:
{sec_filings_text}

🏦 Institutional Ownership: {fmt_pct(info.get('heldPercentInstitutions'))}
👤 Insider Ownership: {fmt_pct(info.get('heldPercentInsiders'))}
💵 Cash per Share: {info.get('totalCashPerShare', 'N/A')}
💳 Total Debt: {fmt_num(info.get('totalDebt'))}

⭐ Risk: {overall_risk}
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
