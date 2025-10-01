#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
rss_to_posts.py — NHKがログイン必須でも“他社へ自動振替”して全文取得する版
- NHK記事は原則 Web へ取りに行かない
- タイトル類似度で同一ニュースを他社フィードから自動検索し、見つかればそのURLから本文抽出
- 見つからない場合のみ、NHKはRSSサマリー＋出典リンクのみ
- 画像は本文ソース（=他社）から取得。サマリー落ち時はRSSの media:* を利用
- 有料/転載不可ワードはスキップ
- 重複防止（URLハッシュ）
- カテゴリ推定（簡易）
- PRIVATE_MODE=1 の時だけ NHK も直接本文取得したい人向けのフックあり（公開は非推奨）

依存:
  pip install feedparser readability-lxml lxml python-slugify requests

使い方:
  python scripts/rss_to_posts.py
"""

import os
import re
import json
import time
import hashlib
import datetime
import pathlib
from urllib.parse import urlparse
import requests
import feedparser
from slugify import slugify
from readability import Document
from lxml import html as lxml_html
from difflib import SequenceMatcher

# ==============================
# 基本設定
# ==============================
BASE   = pathlib.Path(__file__).resolve().parents[1]
POSTS  = BASE / "_posts"
IMGDIR = BASE / "assets" / "img"
DB     = BASE / ".data"
STATE  = DB / "rss_state.json"

for p in (DB, POSTS, IMGDIR):
    p.mkdir(parents=True, exist_ok=True)

UA = {"User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}

# フィードリスト: scripts/feeds.txt があればそれを優先
FEEDS_TXT = BASE / "scripts" / "feeds.txt"
if FEEDS_TXT.exists():
    FEEDS = [line.strip() for line in FEEDS_TXT.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.strip().startswith("#")]
else:
    # 最低限の例。実際は feeds.txt に各社RSSを列挙して運用してね
    FEEDS = [
        "https://www3.nhk.or.jp/rss/news/cat0.xml",  # NHK 総合
        # 例: 共同通信/時事/毎日/読売/朝日/地方紙などを追加しておくと振替成功率が上がる
    ]

# NHK判定
NHK_HOSTS = ("nhk.or.jp", "www3.nhk.or.jp")

def is_nhk(url: str) -> bool:
    try:
        h = urlparse(url).hostname or ""
        return any(h == d or h.endswith("." + d) for d in NHK_HOSTS)
    except Exception:
        return False

# 公開に向かないキーワード: 有料/会員限定/転載不可等
BLOCK_WORDS = [
    "有料記事", "会員限定", "会員のみ", "電子版限定",
    "転載を禁じます", "無断転載を禁じます", "著作権", "Copyright",
]

def should_skip(entry) -> bool:
    t = " ".join([
        (entry.get("title") or ""),
        (entry.get("summary") or ""),
        (entry.get("description") or ""),
    ])
    return any(w in t for w in BLOCK_WORDS)

# PRIVATE_MODE=1 の時だけ NHK も直取りしたい場合のフラグ（※公開は非推奨）
PRIVATE_MODE = os.getenv("PRIVATE_MODE") == "1"

# ==============================
# ユーティリティ
# ==============================
def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)

def safe_filename_from_title(title: str) -> str:
    slug = slugify(title) or "post"
    return slug[:80]

def dt_from_entry(entry):
    tm = entry.get("published_parsed") or entry.get("updated_parsed")
    if tm:
        dt = datetime.datetime(*tm[:6])
    else:
        dt = datetime.datetime.now()
    return dt  # JST表示はフロントマターで +0900 を付ける

def clean_html_to_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\r", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def readability_to_markdown(html_text: str) -> str:
    doc = Document(html_text)
    content_html = doc.summary()
    text = clean_html_to_text(content_html)
    text = re.sub(r"\n{2,}", "\n\n", text).strip()
    return text

def extract_first_image_url(doc_html: str, base_url: str) -> str | None:
    try:
        tree = lxml_html.fromstring(doc_html)
        # 画像タグ
        for img in tree.xpath("//img[@src]"):
            src = img.get("src")
            if src and not src.lower().startswith("data:"):
                return lxml_html.make_links_absolute(src, base_url)
        # og:image
        meta = tree.xpath("//meta[translate(@property,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='og:image' or translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='og:image']/@content")
        if meta:
            return lxml_html.make_links_absolute(meta[0], base_url)
    except Exception:
        return None
    return None

def download_image(url: str, prefix_dt: datetime.datetime) -> str | None:
    try:
        r = requests.get(url, headers=UA, timeout=10)
        if r.status_code != 200 or not r.content:
            return None
        ext = ".jpg"
        path = IMGDIR / f"{prefix_dt.strftime('%Y%m%d')}-{sha256(url)[:10]}{ext}"
        path.write_bytes(r.content)
        return f"/assets/img/{path.name}"
    except Exception:
        return None

def extract_summary_from_rss(entry: dict) -> str:
    # 優先度: content:encoded > summary_detail > description > summary
    for key in ("content", "summary_detail", "description", "summary"):
        v = entry.get(key)
        if not v:
            continue
        if key == "content":
            try:
                html_val = (v[0].get("value") or "").strip()
            except Exception:
                html_val = ""
            if html_val:
                return clean_html_to_text(html_val)
        elif key == "summary_detail":
            html_val = (v.get("value") or "").strip()
            if html_val:
                return clean_html_to_text(html_val)
        else:
            html_val = (v or "").strip()
            if html_val:
                return clean_html_to_text(html_val)
    return ""

def extract_image_from_rss(entry: dict) -> str | None:
    thumbs = entry.get("media_thumbnail") or entry.get("media_content") or []
    try:
        if isinstance(thumbs, list) and thumbs:
            url = thumbs[0].get("url")
            return url if url else None
    except Exception:
        pass
    enclosures = entry.get("enclosures") or []
    try:
        if isinstance(enclosures, list) and enclosures:
            url = enclosures[0].get("href")
            if url and any(url.lower().endswith(ext) for ext in (".jpg",".jpeg",".png",".webp",".gif")):
                return url
    except Exception:
        pass
    return None

# タイトル正規化と類似度
def normalize_title(t: str) -> str:
    t = t or ""
    t = re.sub(r"【[^】]*】", "", t)  # 先頭の【○○】除去
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[0-9]{1,2}日|[0-9]{1,2}時|[0-9]{1,2}分", "", t)  # 日時の揺れ削り
    t = t.strip()
    return t

def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()

def guess_categories(entry, link, is_nhk_article: bool):
    cats = []
    if is_nhk_article:
        cats.append("NHK")
    title = (entry.get("title") or "")
    PREFS = ["北海道","青森県","岩手県","宮城県","秋田県","山形県","福島県","茨城県","栃木県","群馬県",
             "埼玉県","千葉県","東京都","神奈川県","新潟県","富山県","石川県","福井県","山梨県","長野県",
             "岐阜県","静岡県","愛知県","三重県","滋賀県","京都府","大阪府","兵庫県","奈良県","和歌山県",
             "鳥取県","島根県","岡山県","広島県","山口県","徳島県","香川県","愛媛県","高知県","福岡県",
             "佐賀県","長崎県","熊本県","大分県","宮崎県","鹿児島県","沖縄県"]
    for p in PREFS:
        if p in title:
            cats.append(p)
            break
    if not cats:
        cats.append("ニュース")
    return cats

# ==============================
# フィードの読み込み（全件プール）
# ==============================
def load_all_entries():
    all_entries = []
    for feed_url in FEEDS:
        try:
            d = feedparser.parse(feed_url)
        except Exception:
            continue
        for e in d.entries:
            link  = (e.get("link") or "").strip()
            title = (e.get("title") or "").strip()
            if not link or not title:
                continue
            all_entries.append({
                "raw": e,
                "link": link,
                "title": title,
                "is_nhk": is_nhk(link),
                "dt": dt_from_entry(e),
                "feed": feed_url,
                "host": urlparse(link).hostname or "",
            })
    # 新しい順に
    all_entries.sort(key=lambda x: x["dt"], reverse=True)
    return all_entries

# NHK→他社へ自動振替
def find_alternative_source(nhk_entry_dict, pool, sim_threshold=0.82, time_window_hours=72):
    nhk_title = nhk_entry_dict["title"]
    nhk_dt    = nhk_entry_dict["dt"]
    for cand in pool:
        if cand["is_nhk"]:
            continue
        # 時間窓
        if abs((nhk_dt - cand["dt"]).total_seconds()) > time_window_hours * 3600:
            continue
        # 類似度
        if title_similarity(nhk_title, cand["title"]) >= sim_threshold:
            return cand
    return None

# ==============================
# 本文保存ロジック
# ==============================
def process_entry(entry_dict, state, pool_non_nhk):
    entry = entry_dict["raw"]
    link  = entry_dict["link"]
    title = entry_dict["title"]
    is_nhk_article = entry_dict["is_nhk"]
    dt    = entry_dict["dt"]

    # 重複
    key = sha256(link)
    if key in state.get("done", {}):
        return False, "dup"

    # ブロック
    if should_skip(entry):
        state.setdefault("skipped", {})[key] = {"link": link, "reason": "blocked_words"}
        return False, "blocked"

    ymd = dt.strftime("%Y-%m-%d")
    fn  = f"{ymd}-{safe_filename_from_title(title)}.md"
    path = POSTS / fn

    image_path = ""
    body_md = ""
    used_link = link
    used_host = entry_dict["host"]
    used_note = ""

    if is_nhk_article and not PRIVATE_MODE:
        # まず“他社ソース”を探す
        alt = find_alternative_source(entry_dict, pool_non_nhk)
        if alt:
            # 他社から本文を取得
            try:
                r = requests.get(alt["link"], headers=UA, timeout=15)
                html_text = r.text if r.status_code == 200 else ""
            except Exception:
                html_text = ""
            if html_text:
                body_md = readability_to_markdown(html_text)
                img_url = extract_first_image_url(html_text, alt["link"])
                if img_url:
                    saved = download_image(img_url, dt)
                    if saved:
                        image_path = saved
            else:
                # 他社も取れない時はNHKサマリー
                summary = extract_summary_from_rss(entry)
                body_md = f"## {title}\n\n{summary or '（※各社ページの取得に失敗しました）'}\n\n[出典（NHK）]({link})"
            used_link = alt["link"]
            used_host = alt["host"]
            used_note = f"> ※ 本文は {used_host} から取得。NHKは出典として併記。"
            # 末尾に出典を添える
            body_md += f"\n\n{used_note}\n\n[出典（NHK）]({link})"
        else:
            # 他社が見つからない → サマリーのみ
            summary = extract_summary_from_rss(entry)
            if not summary:
                summary = "（※NHKの仕様変更により、RSS概要以外を取得できません。詳細は出典でご覧ください。）"
            body_md = f"## {title}\n\n{summary}\n\n> ※ NHK記事は本文・画像の転載が許可されていないため、概要のみ掲載しています。\n[出典（NHK）]({link})"
    else:
        # NHK以外、または PRIVATE_MODE=1（自己用）
        html_text = ""
        try:
            r = requests.get(link, headers=UA, timeout=15)
            if r.status_code == 200:
                html_text = r.text
        except Exception:
            pass
        if html_text:
            body_md = readability_to_markdown(html_text)
            img_url = extract_first_image_url(html_text, link)
            if img_url:
                saved = download_image(img_url, dt)
                if saved:
                    image_path = saved
        else:
            # フォールバック
            summary = extract_summary_from_rss(entry)
            body_md = f"## {title}\n\n{summary or ''}\n\n[出典はこちら]({link})".strip()
    
        # YAMLフロントマター
    cats = guess_categories(entry, link, is_nhk_article)

    # ★追加：ここで安全なタイトルを作る
    safe_title = (title or "").replace('"', '”')

    fm_lines = [
        "---",
        f'title: "{safe_title}"',   # ← ここはもうエラーにならない
        f"date: {dt.strftime('%Y-%m-%d %H:%M:%S +0900')}",
        f"categories: [{', '.join(cats)}]",
        f"image: {image_path}",
        "---",
        "",
    ]
    
    
    content = "\n".join(fm_lines) + body_md.rstrip() + "\n"

    final_path = path
    if final_path.exists():
        i = 2
        stem = final_path.stem
        while True:
            altp = POSTS / f"{stem}-{i}.md"
            if not altp.exists():
                final_path = altp
                break
            i += 1

    final_path.write_text(content, encoding="utf-8")

    state.setdefault("done", {})[key] = {
        "link": link,
        "title": title,
        "saved": final_path.name,
        "ts": int(time.time()),
        "used_source": used_host,
    }
    return True, f"saved {final_path.name}"

# ==============================
# メイン
# ==============================
def main():
    state = load_state()
    all_entries = load_all_entries()
    # NHK以外のプール（振替探索用）
    pool_non_nhk = [e for e in all_entries if not e["is_nhk"]]

    total_new = 0
    logs = []

    # 新しい順で処理
    for ed in all_entries:
        ok, msg = process_entry(ed, state, pool_non_nhk)
        if ok:
            total_new += 1
        logs.append(f"{'OK' if ok else '..'} {msg}")

    save_state(state)
    print(f"done. new={total_new}")
    for line in logs:
        print(line)

if __name__ == "__main__":
    main()