import asyncio
import json
import os
import hashlib
from datetime import datetime
import urllib.request
import urllib.parse
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

TELEGRAM_BOT_TOKEN = "8798274501:AAGUCgF9bz6_w2VeTvy1CK_L4-6G4u7SGSM"
TELEGRAM_CHAT_ID   = "8761012731"
API_URL = "https://newsfeed-production-9b3b.up.railway.app"
API_ID   = 37103823
API_HASH = "ebbfc63eb333bd7130ace1a23df460e9"
SESSION  = "news_session"

CHANNELS = [
    {"username": "ssternenko",     "name": "Стерненко",     "emoji": "🇺🇦"},
    {"username": "vach_govorit",   "name": "Вач говорит",   "emoji": "🎙"},
    {"username": "lachentyt",      "name": "Лаченко",       "emoji": "📢"},
    {"username": "vanek_nikolaev", "name": "Ваня Николаев", "emoji": "👤"},
    {"username": "truexanewsua",   "name": "TrueXA News",   "emoji": "📰"},
]

CHECK_INTERVAL = 60
SEEN_FILE = "seen_news.json"

def make_id(channel, msg_id):
    return hashlib.md5(f"{channel}{msg_id}".encode()).hexdigest()[:16]

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)

async def upload_media(client, msg, media_type="photo"):
    try:
        img_bytes = await client.download_media(msg.media, bytes)
        if not img_bytes:
            return None

        boundary = "boundary789"
        content_type = "image/jpeg" if media_type == "photo" else "video/mp4"
        filename = "photo.jpg" if media_type == "photo" else "video.mp4"
        endpoint = "sendPhoto" if media_type == "photo" else "sendVideo"
        field = "photo" if media_type == "photo" else "video"

        part1 = (
            "--" + boundary + "\r\n"
            'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            + TELEGRAM_CHAT_ID + "\r\n"
            "--" + boundary + "\r\n"
            'Content-Disposition: form-data; name="' + field + '"; filename="' + filename + '"\r\n'
            "Content-Type: " + content_type + "\r\n\r\n"
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
                if media_type == "photo":
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
        print("    Ошибка медиа: " + str(e))
    return None

def is_video(msg):
    if not isinstance(msg.media, MessageMediaDocument):
        return False
    doc = msg.media.document
    for attr in doc.attributes:
        if hasattr(attr, 'round_message') or attr.__class__.__name__ == 'DocumentAttributeVideo':
            return True
    return False

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

async def run():
    seen = load_seen()
    print("Запускаю Telethon...")

    async with TelegramClient(SESSION, API_ID, API_HASH) as client:
        print("Telethon подключён!")

        try:
            await send_tg("✅ <b>NewsFeed запущен с фото и видео!</b> 🚀")
        except Exception as e:
            print("Ошибка Telegram: " + str(e))

        while True:
            now = datetime.now().strftime("%H:%M")
            print("[" + now + "] Проверяю новости...")
            all_new_items = []

            for ch in CHANNELS:
                try:
                    messages = await client.get_messages(ch["username"], limit=10)
                    new_items = []

                    for msg in messages:
                        news_id = make_id(ch["username"], msg.id)
                        if news_id in seen:
                            continue

                        text = msg.text or msg.message or ""
                        if len(text) < 10:
                            continue

                        img_url = None
                        media_type = None

                        if msg.media and isinstance(msg.media, MessageMediaPhoto):
                            img_url = await upload_media(client, msg, "photo")
                            media_type = "photo"
                            if img_url:
                                print("    📷 Фото готово!")

                        elif msg.media and is_video(msg):
                            img_url = await upload_media(client, msg, "video")
                            media_type = "video"
                            if img_url:
                                print("    🎥 Видео готово!")

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
                            "media_type": media_type,
                            "link": link,
                            "time": str(msg.date),
                        }
                        new_items.append(item)
                        seen.add(news_id)

                    if not new_items:
                        print("  - @" + ch["username"] + ": нет новых")
                        continue

                    print("  + @" + ch["username"] + ": " + str(len(new_items)) + " новых")
                    all_new_items.extend(new_items)

                    for item in new_items[:2]:
                        msg_text = (
                            ch["emoji"] + " <b>@" + ch["username"] + "</b>\n\n"
                            "<b>" + item["title"] + "</b>\n\n"
                            + item["body"][:400] + "\n\n"
                            "<a href='" + item["link"] + "'>Читать в Telegram →</a>"
                        )
                        await send_tg(msg_text)
                        await asyncio.sleep(3)

                except Exception as e:
                    print("  Ошибка " + ch["username"] + ": " + str(e))

            if all_new_items:
                push_to_api(all_new_items)

            save_seen(seen)
            print("Жду 1 мин...\n")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
