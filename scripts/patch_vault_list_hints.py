"""Patch vault md：對 N:1（多 list 項目共用 1 個 URL）的 FAQ，
在 body list 區塊結尾加一行統一的「以上項目可從此連結頁面瀏覽或下載」說明 + 連結。

設計：
- 偵測：frontmatter 恰有 1 個 URL；body 有 ≥ 2 個編號 list 項目（`1. xxx` `2. xxx`...）
- 套法：list 區塊結尾插入 hint line（含 marker 註解避免重做）
- 不動 frontmatter（你 manage_tags add 的 tag 安全）
- 不動 body 既有內容、只 append
- 可重跑（HTML comment marker `<!-- ... -->` 標記已 patch）

用法：
  python3 scripts/patch_vault_list_hints.py --dry-run   # 只列要動的檔
  python3 scripts/patch_vault_list_hints.py             # 實際寫入
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


HERE = Path(__file__).resolve().parent
VAULT_DIR = HERE.parent / "vault"

# 行首編號 list：`1. xxx`、`2. xxx` ...
LIST_LINE_RE = re.compile(r"^\s*\d+\.\s+\S.*$", re.MULTILINE)

# 已插入過的 marker（重跑時偵測、避免重做）
MARKER = "<!-- iask:folder-hint -->"


def is_folder_url(url: str) -> bool:
    """偵測 URL 是不是資料夾 / 集合頁。"""
    if not url:
        return False
    u = url.lower()
    return ("folderid=" in u) or ("/:f:/" in u)


def split_md(text: str) -> tuple[str, str] | None:
    """切 frontmatter + body。回傳 (fm_yaml_text, body) 或 None。"""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    return text[4:end], text[end + 5:]


def detect_list_block(body: str) -> tuple[int, int, int] | None:
    """找到 body 內第一個連續編號 list 區塊。
    回傳 (line_start_offset, end_offset, n_items)；找不到回 None。
      line_start_offset：list 第一行的行首（用於在 list **之前**插入 hint）
      end_offset：list 最後一行的行尾
    """
    matches = list(LIST_LINE_RE.finditer(body))
    if len(matches) < 2:
        return None
    first_start = matches[0].start()
    # 把 first_start 對齊到該行行首（含可能的 leading spaces）
    line_start = body.rfind("\n", 0, first_start) + 1
    last_end = matches[-1].end()
    nl = body.find("\n", last_end)
    if nl < 0:
        nl = len(body)
    return line_start, nl, len(matches)


def patch_one(md_path: Path) -> tuple[bool, str]:
    """回傳 (是否要 patch, 原因/說明)。"""
    text = md_path.read_text(encoding="utf-8")
    parts = split_md(text)
    if not parts:
        return False, "no frontmatter"
    fm_text, body = parts

    if MARKER in body:
        return False, "already patched"

    try:
        fm = yaml.safe_load(fm_text) or {}
    except Exception as e:
        return False, f"yaml error: {e}"

    links = [L for L in (fm.get("links") or []) if L.get("url")]
    if len(links) != 1:
        return False, f"links={len(links)} (need exactly 1 for N:1 case)"

    list_block = detect_list_block(body)
    if not list_block:
        return False, "no list block ≥2 items"
    line_start, _, n_items = list_block

    link = links[0]
    name = link.get("name") or "參考連結"
    url = link["url"]

    hint = f"{MARKER}\n以下項目可從此連結頁面瀏覽或下載：[{name}]({url})\n\n"

    new_body = body[:line_start] + hint + body[line_start:]
    new_text = text[: text.find("\n---\n", 4) + 5] + new_body

    return True, f"N:1 list={n_items} items"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只列、不寫")
    ap.add_argument("--vault", default=str(VAULT_DIR))
    args = ap.parse_args()

    vault = Path(args.vault)
    if not vault.exists():
        print(f"vault not found: {vault}", file=sys.stderr)
        return 2

    patched: list[tuple[Path, str]] = []
    skipped: list[tuple[Path, str]] = []

    md_files = sorted(vault.rglob("*.md"))
    # 排除 index / log / _index / concepts/*
    md_files = [
        m for m in md_files
        if not m.name.startswith("_")
        and m.name not in ("index.md", "log.md")
        and "concepts" not in m.parts
    ]

    for md in md_files:
        will_patch, reason = patch_one(md)
        rel = md.relative_to(vault)
        if will_patch:
            patched.append((md, reason))
            if not args.dry_run:
                # 真的寫
                text = md.read_text(encoding="utf-8")
                parts = split_md(text)
                fm_text, body = parts
                fm = yaml.safe_load(fm_text)
                links = [L for L in fm["links"] if L.get("url")]
                link = links[0]
                name = link.get("name") or "參考連結"
                url = link["url"]
                list_block = detect_list_block(body)
                line_start, _, _ = list_block
                hint = f"{MARKER}\n以下項目可從此連結頁面瀏覽或下載：[{name}]({url})\n\n"
                new_body = body[:line_start] + hint + body[line_start:]
                new_text = text[: text.find("\n---\n", 4) + 5] + new_body
                md.write_text(new_text, encoding="utf-8")
        else:
            skipped.append((md, reason))

    print(f"\n=== 會被 patch 的 FAQ（{len(patched)} 個）===\n")
    for md, reason in patched:
        rel = md.relative_to(vault)
        print(f"  ✓ {rel}  ({reason})")

    print(f"\n=== 不動 ===")
    # 統計 skip 原因
    from collections import Counter
    reasons = Counter(r for _, r in skipped)
    for r, n in sorted(reasons.items(), key=lambda x: -x[1])[:10]:
        print(f"  {n} × {r}")

    if args.dry_run:
        print(f"\n[dry-run] 沒寫入。確認清單後拿掉 --dry-run 再跑。")
    else:
        print(f"\n[applied] 已寫入 {len(patched)} 個檔。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
