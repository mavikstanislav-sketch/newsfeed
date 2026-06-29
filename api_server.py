from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import re
from html import unescape
import os
import psycopg2

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ.get("PG_URL", "")

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
                video_url TEXT,
                media_type TEXT,
                video_duration TEXT,
                link TEXT,
                time TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # Добавляем новые колонки если их нет (для старой БД)
        for col, coltype in [
            ("video_url", "TEXT"),
            ("media_type", "TEXT"),
            ("video_duration", "TEXT"),
        ]:
            try:
                cur.execute(f"ALTER TABLE news ADD COLUMN IF NOT EXISTS {col} {coltype}")
            except Exception:
                pass
        conn.commit()
        cur.close()
        conn.close()
        print("БД инициализирована!")
    except Exception as e:
        print(f"Ошибка БД: {e}")

@app.on_event("startup")
def startup():
    init_db()

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
                INSERT INTO news (id, ch, name, emoji, title, body, img, video_url, media_type, video_duration, link, time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    img = EXCLUDED.img,
                    video_url = EXCLUDED.video_url,
                    media_type = EXCLUDED.media_type,
                    video_duration = EXCLUDED.video_duration
            """, (
                item.get("id"), item.get("ch"), item.get("name"),
                item.get("emoji"), item.get("title"), item.get("body"),
                item.get("img"), item.get("video_url"), item.get("media_type"),
                item.get("video_duration"), item.get("link"), item.get("time")
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
            SELECT id, ch, name, emoji, title, body, img, video_url, media_type, video_duration, link, time
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
                    "img": row[6], "video_url": row[7], "media_type": row[8],
                    "video_duration": row[9], "link": row[10], "time": row[11]
                })
            print(f"Отдаём из БД: {len(news)} новостей")
            return {"news": news, "total": len(news)}
    except Exception as e:
        print(f"Ошибка чтения БД: {e}")
    return {"news": [], "total": 0}
