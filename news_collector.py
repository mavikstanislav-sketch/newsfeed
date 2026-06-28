import asyncio
import feedparser
import json
import os
import hashlib
from datetime import datetime
from html import unescape
import re

TELEGRAM_BOT_TOKEN = "8798274501:AAGUCgF9bz6_w2VeTvy1CK_L4-6G4u7SGSM"
TELEGRAM_CHAT_ID   = "8761012731"

CHANNELS = [
    {"username": "ssternenko",    "name": "Стерненко",     "emoji": "🇺🇦"},
    {"username": "vach_govorit",  "name": "Вач говорит",   "emoji": "🎙"},
    {"username": "lachentyt",     "name": "Лаченко",       "emoji": "📢"},
    {"username": "vanek_nikolaev","name": "Ваня Николаев", "emoji": "👤"},
    {"username": "truexanewsua",  "name": "TrueXA News",   "emoji": "📰"},
]

CHECK_INTERVAL = 300
SEEN_FILE = "seen_news.json"

def clean_html(text):
    text = re.sub(r'<[^>]+>', '', text)
    return unescape(text).strip()

def make_id(url, title):
    return hashlib.md5((url + title).encode()).hexdigest()[:16]

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)

def fetch_news(channel):
    urls = [
        f"https://tg.i-c-a.su/rss/{channel['username']}",
        f"https://rsshub.app/telegram/channel/{channel['username']}",
    ]
    for url in urls:
        try:
            feed = feedparser.parse(url)
            if feed.entries:
                items = []
                for entry in feed.entries[:3]:
                    title = clean_html(entry.get("title", ""))
                    body  = clean_html(entry.get("summary", ""))
                    link  = entry.get("link", "")
                    if len(title) < 5 and len(body) < 10:
                        continue
                    items.append({
                        "id": make_id(link, title),
                        "title": title[:300],
                        "body": body[:600],
                        "link": link,
                        "channel": channel,
                    })
                return items
        except Exception as e:
            print(f"    ошибка: {e}")
    return []

async def send_tg(text):
    import urllib.request, urllib.parse
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
    print("Бот запущен!")

    try:
        await send_tg("✅ <b>NewsFeed бот запущен!</b>\n\nРаботаю 24/7\n\n" +
            "\n".join(f"{c['emoji']} @{c['username']}" for c in CHANNELS))
        print("Стартовое сообщение отправлено!")
    except Exception as e:
        print(f"Ошибка Telegram: {e}")
        return

    while True:
        print(f"[{datetime.now().strftime('%H:%M')}] Проверяю новости...")
        for ch in CHANNELS:
            try:
                items = fetch_news(ch)
                new_items = [i for i in items if i["id"] not in seen]
                if not new_items:
                    print(f"  - @{ch['username']}: нет новых")
                    continue
                print(f"  + @{ch['username']}: {len(new_items)} новых")
                for item in new_items[:2]:
                    msg = (
                        f"{ch['emoji']} <b>@{ch['username']}</b>\n\n"
                        f"<b>{item['title']}</b>\n\n"
                        f"{item['body'][:400]}\n\n"
                        f"<a href='{item['link']}'>Читать в Telegram →</a>"
                    )
                    await send_tg(msg)
                    seen.add(item["id"])
                    print(f"    отправлено: {item['title'][:50]}")
                    await asyncio.sleep(3)
            except Exception as e:
                print(f"  Ошибка {ch['username']}: {e}")
        save_seen(seen)
        print("Жду 5 мин...\n")
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run())
