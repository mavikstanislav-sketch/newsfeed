from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
import psycopg2
import json
import urllib.request
from datetime import datetime, timezone, timedelta

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")

# Кэш сводки: {"text": "...", "time": datetime}
_summary_cache = {"text": "", "time": None}
SUMMARY_CACHE_MINUTES = 60  # обновлять сводку не чаще раза в час

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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                news_id TEXT,
                user_id TEXT,
                reaction TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (news_id, user_id)
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

@app.post("/react")
def react(data: dict):
    news_id = data.get("news_id")
    user_id = data.get("user_id")
    reaction = data.get("reaction")  # like / fire / heart
    if not news_id or not user_id or not reaction:
        return {"ok": False, "error": "news_id, user_id, reaction required"}
    try:
        conn = get_db()
        cur = conn.cursor()
        # Смотрим, была ли уже реакция этого пользователя на эту новость
        cur.execute("SELECT reaction FROM reactions WHERE news_id=%s AND user_id=%s", (news_id, user_id))
        row = cur.fetchone()
        if row and row[0] == reaction:
            # Та же реакция повторно — убираем (toggle off)
            cur.execute("DELETE FROM reactions WHERE news_id=%s AND user_id=%s", (news_id, user_id))
        else:
            # Новая реакция или смена реакции — записываем/обновляем
            cur.execute("""
                INSERT INTO reactions (news_id, user_id, reaction)
                VALUES (%s,%s,%s)
                ON CONFLICT (news_id, user_id) DO UPDATE SET reaction = EXCLUDED.reaction
            """, (news_id, user_id, reaction))
        conn.commit()
        # Возвращаем свежие счётчики для этой новости
        cur.execute("""
            SELECT reaction, COUNT(*) FROM reactions WHERE news_id=%s GROUP BY reaction
        """, (news_id,))
        counts = {"like": 0, "fire": 0, "heart": 0}
        for r in cur.fetchall():
            if r[0] in counts:
                counts[r[0]] = r[1]
        cur.close()
        conn.close()
        return {"ok": True, "counts": counts}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/reactions")
def get_reactions(user_id: str = ""):
    """Возвращает счётчики реакций по всем новостям + что выбрал этот пользователь"""
    try:
        conn = get_db()
        cur = conn.cursor()
        # Счётчики по всем новостям
        cur.execute("SELECT news_id, reaction, COUNT(*) FROM reactions GROUP BY news_id, reaction")
        counts = {}
        for r in cur.fetchall():
            nid, rtype, cnt = r[0], r[1], r[2]
            if nid not in counts:
                counts[nid] = {"like": 0, "fire": 0, "heart": 0}
            if rtype in counts[nid]:
                counts[nid][rtype] = cnt
        # Что выбрал этот пользователь
        mine = {}
        if user_id:
            cur.execute("SELECT news_id, reaction FROM reactions WHERE user_id=%s", (user_id,))
            for r in cur.fetchall():
                mine[r[0]] = r[1]
        cur.close()
        conn.close()
        return {"counts": counts, "mine": mine}
    except Exception as e:
        print(f"Ошибка reactions: {e}")
        return {"counts": {}, "mine": {}}

@app.get("/summary")
def get_summary():
    """AI-сводка главных событий за последние 24 часа. Кэш на час."""
    global _summary_cache
    # Если есть свежая сводка в кэше — отдаём её, не дёргаем Claude лишний раз
    if _summary_cache["text"] and _summary_cache["time"]:
        age = datetime.now(timezone.utc) - _summary_cache["time"]
        if age < timedelta(minutes=SUMMARY_CACHE_MINUTES):
            return {"summary": _summary_cache["text"], "cached": True}

    if not CLAUDE_API_KEY:
        return {"summary": "", "error": "no API key"}

    try:
        # Берём заголовки новостей за последние 24 часа
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT title, category, city FROM news
            WHERE created_at > NOW() - INTERVAL '24 hours'
            ORDER BY created_at DESC
            LIMIT 60
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        if not rows:
            return {"summary": "За останню добу новин поки немає.", "cached": False}

        # Собираем заголовки в текст для Claude
        news_block = ""
        for i, r in enumerate(rows, 1):
            title = (r[0] or "").replace("\n", " ")[:150]
            cat = r[1] or ""
            city = r[2] or ""
            extra = ""
            if city:
                extra = " [" + city + "]"
            news_block += str(i) + ". " + title + extra + "\n"

        prompt = ("Ось заголовки українських новин за останню добу:\n\n"
                  + news_block + "\n"
                  "Зроби коротку зведення головних подій дня — 5-7 найважливіших пунктів. "
                  "Кожен пункт з нового рядка, починай з '• '. Пиши українською, стисло, "
                  "об'єднуй схожі новини в один пункт. Без вступу і висновку, лише пункти.")

        data = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
            summary_text = result["content"][0]["text"].strip()

        _summary_cache = {"text": summary_text, "time": datetime.now(timezone.utc)}
        return {"summary": summary_text, "cached": False}
    except Exception as e:
        print(f"Ошибка summary: {e}")
        return {"summary": "", "error": str(e)}

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
