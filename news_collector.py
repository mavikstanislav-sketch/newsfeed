import asyncio
import anthropic
import json
import os
import hashlib
from datetime import datetime

from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument

TELEGRAM_BOT_TOKEN = "8798274501:AAGUCgF9bz6_w2VeTvy1CK_L4-6G4u7SGSM"
TELEGRAM_CHAT_ID   = 8761012731
API_ID             = 37103823
API_HASH           = "ebbfc63eb333bd7130ace1a23df460e9"
ANTHROPIC_API_KEY  = "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"

CHANNELS = [
    "ssternenko",
    "vach_govorit",
    "lachentyt",
    "vanek_nikolaev",
    "truexanewsua",
]

CHECK_INTERVAL = 300
SEEN_FILE = "seen_news.json"

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-1000:], f)

def get_ai_comment(text, channel):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content":
            f"Пост из канала @{channel}:\n\n{text[:800]}\n\n"
            "Напиши комментарий 2-3 предложения на русском: суть + мнение. Без вводных слов."
        }]
    )
    return msg.content[0].text.strip()

async def send_tg_text(bot_token, chat_id, text):
    import urllib.request, urllib.parse
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

async def send_tg_photo(bot_token, chat_id, photo_path, caption):
    import urllib.request
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    with open(photo_path, "rb") as f:
        photo_data = f.read()
    import urllib.parse
    boundary = "boundary123"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption[:1000]}\r\n'
        f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo"; filename="photo.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

async def main():
    seen = load_seen()
    print("Запуск бота с поддержкой фото и видео...")

    async with TelegramClient("newsfeed_session", API_ID, API_HASH) as client:
        print("Подключён к Telegram!")

        await send_tg_text(
            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
            "✅ <b>NewsFeed запущен!</b>\n\nТеперь с фото и видео!\n\n" +
            "\n".join(f"@{ch}" for ch in CHANNELS)
        )

        while True:
            print(f"[{datetime.now().strftime('%H:%M')}] Проверяю каналы...")

            for channel in CHANNELS:
                try:
                    async for msg in client.iter_messages(channel, limit=5):
                        msg_id = f"{channel}_{msg.id}"
                        if msg_id in seen:
                            continue

                        text = msg.text or msg.message or ""
                        if len(text) < 10 and not msg.media:
                            seen.add(msg_id)
                            continue

                        try:
                            comment = get_ai_comment(text or "пост без текста", channel)
                        except:
                            comment = ""

                        caption = f"📢 <b>@{channel}</b>\n\n{text[:600]}"
                        if comment:
                            caption += f"\n\n<i>{comment}</i>"

                        if isinstance(msg.media, MessageMediaPhoto):
                            photo_path = await client.download_media(msg.media, file="temp_photo.jpg")
                            if photo_path:
                                await send_tg_photo(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, photo_path, caption)
                                os.remove(photo_path)
                            else:
                                await send_tg_text(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, caption)
                        else:
                            await send_tg_text(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, caption)

                        seen.add(msg_id)
                        print(f"  + @{channel}: отправлено")
                        await asyncio.sleep(3)

                except Exception as e:
                    print(f"  Ошибка @{channel}: {e}")

            save_seen(seen)
            print(f"Жду 5 мин...\n")
            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())