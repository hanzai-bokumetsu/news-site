#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
rss_to_posts.py — RSSの概要のみ掲載版（本文スクレイピングなし）
- 本文取得のためのWebアクセスは一切行わない
- RSSのタイトル・概要・リンクのみを使用
- 末尾に「続きを読む」リンクを付ける
- 重複防止（URLハッシュ）
- カテゴリ推定（簡易）

依存:
  pip install feedparser python-slugify

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

UA = {"User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}

# ==============================
# 基本設定
# ==============================
BASE   = pathlib.Path(__file__).resolve().parents[1]
POSTS  = BASE / "_posts"
DB     = BASE / ".data"
STATE  = DB / "rss_state.json"

for p in (DB, POSTS):
    p.mkdir(parents=True, exist_ok=True)

# フィードリスト: scripts/feeds.txt があればそれを優先
FEEDS_TXT = BASE / "scripts" / "feeds.txt"
if FEEDS_TXT.exists():
    FEEDS = [line.strip() for line in FEEDS_TXT.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.strip().startswith("#")]
else:
    FEEDS = [
        "https://www3.nhk.or.jp/rss/news/cat0.xml",
    ]

# NHK判定
NHK_HOSTS = ("nhk.or.jp", "www3.nhk.or.jp")

def is_nhk(url: str) -> bool:
    try:
        h = urlparse(url).hostname or ""
        return any(h == d or h.endswith("." + d) for d in NHK_HOSTS)
    except Exception:
        return False

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
    # 30日以上前のエントリを削除してファイルサイズを抑制
    cutoff = int(time.time()) - 30 * 24 * 3600
    done = state.get("done", {})
    state["done"] = {k: v for k, v in done.items() if v.get("ts", 0) > cutoff}

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
        # UTC → JST (+9時間)
        dt = dt + datetime.timedelta(hours=9)
    else:
        dt = datetime.datetime.now()
    return dt

def clean_html_to_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\r", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def extract_summary_from_rss(entry: dict) -> str:
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

def extract_image_from_rss(entry) -> str:
    """RSSエントリから画像URLを取得する（ダウンロードなし・URL直接使用）"""
    # media:thumbnail / media:content
    for key in ("media_thumbnail", "media_content"):
        thumbs = entry.get(key) or []
        if isinstance(thumbs, list) and thumbs:
            url = thumbs[0].get("url")
            if url:
                return url

    # enclosure（音声・動画・画像）
    enclosures = entry.get("enclosures") or []
    for enc in enclosures:
        url = enc.get("href") or enc.get("url") or ""
        if url and any(url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return url

    # summary内のimgタグ
    summary = entry.get("summary") or ""
    m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
    if m:
        return m.group(1)

    return ""


def guess_categories(entry, link, is_nhk_article: bool):
    cats = []
    host = urlparse(link).hostname or ""

    # ソース判定
    if is_nhk_article:
        cats.append("NHK")
    elif "reuters.com" in host:
        cats.append("Reuters")
    elif "bbc.co.uk" in host or "bbc.com" in host:
        cats.append("BBC")
    elif "cnn.co.jp" in host or "cnn.com" in host:
        cats.append("CNN")

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
# フィードの読み込み
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
    all_entries.sort(key=lambda x: x["dt"], reverse=True)
    return all_entries

# ==============================
# 記事保存ロジック
# ==============================
def process_entry(entry_dict, state):
    entry = entry_dict["raw"]
    link  = entry_dict["link"]
    title = entry_dict["title"]
    is_nhk_article = entry_dict["is_nhk"]
    dt    = entry_dict["dt"]

    # 重複チェック
    key = sha256(link)
    if key in state.get("done", {}):
        return False, "dup"

    ymd = dt.strftime("%Y-%m-%d")
    fn  = f"{ymd}-{safe_filename_from_title(title)}.md"
    path = POSTS / fn

    # RSSから概要を取得
    summary = extract_summary_from_rss(entry)
    if not summary:
        summary = "（概要はありません）"

    # RSSから画像URLを直接取得（NHKは画像なし）
    image_url = "" if is_nhk_article else extract_image_from_rss(entry)

    # 本文はRSS概要＋元記事リンクのみ
    body_md = f"{summary}\n\n[続きを読む →]({link})"

    # カテゴリ
    cats = guess_categories(entry, link, is_nhk_article)

    # タイトルのサニタイズ
    safe_title = (title or "").replace('"', '\\"')

    # フロントマター
    fm_lines = [
        "---",
        f'title: "{safe_title}"',
        f"date: {dt.strftime('%Y-%m-%d %H:%M:%S +0900')}",
        f"categories: [{', '.join(cats)}]",
    ]
    if image_url:
        fm_lines.append(f'image: "{image_url}"')
    fm_lines += ["---", ""]

    content = "\n".join(fm_lines) + body_md.rstrip() + "\n"

    # ファイル名衝突回避
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
    }
    return True, f"saved {final_path.name}"

# ==============================
# メイン
# ==============================
def main():
    state = load_state()
    all_entries = load_all_entries()

    total_new = 0
    logs = []

    for ed in all_entries:
        ok, msg = process_entry(ed, state)
        if ok:
            total_new += 1
        logs.append(f"{'OK' if ok else '..'} {msg}")

    save_state(state)
    print(f"done. new={total_new}")
    for line in logs:
        print(line)

if __name__ == "__main__":
    main()
