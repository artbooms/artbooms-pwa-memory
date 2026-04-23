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

NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9"
}


def clean_html_fragment(fragment_html: str) -> str:
    soup = BeautifulSoup(fragment_html, "html.parser")
    allowed = {"p", "h2", "h3", "h4", "blockquote", "ul", "ol", "li", "strong", "em", "br"}

    for tag in soup.find_all(True):
        if tag.name not in allowed:
            tag.unwrap()
            continue
        tag.attrs = {}

    return str(soup)


def normalize_title(title: str) -> str:
    if not title:
        return ""
    title = re.sub(r"\s+—\s+ARTBOOMS\s+—\s+ARTBOOMS\s*$", " — ARTBOOMS", title.strip(), flags=re.I)
    title = re.sub(r"\s+—\s+ARTBOOMS\s*$", "", title.strip(), flags=re.I)
    return title.strip()


def normalize_url(url: str) -> str:
    if not url:
        return ""
    return url.strip().replace("http://", "https://")


def extract_article_memory(article_url: str, title: str, pub_date: str):
    resp = requests.get(article_url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    description = ""
    meta_desc = soup.find("meta", attrs={"itemprop": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"].strip()

    image = ""
    thumb = soup.find("meta", attrs={"itemprop": "thumbnailUrl"})
    if thumb and thumb.get("content"):
        image = thumb["content"].strip()
    else:
        image_meta = soup.find("meta", attrs={"itemprop": "image"})
        if image_meta and image_meta.get("content"):
            image = image_meta["content"].strip()

    if not image:
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            image = og_image["content"].strip()

    image = normalize_url(image)

    selectors = [
        "article .sqs-html-content",
        ".blog-item-wrapper .sqs-html-content",
        "main .sqs-html-content",
        ".entry-content .sqs-html-content"
    ]

    blocks = []
    container_nodes = []
    for sel in selectors:
        container_nodes = soup.select(sel)
        if container_nodes:
            break

    for node in container_nodes:
        for el in node.find_all(["p", "h2", "h3", "h4", "blockquote", "ul", "ol"], recursive=True):
            txt = el.get_text(" ", strip=True)
            if txt:
                blocks.append(clean_html_fragment(str(el)))

    content_html = "\n".join(blocks).strip()
    if not content_html and description:
        content_html = f"<p>{description}</p>"

    return {
        "url": normalize_url(article_url),
        "title": normalize_title(title),
        "display_date": pub_date[:10] if pub_date else "",
        "excerpt": description,
        "image": image,
        "content_html": content_html
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
            "publication_date": pub_date
        })

    articles = []
    for item in urls[:3]:
        articles.append(
            extract_article_memory(
                item["url"],
                item["title"],
                item["publication_date"]
            )
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
