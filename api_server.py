from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import feedparser
import json
import re
from html import unescape

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHANNELS = [
    {"username": "ssternenko",    "name": "Стерненко",     "emoji": "🇺🇦"},
    {"username": "vach_govorit",  "name": "Вач говорит",   "emoji": "🎙"},
    {"username": "lachentyt",     "name": "Лаченко",       "emoji": "📢"},
    {"username": "vanek_nikolaev","name": "Ваня Николаев", "emoji": "👤"},
    {"username": "truexanewsua",  "name": "TrueXA News",   "emoji": "📰"},
]

def clean_html(text):
    text = re.sub(r'<[^>]+>', '', text)
    return unescape(text).strip()

def get_image(html):
    import re
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    return match.group(1) if match else None

@app.get("/")
def root():
    return {"status": "NewsFeed API работает!"}

@app.get("/news")
def get_news():
    all_news = []
    for ch in CHANNELS:
        try:
            urls = [
                f"https://rsshub.app/telegram/channel/{ch['username']}",
                f"https://tg.i-c-a.su/rss/{ch['username']}",
            ]
            for url in urls:
                feed = feedparser.parse(url)
                if feed.entries:
                    for entry in feed.entries[:5]:
                        desc = entry.get("summary", "")
                        img = get_image(desc)
                        text = clean_html(desc)
                        all_news.append({
                            "id": entry.get("id", "")[-16:],
                            "ch": ch["username"],
                            "name": ch["name"],
                            "emoji": ch["emoji"],
                            "title": clean_html(entry.get("title", ""))[:200],
                            "body": text[:600],
                            "img": img,
                            "link": entry.get("link", ""),
                            "time": entry.get("published", ""),
                        })
                    break
        except Exception as e:
            print(f"Ошибка {ch['username']}: {e}")
    return {"news": all_news, "total": len(all_news)}

@app.get("/news/{channel}")
def get_channel_news(channel: str):
    ch = next((c for c in CHANNELS if c["username"] == channel), None)
    if not ch:
        return {"error": "Канал не найден"}
    news = get_news()
    filtered = [n for n in news["news"] if n["ch"] == channel]
    return {"news": filtered}