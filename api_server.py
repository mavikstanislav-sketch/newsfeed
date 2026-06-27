from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import feedparser
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
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html or '')
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
                f"https://tg.i-c-a.su/rss/{ch['username']}",
                f"https://rsshub.app/telegram/channel/{ch['username']}",
            ]
            for url in urls:
                try:
                    feed = feedparser.parse(url, request_headers={
                        'User-Agent': 'Mozilla/5.0 NewsFeedBot/1.0'
                    })
                    if feed.entries:
                        for entry in feed.entries[:5]:
                            desc = entry.get("summary", "") or entry.get("description", "")
                            img = get_image(desc)
                            text = clean_html(desc)
                            title = clean_html(entry.get("title", ""))
                            if not title and not text:
                                continue
                            all_news.append({
                                "id": entry.get("id", entry.get("link", ""))[-20:],
                                "ch": ch["username"],
                                "name": ch["name"],
                                "emoji": ch["emoji"],
                                "title": title[:200],
                                "body": text[:600],
                                "img": img,
                                "link": entry.get("link", ""),
                                "time": entry.get("published", ""),
                            })
                        break
                except Exception as e:
                    print(f"Ошибка URL {url}: {e}")
                    continue
        except Exception as e:
            print(f"Ошибка канала {ch['username']}: {e}")
    print(f"Загружено новостей: {len(all_news)}")
    return {"news": all_news, "total": len(all_news)}
