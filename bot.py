from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import os
import socket
import asyncio
import yfinance as yf
import feedparser
from datetime import datetime
from finvizfinance.quote import finvizfinance
from deep_translator import GoogleTranslator

# MUHIM: hech qanday tarmoq so'rovi cheksiz osilib qolmasligi uchun global timeout.
# Buning yo'qligi butun botni "muzlatib" qo'yishi mumkin edi (bitta foydalanuvchi
# uchun sekin javob butun botni barcha userlar uchun to'xtatib qo'yardi).
socket.setdefaulttimeout(12)

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


def get_iborrowdesk_data(symbol):
    """iborrowdesk.com'ning hujjatlashtirilmagan, lekin ochiq JSON endpointidan
    Interactive Brokers'ning Borrow Fee va Shares Available ma'lumotlarini oladi."""
    result = {"borrow_fee": None, "shares_available": None}
    try:
        url = f"https://iborrowdesk.com/api/ticker/{symbol}"
        resp = _requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return result

        data = resp.json()
        daily = data.get("daily") or []
        if not daily:
            return result

        latest = daily[-1]
        result["borrow_fee"] = latest.get("fee")
        result["shares_available"] = latest.get("available")
    except Exception:
        pass

    return result


def calculate_squeeze_score(short_float_pct, borrow_fee_pct, shares_available, float_shares):
    """ODDIY, TAXMINIY (heuristic) short-squeeze bali — Ortex/Fintel kabi
    pullik xizmatlarning maxfiy formulasi EMAS. Faqat mavjud ochiq
    ko'rsatkichlar asosida qo'pol baholash uchun."""
    score = 0

    # Short Float qancha yuqori bo'lsa, shuncha ko'p ball (max 40)
    if short_float_pct is not None:
        score += min(short_float_pct / 30 * 40, 40)

    # Borrow Fee qancha yuqori bo'lsa, shuncha ko'p ball (max 35) —
    # yuqori fee odatda kam qolgan aksiya borligini bildiradi
    if borrow_fee_pct is not None:
        score += min(borrow_fee_pct / 50 * 35, 35)

    # Available shares float'ga nisbatan qancha kam bo'lsa, shuncha ko'p ball (max 25)
    if shares_available is not None and float_shares:
        ratio = shares_available / float_shares
        score += max(0, 25 * (1 - min(ratio / 0.05, 1)))

    return round(min(score, 100))


def get_finviz_data(symbol, debug=False):
    """Finviz'dan to'g'ridan-to'g'ri HTML scraping orqali oladi."""
    result = {"short_float": "N/A", "week_52_range": "N/A", "week_52_high": "N/A", "week_52_low": "N/A"}
    diag = {}
    try:
        import requests
        from bs4 import BeautifulSoup

        url = f"https://finviz.com/quote.ashx?t={symbol}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        diag["status_code"] = resp.status_code
        diag["response_length"] = len(resp.text)

        if resp.status_code != 200:
            if debug:
                result["_debug"] = diag
            return result

        soup = BeautifulSoup(resp.text, "lxml")
        table = soup.find("table", class_="snapshot-table2")
        diag["table_found"] = table is not None

        if not table:
            diag["page_title"] = soup.title.text if soup.title else "N/A"
            if debug:
                result["_debug"] = diag
            return result

        cells = [c.get_text(strip=True) for c in table.find_all("td")]
        data = {cells[i]: cells[i + 1] for i in range(0, len(cells) - 1, 2)}
        diag["keys_found"] = list(data.keys())

        result["short_float"] = data.get("Short Float", "N/A")

        high = data.get("52W High") or None
        low = data.get("52W Low") or None
        result["week_52_high"] = high or "N/A"
        result["week_52_low"] = low or "N/A"
        if high and low:
            result["week_52_range"] = f"{low} / {high}"

    except Exception as e:
        diag["exception"] = str(e)

    if debug:
        result["_debug"] = diag
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
        "Ticker ma'lumotlari uchun:\n"
        "#BIYA\n"
        "yoki\n"
        "/ticker BIYA\n\n"
        "Faqat yangiliklar uchun:\n"
        "$BIYA yoki /news BIYA\n\n"
        "Short/Squeeze ma'lumotlari uchun:\n"
        "*BIYA yoki /short BIYA"
    )


async def run_blocking(func, *args, timeout=10, default=None, **kwargs):
    """Har qanday bloklovchi (sinxron) funksiyani alohida oqimda (thread) ishga tushiradi,
    qat'iy vaqt chegarasi bilan. Shu orqali bitta sekin so'rov butun botni
    (barcha foydalanuvchilar uchun) muzlatib qo'yishining oldi olinadi."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout)
    except Exception:
        return default


import requests as _requests
from requests.adapters import HTTPAdapter


class _TimeoutHTTPAdapter(HTTPAdapter):
    """Har bir so'rovga aniq timeout majburlaydigan adapter — agar chaqiruvchi
    timeout ko'rsatmasa ham, bu qiymat ishlatiladi. Shu orqali yfinance kabi
    kutubxonalar ham hech qachon cheksiz osilib qolmaydi."""

    def __init__(self, *args, timeout=15, **kwargs):
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


def make_timeout_session(timeout=15):
    session = _requests.Session()
    adapter = _TimeoutHTTPAdapter(timeout=timeout)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_yfinance_data(symbol):
    """yfinance orqali barcha kerakli ma'lumotlarni bitta chaqiruvda yig'adi
    (bir nechta alohida yf.Ticker so'rovlarini kamaytirish uchun)"""
    stock = yf.Ticker(symbol)
    info = stock.info

    earnings = info.get("earningsTimestamp")
    if earnings:
        earnings_str = datetime.fromtimestamp(earnings).strftime("%Y-%m-%d")
    else:
        earnings_str = "N/A"
        try:
            cal = stock.calendar
            earn_date = None
            if isinstance(cal, dict):
                earn_date = cal.get("Earnings Date")
            if earn_date:
                if isinstance(earn_date, (list, tuple)):
                    earn_date = earn_date[0]
                earnings_str = str(earn_date)
        except Exception:
            pass

    flags, events = get_news_flags_and_events(stock, translate=False)

    return {"info": info, "earnings": earnings_str, "flags": flags, "events": events}


async def ticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Misol:\n#BIYA\nyoki\n/ticker BIYA")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"⏳ {symbol} tekshirilyapti...")

    default_flags = {key: False for key in RISK_KEYWORDS}
    default_finviz = {"short_float": "N/A", "week_52_range": "N/A", "week_52_high": "N/A", "week_52_low": "N/A"}

    # Barcha tarmoq so'rovlarini PARALLEL, har biriga alohida vaqt chegarasi bilan yuboramiz.
    # Shunday qilib, masalan Finviz osilib qolsa ham, u faqat o'zining bo'limini
    # "N/A" qilib qoldiradi — butun bot yoki boshqa maydonlarni to'xtatib qo'ymaydi.
    yf_task = run_blocking(
        fetch_yfinance_data, symbol, timeout=15,
        default={"info": {}, "earnings": "N/A", "flags": default_flags, "events": []},
    )
    finviz_task = run_blocking(get_finviz_data, symbol, timeout=8, default=default_finviz)
    stocktitan_news_task = run_blocking(
        get_stocktitan_news, symbol, timeout=8, default=[], limit=5, translate=False
    )
    sec_task = run_blocking(
        lambda: get_stocktitan_sec_filings(symbol) or get_sec_filings_rss(symbol),
        timeout=10, default="Topilmadi",
    )

    yf_result, finviz_data, stocktitan_events, sec_filings_text = await asyncio.gather(
        yf_task, finviz_task, stocktitan_news_task, sec_task
    )

    try:
        info = yf_result["info"]
        earnings = yf_result["earnings"]
        flags = dict(yf_result["flags"])

        avg_volume = info.get("averageVolume", 0)
        current_volume = info.get("volume", 0)
        rvol = round(current_volume / avg_volume, 2) if avg_volume else "N/A"

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

        msg = f"""📊 {symbol}

💵 Price: {info.get('currentPrice', 'N/A')}
📈 Change: {info.get('regularMarketChangePercent', 'N/A')}%
💰 Market Cap: {fmt_num(info.get('marketCap'))}
🏦 Float: {fmt_num(info.get('floatShares'))}
📉 Short Float: {finviz_data['short_float']}
📊 52W High: {finviz_data['week_52_high']}
📊 52W Low: {finviz_data['week_52_low']}
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


async def debugyf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vaqtinchalik: yfinance nima qaytarayotganini yoki qanday xato berayotganini ko'rsatadi"""
    if not context.args:
        await update.message.reply_text("Misol:\n/debugyf VRXA")
        return

    symbol = context.args[0].upper()

    def _debug_fetch(sym):
        result = {}
        try:
            stock = yf.Ticker(sym)
            info = stock.info
            result["info_keys_count"] = len(info) if info else 0
            result["has_price"] = "currentPrice" in info if info else False
            result["sample"] = {
                k: info.get(k) for k in ["shortName", "symbol", "currentPrice", "regularMarketPrice", "quoteType"]
            } if info else {}
        except Exception as e:
            result["exception"] = f"{type(e).__name__}: {e}"
        return result

    result = await run_blocking(_debug_fetch, symbol, timeout=15, default={"exception": "Timeout (15s)"})

    lines = ["🔍 yfinance diagnostikasi:"]
    for k, v in result.items():
        lines.append(f"{k}: {v}")

    await update.message.reply_text("\n".join(lines))


async def shortinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Misol:\n*BIYA\nyoki\n/short BIYA")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"⏳ {symbol} short ma'lumotlari qidirilyapti...")

    def _fetch_all(sym):
        finviz = get_finviz_data(sym)
        borrow = get_iborrowdesk_data(sym)
        try:
            info = yf.Ticker(sym).info
        except Exception:
            info = {}
        return finviz, borrow, info

    finviz_data, borrow_data, info = await run_blocking(
        _fetch_all, symbol, timeout=15,
        default=(
            {"short_float": "N/A"},
            {"borrow_fee": None, "shares_available": None},
            {},
        ),
    )

    # Short Float'ni foizga (raqamga) aylantirishga urinamiz, score hisoblash uchun
    short_float_num = None
    try:
        sf = finviz_data.get("short_float", "")
        if sf and sf != "N/A":
            short_float_num = float(str(sf).replace("%", "").strip())
    except (ValueError, TypeError):
        pass

    float_shares = info.get("floatShares")
    borrow_fee = borrow_data.get("borrow_fee")
    shares_available = borrow_data.get("shares_available")

    score = calculate_squeeze_score(short_float_num, borrow_fee, shares_available, float_shares)

    def fmt_or_na(val, suffix=""):
        return f"{val}{suffix}" if val is not None else "N/A"

    msg = f"""📊 {symbol} — Short / Squeeze ma'lumotlari

📉 Short Interest (Float): {finviz_data.get('short_float', 'N/A')}
💰 Borrow Fee: {fmt_or_na(borrow_fee, '%')}
📦 Shares Available: {fmt_or_na(shares_available)}
🔥 Short Squeeze Score (taxminiy): {score}/100

⚠️ Eslatma: Squeeze Score — bu Ortex/Fintel kabi pullik xizmatlarning rasmiy
formulasi emas, faqat ochiq ma'lumotlar (Short Float, Borrow Fee, mavjud
aksiyalar) asosida hisoblangan oddiy, taxminiy ko'rsatkich.
"""
    await update.message.reply_text(msg)


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Misol:\n/news DAIC")
        return

    symbol = context.args[0].upper()
    await update.message.reply_text(f"⏳ {symbol} yangiliklari qidirilyapti...")

    def fetch_yahoo_news(sym):
        stock = yf.Ticker(sym)
        _, yahoo_events = get_news_flags_and_events(stock, translate=True)
        return yahoo_events

    stocktitan_task = run_blocking(
        get_stocktitan_news, symbol, timeout=10, default=[], limit=5, translate=True
    )
    yahoo_task = run_blocking(fetch_yahoo_news, symbol, timeout=15, default=[])

    stocktitan_events, yahoo_events = await asyncio.gather(stocktitan_task, yahoo_task)

    msg = f"""📰 {symbol} — Yangiliklar

📰 StockTitan:
{format_events(stocktitan_events)}

📰 Yahoo Finance:
{format_events(yahoo_events)}
"""
    await update.message.reply_text(msg)


async def debugfinviz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vaqtinchalik: Finviz scrapingning aynan qayerda muvaffaqiyatsiz bo'layotganini ko'rsatadi"""
    if not context.args:
        await update.message.reply_text("Misol:\n/debugfinviz DAIC")
        return

    symbol = context.args[0].upper()
    result = get_finviz_data(symbol, debug=True)
    diag = result.pop("_debug", {})

    lines = ["🔍 Natija:"]
    for k, v in result.items():
        lines.append(f"{k}: {v}")

    lines.append("\n🛠 Diagnostika:")
    for k, v in diag.items():
        if k == "keys_found":
            lines.append(f"{k}: {len(v)} ta kalit topildi")
        else:
            lines.append(f"{k}: {v}")

    await update.message.reply_text("\n".join(lines))


async def dollar_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()

    if text.startswith("$"):
        context.args = [text[1:]]
        await news(update, context)
    elif text.startswith("*"):
        context.args = [text[1:]]
        await shortinfo(update, context)
    elif text.startswith("#"):
        context.args = [text[1:]]
        await ticker(update, context)


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ticker", ticker))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("debugfinviz", debugfinviz))
    app.add_handler(CommandHandler("debugyf", debugyf))
    app.add_handler(CommandHandler("short", shortinfo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dollar_news))

    print("Bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
