import json
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

NEWS_SITEMAP_URL = "https://artbooms-rss-x6pc.onrender.com/news-sitemap.xml"
OUTPUT_PATH = "memory-data.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip().replace("http://", "https://")
    if "images.squarespace-cdn.com" in url and "format=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}format=1500w"
    return url


def normalize_title(title: str) -> str:
    title = (title or "").strip()
    title = re.sub(r"\s+—\s+ARTBOOMS\s+—\s+ARTBOOMS\s*$", "", title, flags=re.I)
    title = re.sub(r"\s+—\s+ARTBOOMS\s*$", "", title, flags=re.I)
    return title.strip()


def img_src(tag):
    for attr in ("src", "data-src", "data-image"):
        val = tag.get(attr)
        if val:
            return normalize_url(val)

    srcset = tag.get("srcset") or tag.get("data-srcset")
    if srcset:
        parts = [p.strip().split(" ")[0] for p in srcset.split(",") if p.strip()]
        if parts:
            return normalize_url(parts[-1])

    return ""


def clean_text_html(fragment_html: str) -> str:
    soup = BeautifulSoup(fragment_html, "html.parser")
    allowed = {"p", "blockquote", "ul", "ol", "li", "strong", "em", "br"}

    for tag in soup.find_all(True):
        if tag.name in {"h2", "h3", "h4"}:
            tag.name = "p"

        if tag.name not in allowed:
            tag.unwrap()
            continue

        tag.attrs = {}

    return str(soup)


def make_figure(img_url: str, caption: str = "", alt: str = "") -> str:
    img_url = normalize_url(img_url)
    if not img_url:
        return ""

    caption = (caption or "").strip()
    alt_attr = (alt or "").replace('"', "&quot;")
    caption_html = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f'<figure><img src="{img_url}" alt="{alt_attr}">{caption_html}</figure>'


def is_caption_like(el) -> bool:
    classes = " ".join(el.get("class", [])).lower()
    return any(x in classes for x in ["caption", "image-caption", "sqs-image-caption"])


def extract_article_memory(article_url: str, title: str, pub_date: str):
    resp = requests.get(article_url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    description = ""
    meta_desc = soup.find("meta", attrs={"itemprop": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"].strip()

    main_image = ""
    for selector in [
        {"itemprop": "thumbnailUrl"},
        {"itemprop": "image"},
        {"property": "og:image"},
    ]:
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content"):
            main_image = normalize_url(tag["content"])
            break

    root = (
        soup.select_one("article")
        or soup.select_one(".blog-item-wrapper")
        or soup.select_one("main")
        or soup.select_one(".entry-content")
        or soup.body
        or soup
    )

    blocks = []
    seen_images = set()
    seen_captions = set()

    for el in root.find_all(["p", "h2", "h3", "h4", "blockquote", "ul", "ol", "figure", "img"], recursive=True):
        if el.find_parent("figure") and el.name != "figure":
            continue

        if is_caption_like(el):
            text = el.get_text(" ", strip=True)
            if text:
                seen_captions.add(text)
            continue

        if el.name == "img":
            src = img_src(el)
            if src and src not in seen_images:
                seen_images.add(src)
                blocks.append(make_figure(src, alt=el.get("alt", "")))
            continue

        if el.name == "figure":
            img = el.find("img")
            if not img:
                continue

            src = img_src(img)
            if not src or src in seen_images:
                continue

            seen_images.add(src)

            caption = ""
            cap_tag = el.find(["figcaption", "p"])
            if cap_tag:
                caption = cap_tag.get_text(" ", strip=True)
                if caption in seen_captions:
                    caption = ""
                elif caption:
                    seen_captions.add(caption)

            blocks.append(make_figure(src, caption, img.get("alt", "")))
            continue

        text = el.get_text(" ", strip=True)
        if not text:
            continue

        if text in seen_captions:
            continue

        blocks.append(clean_text_html(str(el)))

    content_html = "\n".join(blocks).strip()
    if not content_html and description:
        content_html = f"<p>{description}</p>"

    images = list(seen_images)
    if main_image and main_image not in images:
        images.insert(0, main_image)

    return {
        "url": normalize_url(article_url),
        "title": normalize_title(title),
        "display_date": pub_date[:10] if pub_date else "",
        "excerpt": description,
        "image": main_image,
        "images": images,
        "content_html": content_html,
    }


def main():
    r = requests.get(NEWS_SITEMAP_URL, headers=HEADERS, timeout=20)
    r.raise_for_status()

    root = ET.fromstring(r.text)
    urls = []

    for node in root.findall("sm:url", NS):
        loc = node.find("sm:loc", NS)
        if loc is None or not (loc.text or "").strip():
            continue

        article_url = loc.text.strip()
        title = ""
        pub_date = ""

        for child in node.iter():
            tag = child.tag.lower()
            if tag.endswith("title") and child.text:
                title = child.text.strip()
            if tag.endswith("publication_date") and child.text:
                pub_date = child.text.strip()

        urls.append({
            "url": article_url,
            "title": title,
            "publication_date": pub_date,
        })

    articles = [
        extract_article_memory(item["url"], item["title"], item["publication_date"])
        for item in urls[:3]
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
