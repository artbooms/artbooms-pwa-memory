import html
import json
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

RSS_FEED_URL = "https://artbooms-rss-x6pc.onrender.com/rss"
OUTPUT_PATH = "memory-data.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}


def normalize_url(url: str) -> str:
    if not url:
        return ""

    url = url.strip().replace("http://", "https://")

    if (
        ("images.squarespace-cdn.com" in url or "static1.squarespace.com" in url)
        and "format=" not in url
    ):
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}format=1500w"

    return url


def normalize_title(title: str) -> str:
    title = (title or "").strip()
    title = re.sub(r"\s+—\s+ARTBOOMS\s+—\s+ARTBOOMS\s*$", "", title, flags=re.I)
    title = re.sub(r"\s+—\s+ARTBOOMS\s*$", "", title, flags=re.I)
    return title.strip()


def normalize_date(value: str) -> str:
    value = (value or "").strip()

    if not value:
        return ""

    try:
        if "T" in value:
            return value[:10]

        parsed = parsedate_to_datetime(value)
        if parsed:
            return parsed.date().isoformat()
    except Exception:
        pass

    return value[:10]


def strip_html_text(fragment: str) -> str:
    soup = BeautifulSoup(fragment or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def image_key(url: str) -> str:
    url = normalize_url(url)

    try:
        path = urlparse(url).path
        name = unquote(path).split("/")[-1].lower()
        return name.split("?")[0]
    except Exception:
        return url.split("?")[0].lower()


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


def class_text(tag) -> str:
    classes = tag.get("class", [])

    if isinstance(classes, list):
        return " ".join(classes).lower()

    return str(classes).lower()


def is_caption_node(tag) -> bool:
    if not getattr(tag, "name", None):
        return False

    cls = class_text(tag)
    return tag.name == "figcaption" or "caption" in cls


def is_image_container(tag) -> bool:
    if not getattr(tag, "name", None):
        return False

    cls = class_text(tag)

    return (
        "image-block" in cls
        or "sqs-block-image" in cls
        or "image-block-outer-wrapper" in cls
        or "image-block-wrapper" in cls
    )


def clean_text_html(fragment_html: str) -> str:
    soup = BeautifulSoup(fragment_html, "html.parser")
    allowed = {
        "p",
        "h2",
        "h3",
        "h4",
        "blockquote",
        "ul",
        "ol",
        "li",
        "strong",
        "em",
        "br",
    }

    for tag in soup.find_all(True):
        if tag.name not in allowed:
            tag.unwrap()
            continue

        tag.attrs = {}

    return str(soup)


def caption_from_container(container) -> str:
    if not container:
        return ""

    figcaption = container.find("figcaption")
    if figcaption:
        return figcaption.get_text(" ", strip=True)

    caption_tag = container.find(
        class_=lambda c: c and "caption" in " ".join(c if isinstance(c, list) else [c]).lower()
    )
    if caption_tag:
        return caption_tag.get_text(" ", strip=True)

    return ""


def caption_for_image(img) -> str:
    figure = img.find_parent("figure")
    if figure:
        cap = caption_from_container(figure)
        if cap:
            return cap

    parent = img.parent
    depth = 0

    while parent is not None and depth < 8:
        cap = caption_from_container(parent)
        if cap:
            return cap

        parent = parent.parent
        depth += 1

    return ""


def make_figure(img_url: str, caption: str = "", alt: str = "") -> str:
    img_url = normalize_url(img_url)

    if not img_url:
        return ""

    safe_caption = html.escape(caption, quote=False) if caption else ""
    safe_alt = html.escape(alt, quote=True) if alt else ""
    caption_html = f"<figcaption>{safe_caption}</figcaption>" if safe_caption else ""

    return f'<figure><img src="{img_url}" alt="{safe_alt}">{caption_html}</figure>'


def read_rss_items():
    response = requests.get(RSS_FEED_URL, headers=HEADERS, timeout=25)
    response.raise_for_status()

    root = ET.fromstring(response.text)
    channel = root.find("channel")

    if channel is None:
        return []

    items = []

    for item in channel.findall("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = item.findtext("description") or ""

        if not link:
            continue

        items.append({
            "url": normalize_url(link),
            "title": normalize_title(title),
            "publication_date": normalize_date(pub_date),
            "excerpt": strip_html_text(description),
        })

        if len(items) == 3:
            break

    return items


def find_article_root(soup):
    selectors = [
        ".blog-item-content",
        ".entry-content",
        ".blog-item-wrapper",
        ".BlogItem",
        "article",
        "main",
    ]

    for selector in selectors:
        root = soup.select_one(selector)
        if root:
            return root

    return soup.body or soup


def extract_raw_squarespace_images(raw_html: str):
    urls = []

    patterns = [
        r'https?://static1\.squarespace\.com/[^"\')\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\')\s<>]*)?',
        r'https?://images\.squarespace-cdn\.com/[^"\')\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^"\')\s<>]*)?',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, raw_html, flags=re.I):
            url = html.unescape(match.group(0))

            if "artbooms-logo" in url.lower():
                continue

            urls.append(normalize_url(url))

    unique = []
    seen = set()

    for url in urls:
        key = image_key(url)

        if not key or key in seen:
            continue

        seen.add(key)
        unique.append(url)

    return unique


def extract_caption_candidates(root):
    captions = []

    for tag in root.find_all(True):
        if is_caption_node(tag):
            text = tag.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()

            if text:
                captions.append(text)

    unique = []
    seen = set()

    for caption in captions:
        if caption in seen:
            continue

        seen.add(caption)
        unique.append(caption)

    return unique


def fallback_figures_from_raw_html(raw_html: str, root, existing_image_keys):
    figures = []
    image_urls = extract_raw_squarespace_images(raw_html)
    captions = extract_caption_candidates(root)
    caption_index = 0

    for url in image_urls:
        key = image_key(url)

        if not key or key in existing_image_keys:
            continue

        caption = captions[caption_index] if caption_index < len(captions) else ""
        caption_index += 1

        existing_image_keys.add(key)
        figures.append(make_figure(url, caption))

    return figures


def extract_article_memory(article_url: str, title: str, pub_date: str, rss_excerpt: str = ""):
    response = requests.get(article_url, headers=HEADERS, timeout=25)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")

    description = rss_excerpt or ""

    if not description:
        meta_desc = soup.find("meta", attrs={"itemprop": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc["content"].strip()

    if not description:
        fallback_desc = soup.find("meta", attrs={"name": "description"})
        if fallback_desc and fallback_desc.get("content"):
            description = fallback_desc["content"].strip()

    image = ""

    for selector in [
        {"itemprop": "thumbnailUrl"},
        {"itemprop": "image"},
        {"property": "og:image"},
    ]:
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content"):
            image = normalize_url(tag["content"])
            break

    root = find_article_root(soup)

    blocks = []
    seen_image_keys = set()
    seen_text = set()
    processed_containers = set()

    for el in root.find_all(
        ["div", "figure", "img", "p", "h2", "h3", "h4", "blockquote", "ul", "ol"],
        recursive=True
    ):
        if any(parent in processed_containers for parent in el.parents):
            continue

        if is_caption_node(el) or el.find_parent(is_caption_node):
            continue

        if is_image_container(el):
            img = el.find("img")

            if img:
                src = img_src(img)
                key = image_key(src)

                if src and key and key not in seen_image_keys:
                    seen_image_keys.add(key)
                    processed_containers.add(el)
                    blocks.append(make_figure(src, caption_for_image(img), img.get("alt", "")))

            continue

        if el.name != "figure" and el.find_parent("figure"):
            continue

        if el.name == "figure":
            img = el.find("img")

            if not img:
                continue

            src = img_src(img)
            key = image_key(src)

            if not src or not key or key in seen_image_keys:
                continue

            seen_image_keys.add(key)
            processed_containers.add(el)
            blocks.append(make_figure(src, caption_for_image(img), img.get("alt", "")))
            continue

        if el.name == "img":
            src = img_src(el)
            key = image_key(src)

            if src and key and key not in seen_image_keys:
                seen_image_keys.add(key)
                blocks.append(make_figure(src, caption_for_image(el), el.get("alt", "")))

            continue

        if el.name in {"p", "h2", "h3", "h4", "blockquote", "ul", "ol"}:
            text = el.get_text(" ", strip=True)
            compact = re.sub(r"\s+", " ", text).strip()

            if not compact:
                continue

            if compact in seen_text:
                continue

            seen_text.add(compact)
            blocks.append(clean_text_html(str(el)))

    fallback_figures = fallback_figures_from_raw_html(response.text, root, seen_image_keys)

    if fallback_figures:
        insert_at = 0

        for index, block in enumerate(blocks):
            if block.startswith("<p") or block.startswith("<h2") or block.startswith("<h3") or block.startswith("<h4"):
                insert_at = index
                break

        blocks[insert_at:insert_at] = fallback_figures

    content_html = "\n".join(blocks).strip()

    if not content_html and description:
        content_html = f"<p>{html.escape(description, quote=False)}</p>"

    all_images = []
    all_image_keys = set()

    for block in blocks:
        for match in re.finditer(r'<img[^>]+src="([^"]+)"', block):
            src = normalize_url(match.group(1))
            key = image_key(src)

            if src and key and key not in all_image_keys:
                all_image_keys.add(key)
                all_images.append(src)

    if image:
        lead_key = image_key(image)

        if lead_key and lead_key not in all_image_keys:
            all_images.insert(0, image)

    return {
        "url": normalize_url(article_url),
        "title": normalize_title(title),
        "display_date": normalize_date(pub_date),
        "excerpt": description,
        "image": image,
        "images": all_images,
        "content_html": content_html,
    }


def main():
    items = read_rss_items()

    articles = [
        extract_article_memory(
            item["url"],
            item["title"],
            item["publication_date"],
            item.get("excerpt", ""),
        )
        for item in items[:3]
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
