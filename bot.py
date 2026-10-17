import os, re, json, time, html
from urllib.parse import urlparse
import requests
import feedparser
import trafilatura

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

PROMPT = """তুমি বাংলাদেশের একজন জনপ্রিয় টেক নিউজ লেখক। নিচের ইংরেজি খবরটি পড়ে পুরো খবরটা নিজের ভাষায় বিস্তারিত বাংলা প্রতিবেদন আকারে লেখো, যাতে পাঠককে মূল ইংরেজি লেখা আর পড়তে না হয়।

নিয়ম:
১. প্রথম লাইনে শুধু শিরোনাম: কৌতূহল জাগানো, কিন্তু ক্লিকবেইট বা মিথ্যা নয়। শুরুতে একটা ইমোজি দিতে পারো।
২. তারপর একটা ফাঁকা লাইন।
৩. তারপর মূল প্রতিবেদন: ২৫০ থেকে ৩৫০ শব্দ, ৩-৫টা ছোট অনুচ্ছেদ, অনুচ্ছেদের মাঝে ফাঁকা লাইন।
৪. ভাষা সহজ ও কথোপকথনের ঢঙে, যেন বন্ধুকে মজা করে গল্প শোনাচ্ছ। মাঝে মাঝে মানানসই ইমোজি ব্যবহার করো, তবে বেশি নয়।
৫. কঠিন টেকনিক্যাল শব্দ সহজ উদাহরণ বা তুলনা দিয়ে বুঝিয়ে দাও।
৬. মূল লেখার সব গুরুত্বপূর্ণ তথ্য, নাম, সংখ্যা ও তারিখ ঠিকঠাক রাখো। নিজে কিছু বানিয়ে যোগ করো না।
৭. শেষ অনুচ্ছেদ শুরু করো "💡 কেন গুরুত্বপূর্ণ:" দিয়ে, এক-দুই লাইনে বলো এটা সাধারণ মানুষের জন্য কেন জরুরি।
৮. কোম্পানি, প্রোডাক্ট ও টেকনিক্যাল নামগুলো (OpenAI, GPT, API ইত্যাদি) ইংরেজিতে রাখো।
৯. মার্কডাউন চিহ্ন (*, #) ব্যবহার করো না। কোনো ভূমিকা বা অতিরিক্ত মন্তব্য দিও না।

খবর:
"""


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_state(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-1500:], f)


def clean(text, limit=1500):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


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


def get_article_text(item):
    try:
        downloaded = trafilatura.fetch_url(item["link"])
        text = trafilatura.extract(downloaded) if downloaded else None
        if text and len(text) > 400:
            return text[:8000]
    except Exception as ex:
        print("Article fetch error:", ex)
    return item["summary"]


def write_bengali(item):
    article = get_article_text(item)
    prompt = f"{PROMPT}Title: {item['title']}\n\n{article}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    for _ in range(2):
        r = requests.post(
            url,
            headers={"x-goog-api-key": GEMINI_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=90,
        )
        if r.status_code == 429:
            time.sleep(40)
            continue
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    raise RuntimeError("Gemini rate limited")


def trim(body, limit=3400):
    if len(body) <= limit:
        return body
    cut = body[:limit]
    idx = max(cut.rfind("।"), cut.rfind("."))
    return cut[: idx + 1] if idx > 0 else cut


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={
            "chat_id": CHANNEL,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
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
            bn = write_bengali(item)
            parts = bn.split("\n", 1)
            head = parts[0].strip().strip("*#").strip()
            body = trim(parts[1].strip() if len(parts) > 1 else "")
            link = html.escape(item["link"], quote=True)
            domain = urlparse(item["link"]).netloc.replace("www.", "")
            msg = (
                f"<b>{html.escape(head)}</b>\n\n"
                f"{html.escape(body)}\n\n"
                f'📰 সূত্র: <a href="{link}">{html.escape(domain)}</a>'
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
