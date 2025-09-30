import os, re, json, hashlib, datetime, pathlib, textwrap, unicodedata
import feedparser, requests
from urllib.parse import urlparse
UA = {"User-Agent": "Mozilla/5.0"}
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
    "https://www3.nhk.or.jp/rss/news/cat0.xml",                 # NHK 総合
    "https://news.yahoo.co.jp/rss/topics/domestic.xml",         # Yahoo! 国内
    "https://news.yahoo.co.jp/rss/topics/world.xml",            # Yahoo! 国際
    "https://news.yahoo.co.jp/rss/topics/local.xml",            # Yahoo! 地域
    "https://news.yahoo.co.jp/rss/topics/sports.xml",           # Yahoo! スポーツ

    # —— Google News（RSS生成。媒体横断）——
    # 日本のトップニュース
    "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja",
    # 犯罪・事件系（キーワードベース）
    "https://news.google.com/rss/search?q=逮捕+OR+容疑+OR+事件&hl=ja&gl=JP&ceid=JP:ja",
    # スポーツ全般
    "https://news.google.com/rss/search?q=スポーツ&hl=ja&gl=JP&ceid=JP:ja",
]
SITE_BASEURL = "{{ site.baseurl }}"

CATEGORY_MAPPING = {
    "教員": ["性犯罪", "教員"],
    "教師": ["性犯罪", "教員"],
    "女子生徒": ["児童"],
    "京都": ["京都府"],
    "大阪": ["大阪府"],
}

# 犯罪ワード
CRIME_KEYWORDS = [
    "逮捕","容疑","容疑者","送検","起訴","不起訴","被告","強盗","窃盗","詐欺",
    "わいせつ","盗撮","強制","傷害","暴行","殺人","覚醒剤","麻薬","拳銃","横領",
    "児童買春","淫行","誘拐","性犯罪","猥褻","強制性交","迷惑防止条例"
]

# スポーツワード
SPORTS_KEYWORDS = [
    "ホームラン","打点","先発","登板","投手","打者","カープ","阪神","巨人","DeNA",
    "ベイスターズ","ヤクルト","中日","日本ハム","日ハム","オリックス","ソフトバンク",
    "ロッテ","楽天","NPB","Jリーグ","Ｊ１","ゴール","アシスト","ワールドカップ","W杯",
    "オリンピック","相撲","ボクシング","ラグビー","F1","グランプリ","試合","優勝","順位",
    "打率","防御率","本塁打"
]

# 都道府県リスト
PREFS = [
    "北海道","青森県","岩手県","宮城県","秋田県","山形県","福島県",
    "茨城県","栃木県","群馬県","埼玉県","千葉県","東京都","神奈川県",
    "新潟県","富山県","石川県","福井県","山梨県","長野県",
    "岐阜県","静岡県","愛知県","三重県",
    "滋賀県","京都府","大阪府","兵庫県","奈良県","和歌山県",
    "鳥取県","島根県","岡山県","広島県","山口県",
    "徳島県","香川県","愛媛県","高知県",
    "福岡県","佐賀県","長崎県","熊本県","大分県","宮崎県","鹿児島県","沖縄県"
]

# フィード固有のカテゴリ（必要なら追加）
FEED_DEFAULTS = {
    "https://news.yahoo.co.jp/rss/topics/sports.xml": ["スポーツ"],
    "https://news.google.com/rss/search?q=スポーツ&hl=ja&gl=JP&ceid=JP:ja": ["スポーツ"],
}


IMG_TIMEOUT = 10


def load_seen():
    f = DB / "seen.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}

def save_seen(d):
    (DB / "seen.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def normalize_text(s: str) -> str:
    if not s:
        return ""
    return unicodedata.normalize("NFKC", s)

def guess_categories(title, summary, entry=None, feed_url=None):
    cats = set()
    text = normalize_text(f"{title} {summary}")

    # マッピングから付与
    for k, vals in CATEGORY_MAPPING.items():
        if k in text:
            cats.update(vals)

    # 犯罪ワード
    if any(k in text for k in CRIME_KEYWORDS):
        cats.add("犯罪")

    # スポーツワード
    if any(k in text for k in SPORTS_KEYWORDS):
        cats.add("スポーツ")

    # RSSタグからも判定
    try:
        for t in (entry.get("tags") or []):
            term = normalize_text((t.get("term") or ""))
            if any(x in term for x in ["スポーツ","野球","サッカー","相撲","テニス","ゴルフ","ラグビー","F1"]):
                cats.add("スポーツ")
            if any(x in term for x in ["事件","事故","裁判","犯罪","わいせつ"]):
                cats.add("犯罪")
    except Exception:
        pass

    # 都道府県
    for p in PREFS:
        if p in text:
            cats.add(p)

    # フィード固有カテゴリ
    if feed_url in FEED_DEFAULTS:
        cats.update(FEED_DEFAULTS[feed_url])

    # 何も付かないときは汎用
    if not cats:
        cats.add("ニュース")

    return list(cats)

def resolve_google_news_url(url: str) -> str:
    """news.google.com の中間ページから、配信社の実記事URLを拾う"""
    if "news.google.com" not in url:
        return url
    try:
        r = requests.get(url, timeout=10, headers=UA)
        r.raise_for_status()
        html_text = r.text
        # ページ内の最初の外部httpsリンクを拾う（google系は除外）
        for m in re.finditer(r'href="(https?://[^"]+)"', html_text):
            dest = m.group(1)
            host = urlparse(dest).netloc.lower()
            if "google.com" not in host and "news.google.com" not in host:
                return dest
    except Exception:
        pass
    return url

def _decode_html(response):
    """HTMLを正しいエンコーディングで文字列化（<meta charset>を優先）"""
    raw = response.content
    # <meta charset="..."> / <meta http-equiv="Content-Type" ... charset=...>
    m = re.search(br'<meta[^>]+charset=["\']?([a-zA-Z0-9_\-]+)', raw, re.I)
    if not m:
        m = re.search(br'charset=([a-zA-Z0-9_\-]+)', raw, re.I)
    if m:
        enc = m.group(1).decode(errors="ignore").lower()
        try:
            return raw.decode(enc, errors="replace")
        except Exception:
            pass
    # 次善：requestsの推定 or サーバ宣言
    enc = (getattr(response, "apparent_encoding", None) or response.encoding or "").lower()
    if enc:
        try:
            return raw.decode(enc, errors="replace")
        except Exception:
            pass
    # 最後の手段：UTF-8
    return raw.decode("utf-8", errors="replace")


def fetch_image(url):
    try:
        r = requests.get(url, timeout=IMG_TIMEOUT, headers=UA)
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
        # Google Newsの中間URLなら実記事URLへ
        final_url = resolve_google_news_url(url) if "news.google.com" in url else url

        r = requests.get(final_url, timeout=10, headers=UA)
        r.raise_for_status()

        html_text = _decode_html(r)

        # ありがちな「UTF-8をLatin-1で読んだ」系のモジバケを簡易修正
        # （「ã」「å」等が大量に出る場合はUTF-8で読み直す）
        if html_text.count("ã") + html_text.count("å") > 20:
            try:
                html_text = r.content.decode("utf-8", errors="replace")
            except Exception:
                pass

        doc = Document(html_text)
        title = doc.short_title()
        content_html = doc.summary(html_partial=True)
        return title, content_html, final_url
    except Exception:
        return None, None, url



def make_front_matter(title, date, categories, image_path, original_url):
    cats_yaml = ", ".join([f'"{c}"' for c in sorted(categories)])
    safe_title = title.replace('"', '\\"')

    lines = [
        "---",
        f'title: "{safe_title}"',
        f"date: {date.strftime('%Y-%m-%d %H:%M:%S')} +0900",
        f"categories: [{cats_yaml}]",
    ]
    if image_path:
        lines.append(f"image: {image_path}")
    lines.append(f'source: "{original_url}"')
    lines.append("---")
    return "\n".join(lines) + "\n"

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
            cats = guess_categories(title, summary, entry=e, feed_url=feed_url)

            img_url = extract_main_image(e)
            image_path = None
            if img_url:
                image_path = fetch_image(img_url)

            full_title, content_html, final_url = fetch_fulltext(link)
            if full_title and len(full_title) > 8:
                title = full_title


            slug = sanitize_filename(title)[:80] or hashlib.sha1(title.encode()).hexdigest()[:10]
            post_path = (BASE / "_posts" / f"{dt.strftime('%Y-%m-%d')}-{slug}.md")
            if post_path.exists():
                slug = f"{slug}-{hashlib.sha1(uid.encode()).hexdigest()[:6]}"
                post_path = (BASE / "_posts" / f"{dt.strftime('%Y-%m-%d')}-{slug}.md")

            fm = make_front_matter(title, dt, cats, image_path, final_url)

            body = ""
            if image_path:
                body += "![記事イメージ]({{ site.baseurl }}" + image_path + ")\n\n"

            if content_html:
                body += "## 記事本文（自動抽出）\n" + content_html + "\n\n"

            body += f"[出典はこちら]({final_url})\n"




            post_path.parent.mkdir(parents=True, exist_ok=True)
            post_path.write_text(fm + "\n" + body, encoding="utf-8")

            seen[uid] = {
                "title": title,
                "path": str(post_path.relative_to(BASE)),
                "date": dt.isoformat(),
                "link": final_url
            }

            new_count += 1

    save_seen(seen)
    print(f"Created {new_count} posts.")

if __name__ == "__main__":
    main()
