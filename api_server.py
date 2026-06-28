from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import feedparser
import re
from html import unescape
from concurrent.futures import ThreadPoolExecutor

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

def fetch_channel(ch):
    urls = [
        f"https://tg.i-c-a.su/rss/{ch['username']}",
        f"https://rsshub.app/telegram/channel/{ch['username']}",
    ]
    for url in urls:
        try:
            feed = feedparser.parse(url, request_headers={
                'User-Agent': 'Mozilla/5.0',
            })
            if feed.entries:
                items = []
                for entry in feed.entries[:10]:
                    desc = entry.get("summary", "") or entry.get("description", "")
                    img = get_image(desc)
                    text = clean_html(desc)
                    title = clean_html(entry.get("title", ""))
                    if not title and not text:
                        continue
                    items.append({
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
                if items:
                    return items
        except Exception as e:
            print(f"Error {ch['username']}: {e}")
    return []

@app.get("/")
def root():
    return {"status": "NewsFeed API работает!"}

@app.get("/news")
def get_news():
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(fetch_channel, CHANNELS))
    all_news = []
    for items in results:
        all_news.extend(items)
    print(f"Загружено новостей: {len(all_news)}")
    return {"news": all_news, "total": len(all_news)}
