from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
import asyncio
import os
import base64
import hashlib

API_ID = 37103823
API_HASH = "ebbfc63eb333bd7130ace1a23df460e9"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CHANNELS = [
    {"username": "ssternenko",    "name": "Стерненко",     "emoji": "🇺🇦"},
    {"username": "vach_govorit",  "name": "Вач говорит",   "emoji": "🎙"},
    {"username": "lachentyt",     "name": "Лаченко",       "emoji": "📢"},
    {"username": "vanek_nikolaev","name": "Ваня Николаев", "emoji": "👤"},
    {"username": "truexanewsua",  "name": "TrueXA News",   "emoji": "📰"},
]

client = TelegramClient("session", API_ID, API_HASH)

@app.on_event("startup")
async def startup():
    await client.start()

@app.on_event("shutdown")
async def shutdown():
    await client.disconnect()

@app.get("/")
def root():
    return {"status": "NewsFeed API с медиа работает!"}

@app.get("/news")
async def get_news():
    all_news = []
    for ch in CHANNELS:
        try:
            entity = await client.get_entity(ch["username"])
            messages = await client.get_messages(entity, limit=5)
            for msg in messages:
                if not msg.text and not msg.message:
                    continue
                text = msg.message or msg.text or ""
                if len(text) < 10:
                    continue

                img_url = None
                media_type = None

                # Фото
                if isinstance(msg.media, MessageMediaPhoto):
                    try:
                        photo_bytes = await client.download_media(msg.media, bytes)
                        img_b64 = base64.b64encode(photo_bytes).decode()
                        img_url = f"data:image/jpeg;base64,{img_b64}"
                        media_type = "photo"
                    except:
                        pass

                # Видео
                elif isinstance(msg.media, MessageMediaDocument):
                    if msg.media.document.mime_type.startswith("video"):
                        media_type = "video"
                        # для видео берём превью если есть
                        try:
                            thumb_bytes = await client.download_media(msg.media, bytes, thumb=-1)
                            if thumb_bytes:
                                thumb_b64 = base64.b64encode(thumb_bytes).decode()
                                img_url = f"data:image/jpeg;base64,{thumb_b64}"
                        except:
                            pass

                msg_id = hashlib.md5(f"{ch['username']}{msg.id}".encode()).hexdigest()[:16]

                all_news.append({
                    "id": msg_id,
                    "ch": ch["username"],
                    "name": ch["name"],
                    "emoji": ch["emoji"],
                    "title": text[:100].split('\n')[0],
                    "body": text[:600],
                    "img": img_url,
                    "media_type": media_type,
                    "link": f"https://t.me/{ch['username']}/{msg.id}",
                    "time": msg.date.isoformat() if msg.date else "",
                })
        except Exception as e:
            print(f"Ошибка {ch['username']}: {e}")

    print(f"Загружено новостей: {len(all_news)}")
    return {"news": all_news, "total": len(all_news)}
