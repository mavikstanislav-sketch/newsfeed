from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import feedparser
import re
from html import unescape
import asyncio
import httpx

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

def parse_feed(content, ch):
    items = []
    try:
        feed = feedparser.parse(content)
        for entry in feed.entries[:5]:
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
    except Exception as e:
        print(f"Parse error {ch['username']}: {e}")
    return items

async def fetch_channel(client, ch):
    urls = [
        f"https://tg.i-c-a.su/rss/{ch['username']}",
        f"https://rsshub.app/telegram/channel/{ch['username']}",
    ]
    for url in urls:
        try:
            r = await client.get(url, timeout=8)
            if r.status_code == 200:
                items = parse_feed(r.text, ch)
                if items:
                    return items
        except Exception as e:
            print(f"Error {ch['username']} {url}: {e}")
    return []

@app.get("/")
def root():
    return {"status": "NewsFeed API работает!"}

@app.get("/news")
async def get_news():
    async with httpx.AsyncClient() as client:
        # Загружаем все каналы параллельно!
        tasks = [fetch_channel(client, ch) for ch in CHANNELS]
        results = await asyncio.gather(*tasks)
    
    all_news = []
    for items in results:
        all_news.extend(items)
    
    print(f"Загружено новостей: {len(all_news)}")
    return {"news": all_news, "total": len(all_news)}
