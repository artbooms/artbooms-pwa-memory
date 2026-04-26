#!/usr/bin/env python3
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


RSS_URL = "https://artbooms-rss-x6pc.onrender.com/rss"
ONESIGNAL_APP_ID = "5e419b44-dbb3-4af3-ae22-e921c52aa02f"
STATE_FILE = Path("data/last-notified.json")
ONESIGNAL_API_URL = "https://api.onesignal.com/notifications"


def log(message):
    print(f"[artbooms-rss-push] {message}", flush=True)


def fetch_url(url, timeout=30):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ARTBOOMS-PWA-RSS-Notifier/1.0 (+https://www.artbooms.com/)"
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def text_or_empty(value):
    return (value or "").strip()


def strip_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def clean_title(value):
    value = html.unescape(value or "")
    value = re.sub(r"\s+—\s+ARTBOOMS\s*$", "", value, flags=re.I)
    value = re.sub(r"\s+-\s+ARTBOOMS\s*$", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def truncate(value, limit):
    value = text_or_empty(value)
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def find_first_item(root):
    channel = root.find("channel")
    if channel is not None:
        item = channel.find("item")
        if item is not None:
            return item

    item = root.find(".//item")
    if item is not None:
        return item

    return None


def get_child_text(item, name):
    child = item.find(name)
    if child is None:
        return ""
    return text_or_empty(child.text)


def get_image_url(item):
    enclosure = item.find("enclosure")
    if enclosure is not None:
        url = enclosure.attrib.get("url", "").strip()
        media_type = enclosure.attrib.get("type", "").strip().lower()
        if url and (not media_type or media_type.startswith("image/")):
            return url.replace("http://", "https://")

    for child in list(item):
        tag = child.tag.lower()
        if tag.endswith("content") or tag.endswith("thumbnail"):
            url = child.attrib.get("url", "").strip()
            if url:
                return url.replace("http://", "https://")

    html_fields = [
        get_child_text(item, "description"),
        get_child_text(item, "content:encoded"),
    ]

    for value in html_fields:
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', value or "", flags=re.I)
        if match:
            return match.group(1).strip().replace("http://", "https://")

    return ""


def parse_latest_article(rss_bytes):
    root = ET.fromstring(rss_bytes)
    item = find_first_item(root)

    if item is None:
        raise RuntimeError("Nessun <item> trovato nel RSS.")

    title = clean_title(get_child_text(item, "title"))
    link = get_child_text(item, "link")
    guid = get_child_text(item, "guid")
    description = strip_html(get_child_text(item, "description"))
    pub_date = get_child_text(item, "pubDate")
    image_url = get_image_url(item)

    article_id = link or guid

    if not article_id:
        raise RuntimeError("Il primo articolo RSS non ha link/guid.")

    if not title:
        title = "Nuovo articolo ARTBOOMS"

    if not description:
        description = "È online un nuovo articolo su ARTBOOMS."

    return {
        "id": article_id.strip(),
        "url": link.strip() or guid.strip(),
        "title": truncate(title, 80),
        "message": truncate(description, 140),
        "pubDate": pub_date,
        "image": image_url,
    }


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(article, notified):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "last_article_id": article["id"],
        "last_article_url": article["url"],
        "last_article_title": article["title"],
        "last_article_pubDate": article.get("pubDate", ""),
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "last_action": "notified" if notified else "initialized_without_notification",
    }

    STATE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def send_onesignal_push(article):
    api_key = os.environ.get("ONESIGNAL_REST_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("Manca il secret ONESIGNAL_REST_API_KEY.")

    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "target_channel": "push",
        "included_segments": ["Subscribed Users"],
        "headings": {"it": "ARTBOOMS", "en": "ARTBOOMS"},
        "contents": {"it": article["title"], "en": article["title"]},
        "subtitle": {"it": article["message"], "en": article["message"]},
        "url": article["url"],
        "web_url": article["url"],
        "chrome_web_icon": "https://app.artbooms.com/icons/icon-192.png",
        "chrome_web_badge": "https://app.artbooms.com/icons/icon-192.png",
        "data": {
            "source": "artbooms-rss",
            "article_url": article["url"],
        },
    }

    if article.get("image"):
        payload["big_picture"] = article["image"]
        payload["chrome_web_image"] = article["image"]

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        ONESIGNAL_API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Key {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            log(f"OneSignal response {response.status}: {response_body}")
            return response.status, response_body
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OneSignal HTTP {exc.code}: {error_body}") from exc


def main():
    log(f"Lettura RSS: {RSS_URL}")
    rss_bytes = fetch_url(RSS_URL)
    article = parse_latest_article(rss_bytes)

    log(f"Primo articolo RSS: {article['title']}")
    log(f"URL: {article['url']}")

    state = load_state()
    previous_id = state.get("last_article_id", "")

    if not previous_id:
        log("Nessuno stato precedente: inizializzo senza inviare notifiche.")
        save_state(article, notified=False)
        return 0

    if previous_id == article["id"]:
        log("Nessun nuovo articolo: notifica non inviata.")
        return 0

    log("Nuovo articolo rilevato: invio push OneSignal.")
    send_onesignal_push(article)
    save_state(article, notified=True)
    log("Notifica inviata e stato aggiornato.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"ERRORE: {exc}")
        raise
