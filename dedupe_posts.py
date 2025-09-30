#!/usr/bin/env python3
"""
dedupe_posts.py — _posts 内の重複記事を最新1本だけ残して掃除
グルーピング優先度: canonical_url → content_sha → 本文ハッシュ(簡易)
既定はドライラン（表示のみ）。--apply を付けると実際に削除/退避します。

使い方:
  python dedupe_posts.py
  python dedupe_posts.py --apply                 # 実行
  python dedupe_posts.py --trash .trash_posts    # 削除の代わりに移動
"""

import argparse, pathlib, re, hashlib, shutil, datetime, sys, os
from typing import Dict, List, Tuple

BASE   = pathlib.Path.cwd()
POSTS  = BASE / "_posts"

FM_RE  = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.S)
DATE_IN_NAME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})-")

def normalize_url(u: str) -> str:
    if not u: return ""
    # クエリ・フラグメントを全捨て、AMP→www、末尾スラッシュ統一（拡張子無ければ）
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"\?.*$", "", u)
    u = re.sub(r"//amp\.", "//www.", u)
    if not re.search(r"\.(html?|xml|jpg|jpeg|png|gif|webp|pdf)$", u, re.I) and not u.endswith("/"):
        u += "/"
    return u

def parse_front_matter(txt: str) -> Tuple[Dict[str,str], str]:
    m = FM_RE.match(txt)
    if not m:
        return {}, txt
    raw, body = m.group(1), m.group(2)
    fm = {}
    # ごく簡易に key: value を拾う（ダブルクォート対応・配列は未パースでOK）
    for line in raw.splitlines():
        if ":" not in line: continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        fm[k] = v
    return fm, body

def body_hash(body: str) -> str:
    # HTMLタグ除去→空白圧縮→先頭1500字ハッシュ（重複判定には十分）
    t = re.sub(r"<[^>]+>", "", body or "")
    t = re.sub(r"\s+", " ", t).strip()[:1500]
    return hashlib.sha256(t.encode("utf-8","ignore")).hexdigest()

def extract_date(p: pathlib.Path, fm: Dict[str,str]) -> datetime.datetime:
    # 1) ファイル名から yyyy-mm-dd
    m = DATE_IN_NAME_RE.search(p.name)
    if m:
        y, M, d = map(int, m.groups())
        return datetime.datetime(y, M, d, 0, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    # 2) front matter の date（+0900入りでも雑に読み取る）
    if "date" in fm:
        ds = fm["date"]
        # 例: 2025-09-28 14:23:00 +0900
        try:
            ds2 = ds.split("+")[0].strip()
            return datetime.datetime.strptime(ds2, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=datetime.timezone(datetime.timedelta(hours=9))
            )
        except Exception:
            pass
    # 3) 最終フォールバック: ファイルのmtime
    ts = p.stat().st_mtime
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone(datetime.timedelta(hours=9)))

def pick_group_key(fm: Dict[str,str], body: str) -> Tuple[str,str]:
    cu = normalize_url(fm.get("canonical_url","") or fm.get("source_url",""))
    if cu:
        return ("canon", cu)
    sha = fm.get("content_sha","").strip()
    if sha:
        return ("sha", sha)
    return ("body", body_hash(body))

def find_posts() -> List[pathlib.Path]:
    return sorted(list(POSTS.glob("*.md")) + list(POSTS.glob("*.markdown")))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="実行（実ファイル削除/移動）")
    ap.add_argument("--trash", default="", help="削除の代わりにこのディレクトリへ移動（例: .trash_posts）")
    ap.add_argument("--limit", type=int, default=0, help="処理グループ数の上限（テスト用）")
    args = ap.parse_args()

    if not POSTS.exists():
        print(f"ERROR: {POSTS} が見つかりません。リポジトリルートで実行してください。", file=sys.stderr)
        sys.exit(1)

    if args.trash:
        trash_dir = BASE / args.trash
        if args.apply:
            trash_dir.mkdir(parents=True, exist_ok=True)
    else:
        trash_dir = None

    groups: Dict[Tuple[str,str], List[dict]] = {}
    for p in find_posts():
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:
            continue
        fm, body = parse_front_matter(txt)
        key = pick_group_key(fm, body)
        dt  = extract_date(p, fm)
        title = fm.get("title","")
        groups.setdefault(key, []).append({
            "path": p,
            "date": dt,
            "title": title,
        })

    dup_groups = [(k, v) for k, v in groups.items() if len(v) > 1]
    # 処理順はグループサイズの大きい順→見やすさのため
    dup_groups.sort(key=lambda kv: len(kv[1]), reverse=True)

    total_delete = 0
    total_groups = 0

    for idx, (key, items) in enumerate(dup_groups, 1):
        items.sort(key=lambda x: x["date"])  # 古→新
        keep = items[-1]
        dels = items[:-1]
        total_groups += 1

        print(f"\n=== GROUP {idx} / {len(dup_groups)}  key={key[0]}:{key[1][:80]}")
        print(f" KEEP -> {keep['path'].name}  [{keep['date']:%Y-%m-%d %H:%M}]  {keep['title']}")
        for d in dels:
            print(f" DEL  -> {d['path'].name}  [{d['date']:%Y-%m-%d %H:%M}]  {d['title']}")

        if args.apply:
            for d in dels:
                try:
                    if trash_dir:
                        # 退避: 同名が居たら連番
                        dst = trash_dir / d["path"].name
                        i = 1
                        while dst.exists():
                            stem = d["path"].stem
                            ext = d["path"].suffix
                            dst = trash_dir / f"{stem}-{i}{ext}"
                            i += 1
                        shutil.move(str(d["path"]), str(dst))
                    else:
                        d["path"].unlink()
                    total_delete += 1
                except Exception as e:
                    print(f"  !! 処理失敗: {d['path']}  {e}", file=sys.stderr)

        if args.limit and total_groups >= args.limit:
            break

    print("\n--- SUMMARY ---")
    print(f"重複グループ数: {len(dup_groups)}")
    if args.apply:
        if trash_dir:
            print(f"移動先: {trash_dir}")
        print(f"削除/移動したファイル数: {total_delete}")
    else:
        print("※ ドライラン（--apply で実行、--trash DIR で移動モード）")

if __name__ == "__main__":
    main()
