import os, re, json, time, html
import requests
import feedparser

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
CHANNEL = os.environ["TELEGRAM_CHANNEL"]

MODEL = "gemini-flash-latest"
MAX_POSTS = 5
STATE_FILE = "posted.json"

FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://hnrss.org/frontpage?points=200",
]


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_state(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-1500:], f)


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:1500]


def fetch_entries():
    items = {}
    for url in FEEDS:
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0")
            for e in feed.entries[:15]:
                link = e.get("link")
                if not link or link in items:
                    continue
                ts = time.mktime(e.published_parsed) if e.get("published_parsed") else 0
                items[link] = {
                    "link": link,
                    "title": clean(e.get("title", "")),
                    "summary": clean(e.get("summary", "")),
                    "ts": ts,
                }
        except Exception as ex:
            print("Feed error:", url, ex)
    return sorted(items.values(), key=lambda x: x["ts"], reverse=True)


def translate(item):
    prompt = (
        "তুমি একজন বাংলা টেক নিউজ এডিটর। নিচের ইংরেজি খবরটি বাংলায় লেখো।\n"
        "নিয়ম: প্রথম লাইনে ছোট আকর্ষণীয় শিরোনাম, তারপর একটা ফাঁকা লাইন, "
        "তারপর ২-৩ বাক্যের সহজ সারাংশ। কোম্পানি, প্রোডাক্ট ও টেকনিক্যাল শব্দ "
        "(যেমন OpenAI, GPT, API) ইংরেজিতেই রাখো। মার্কডাউন, তারকাচিহ্ন বা "
        "অতিরিক্ত মন্তব্য দিও না। শুধু শিরোনাম আর সারাংশ দাও।\n\n"
        f"Title: {item['title']}\nSummary: {item['summary']}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    for _ in range(2):
        r = requests.post(
            url,
            headers={"x-goog-api-key": GEMINI_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if r.status_code == 429:
            time.sleep(40)
            continue
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise RuntimeError("Gemini rate limited")


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHANNEL, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )
    if not r.ok:
        print("Telegram error:", r.text)
    r.raise_for_status()


def main():
    seen = load_state()
    seen_set = set(seen)
    new = [i for i in fetch_entries() if i["link"] not in seen_set]
    to_post, skipped = new[:MAX_POSTS], new[MAX_POSTS:]
    seen += [i["link"] for i in skipped]

    for item in to_post:
        try:
            bn = translate(item)
            parts = bn.split("\n", 1)
            head = parts[0].strip().strip("*#").strip()
            body = parts[1].strip() if len(parts) > 1 else ""
            link = html.escape(item["link"], quote=True)
            msg = (
                f"<b>{html.escape(head)}</b>\n\n"
                f"{html.escape(body)}\n\n"
                f'🔗 <a href="{link}">মূল খবর পড়ুন</a>'
            )
            send(msg)
            seen.append(item["link"])
            print("Posted:", item["title"])
        except Exception as ex:
            print("Failed:", item["title"], ex)
        time.sleep(8)

    save_state(seen)


if __name__ == "__main__":
    main()
