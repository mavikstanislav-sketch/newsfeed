import asyncio
import json
import os
import hashlib
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.parse
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
import psycopg2

TELEGRAM_BOT_TOKEN = "8798274501:AAGUCgF9bz6_w2VeTvy1CK_L4-6G4u7SGSM"
TELEGRAM_CHAT_ID   = "8761012731"
API_URL = "https://newsfeed-production-9b3b.up.railway.app"
API_ID   = 37103823
API_HASH = "ebbfc63eb333bd7130ace1a23df460e9"
SESSION  = "news_session"
DATABASE_URL = os.environ.get("PG_URL", "")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")

CHANNELS = []

CHECK_INTERVAL = 10
MAX_NEWS_AGE_HOURS = 1  # только свежак
DISCOVERY_INTERVAL_HOURS = 6  # как часто искать новые каналы

DISCOVERY_KEYWORDS = [
    "новини україна", "украина новости",
    "зсу новини", "фронт україна новини", "наступ зсу",
    "київ новини",
    "тривога новини", "шахед новини", "ракетна атака",
    "нато допомога україні", "зброя для україни",
    "путін кремль новини", "росія новини",
    "приліт по росії", "удар по росії",
]

# Ключевые слова для глобального поиска новостей по категориям (по всему Telegram)
CATEGORY_SEARCH_KEYWORDS = {
    "front":   ["фронт", "зсу", "наступ"],
    "kyiv":    ["київ"],
    "alarm":   ["тривога", "шахед", "ракета"],
    "allies":  ["нато", "зброя"],
    "russia":  ["кремль", "путін"],
    "strikes": ["приліт"],
}
GLOBAL_SEARCH_LIMIT = 8

CATEGORY_EMOJI = {
    "front": "⚔️",
    "kyiv": "🏙",
    "alarm": "🚨",
    "allies": "🤝",
    "russia": "🇷🇺",
    "strikes": "💥",
}

VALID_CATEGORIES = set(CATEGORY_EMOJI.keys())

def get_db():
    url = DATABASE_URL
    if url and "sslmode" not in url:
        url += "?sslmode=require"
    return psycopg2.connect(url)

def init_seen_table():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS seen_news (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Таблица seen_news готова!")
    except Exception as e:
        print("Ошибка init seen: " + str(e))

def load_seen():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id FROM seen_news")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        seen = set(row[0] for row in rows)
        print("Загружено из БД seen: " + str(len(seen)) + " записей")
        return seen
    except Exception as e:
        print("Ошибка load seen: " + str(e))
        return set()

def save_seen_ids(new_ids):
    if not new_ids:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        for nid in new_ids:
            cur.execute(
                "INSERT INTO seen_news (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                (nid,)
            )
        cur.execute("""
            DELETE FROM seen_news WHERE id NOT IN (
                SELECT id FROM seen_news ORDER BY created_at DESC LIMIT 2000
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Ошибка save seen: " + str(e))

def make_id(channel, msg_id):
    return hashlib.md5((channel + str(msg_id)).encode()).hexdigest()[:16]

def init_pending_table():
    try:
        conn = get_db()
        cur = conn.cursor()
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
        print("Таблицы discovery готовы!")
    except Exception as e:
        print("Ошибка init discovery: " + str(e))

def get_known_usernames():
    """Уже отслеживаемые + уже предложенные каналы — чтобы не дублировать"""
    known = set(ch["username"] for ch in CHANNELS)
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT username FROM pending_channels")
        known.update(row[0] for row in cur.fetchall())
        cur.execute("SELECT username FROM approved_channels")
        known.update(row[0] for row in cur.fetchall())
        cur.close()
        conn.close()
    except Exception as e:
        print("Ошибка get_known_usernames: " + str(e))
    return known

def get_approved_channels():
    """Каналы, подтверждённые пользователем через кабинет — добавляются к мониторингу"""
    extra = []
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT username, name, emoji FROM approved_channels")
        for row in cur.fetchall():
            extra.append({"username": row[0], "name": row[1] or row[0], "emoji": row[2] or "📢"})
        cur.close()
        conn.close()
    except Exception as e:
        print("Ошибка get_approved_channels: " + str(e))
    return extra

async def verify_channel_with_ai(title, about, username, sample_texts=None):
    """AI оценивает, является ли канал реальним українським новинним каналом,
    анализируя название и образцы последних сообщений (на консистентность теми)"""
    if not CLAUDE_API_KEY:
        return "unknown", ""
    try:
        import re
        safe_title = (title or "").replace('"', "'")[:150]
        safe_about = (about or "")[:300].replace('"', "'")
        samples_block = ""
        if sample_texts:
            cleaned = [s.replace('"', "'")[:200] for s in sample_texts[:3] if s]
            if cleaned:
                samples_block = "\n\nОстанні повідомлення каналу (для аналізу теми та реальності):\n"
                for i, s in enumerate(cleaned, 1):
                    samples_block += str(i) + ". " + s + "\n"

        prompt = ("Оціни Telegram-канал як можливе джерело українських новин.\n\n"
                   "Назва каналу: " + safe_title + "\n"
                   "Опис каналу: " + safe_about + "\n"
                   "Username: @" + username + samples_block + "\n\n"
                   "Визнач:\n"
                   "1. verdict: \"good\" якщо канал РЕГУЛЯРНО публікує реальні українські новини "
                   "(судячи з прикладів повідомлень — це не реклама, не спам, не бот, не флуд, а саме новини), "
                   "\"bad\" якщо це спам/реклама/бот/не новинний/не український/нерелевантний\n"
                   "2. reason: коротко чому\n\n"
                   "Відповідай СУВОРО у форматі JSON, без жодного додаткового тексту:\n"
                   "{\"verdict\": \"good\", \"reason\": \"коротко\"}")

        data = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 100,
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
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            txt = result["content"][0]["text"].strip()
            if "```" in txt:
                txt = txt.split("```")[1].replace("json", "").strip()
            # Достаём именно JSON-объект, даже если вокруг есть лишний текст
            match = re.search(r'\{.*\}', txt, re.DOTALL)
            if match:
                txt = match.group(0)
            parsed = json.loads(txt)
            return parsed.get("verdict", "unknown"), parsed.get("reason", "")
    except Exception as e:
        print("    Ошибка AI verify: " + str(e))
        return "unknown", ""

def save_approved_channel(username, title):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO approved_channels (username, name, emoji)
            VALUES (%s,%s,%s)
            ON CONFLICT (username) DO NOTHING
        """, (username, title or username, "📢"))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Ошибка save_approved_channel: " + str(e))

def save_pending_channel(username, title, about, participants, verdict, reason):
    try:
        conn = get_db()
        cur = conn.cursor()
        status = "approved" if verdict == "good" else "pending"
        cur.execute("""
            INSERT INTO pending_channels (username, title, about, participants, ai_verdict, ai_reason, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (username) DO NOTHING
        """, (username, title, about, participants, verdict, reason, status))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Ошибка save_pending_channel: " + str(e))

async def discover_channels(client):
    """Ищет новые каналы по ключевым словам через Telegram API"""
    print("[Discovery] Запускаю поиск новых каналов...")
    known = get_known_usernames()
    found_count = 0
    try:
        from telethon.tl.functions.contacts import SearchRequest
        for kw in DISCOVERY_KEYWORDS:
            try:
                result = await client(SearchRequest(q=kw, limit=15))
                for chat in result.chats:
                    username = getattr(chat, "username", None)
                    if not username:
                        continue
                    username = username.lower()
                    if username in known:
                        continue
                    is_channel = chat.__class__.__name__ == "Channel" and getattr(chat, "broadcast", False)
                    if not is_channel:
                        continue
                    known.add(username)

                    title = getattr(chat, "title", "") or ""
                    participants = getattr(chat, "participants_count", 0) or 0
                    about = ""
                    sample_texts = []
                    try:
                        recent_msgs = await client.get_messages(username, limit=5)
                        for m in recent_msgs:
                            t = m.text or m.message or ""
                            if len(t) > 15:
                                sample_texts.append(t)
                    except Exception:
                        pass

                    verdict, reason = await verify_channel_with_ai(title, about, username, sample_texts)
                    save_pending_channel(username, title, about, participants, verdict, reason)
                    if verdict == "good":
                        save_approved_channel(username, title)
                        print("    ✅ Автоматически добавлен @" + username + " (" + title + ")")
                    else:
                        print("    Найден канал @" + username + " (" + title + ") -> " + verdict + " (не добавлен)")
                    found_count += 1
                await asyncio.sleep(2)
            except Exception as e:
                print("    Ошибка поиска по '" + kw + "': " + str(e))
                await asyncio.sleep(3)
    except Exception as e:
        print("Ошибка discover_channels: " + str(e))
    print("[Discovery] Поиск завершён, найдено новых: " + str(found_count))

def is_fresh(msg_date):
    """Проверка: новость не старше MAX_NEWS_AGE_HOURS"""
    try:
        now = datetime.now(timezone.utc)
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        age = now - msg_date
        return age <= timedelta(hours=MAX_NEWS_AGE_HOURS)
    except Exception:
        return True  # если не смогли посчитать — пропускаем, не блокируем

async def analyze_with_ai(text, channel):
    """
    Claude анализирует новость — категория + фейк.
    Возвращает (category или None, is_fake, fake_reason).
    category=None означает "не подходит ни под одну из 6 категорий" -> не публикуем.
    """
    if not CLAUDE_API_KEY:
        return get_category_simple(text), False, ""
    try:
        prompt = """Ти — модератор українського новинного Telegram-каналу. Проаналізуй текст новини.

Новина від @""" + channel + """:
\"\"\"""" + text[:500] + """\"\"\"

Визнач:
1. Категорію (ОБЕРИ ОДНУ, найбільш відповідну):
front = бойові дії, наступ, ЗСУ, бригади, позиції, фронт
kyiv = новини про Київ, столицю
alarm = повітряна тривога, ракети, шахеди, БпЛА, вибухи, ППО, обстріл
allies = допомога від США/НАТО, зброя, заяви політиків-союзників
russia = новини про Москву, Кремль, Путіна, внутрішні події в РФ
strikes = удари по території Росії, атаки БПЛА на РФ, прильоти в РФ
none = якщо новина НЕ підходить під жодну з категорій вище

2. Ознаки фейку/маніпуляції (is_fake: true/false): відсутність джерела, панічне формулювання, неперевірені чутки, явна пропаганда, провокаційний непідтверджений контент.

Відповідай СУВОРО у форматі JSON, без жодного додаткового тексту:
{"category": "front/kyiv/alarm/allies/russia/strikes/none", "is_fake": true, "fake_reason": "коротко або порожньо"}"""

        data = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 150,
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
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            txt = result["content"][0]["text"].strip()
            if "```" in txt:
                txt = txt.split("```")[1].replace("json", "").strip()
            import re as _re
            match = _re.search(r'\{.*\}', txt, _re.DOTALL)
            if match:
                txt = match.group(0)
            parsed = json.loads(txt)
            category = parsed.get("category", "none")
            is_fake = bool(parsed.get("is_fake", False))
            fake_reason = parsed.get("fake_reason", "")

            if category not in VALID_CATEGORIES:
                category = None

            print("    AI: категория=" + str(category) + " фейк=" + str(is_fake))
            return category, is_fake, fake_reason
    except Exception as e:
        print("    Ошибка AI: " + str(e))
        # резервный вариант — пробуем по ключевым словам, фейк не помечаем
        fallback_cat = get_category_simple(text)
        return fallback_cat, False, ""

def get_category_simple(text):
    """Резервная категоризация по ключевым словам (если AI недоступен)"""
    text_lower = text.lower()
    cats = {
        "front":   ["фронт", "передок", "наступ", "зсу", "бригада", "позиції", "бої", "штурм"],
        "kyiv":    ["київ", "kyiv", "столиця", "київська"],
        "alarm":   ["тривога", "ракета", "шахед", "бпл", "вибух", "ппо", "обстріл"],
        "allies":  ["сша", "нато", "допомога", "зброя", "байден", "трамп"],
        "russia":  ["москва", "кремль", "путін", "росія", "рф"],
        "strikes": ["приліт", "удар по росії", "бпла атакував", "горить в росії"],
    }
    for cat, keywords in cats.items():
        for kw in keywords:
            if kw in text_lower:
                return cat
    return None  # не подходит ни под одну — не публикуем

# === ДЕДУПЛИКАЦИЯ ПОХОЖИХ НОВОСТЕЙ ===
recent_published = []  # список (timestamp, set_слов, text)
DEDUP_SIMILARITY_THRESHOLD = 0.55  # доля общих слов, выше которой считаем дублем
DEDUP_WINDOW_HOURS = 1

def normalize_words(text):
    import re
    text = text.lower()
    text = re.sub(r'[^\w\sа-яіїєґa-z0-9]', ' ', text)
    words = [w for w in text.split() if len(w) > 3]
    return set(words)

def prune_recent_published():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=DEDUP_WINDOW_HOURS)
    while recent_published and recent_published[0][0] < cutoff:
        recent_published.pop(0)

def is_duplicate_news(text):
    """Проверяет, не публиковалась ли уже похожая новость за последний час"""
    prune_recent_published()
    words = normalize_words(text)
    if not words:
        return False
    for _, other_words, _ in recent_published:
        if not other_words:
            continue
        overlap = len(words & other_words)
        union = len(words | other_words)
        if union == 0:
            continue
        similarity = overlap / union
        if similarity >= DEDUP_SIMILARITY_THRESHOLD:
            return True
    return False

def register_published_news(text):
    recent_published.append((datetime.now(timezone.utc), normalize_words(text), text))

def get_video_duration(msg):
    try:
        for attr in msg.media.document.attributes:
            if attr.__class__.__name__ == "DocumentAttributeVideo":
                secs = int(attr.duration)
                return str(secs // 60).zfill(2) + ":" + str(secs % 60).zfill(2)
    except Exception:
        pass
    return None

def is_video(msg):
    if not isinstance(msg.media, MessageMediaDocument):
        return False
    for attr in msg.media.document.attributes:
        if attr.__class__.__name__ == "DocumentAttributeVideo":
            return True
    return False

def get_video_size(msg):
    try:
        return msg.media.document.size
    except Exception:
        return 999999999

async def upload_to_tg_bot(img_bytes, filetype="photo"):
    try:
        boundary = "boundary789"
        field = "photo" if filetype == "photo" else "video"
        filename = "photo.jpg" if filetype == "photo" else "video.mp4"
        content_type = "image/jpeg" if filetype == "photo" else "video/mp4"
        endpoint = "sendPhoto" if filetype == "photo" else "sendVideo"
        part1 = (
            "--" + boundary + "\r\n"
            + 'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            + TELEGRAM_CHAT_ID + "\r\n"
            + "--" + boundary + "\r\n"
            + 'Content-Disposition: form-data; name="' + field + '"; filename="' + filename + '"\r\n'
            + "Content-Type: " + content_type + "\r\n\r\n"
        ).encode()
        part2 = ("\r\n--" + boundary + "--\r\n").encode()
        body = part1 + img_bytes + part2
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/" + endpoint,
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read())
            if result.get("ok"):
                if filetype == "photo":
                    file_id = result["result"]["photo"][-1]["file_id"]
                else:
                    file_id = result["result"]["video"]["file_id"]
                req2 = urllib.request.Request(
                    "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/getFile?file_id=" + file_id
                )
                with urllib.request.urlopen(req2, timeout=10) as r2:
                    file_info = json.loads(r2.read())
                    if file_info.get("ok"):
                        file_path = file_info["result"]["file_path"]
                        return "https://api.telegram.org/file/bot" + TELEGRAM_BOT_TOKEN + "/" + file_path
    except Exception as e:
        print("    Ошибка загрузки: " + str(e))
    return None

async def get_photo_url(client, msg):
    try:
        img_bytes = await client.download_media(msg.media, bytes)
        if not img_bytes:
            return None
        return await upload_to_tg_bot(img_bytes, "photo")
    except Exception as e:
        print("    Ошибка фото: " + str(e))
    return None

async def get_video_thumb(client, msg):
    try:
        thumbs = msg.media.document.thumbs
        if thumbs:
            thumb_bytes = await client.download_media(msg.media, bytes, thumb=-1)
            if thumb_bytes:
                return await upload_to_tg_bot(thumb_bytes, "photo")
    except Exception as e:
        print("    Ошибка превью: " + str(e))
    return None

async def get_video_url(client, msg):
    try:
        size = get_video_size(msg)
        if size > 19 * 1024 * 1024:
            print("    Видео большое — только превью")
            return None, await get_video_thumb(client, msg)
        video_bytes = await client.download_media(msg.media, bytes)
        if not video_bytes:
            return None, None
        url = await upload_to_tg_bot(video_bytes, "video")
        return url, None
    except Exception as e:
        print("    Ошибка видео: " + str(e))
    return None, None

def push_to_api(news_items):
    try:
        data = json.dumps({"news": news_items}).encode()
        req = urllib.request.Request(
            API_URL + "/push",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            print("  Сохранено в БД: " + str(result))
    except Exception as e:
        print("  Ошибка отправки в API: " + str(e))

async def send_tg(text):
    url = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

async def process_channel(client, ch, seen):
    new_items = []
    new_seen_ids = []
    try:
        messages = await client.get_messages(ch["username"], limit=10)
        for msg in messages:
            news_id = make_id(ch["username"], msg.id)
            if news_id in seen:
                continue

            # Помечаем как просмотренное в любом случае, чтобы не анализировать повторно
            new_seen_ids.append(news_id)
            seen.add(news_id)

            # Фильтр свежести — только свежак
            if not is_fresh(msg.date):
                continue

            text = msg.text or msg.message or ""
            if len(text) < 10:
                continue

            img_url = None
            video_url = None
            media_type = None
            video_duration = None

            if msg.media and isinstance(msg.media, MessageMediaPhoto):
                img_url = await get_photo_url(client, msg)
                media_type = "photo"
                if img_url:
                    print("    Фото! @" + ch["username"])

            elif msg.media and is_video(msg):
                video_duration = get_video_duration(msg)
                video_url, thumb_url = await get_video_url(client, msg)
                if video_url:
                    img_url = thumb_url or await get_video_thumb(client, msg)
                    media_type = "video"
                else:
                    img_url = thumb_url
                    media_type = "video_link"

            # AI анализ: категория + фейк
            category, is_fake, fake_reason = await analyze_with_ai(text, ch["username"])

            # Не подходит ни под одну категорию — пропускаем
            if category is None:
                print("    Пропуск: не подходит ни под одну категорию")
                continue

            # Похоже на фейк — пропускаем
            if is_fake:
                print("    Пропуск: похоже на фейк (" + fake_reason + ")")
                continue

            # Дубликат уже опубликованной новости (та же тема из другого канала)
            if is_duplicate_news(text):
                print("    Пропуск: дубликат уже опубликованной новости")
                continue
            register_published_news(text)

            link = "https://t.me/" + ch["username"] + "/" + str(msg.id)
            title = text[:100].split("\n")[0]
            body = text[:600]

            item = {
                "id": news_id,
                "ch": ch["username"],
                "name": ch["name"],
                "emoji": ch["emoji"],
                "title": title,
                "body": body,
                "img": img_url,
                "video_url": video_url,
                "media_type": media_type,
                "video_duration": video_duration,
                "category": category,
                "link": link,
                "time": str(msg.date),
            }
            new_items.append(item)

        if new_items:
            print("  + @" + ch["username"] + ": " + str(len(new_items)) + " новых")
        else:
            print("  - @" + ch["username"] + ": нет новых")

    except Exception as e:
        print("  Ошибка " + ch["username"] + ": " + str(e))

    return new_items, new_seen_ids

async def run():
    init_seen_table()
    init_pending_table()
    seen = load_seen()
    print("Запускаю Telethon...")

    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        print("Telethon подключён!")

        try:
            await send_tg("NewsFeed с AI категориями запущен! 🚀")
        except Exception as e:
            print("Ошибка Telegram: " + str(e))

        last_discovery = datetime.now() - timedelta(hours=DISCOVERY_INTERVAL_HOURS)  # сразу при старте

        while True:
            now = datetime.now().strftime("%H:%M")
            print("[" + now + "] Проверяю все каналы параллельно...")

            active_channels = CHANNELS + get_approved_channels()
            tasks = [process_channel(client, ch, seen) for ch in active_channels]
            results = await asyncio.gather(*tasks)

            all_new_items = []
            all_new_seen = []

            for new_items, new_seen_ids in results:
                all_new_items.extend(new_items)
                all_new_seen.extend(new_seen_ids)

            if all_new_items:
                push_to_api(all_new_items)
                for item in all_new_items[:2]:
                    cat_emoji = CATEGORY_EMOJI.get(item.get("category", ""), "📰")
                    msg_text = (
                        cat_emoji + " " + item["emoji"] + " <b>@" + item["ch"] + "</b>\n\n"
                        + "<b>" + item["title"] + "</b>\n\n"
                        + item["body"][:400] + "\n\n"
                        + "<a href='" + item["link"] + "'>Читать в Telegram</a>"
                    )
                    await send_tg(msg_text)
                    await asyncio.sleep(2)

            if all_new_seen:
                save_seen_ids(all_new_seen)

            # Поиск новых каналов раз в DISCOVERY_INTERVAL_HOURS
            if datetime.now() - last_discovery >= timedelta(hours=DISCOVERY_INTERVAL_HOURS):
                await discover_channels(client)
                last_discovery = datetime.now()

            print("Жду " + str(CHECK_INTERVAL) + " сек...\n")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
