"""
iASK 2.0 PoC eval runner — 走 OpenRouter，啟用 prompt caching。

執行：
  . ~/.iask_openrouter_key
  python3 scripts/run_eval.py                              # 全 30 題，sonnet-4.6
  python3 scripts/run_eval.py --model anthropic/claude-haiku-4.5
  python3 scripts/run_eval.py --limit 5                     # 先 5 題 sanity check
  python3 scripts/run_eval.py --start 21                    # 只跑 Q21 之後
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("缺少 PyYAML，請執行: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from chatbot import SYSTEM_INSTRUCTIONS, load_vault  # noqa: E402
from llm_client import build_system_blocks, call, estimate_cost_usd, make_client  # noqa: E402
from ladder_retriever import ladder_query, load_navigation_layer  # noqa: E402


ROOT = HERE.parent
VAULT_DIR = ROOT / "vault"
QUESTIONS_PATH = ROOT / "eval" / "questions.yaml"
RESULTS_DIR = ROOT / "eval"

DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"
ID_PATTERN = re.compile(r"\[([A-Z]{2,4}\d{2,4})\]")


def extract_cited_ids(answer: str) -> list[str]:
    seen, out = set(), []
    for m in ID_PATTERN.finditer(answer):
        rid = m.group(1)
        if rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def score_question(q: dict, answer: str, cited: list[str]) -> dict:
    expected = set(q.get("expected_ids") or [])
    must_decline = q.get("must_decline", False)
    must_refuse_unsafe = q.get("must_refuse_unsafe", False)

    looks_declined = any(kw in answer for kw in ["沒有", "無此", "建議洽", "不在 FAQ", "找不到", "FAQ 中沒有", "目前 FAQ", "未提及"])
    looks_refused = any(kw in answer for kw in ["無法", "拒絕", "不適合", "違反", "不該"])

    if must_refuse_unsafe:
        ok = looks_refused or looks_declined
        return {"pass": ok, "notes": f"unsafe → expect refuse/decline. declined={looks_declined}, refused={looks_refused}"}

    if must_decline:
        if expected:
            hit = expected & set(cited)
            ok = bool(hit) and any(k in answer for k in ["依", "年資", "看", "視", "需依", "請依"])
            return {"pass": ok, "notes": f"must explain constraint. cited={cited}, expected_hit={list(hit)}"}
        ok = looks_declined
        return {"pass": ok, "notes": f"must decline. declined={looks_declined}"}

    if not expected:
        return {"pass": True, "notes": "no expected ids defined"}

    hit = expected & set(cited)
    return {"pass": bool(hit), "notes": f"cited={cited}, expected={list(expected)}, hit={list(hit)}"}


def run(model: str, limit: int | None, start: int, sleep_sec: float, ladder: bool = False) -> None:
    print(f"載入 vault: {VAULT_DIR}")
    if ladder:
        nav_text = load_navigation_layer(VAULT_DIR)
        print(f"  navigation layer: {len(nav_text):,} chars (ladder 模式)")
    else:
        vault_text, n_files = load_vault(VAULT_DIR)
        print(f"  {n_files} files, {len(vault_text):,} chars (full-context)")

    questions = yaml.safe_load(QUESTIONS_PATH.read_text(encoding="utf-8"))
    if start > 1:
        questions = [q for q in questions if q["id"] >= start]
    if limit:
        questions = questions[:limit]
    print(f"跑 {len(questions)} 題（從 Q{questions[0]['id']} 開始），model={model}, ladder={ladder}\n")

    client = make_client()
    if not ladder:
        system_blocks = build_system_blocks(SYSTEM_INSTRUCTIONS, vault_text)

    rows = []
    fresh_in_total = 0
    cached_in_total = 0
    out_total = 0
    cost_total = 0.0
    start_t = time.time()

    for i, q in enumerate(questions, 1):
        qid = q["id"]
        cat = q["category"]
        text = q["question"]
        print(f"[{i}/{len(questions)}] Q{qid:>2} ({cat}) {text}")
        try:
            if ladder:
                answer, usage, debug = ladder_query(client, model, VAULT_DIR, text, navigation_text=nav_text)
                print(f"      [ladder] candidates={debug['candidate_ids_found']}")
            else:
                answer, usage = call(client, model, system_blocks, [{"role": "user", "content": text}])
            cost = estimate_cost_usd(model, usage)
        except Exception as e:
            answer = f"[ERROR] {e}"
            usage = {"input": 0, "cached_input": 0, "output": 0, "total": 0}
            cost = 0.0

        cited = extract_cited_ids(answer)
        score = score_question(q, answer, cited)
        cache_hit_pct = (100 * usage["cached_input"] / (usage["input"] + usage["cached_input"])
                        if usage["input"] + usage["cached_input"] > 0 else 0)
        print(f"      cited={cited}  pass={score['pass']}")
        print(f"      fresh_in={usage['input']:,}  cached={usage['cached_input']:,} ({cache_hit_pct:.0f}% hit)  "
              f"out={usage['output']}  ≈${cost:.4f}\n")

        fresh_in_total += usage["input"]
        cached_in_total += usage["cached_input"]
        out_total += usage["output"]
        cost_total += cost
        rows.append({"q": q, "answer": answer, "cited": cited, "score": score, "usage": usage, "cost": cost})

        if sleep_sec and i < len(questions):
            time.sleep(sleep_sec)

    elapsed = time.time() - start_t
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"results_{ts}.md"
    write_report(out_path, rows, model, {
        "fresh_in": fresh_in_total,
        "cached_in": cached_in_total,
        "output": out_total,
        "cost": cost_total,
        "elapsed": elapsed,
    })
    print(f"\n報告寫入: {out_path}")
    print(f"耗時 {elapsed:.0f}s, fresh_in={fresh_in_total:,}, cached={cached_in_total:,}, "
          f"out={out_total:,}, ≈${cost_total:.2f}")


def write_report(path: Path, rows: list[dict], model: str, usage_total: dict) -> None:
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["q"]["category"], []).append(r)

    lines = []
    lines.append("# iASK 2.0 PoC Eval Report")
    lines.append("")
    lines.append(f"- Model: `{model}` (via OpenRouter, prompt caching 啟用)")
    lines.append(f"- 時間: {_dt.datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 題數: {len(rows)}")
    lines.append(f"- 耗時: {usage_total.get('elapsed', 0):.0f}s")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Total | Pass | Pass% |")
    lines.append("|---|---|---|---|")
    overall_pass = 0
    for cat in ["direct", "paraphrase", "routing", "adversarial"]:
        items = by_cat.get(cat, [])
        if not items:
            continue
        passes = sum(1 for r in items if r["score"]["pass"])
        overall_pass += passes
        pct = 100 * passes / len(items) if items else 0
        lines.append(f"| {cat} | {len(items)} | {passes} | {pct:.0f}% |")
    overall_pct = 100 * overall_pass / len(rows) if rows else 0
    lines.append(f"| **OVERALL** | **{len(rows)}** | **{overall_pass}** | **{overall_pct:.0f}%** |")
    lines.append("")
    fresh = usage_total["fresh_in"]
    cached = usage_total["cached_in"]
    total_in = fresh + cached
    hit_pct = 100 * cached / total_in if total_in else 0
    lines.append(f"**Token usage：** fresh_in={fresh:,}, cached_in={cached:,} ({hit_pct:.0f}% cache hit), "
                 f"output={usage_total['output']:,}")
    lines.append(f"**估算成本：** ≈${usage_total['cost']:.2f} USD")
    lines.append("")

    lines.append("## Per-Question Detail")
    lines.append("")
    for r in rows:
        q = r["q"]
        passed = "✅" if r["score"]["pass"] else "❌"
        lines.append(f"### {passed} [Q{q['id']}] {q['category']} — {q['question']}")
        lines.append("")
        lines.append(f"- expected: `{q.get('expected_ids', [])}`")
        lines.append(f"- cited: `{r['cited']}`")
        lines.append(f"- notes: {r['score']['notes']}")
        u = r["usage"]
        lines.append(f"- tokens: fresh_in={u['input']}, cached_in={u['cached_input']}, out={u['output']}, ≈${r['cost']:.4f}")
        lines.append("")
        lines.append("<details><summary>Answer</summary>")
        lines.append("")
        lines.append(r["answer"])
        lines.append("")
        lines.append("</details>")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 題（套用 start 之後）")
    parser.add_argument("--start", type=int, default=1, help="從第幾題 ID 開始（含）")
    parser.add_argument("--sleep", type=float, default=0.0, help="每題之間暫停秒數，避免 rate limit")
    parser.add_argument("--ladder", action="store_true", help="使用 6-step ladder 檢索而非 full-context")
    args = parser.parse_args()
    run(args.model, args.limit, args.start, args.sleep, ladder=args.ladder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
