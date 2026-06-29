import asyncio
import json
import os
import hashlib
from datetime import datetime
import urllib.request
import urllib.parse
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaWebPage

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

async def get_photo_url(client, msg, ch_username, msg_id):
    """Скачиваем фото и отправляем боту чтобы получить file_id"""
    try:
        img_bytes = await client.download_media(msg.media, bytes)
        if not img_bytes:
            return None

        # Отправляем фото боту и получаем file_id
        boundary = "boundary789"
        part1 = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{TELEGRAM_CHAT_ID}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="photo.jpg"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n"
        ).encode()
        part2 = f"\r\n--{boundary}--\r\n".encode()
        body = part1 + img_bytes + part2

        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            result = json.loads(r.read())
            if result.get("ok"):
                photos = result["result"]["photo"]
                file_id = photos[-1]["file_id"]
                # Получаем прямую ссылку
                req2 = urllib.request.Request(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
                )
                with urllib.request.urlopen(req2, timeout=10) as r2:
                    file_info = json.loads(r2.read())
                    if file_info.get("ok"):
                        file_path = file_info["result"]["file_path"]
                        return f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    except Exception as e:
        print(f"    Ошибка фото: {e}")
    return None

def push_to_api(news_items):
    try:
        data = json.dumps({"news": news_items}).encode()
        req = urllib.request.Request(
            f"{API_URL}/push",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
            print(f"  Сохранено в БД: {result}")
    except Exception as e:
        print(f"  Ошибка отправки в API: {e}")

async def send_tg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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
            await send_tg("✅ <b>NewsFeed запущен с фото!</b> 🚀")
        except Exception as e:
            print(f"Ошибка Telegram: {e}")

        while True:
            print(f"[{datetime.now().strftime('%H:%M')}]
