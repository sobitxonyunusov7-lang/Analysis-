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
import feedparser
from datetime import datetime
from finvizfinance.quote import finvizfinance
from deep_translator import GoogleTranslator

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# SEC data.sec.gov / www.sec.gov so'rovlari uchun: SEC "fair access" siyosatiga ko'ra
# User-Agent'da ilova nomi + aloqa email bo'lishi kerak, aks holda 403 qaytarishi mumkin.
SEC_HEADERS = {
    "User-Agent": "StockAnalyzerBot (contact: your-email@example.com)"
}

_CIK_MAP_CACHE = None


def _load_cik_map():
    """SEC'ning rasmiy ticker -> CIK xaritasini yuklab, keshda saqlaydi"""
    global _CIK_MAP_CACHE
    if _CIK_MAP_CACHE is not None:
        return _CIK_MAP_CACHE

    import requests as _requests
    try:
        resp = _requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        _CIK_MAP_CACHE = {
            v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()
        }
    except Exception:
        _CIK_MAP_CACHE = {}

    return _CIK_MAP_CACHE


RISK_KEYWORDS = {
    "delisting": ["delisting", "delist", "noncompliance", "non-compliance", "minimum bid"],
    "reverse_split": ["reverse split", "reverse stock split"],
    "split": [" stock split", "forward split"],
    "offering": ["offering", "registered direct", "private placement", "atm offering", "shelf registration"],
    "dilution": ["dilution", "dilutive", "shares outstanding increase"],
}


def fmt_num(value, suffix=""):
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


def translate_uz(text):
    """Matnni o'zbekchaga tarjima qiladi, xato bo'lsa original matnni qaytaradi"""
    if not text:
        return text
    try:
        return GoogleTranslator(source="auto", target="uz").translate(text)
    except Exception:
        return text


def get_finviz_data(symbol):
    """finvizfinance kutubxonasi orqali Short Float va 52W Range ni oladi (barqaror usul)"""
    result = {"short_float": "N/A", "week_52_range": "N/A"}
    try:
        stock = finvizfinance(symbol)
        data = stock.ticker_fundament()

        result["short_float"] = data.get("Short Float", "N/A")

        # Kutubxona "52W Range"ni ikkiga bo'lib saqlaydi: "52W Range From" / "52W Range To"
        low = data.get("52W Range From")
        high = data.get("52W Range To")
        if low and high:
            result["week_52_range"] = f"{low} - {high}"
        else:
            result["week_52_range"] = data.get("52W Range", "N/A")
    except Exception:
        pass
    return result


def get_stocktitan_news(symbol, limit=5, translate=True):
    """StockTitan'ning rasmiy per-ticker RSS feedidan yangiliklarni oladi"""
    events = []
    try:
        url = f"https://www.stocktitan.net/rss/news/{symbol}"
        feed = feedparser.parse(url, request_headers=HEADERS)

        for entry in feed.entries[:limit]:
            title = entry.get("title", "").strip()
            if not title:
                continue
            link = entry.get("link", "")
            display_title = translate_uz(title) if translate else title
            events.append({"title": display_title, "link": link, "publisher": "StockTitan"})
    except Exception:
        pass

    return events


def get_stocktitan_sec_filings(symbol, limit=8):
    """StockTitan'ning rasmiy per-ticker SEC filings RSS feedidan oladi"""
    try:
        url = f"https://www.stocktitan.net/rss/sec-filings/{symbol}"
        feed = feedparser.parse(url, request_headers=HEADERS)
        if not feed.entries:
            return None

        lines = []
        for entry in feed.entries[:limit]:
            title = entry.get("title", "N/A")
            published = entry.get("published", "")[:16]
            lines.append(f"• {title} ({published})")

        return "\n".join(lines)
    except Exception:
        return None


def get_sec_filings_rss(symbol, limit=6):
    """Avval ticker->CIK xaritasidan CIK topib, keyin shu CIK bo'yicha rasmiy Atom feedni o'qiydi.
    Bu company nomi bo'yicha qidirishdan ancha ishonchli."""
    try:
        cik_map = _load_cik_map()
        cik = cik_map.get(symbol.upper())

        if cik:
            url = (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type=&dateb=&owner=include"
                f"&count={limit}&output=atom"
            )
        else:
            # Zaxira variant: kompaniya nomi bo'yicha qidirish
            url = (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&company={symbol}&type=&dateb=&owner=include"
                f"&count={limit}&output=atom"
            )

        feed = feedparser.parse(url, request_headers=SEC_HEADERS)
        if not feed.entries:
            return "Topilmadi"

        lines = []
        for entry in feed.entries[:limit]:
            title = entry.get("title", "N/A")
            updated = entry.get("updated", "")[:10]
            lines.append(f"• {title} ({updated})")

        return "\n".join(lines)
    except Exception:
        return "Topilmadi"


def get_news_flags_and_events(stock, translate=True):
    flags = {key: False for key in RISK_KEYWORDS}
    events = []

    try:
        news_items = stock.news or []
    except Exception:
        news_items = []

    for item in news_items[:15]:
        content = item.get("content", item)
        title = (content.get("title") or "").strip()
        if not title:
            continue

        title_lower = title.lower()
        for key, keywords in RISK_KEYWORDS.items():
            if any(kw in title_lower for kw in keywords):
                flags[key] = True

        if len(events) < 5:
            display_title = translate_uz(title) if translate else title

            # Havolani turli mumkin bo'lgan joylardan qidiramiz
            link = (
                (content.get("canonicalUrl") or {}).get("url")
                or (content.get("clickThroughUrl") or {}).get("url")
                or item.get("link")
                or ""
            )
            publisher = (content.get("provider") or {}).get("displayName", "")

            events.append({"title": display_title, "link": link, "publisher": publisher})

    return flags, events


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
        rvol = round(current_volume / avg_volume, 2) if avg_volume else "N/A"

        earnings = info.get("earningsTimestamp")
        if earnings:
            earnings = datetime.fromtimestamp(earnings).strftime("%Y-%m-%d")
        else:
            # Zaxira variant: yfinance'ning calendar ma'lumotidan olish
            earnings = "N/A"
            try:
                cal = stock.calendar
                earn_date = None
                if isinstance(cal, dict):
                    earn_date = cal.get("Earnings Date")
                if earn_date:
                    if isinstance(earn_date, (list, tuple)):
                        earn_date = earn_date[0]
                    earnings = str(earn_date)
            except Exception:
                pass

        finviz_data = get_finviz_data(symbol)
        flags, events = get_news_flags_and_events(stock, translate=True)

        # StockTitan'dan qo'shimcha yangiliklar (ayniqsa kichik/penny stocklar uchun foydali)
        stocktitan_events = get_stocktitan_news(symbol, limit=5, translate=True)

        # StockTitan yangiliklarida ham xavf so'zlarini tekshiramiz
        for e in stocktitan_events:
            title_lower = e["title"].lower()
            for key, keywords in RISK_KEYWORDS.items():
                if any(kw in title_lower for kw in keywords):
                    flags[key] = True

        def flag_icon(key):
            return "🔴 Bor" if flags[key] else "🟢 Yo'q"

        dilution_score = sum([
            flags["offering"], flags["dilution"], flags["reverse_split"], flags["delisting"]
        ])
        if dilution_score >= 2:
            dilution_risk = "🔴 Yuqori"
        elif dilution_score == 1:
            dilution_risk = "🟡 O'rta"
        else:
            dilution_risk = "🟢 Past"

        market_cap = info.get("marketCap") or 0
        if dilution_score >= 2 or (market_cap and market_cap < 50_000_000):
            overall_risk = "🔴 Yuqori"
        elif dilution_score == 1 or (market_cap and market_cap < 300_000_000):
            overall_risk = "🟡 O'rta"
        else:
            overall_risk = "🟢 Past"

        def format_events(items, empty_text="• Ma'lumot topilmadi"):
            if not items:
                return empty_text
            lines = []
            for e in items:
                line = f"• {e['title']}"
                if e.get("publisher"):
                    line += f" ({e['publisher']})"
                if e.get("link"):
                    line += f"\n  🔗 {e['link']}"
                lines.append(line)
            return "\n".join(lines)

        stocktitan_text = format_events(stocktitan_events)
        yahoo_text = format_events(events)
        sec_filings_text = get_stocktitan_sec_filings(symbol) or get_sec_filings_rss(symbol)

        msg = f"""📊 {symbol}

💵 Price: {info.get('currentPrice', 'N/A')}
📈 Change: {info.get('regularMarketChangePercent', 'N/A')}%
💰 Market Cap: {fmt_num(info.get('marketCap'))}
🏦 Float: {fmt_num(info.get('floatShares'))}
📉 Short Float: {finviz_data['short_float']}
📊 52W High/Low: {finviz_data['week_52_range']}
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

📰 StockTitan yangiliklari:
{stocktitan_text}

📰 Boshqa yangiliklar (Yahoo Finance):
{yahoo_text}

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
