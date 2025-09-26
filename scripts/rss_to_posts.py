import os, re, json, hashlib, datetime, pathlib, textwrap
import feedparser, requests
from slugify import slugify
from lxml import html
from readability import Document

BASE = pathlib.Path(__file__).resolve().parents[1]
POSTS = BASE / "_posts"
IMGDIR = BASE / "assets" / "img"
DB = BASE / ".data"
DB.mkdir(exist_ok=True)
POSTS.mkdir(exist_ok=True, parents=True)
IMGDIR.mkdir(exist_ok=True, parents=True)

# —— 設定 —— #
FEEDS = [
        "https://www3.nhk.or.jp/rss/news/cat0.xml",
    # ★ここに取り込みたいRSS/Atomを入れる（最初は2〜3個でOK）
    # 例:
    # "https://www3.nhk.or.jp/rss/news/cat0.xml",
    # "https://news.yahoo.co.jp/rss/topics/domestic.xml",
]
SITE_BASEURL = "{{ site.baseurl }}"
CATEGORY_MAPPING = {
    # キーワード→カテゴリ自動付与（必要に応じて足す）
    "教員": ["性犯罪", "教員"],
    "教師": ["性犯罪", "教員"],
    "女子生徒": ["児童"],
    "京都": ["京都府"],
    "大阪": ["大阪府"],
}
DEFAULT_TOPIC = ["犯罪"]
IMG_TIMEOUT = 10

def load_seen():
    f = DB / "seen.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}

def save_seen(d):
    (DB / "seen.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def guess_categories(title, summary):
    cats = set(DEFAULT_TOPIC)
    text = f"{title} {summary}"
    for k, vals in CATEGORY_MAPPING.items():
        if k in text:
            cats.update(vals)
    return list(cats)

def fetch_image(url):
    try:
        r = requests.get(url, timeout=IMG_TIMEOUT, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        ext = ".jpg"
        ctype = r.headers.get("Content-Type","")
        if "png" in ctype: ext = ".png"
        if "webp" in ctype: ext = ".webp"
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + ext
        path = IMGDIR / name
        path.write_bytes(r.content)
        return f"/assets/img/{name}"
    except Exception:
        return None

def extract_main_image(entry):
    if "media_content" in entry and entry.media_content:
        murl = entry.media_content[0].get("url")
        if murl: return murl
    if "links" in entry:
        for l in entry.links:
            if l.get("rel") in ("enclosure", "image"):
                if l.get("href"): return l["href"]
    html_snip = entry.get("summary", "") or entry.get("content",[{"value":""}])[0]["value"]
    try:
        tree = html.fromstring(html_snip)
        imgs = tree.xpath("//img/@src")
        if imgs: return imgs[0]
    except Exception:
        pass
    return None

def fetch_fulltext(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        doc = Document(r.text)
        title = doc.short_title()
        content_html = doc.summary(html_partial=True)
        return title, content_html
    except Exception:
        return None, None

def make_front_matter(title, date, categories, image_path, original_url):
    cats_yaml = ", ".join([f'"{c}"' for c in sorted(categories)])
    fm = textwrap.dedent(f"""\
    ---
    title: "{title.replace('"','\\"')}"
    date: {date.strftime("%Y-%m-%d %H:%M:%S")} +0900
    categories: [{cats_yaml}]
    image: {image_path if image_path else ""}
    source: "{original_url}"
    ---
    """)
    return fm

def sanitize_filename(s):
    return re.sub(r"[^a-z0-9\-]+","-", slugify(s)).strip("-")

def main():
    seen = load_seen()
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    new_count = 0

    for feed_url in FEEDS:
        d = feedparser.parse(feed_url)
        for e in d.entries:
            uid = e.get("id") or e.get("link") or hashlib.sha1(e.get("title","").encode()).hexdigest()
            if uid in seen:
                continue

            title = (e.get("title") or "").strip()
            link = (e.get("link") or "").strip()
            published = e.get("published_parsed") or e.get("updated_parsed")
            dt = now
            if published:
                dt = datetime.datetime(*published[:6], tzinfo=datetime.timezone.utc)\
                     .astimezone(datetime.timezone(datetime.timedelta(hours=9)))

            summary = e.get("summary", "")
            cats = guess_categories(title, summary)

            img_url = extract_main_image(e)
            image_path = None
            if img_url:
                image_path = fetch_image(img_url)

            full_title, content_html = fetch_fulltext(link)
            if full_title and len(full_title) > 8:
                title = full_title

            slug = sanitize_filename(title)[:80] or hashlib.sha1(title.encode()).hexdigest()[:10]
            post_path = (BASE / "_posts" / f"{dt.strftime('%Y-%m-%d')}-{slug}.md")
            if post_path.exists():
                slug = f"{slug}-{hashlib.sha1(uid.encode()).hexdigest()[:6]}"
                post_path = (BASE / "_posts" / f"{dt.strftime('%Y-%m-%d')}-{slug}.md")

            fm = make_front_matter(title, dt, cats, image_path, link)

            body = ""
            if image_path:
                # baseurlはJekyllが置換
                body += f"![記事イメージ]({{ {{ site.baseurl }} }}{image_path})\n\n"
            if content_html:
                body += "## 記事本文（自動抽出）\n" + content_html + "\n\n"
            body += f"[出典はこちら]({link})\n"

            post_path.parent.mkdir(parents=True, exist_ok=True)
            post_path.write_text(fm + "\n" + body, encoding="utf-8")

            seen[uid] = {
                "title": title,
                "path": str(post_path.relative_to(BASE)),
                "date": dt.isoformat(),
                "link": link
            }
            new_count += 1

    save_seen(seen)
    print(f"Created {new_count} posts.")

if __name__ == "__main__":
    main()
