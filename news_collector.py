import asyncio
import json
import os
import hashlib
from datetime import datetime
import urllib.request
import urllib.parse
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto

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
            await send_tg("✅ <b>NewsFeed запущен через Telethon!</b>\n\nТеперь новости с фото и без задержки! 🚀")
        except Exception as e:
            print(f"Ошибка Telegram: {e}")

        while True:
            print(f"[{datetime.now().strftime('%H:%M')}] Проверяю новости...")
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

                        # Фото
                        img_url = None
                        if msg.media and isinstance(msg.media, MessageMediaPhoto):
                            try:
                                img_path = f"/tmp/{news_id}.jpg"
                                await client.download_media(msg.media, img_path)
                                img_url = img_path
                            except:
                                pass

                        link = f"https://t.me/{ch['username']}/{msg.id}"
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
                            "link": link,
                            "time": str(msg.date),
                        }
                        new_items.append(item)
                        seen.add(news_id)

                    if not new_items:
                        print(f"  - @{ch['username']}: нет новых")
                        continue

                    print(f"  + @{ch['username']}: {len(new_items)} новых")
                    all_new_items.extend(new_items)

                    for item in new_items[:2]:
                        msg_text = (
                            f"{ch['emoji']} <b>@{ch['username']}</b>\n\n"
                            f"<b>{item['title']}</b>\n\n"
                            f"{item['body'][:400]}\n\n"
                            f"<a href='{item['link']}'>Читать в Telegram →</a>"
                        )
                        await send_tg(msg_text)
                        await asyncio.sleep(3)

                except Exception as e:
                    print(f"  Ошибка {ch['username']}: {e}")

            if all_new_items:
                push_to_api(all_new_items)

            save_seen(seen)
            print("Жду 1 мин...\n")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
