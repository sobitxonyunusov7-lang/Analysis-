def get_news(symbol):
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    feed = feedparser.parse(url)

    news = []

    for item in feed.entries[:5]:
        news.append(f"• {item.title}")

    if not news:
        return "• Yangilik topilmadi."

    return "\n".join(news)
