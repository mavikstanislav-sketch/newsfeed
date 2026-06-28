from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import feedparser
import re
from html import unescape
from concurrent.futures import ThreadPoolExecutor
import os
import psycopg2
import json

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("PG_URL", "")

CHANNELS = [
    {"username": "ssternenko",    "name": "Стерненко",     "emoji": "🇺🇦"},
    {"username": "vach_govorit",  "name": "Вач говорит",   "emoji": "🎙"},
    {"username": "lachentyt",     "name": "Лаченко",       "emoji": "📢"},
    {"username": "vanek_nikolaev","name": "Ваня Николаев", "emoji": "👤"},
    {"username": "truexanewsua",  "name": "TrueXA News",   "emoji": "📰"},
]

def get_db():
    url = DATABASE_URL
    if url and "sslmode" not in url:
        url += "?sslmode=require"
    return psycopg2.connect(url)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id TEXT PRIMARY KEY,
                ch TEXT,
                name TEXT,
                emoji TEXT,
                title TEXT,
                body TEXT,
                img TEXT,
                link TEXT,
                time TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("БД инициализирована!")
    except Exception as e:
        print(f"Ошибка БД: {e}")

@app.on_event("startup")
def startup():
    init_db()

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
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
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

@app.post("/push")
def push_news(data: dict):
    new_items = data.get("news", [])
    added = 0
    try:
        conn = get_db()
        cur = conn.cursor()
        for item in new_items:
            cur.execute("""
                INSERT INTO news (id, ch, name, emoji, title, body, img, link, time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
            """, (
                item.get("id"), item.get("ch"), item.get("name"),
                item.get("emoji"), item.get("title"), item.get("body"),
                item.get("img"), item.get("link"), item.get("time")
            ))
            if cur.rowcount > 0:
                added += 1
        conn.commit()
        cur.close()
        conn.close()
        print(f"Сохранено в БД: {added} новых")
    except Exception as e:
        print(f"Ошибка push: {e}")
    return {"ok": True, "added": added}

@app.get("/news")
def get_news():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, ch, name, emoji, title, body, img, link, time
            FROM news
            ORDER BY created_at DESC
            LIMIT 50
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if rows:
            news = []
            for row in rows:
                news.append({
                    "id": row[0], "ch": row[1], "name": row[2],
                    "emoji": row[3], "title": row[4], "body": row[5],
                    "img": row[6], "link": row[7], "time": row[8]
                })
            print(f"Отдаём из БД: {len(news)} новостей")
            return {"news": news, "total": len(news)}
    except Exception as e:
        print(f"Ошибка чтения БД: {e}")

    # Резервный вариант — RSS
    print("БД недоступна, парсим RSS...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(fetch_channel, CHANNELS))
    all_news = []
    for items in results:
        all_news.extend(items)
    return {"news": all_news, "total": len(all_news)}
