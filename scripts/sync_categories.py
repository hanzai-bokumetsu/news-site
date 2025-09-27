import pathlib, re, yaml
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parents[1]
POSTS = BASE / "_posts"
CATDIR = BASE / "categories"
LAYOUT = "category"

CATDIR.mkdir(exist_ok=True)

def frontmatter(path: pathlib.Path):
    txt = path.read_text(encoding="utf-8", errors="ignore")
    m = re.match(r"^---\n(.*?)\n---\n", txt, flags=re.S)
    if not m: return {}
    return yaml.safe_load(m.group(1)) or {}

def ensure_cat_page(cat: str):
    # 日本語名をそのままファイル名に（GitHubはOK）。既にあればスキップ。
    f = CATDIR / f"{cat}.md"
    if f.exists(): return
    # permalink は /categories/◯◯/ に固定
    fm = (
        "---\n"
        f"layout: {LAYOUT}\n"
        f"title: {cat}\n"
        f"permalink: /categories/{cat}/\n"
        f"category: {cat}\n"
        "---\n"
    )
    f.write_text(fm, encoding="utf-8")
    print("created", f)

def main():
    cats = set()
    for md in POSTS.glob("*.md"):
        fm = frontmatter(md)
        for c in (fm.get("categories") or []):
            # 文字列だけ拾う
            if isinstance(c, str):
                cats.add(c)
    # 一覧ページ自体も置く
    (CATDIR / "index.md").exists() or None
    for c in sorted(cats):
        ensure_cat_page(c)

if __name__ == "__main__":
    main()
