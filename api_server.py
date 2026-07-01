from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
        for col, coltype in [
            ("video_url", "TEXT"),
            ("media_type", "TEXT"),
            ("video_duration", "TEXT"),
            ("category", "TEXT"),
            ("city", "TEXT"),
        ]:
            try:
                cur.execute(f"ALTER TABLE news ADD COLUMN IF NOT EXISTS {col} {coltype}")
            except Exception:
                pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_channels (
                username TEXT PRIMARY KEY,
                title TEXT,
                about TEXT,
                participants INT,
                ai_verdict TEXT,
                ai_reason TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS approved_channels (
                username TEXT PRIMARY KEY,
                name TEXT,
                emoji TEXT,
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
                INSERT INTO news (id, ch, name, emoji, title, body, img, video_url, media_type, video_duration, category, city, link, time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    img = EXCLUDED.img,
                    video_url = EXCLUDED.video_url,
                    media_type = EXCLUDED.media_type,
                    video_duration = EXCLUDED.video_duration,
                    category = EXCLUDED.category,
                    city = EXCLUDED.city
            """, (
                item.get("id"), item.get("ch"), item.get("name"),
                item.get("emoji"), item.get("title"), item.get("body"),
                item.get("img"), item.get("video_url"), item.get("media_type"),
                item.get("video_duration"), item.get("category"), item.get("city"),
                item.get("link"), item.get("time")
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

@app.get("/pending-channels")
def get_pending_channels():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT username, title, about, participants, ai_verdict, ai_reason, status
            FROM pending_channels
            WHERE status = 'pending'
            ORDER BY created_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        channels = [{
            "username": r[0], "title": r[1], "about": r[2],
            "participants": r[3], "ai_verdict": r[4], "ai_reason": r[5], "status": r[6]
        } for r in rows]
        return {"channels": channels}
    except Exception as e:
        print(f"Ошибка pending-channels: {e}")
        return {"channels": []}

@app.post("/pending-channels/approve")
def approve_channel(data: dict):
    username = data.get("username")
    name = data.get("name") or username
    emoji = data.get("emoji") or "📢"
    if not username:
        return {"ok": False, "error": "username required"}
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO approved_channels (username, name, emoji)
            VALUES (%s,%s,%s)
            ON CONFLICT (username) DO NOTHING
        """, (username, name, emoji))
        cur.execute("UPDATE pending_channels SET status = 'approved' WHERE username = %s", (username,))
        conn.commit()
        cur.close()
        conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/pending-channels/reject")
def reject_channel(data: dict):
    username = data.get("username")
    if not username:
        return {"ok": False, "error": "username required"}
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE pending_channels SET status = 'rejected' WHERE username = %s", (username,))
        conn.commit()
        cur.close()
        conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/clear")
def clear_news():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM news")
        conn.commit()
        cur.close()
        conn.close()
        return {"ok": True, "message": "Все новости удалены"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/news")
def get_news():
    try:
        conn = get_db()
        cur = conn.cursor()

        # Удаляем новости старше 7 дней
        cur.execute("DELETE FROM news WHERE created_at < NOW() - INTERVAL '7 days'")
        conn.commit()

        # Берём все новости за 7 дней без лимита
        cur.execute("""
            SELECT id, ch, name, emoji, title, body, img, video_url, media_type, video_duration, category, city, link, time
            FROM news
            ORDER BY created_at DESC
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
                    "video_duration": row[9], "category": row[10], "city": row[11],
                    "link": row[12], "time": row[13]
                })
            print(f"Отдаём из БД: {len(news)} новостей")
            return {"news": news, "total": len(news)}
    except Exception as e:
        print(f"Ошибка чтения БД: {e}")
    return {"news": [], "total": 0}
