import os, re, json, hashlib, datetime, pathlib, unicodedata, urllib.parse, time
import feedparser, requests
from urllib.parse import urlparse
from slugify import slugify
from lxml import html as lxml_html
from readability import Document

# ==============================
# 基本設定
# ==============================
BASE   = pathlib.Path(__file__).resolve().parents[1]
POSTS  = BASE / "_posts"
IMGDIR = BASE / "assets" / "img"
DB     = BASE / ".data"
for p in (DB, POSTS, IMGDIR):
    p.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0"}
IMG_TIMEOUT = 10

FEEDS = [
    # 大手ポータル
    "https://www3.nhk.or.jp/rss/news/cat0.xml",
    "https://news.yahoo.co.jp/rss/topics/domestic.xml",
    "https://news.yahoo.co.jp/rss/topics/world.xml",
    "https://news.yahoo.co.jp/rss/topics/local.xml",
    "https://news.yahoo.co.jp/rss/topics/sports.xml",

    # 大手新聞社など
    "https://www.asahi.com/rss/asahi/newsheadlines.rdf",
    "https://mainichi.jp/rss/etc/mainichi-flash.rss",
    "https://www.yomiuri.co.jp/rss/edition/national/",
    "https://www.jiji.com/rss/rss.php?g=soc",

    # Google News（媒体横断）
    "https://news.google.com/rss?hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=逮捕+OR+容疑+OR+事件&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=スポーツ&hl=ja&gl=JP&ceid=JP:ja",
]

CATEGORY_MAPPING = {
    "教員": ["性犯罪", "教員"],
    "教師": ["性犯罪", "教員"],
    "女子生徒": ["児童"],
    "京都": ["京都府"],
    "大阪": ["大阪府"],
}

CRIME_KEYWORDS = [
    "逮捕","容疑","容疑者","送検","起訴","不起訴","被告","強盗","窃盗","詐欺",
    "わいせつ","盗撮","強制","傷害","暴行","殺人","覚醒剤","麻薬","拳銃","横領",
    "児童買春","淫行","誘拐","性犯罪","猥褻","強制性交","迷惑防止条例"
]
SPORTS_KEYWORDS = [
    "ホームラン","打点","先発","登板","投手","打者","カープ","阪神","巨人","DeNA",
    "ベイスターズ","ヤクルト","中日","日本ハム","日ハム","オリックス","ソフトバンク",
    "ロッテ","楽天","NPB","Jリーグ","Ｊ１","ゴール","アシスト","ワールドカップ","W杯",
    "オリンピック","相撲","ボクシング","ラグビー","F1","グランプリ","試合","優勝","順位",
    "打率","防御率","本塁打"
]
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

FEED_DEFAULTS = {
    "https://news.yahoo.co.jp/rss/topics/sports.xml": ["スポーツ"],
    "https://news.google.com/rss/search?q=スポーツ&hl=ja&gl=JP&ceid=JP:ja": ["スポーツ"],
}

# ==============================
# 既存ポストのインデックス化（起動時1回）
# ==============================
def build_posts_index():
    idx = {"by_url": {}, "by_sha": {}}
    for p in POSTS.glob("*.md"):
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        m = re.match(r"^---\n(.*?)\n---", txt, flags=re.S)
        if not m:
            continue
        fm = m.group(1)
        def _get(key):
            r = re.search(rf"^{key}:\s*\"?([^\n\"]+)\"?", fm, flags=re.M)
            return r.group(1).strip() if r else ""
        cu = normalize_url(_get("canonical_url") or _get("source_url"))
        sh = (_get("content_sha") or "").strip()
        meta = {"path": str(p)}
        if cu: idx["by_url"][cu] = meta
        if sh: idx["by_sha"][sh] = meta
    return idx

# ==============================
# 永続データ（既読管理）
# ==============================
def load_seen():
    f = DB / "seen.json"
    base = {"by_url": {}, "by_sha": {}}
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                base["by_url"].update(d.get("by_url", {}))
                base["by_sha"].update(d.get("by_sha", {}))
        except Exception:
            pass
    # 既存_posts も学習
    base_from_posts = build_posts_index()
    base["by_url"].update(base_from_posts["by_url"])
    base["by_sha"].update(base_from_posts["by_sha"])
    return base

def save_seen(d):
    (DB / "seen.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def already_seen(seen, canon_url: str, sha: str) -> bool:
    if canon_url and canon_url in seen["by_url"]:
        return True
    if sha and sha in seen["by_sha"]:
        return True
    return False

def mark_seen(seen, canon_url: str, sha: str, meta: dict):
    ts = int(time.time())
    if canon_url:
        seen["by_url"][canon_url] = {"ts": ts, **meta}
    if sha:
        seen["by_sha"][sha] = {"ts": ts, **meta}

# ==============================
# ユーティリティ
# ==============================
def normalize_text(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")

def sanitize_filename(s: str) -> str:
    return re.sub(r"[^a-z0-9\-]+","-", slugify(s or "")).strip("-")

def normalize_url(u: str) -> str:
    if not u:
        return ""
    p = urllib.parse.urlsplit(u)
    # クエリは全部捨てる（媒体ごとの差分ノイズを排除）
    new = p._replace(query="", fragment="")
    url = urllib.parse.urlunsplit(new)
    # AMP → www
    url = re.sub(r"//amp\.", "//www.", url)
    # 拡張子無しなら末尾スラッシュ
    if not re.search(r"\.(html?|xml|jpg|jpeg|png|gif|webp|pdf)(\?|$)", url, re.I) and not url.endswith("/"):
        url += "/"
    return url

# 本文ノイズ（更新◯分前・関連記事等）を除去して安定化
_NOISE_PAT = re.compile(
    r"""(?ix)
        (?:
            \d{1,2}:\d{2}                    # 時刻
          | \d{4}[./-]\d{1,2}[./-]\d{1,2}    # 日付
          | 更新 \d+ (?:分|時間) 前?
          | 関連記事|おすすめ|こちらも?おすすめ|広告|PR
        )
    """
)
def _denoise_text(txt: str) -> str:
    t = re.sub(r"<[^>]+>", "", txt or "")
    t = normalize_text(t)
    t = _NOISE_PAT.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def fingerprint(title: str, html_body: str) -> str:
    t = normalize_text((title or "")).strip()
    plain = _denoise_text(html_body)[:1500]
    return hashlib.sha256((t + "\n" + plain).encode("utf-8", "ignore")).hexdigest()

def guess_categories(title, summary, entry=None, feed_url=None):
    cats = set()
    text = normalize_text(f"{title} {summary}")

    for k, vals in CATEGORY_MAPPING.items():
        if k in text:
            cats.update(vals)

    if any(k in text for k in CRIME_KEYWORDS):
        cats.add("犯罪")
    if any(k in text for k in SPORTS_KEYWORDS):
        cats.add("スポーツ")

    try:
        for t in (entry.get("tags") or []):
            term = normalize_text((t.get("term") or ""))
            if any(x in term for x in ["スポーツ","野球","サッカー","相撲","テニス","ゴルフ","ラグビー","F1"]):
                cats.add("スポーツ")
            if any(x in term for x in ["事件","事故","裁判","犯罪","わいせつ"]):
                cats.add("犯罪")
    except Exception:
        pass

    for p in PREFS:
        if p in text:
            cats.add(p)

    if feed_url in FEED_DEFAULTS:
        cats.update(FEED_DEFAULTS[feed_url])

    if not cats:
        cats.add("ニュース")

    return sorted(list(cats))

# ==============================
# HTML処理
# ==============================
def _decode_html(response: requests.Response) -> str:
    raw = response.content
    m = re.search(br'<meta[^>]+charset=["\']?([a-zA-Z0-9_\-]+)', raw, re.I) or \
        re.search(br'charset=([a-zA-Z0-9_\-]+)', raw, re.I)
    if m:
        enc = m.group(1).decode(errors="ignore").lower()
        try:
            return raw.decode(enc, errors="replace")
        except Exception:
            pass
    enc = (getattr(response, "apparent_encoding", None) or response.encoding or "").lower()
    if enc:
        try:
            return raw.decode(enc, errors="replace")
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")

def _fallback_extract_title_content(html_text: str):
    try:
        t = lxml_html.fromstring(html_text)
    except Exception:
        return None, None

    ogt = t.xpath('//meta[@property="og:title"]/@content')
    twt = t.xpath('//meta[@name="twitter:title"]/@content')
    h1  = t.xpath('//h1//text()')
    title = (ogt[0] if ogt else (twt[0] if twt else ("".join(h1).strip() if h1 else None)))

    arts = t.xpath('//article')
    body = None
    if arts:
        body = "\n".join(a.text_content().strip() for a in arts if a.text_content()).strip()
    if not body:
        ogd = t.xpath('//meta[@property="og:description"]/@content')
        if ogd:
            body = ogd[0].strip()
    if body:
        body = re.sub(r'\n{3,}', '\n\n', body)

    return title, body

def _extract_canonical_from_html(html_text: str, base_url: str) -> str:
    try:
        t = lxml_html.fromstring(html_text)
        link = t.xpath('//link[@rel="canonical"]/@href')
        if link:
            can = urllib.parse.urljoin(base_url, link[0])
            return normalize_url(can)
    except Exception:
        pass
    return ""

def _pick_publisher_url_from_gnews_html(html_text: str) -> str | None:
    hrefs = re.findall(r'href="(https?://[^"]+)"', html_text, flags=re.I)
    if not hrefs:
        return None
    bad_domains = ("google.com", "news.google.com", "gstatic.com", "googleusercontent.com")
    bad_exts = (".jpg",".jpeg",".png",".gif",".webp",".svg",".avif")
    candidates = []
    for u in hrefs:
        lu = u.lower()
        if any(b in lu for b in bad_domains):
            continue
        if any(lu.split("?")[0].endswith(ext) for ext in bad_exts):
            continue
        candidates.append(u)
    if not candidates:
        return None
    def _clean(u: str) -> str:
        u2 = re.sub(r"//amp\.", "//www.", u)
        u2 = re.sub(r"[?&](utm_[^=&]+|gclid|fbclid|yclid|ocid)=[^&]+", "", u2)
        return u2
    return _clean(candidates[0])

def _pick_external_from_yahoo_pickup(html_text: str) -> str | None:
    try:
        t = lxml_html.fromstring(html_text)
    except Exception:
        return None

    areas = t.xpath('//*[@role="main"]') or [t]
    bad_hosts = ("yahoo.co.jp", "yimg.jp", "yahooapis.jp")

    for area in areas:
        for href in area.xpath('.//a[@href]/@href'):
            if not href.startswith("http"):
                continue
            host = urlparse(href).netloc.lower()
            if any(b in host for b in bad_hosts):
                continue
            return href
    for href in t.xpath('//a[@href]/@href'):
        if not href.startswith("http"):
            continue
        host = urlparse(href).netloc.lower()
        if any(b in host for b in bad_hosts):
            continue
        return href
    return None

# ==============================
# 画像処理
# ==============================
def fetch_image(url):
    try:
        r = requests.get(url, timeout=IMG_TIMEOUT, headers=UA)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type","").lower()
        ext = ".jpg"
        if "png" in ctype: ext = ".png"
        if "webp" in ctype: ext = ".webp"
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + ext
        path = IMGDIR / name
        path.write_bytes(r.content)
        return f"/assets/img/{name}"
    except Exception:
        return None

def extract_main_image_from_entry(entry):
    if "media_content" in entry and entry.media_content:
        murl = entry.media_content[0].get("url")
        if murl: return murl
    if "links" in entry:
        for l in entry.links:
            if l.get("rel") in ("enclosure", "image"):
                if l.get("href"): return l["href"]
    html_snip = entry.get("summary", "") or entry.get("content",[{"value":""}])[0]["value"]
    try:
        tree = lxml_html.fromstring(html_snip)
        imgs = tree.xpath("//img/@src")
        if imgs: return imgs[0]
    except Exception:
        pass
    return None

def extract_og_image(html_text: str) -> str | None:
    try:
        t = lxml_html.fromstring(html_text)
        og = t.xpath('//meta[@property="og:image"]/@content')
        return og[0] if og else None
    except Exception:
        return None

# ==============================
# 本文取得（Google News / Yahoo! pickup 解決込み）
# 戻り値: (title, content_html, final_url, ogimg, canonical_url)
# ==============================
def fetch_fulltext(url):
    try:
        r0 = requests.get(url, timeout=10, headers=UA, allow_redirects=True)
        r0.raise_for_status()
        final_url = r0.url
        ctype0 = r0.headers.get("Content-Type", "").lower()

        # ---- Google News: 中継 → 配信社URLへ ----
        if "news.google.com" in final_url:
            html0 = _decode_html(r0)
            ext = _pick_publisher_url_from_gnews_html(html0)
            if not ext:
                return None, None, final_url, None, ""
            r1 = requests.get(ext, timeout=10, headers=UA, allow_redirects=True)
            r1.raise_for_status()
            final_url = r1.url
            if "text/html" not in r1.headers.get("Content-Type","").lower():
                return None, None, final_url, None, ""
            html_text = _decode_html(r1)

        # ---- Yahoo! /pickup/：外部記事URLに解決 ----
        elif "news.yahoo.co.jp/pickup/" in final_url:
            html0 = _decode_html(r0)
            ext = _pick_external_from_yahoo_pickup(html0)
            if not ext:
                return None, None, final_url, None, ""
            r1 = requests.get(ext, timeout=10, headers=UA, allow_redirects=True)
            r1.raise_for_status()
            final_url = r1.url
            if "text/html" not in r1.headers.get("Content-Type","").lower():
                return None, None, final_url, None, ""
            html_text = _decode_html(r1)

        # ---- 通常：HTMLページならそのまま抽出 ----
        else:
            if "text/html" not in ctype0:
                return None, None, final_url, None, ""
            html_text = _decode_html(r0)

        # Readability
        doc = Document(html_text)
        title = (doc.short_title() or "").strip()
        content_html = doc.summary(html_partial=True)

        # 短すぎるときはフォールバック
        plain = re.sub(r"<[^>]+>", "", content_html or "").strip()
        if len(plain) < 180:
            fb_title, fb_body = _fallback_extract_title_content(html_text)
            if fb_title and (not title or title.lower() == "google news"):
                title = fb_title
            if fb_body and not plain:
                content_html = "<p>" + fb_body.replace("\n", "<br>") + "</p>"

        ogimg = extract_og_image(html_text)
        canon = _extract_canonical_from_html(html_text, final_url)
        return (title or None), (content_html or None), final_url, ogimg, canon

    except Exception:
        return None, None, url, None, ""

# ==============================
# Front matter
# ==============================
def make_front_matter(title, date, categories, image_path, source_url, canonical_url, content_sha):
    cats_yaml = ", ".join([f'"{c}"' for c in categories])
    safe_title = (title or "").replace('"', '\\"')
    lines = [
        "---",
        f'title: "{safe_title}"',
        f"date: {date.strftime('%Y-%m-%d %H:%M:%S')} +0900",
        f"categories: [{cats_yaml}]",
    ]
    if image_path:
        lines.append(f"image: {image_path}")
    if source_url:
        lines.append(f'source_url: "{source_url}"')
    if canonical_url:
        lines.append(f'canonical_url: "{canonical_url}"')
    if content_sha:
        lines.append(f'content_sha: "{content_sha}"')
    lines.append("---")
    return "\n".join(lines) + "\n"

# ==============================
# メイン
# ==============================
def main():
    seen = load_seen()
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    new_count = 0

    # 同一ラン内のフィード横断重複を抑止
    run_guard = set()  # {(host, title_norm)}

    for feed_url in FEEDS:
        d = feedparser.parse(feed_url)
        for e in d.entries:
            raw_link = (e.get("link") or "").strip()
            if not raw_link:
                continue

            full_title, content_html, final_url, ogimg, canon = fetch_fulltext(raw_link)

            # 本文なし or 依然として Google News のまま → スキップ
            if (not content_html) or ("news.google.com" in (final_url or "").lower()):
                continue

            # URL正規化 & canonical 優先
            n_final = normalize_url(final_url)
            canon_url = normalize_url(canon or n_final)

            # タイトル（本文側がまともなら優先）
            rss_title = (e.get("title") or "").strip()
            title = full_title if (full_title and len(full_title) > 8 and full_title.lower() != "google news") else rss_title

            # ラン内ガード（同ホスト×同タイトル）
            try:
                host = urllib.parse.urlsplit(canon_url or n_final).netloc
            except Exception:
                host = ""
            key_inrun = (host, normalize_text(title))
            if key_inrun in run_guard:
                continue
            run_guard.add(key_inrun)

            # 指紋（内容ハッシュ・ノイズ除去版）
            content_sha = fingerprint(title, content_html)

            # 重複チェック（URL or 内容）
            if already_seen(seen, canon_url, content_sha):
                continue

            # 公開日時
            published = e.get("published_parsed") or e.get("updated_parsed")
            dt = now
            if published:
                try:
                    dt = datetime.datetime(*published[:6], tzinfo=datetime.timezone.utc)\
                            .astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                except Exception:
                    pass

            # カテゴリ推定
            summary = e.get("summary", "")
            cats = guess_categories(title, summary, entry=e, feed_url=feed_url)

            # 画像：RSS → og:image
            img_url = extract_main_image_from_entry(e) or ogimg
            image_path = fetch_image(img_url) if img_url else None

            # ファイル作成（同日スラッグ衝突回避）
            slug_base = sanitize_filename(title)[:80] or hashlib.sha1(title.encode()).hexdigest()[:10]
            slug = slug_base
            post_path = (POSTS / f"{dt.strftime('%Y-%m-%d')}-{slug}.md")
            i = 0
            while post_path.exists():
                i += 1
                suffix = hashlib.sha1(f"{slug_base}-{i}".encode()).hexdigest()[:6]
                slug = f"{slug_base}-{suffix}"
                post_path = (POSTS / f"{dt.strftime('%Y-%m-%d')}-{slug}.md")

            fm = make_front_matter(
                title=title,
                date=dt,
                categories=cats,
                image_path=image_path,
                source_url=n_final,
                canonical_url=canon_url,
                content_sha=content_sha
            )

            body = ""
            if image_path:
                body += "![記事イメージ]({{ site.baseurl }}" + image_path + ")\n\n"
            body += "## 記事本文（自動抽出）\n" + (content_html or "") + "\n\n"
            body += f"[出典はこちら]({n_final})\n"

            post_path.parent.mkdir(parents=True, exist_ok=True)
            post_path.write_text(fm + "\n" + body, encoding="utf-8")

            # 既読登録（都度保存して途中停止でも効果が残る）
            mark_seen(seen, canon_url, content_sha, {
                "title": title,
                "path": str(post_path.relative_to(BASE)),
                "date": dt.isoformat(),
                "link": n_final
            })
            save_seen(seen)

            new_count += 1

    print(f"Created {new_count} posts.")

if __name__ == "__main__":
    main()
