import asyncio
import json
import os
import hashlib
from datetime import datetime
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

CHANNELS = [
    {"username": "ssternenko",     "name": "Стерненко",     "emoji": "🇺🇦"},
    {"username": "vach_govorit",   "name": "Вач говорит",   "emoji": "🎙"},
    {"username": "lachentyt",      "name": "Лаченко",       "emoji": "📢"},
    {"username": "vanek_nikolaev", "name": "Ваня Николаев", "emoji": "👤"},
    {"username": "truexanewsua",   "name": "TrueXA News",   "emoji": "📰"},
]

CHECK_INTERVAL = 10

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

async def analyze_with_ai(text, channel):
    """Claude анализирует новость — категория + фейк"""
    if not CLAUDE_API_KEY:
        return get_category_simple(text), "unknown", ""
    try:
        prompt = """Проаналізуй цю новину з українського Telegram каналу.

Новина від @""" + channel + """:
""" + text[:500] + """

Відповідай ТІЛЬКИ у форматі JSON без зайвого тексту:
{
  "category": "одна з: front / kyiv / alarm / allies / russia / strikes / general",
  "is_fake": "true або false",
  "fake_reason": "коротко чому фейк або порожньо"
}

Категорії:
- front: фронт, бої, ЗСУ, наступ, позиції, бригада
- kyiv: Київ, столиця, київські новини
- alarm: тривога, ракета, шахед, БпЛ, вибух, ППО, обстріл
- allies: США, НАТО, допомога, зброя, союзники
- russia: Росія, Москва, Кремль, Путін, РФ
- strikes: прильоти в РФ, удари по росії, БПЛА атакував РФ
- general: все інше"""

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
            # Чистим JSON если есть лишнее
            if "```" in txt:
                txt = txt.split("```")[1].replace("json", "").strip()
            parsed = json.loads(txt)
            category = parsed.get("category", "general")
            is_fake = parsed.get("is_fake", "false") == "true"
            fake_reason = parsed.get("fake_reason", "")
            print("    AI: категория=" + category + " фейк=" + str(is_fake))
            return category, is_fake, fake_reason
    except Exception as e:
        print("    Ошибка AI: " + str(e))
        return get_category_simple(text), False, ""

def get_category_simple(text):
    """Резервная категоризация по ключевым словам"""
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
    return "general"

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

            # AI анализ
            category, is_fake, fake_reason = await analyze_with_ai(text, ch["username"])

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
                "is_fake": is_fake,
                "fake_reason": fake_reason,
                "link": link,
                "time": str(msg.date),
            }
            new_items.append(item)
            seen.add(news_id)
            new_seen_ids.append(news_id)

        if new_items:
            print("  + @" + ch["username"] + ": " + str(len(new_items)) + " новых")
        else:
            print("  - @" + ch["username"] + ": нет новых")

    except Exception as e:
        print("  Ошибка " + ch["username"] + ": " + str(e))

    return new_items, new_seen_ids

async def run():
    init_seen_table()
    seen = load_seen()
    print("Запускаю Telethon...")

    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        print("Telethon подключён!")

        try:
            await send_tg("NewsFeed с AI категориями запущен! 🚀")
        except Exception as e:
            print("Ошибка Telegram: " + str(e))

        while True:
            now = datetime.now().strftime("%H:%M")
            print("[" + now + "] Проверяю все каналы параллельно...")

            tasks = [process_channel(client, ch, seen) for ch in CHANNELS]
            results = await asyncio.gather(*tasks)

            all_new_items = []
            all_new_seen = []

            for new_items, new_seen_ids in results:
                all_new_items.extend(new_items)
                all_new_seen.extend(new_seen_ids)

            if all_new_items:
                push_to_api(all_new_items)
                for item in all_new_items[:2]:
                    cat_emoji = {"front":"⚔️","kyiv":"🏙","alarm":"🚨","allies":"🤝","russia":"🇷🇺","strikes":"💥"}.get(item.get("category",""), "📰")
                    fake_label = " ⚠️ МОЖЛИВИЙ ФЕЙК" if item.get("is_fake") else ""
                    msg_text = (
                        cat_emoji + " " + item["emoji"] + " <b>@" + item["ch"] + "</b>" + fake_label + "\n\n"
                        + "<b>" + item["title"] + "</b>\n\n"
                        + item["body"][:400] + "\n\n"
                        + "<a href='" + item["link"] + "'>Читать в Telegram</a>"
                    )
                    await send_tg(msg_text)
                    await asyncio.sleep(2)

            if all_new_seen:
                save_seen_ids(all_new_seen)

            print("Жду " + str(CHECK_INTERVAL) + " сек...\n")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
