"""
Karpathy 6-step ladder 檢索器。

兩階段：
  Call 1: 給 LLM index.md + 9 個 _index.md + 6 個 concepts/，
          請 LLM 抽 signal terms、比對 title/tag/summary，回傳候選 FAQ ID（max 15）
  Call 2: 載入候選 FAQ 全文 + 概念頁，產出最終答覆

省成本 vs full-context：
  full-context: ~176K input/query
  ladder:       Call 1 ~10K + Call 2 ~5-15K = ~15-25K（省 85-90%）
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from llm_client import build_system_blocks, call as llm_call, call_stream

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    BM25Okapi = None  # 沒裝就停用 BM25 fallback


# Tokenizer：英文/數字 word-level（lowercased）、中文字 char-level
_BM25_TOKEN_RE = re.compile(r"[a-zA-Z]+|\d+|[一-鿿]")


def bm25_tokenize(text: str) -> list[str]:
    """BM25 用 tokenizer：英數小寫 word、中文 char-level。"""
    if not text:
        return []
    return [t.lower() for t in _BM25_TOKEN_RE.findall(text)]


def build_bm25_index(vault_dir: Path) -> tuple[Any, list[str]]:
    """掃 vault 全部 FAQ md（不含 _index、log、concepts），建 BM25 index。
    回傳 (bm25_object, faq_ids)。沒裝 rank_bm25 時回 (None, [])。
    """
    if BM25Okapi is None:
        return None, []
    docs_tokens: list[list[str]] = []
    faq_ids: list[str] = []
    id_re = re.compile(r"^\[([A-Z]{2,4}\d{2,4})\]")
    for md in sorted(vault_dir.rglob("*.md")):
        if md.name.startswith("_") or md.name in ("index.md", "log.md"):
            continue
        if "concepts" in md.parts:
            continue
        m = id_re.match(md.name)
        if not m:
            continue
        text = md.read_text(encoding="utf-8")
        docs_tokens.append(bm25_tokenize(text))
        faq_ids.append(m.group(1))
    if not docs_tokens:
        return None, []
    return BM25Okapi(docs_tokens), faq_ids


def bm25_search(
    bm25: Any, faq_ids: list[str], query: str, top_k: int = 5, min_score: float = 0.5,
) -> list[str]:
    """用 BM25 找 top-K 相關 FAQ。低分（< min_score）排除以避免拉無關 FAQ。"""
    if bm25 is None or not faq_ids:
        return []
    tokens = bm25_tokenize(query)
    if not tokens:
        return []
    scores = bm25.get_scores(tokens)
    ranked = sorted(enumerate(scores), key=lambda x: -x[1])
    out: list[str] = []
    for i, s in ranked[:top_k]:
        if s < min_score:
            break
        out.append(faq_ids[i])
    return out


SELECTOR_INSTRUCTIONS = """你是 iASK 2.0 的候選 FAQ 選取器（Karpathy wiki-read Step B）。

你的任務：根據使用者問題，從下方 <NAVIGATION_LAYER> 內的索引與概念頁，
找出**最相關的 FAQ ID**，只回傳 JSON。

# 選取規則（依優先序）
1. 抽出問題的「signal terms」（有意義名詞、專有名詞、技術詞、版本號等，跳過 stopword）
2. 對每個 _index.md 內的 FAQ：
   - title 包含任一 signal term（不分大小寫）→ 候選
   - frontmatter `tags` 含任一 signal term（不分大小寫）→ 候選
   - 一行摘要包含 signal term → 候選
3. 含 concepts/ 內的 FAQ ID（concepts 已經做了跨部門聚合）
4. **上限 15 個 ID**，少而精準勝於多而泛濫
5. 若完全找不到匹配，回傳空陣列 []（表示 FAQ 沒此題）

# 輸出格式（嚴格 JSON、不加任何解釋）
{
  "signal_terms": ["term1", "term2", ...],
  "candidate_ids": ["PMC005", "CFC008", ...],
  "reasoning": "一句話說明（30 字內）"
}
"""

REWRITER_INSTRUCTIONS = """你是對話脈絡的問題改寫助手。任務只有一件：把使用者「最新問題」改寫為「自包含」問題，讓檢索器不必看歷史也能找到對的 FAQ。

# 三條紀律
1. 若最新問題本身已自包含、無上下文依賴 → 原樣回傳，不要動。
2. 不要回答問題，只改寫。
3. 解掉代名詞與省略（它、那個、再、接下來、剛剛、那如果...），用前文補上具體名詞。

# 範例
歷史：
[使用者]：我要請特休該怎麼辦？
[iASK]：依「請假管理作業指導書」...

最新問題：那如果沒休完呢？
→ {"standalone_question": "特休沒休完該怎麼處理？"}

歷史：
[使用者]：Pipeline 怎麼提交？
[iASK]：每月 1 號...

最新問題：保密切結書要怎麼簽？
→ {"standalone_question": "保密切結書要怎麼簽？"}

# 輸出格式（嚴格 JSON、不加任何解釋）
{"standalone_question": "..."}
"""


ANSWERER_INSTRUCTIONS = """你是 ProFederal 集團的內部知識助理（iASK 2.0）。
你已收到由檢索器挑出的相關 FAQ 全文，請根據這些 FAQ 回答使用者問題。

<answering_protocol>
1. 用下方 <SELECTED_FAQS> 內的內容組合答案。你可以「串接」多個 FAQ 的事實成為流程或對照
   （例如 FAQ-A 寫「先做 X」加 FAQ-B 寫「再做 Y」，可組成「先 X、後 Y」）。
2. **儘量完整**：保留 FAQ 內的具體數字、門檻、表單名稱、提醒事項與注意事項，不要為了
   簡潔而省略使用者需要知道的細節。寧可長一點，不要漏。
3. 不可引入任何 FAQ 沒寫的事實——具體數字、人名、品牌、步驟、信箱、門檻、URL。
4. **積極作答**：即使使用者問題很短或省略（例如「13步驟」、「step10」、「請假」、「特休」等），
   只要 SELECTED_FAQS 內有與該關鍵字、概念或縮寫對應的內容，就要用該內容組合答案。
   檢索器之所以選出這幾篇 FAQ，就是判斷它們相關，請信任檢索結果、積極答出來。
5. 只有當 SELECTED_FAQS 內**完全找不到任何與使用者問題相關的內容**（連鬆散的關鍵字
   對應都沒有）時，才回答：「目前 FAQ 沒有這題的明確答案，建議洽詢 {部門}」。
</answering_protocol>

<citation_contract>
答案結尾列出引用的 FAQ ID，格式：`來源：[PMC005]、[CFC008]`
答案中提到的每個事實都應追溯到對應 FAQ。寧可多引、不要漏引。
**若 FAQ 內含 BPM 或 SharePoint URL，保留為可點擊 Markdown 連結 `[名稱](URL)`**，不要
把連結改成純文字，也不要省略。
</citation_contract>

<format>
只用中英文字、阿拉伯數字、標準標點、Markdown 標準語法。
不用 emoji 或 Unicode 裝飾符號。流程順序用「然後」「接著」。
繁體中文（台灣用語），不用香港或大陸用語。
直接給答案、不寒暄、不重複問題。
</format>
"""


def load_navigation_layer(vault_dir: Path) -> str:
    """載入 index.md + 9 個 dept _index.md + 所有 concepts/ — 用於 Step A/B。"""
    chunks = []

    # 1. 根 index.md
    root_idx = vault_dir / "index.md"
    if root_idx.exists():
        chunks.append(f"<!-- FILE: index.md -->\n{root_idx.read_text(encoding='utf-8')}")

    # 2. 各部門 _index.md
    for dept_dir in sorted(vault_dir.iterdir()):
        if not dept_dir.is_dir() or dept_dir.name.startswith("."):
            continue
        if dept_dir.name == "concepts":
            continue  # concepts 另外處理
        idx = dept_dir / "_index.md"
        if idx.exists():
            chunks.append(f"<!-- FILE: {dept_dir.name}/_index.md -->\n{idx.read_text(encoding='utf-8')}")

    # 3. concepts/ 全部
    concepts_dir = vault_dir / "concepts"
    if concepts_dir.exists():
        for cf in sorted(concepts_dir.glob("*.md")):
            chunks.append(f"<!-- FILE: concepts/{cf.name} -->\n{cf.read_text(encoding='utf-8')}")

    return "\n---\n".join(chunks)


def load_faq_by_ids(vault_dir: Path, ids: list[str]) -> tuple[str, list[str]]:
    """根據 FAQ ID 載入對應 .md 全文。回傳 (合併內容, 找到的 ID 清單)。"""
    found_ids = []
    chunks = []

    for fid in ids:
        # FAQ 檔名格式: <DEPT>/[<ID>]-xxx.md
        matched = list(vault_dir.rglob(f"[[]{fid}[]]-*.md"))
        if not matched:
            continue
        f = matched[0]
        rel = f.relative_to(vault_dir)
        chunks.append(f"<!-- FILE: {rel} -->\n{f.read_text(encoding='utf-8')}")
        found_ids.append(fid)

    return "\n---\n".join(chunks), found_ids


def extract_candidate_ids(answer_json: str) -> tuple[list[str], list[str], str]:
    """解析 selector 回應的 JSON，容錯處理。"""
    # 嘗試找到 JSON 區塊（可能被 markdown code block 包住）
    m = re.search(r"\{.*?\}", answer_json, re.DOTALL)
    if not m:
        return [], [], "selector 未回傳 JSON"

    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return [], [], f"JSON parse error: {e}"

    signal_terms = data.get("signal_terms", []) or []
    candidate_ids = data.get("candidate_ids", []) or []
    reasoning = data.get("reasoning", "")

    # 確保 ID 是 list of str
    candidate_ids = [str(x).strip() for x in candidate_ids if x]
    # 規範化：去重、限上限 15
    seen = set()
    cleaned = []
    for cid in candidate_ids:
        if cid not in seen:
            seen.add(cid)
            cleaned.append(cid)
        if len(cleaned) >= 15:
            break

    return cleaned, signal_terms, reasoning


def _format_history_for_prompt(history: list[dict]) -> str:
    lines = []
    for h in history:
        lines.append(f"[使用者]：{h['question']}")
        lines.append(f"[iASK]：{h['answer']}")
    return "\n".join(lines)


def rewrite_question(
    client: Any,
    model: str,
    question: str,
    history: list[dict],
) -> tuple[str, dict]:
    """用 LLM 把問題改寫為自包含。歷史空時不該呼叫此函式。

    回傳 (standalone_question, usage)。
    JSON 解析失敗時 fallback 回 original question。
    """
    history_text = _format_history_for_prompt(history)
    user_msg = f"# 對話歷史\n{history_text}\n\n# 最新問題\n{question}"
    system_blocks = [{"type": "text", "text": REWRITER_INSTRUCTIONS}]
    raw, usage = llm_call(
        client, model, system_blocks,
        [{"role": "user", "content": user_msg}],
        max_tokens=256,
    )
    m = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not m:
        return question, usage
    try:
        data = json.loads(m.group(0))
        standalone = (data.get("standalone_question") or "").strip()
        return (standalone or question), usage
    except json.JSONDecodeError:
        return question, usage


def _select_candidates(
    client: Any,
    model: str,
    vault_dir: Path,
    question: str,
    navigation_text: str | None,
    history: list[dict] | None,
    bm25_index: Any,
    bm25_faq_ids: list[str] | None,
) -> dict:
    """跑 Rewriter + Selector（+ BM25 fallback）並載入候選 FAQ 全文。

    這是 ladder_query 與 ladder_query_stream 共用的「檢索階段」。answerer（Step C）
    由各 caller 自己決定要一次回（call）還是 streaming（call_stream）。

    回傳 dict：
      pre_usage              rewriter + selector 累計 usage
      debug                  debug_info（含 candidate_ids_found）
      cold_answer            無候選 / 載入失敗時的訊息字串；否則 None
      answerer_system_blocks 給 answerer 的 system blocks（cold 時 None）
      answerer_messages      給 answerer 的 messages（cold 時 []）
    """
    if navigation_text is None:
        navigation_text = load_navigation_layer(vault_dir)

    # Step R: Rewriter（僅有歷史時跑）
    rewriter_usage = {"input": 0, "cached_input": 0, "output": 0, "total": 0}
    rewritten_question = question
    if history:
        rewritten_question, rewriter_usage = rewrite_question(
            client, model, question, history,
        )

    # Step B: selector — 用 rewritten（standalone）
    selector_system_blocks = build_system_blocks(
        SELECTOR_INSTRUCTIONS,
        "<NAVIGATION_LAYER>\n" + navigation_text + "\n</NAVIGATION_LAYER>",
    )
    selector_answer, selector_usage = llm_call(
        client,
        model,
        selector_system_blocks,
        [{"role": "user", "content": rewritten_question}],
        max_tokens=512,
    )

    candidate_ids, signal_terms, reasoning = extract_candidate_ids(selector_answer)
    debug = {
        "signal_terms": signal_terms,
        "candidate_ids_requested": candidate_ids,
        "candidate_ids_found": [],
        "reasoning": reasoning,
        "selector_raw": selector_answer,
        "original_question": question,
        "rewritten_question": rewritten_question,
        "bm25_fallback_used": False,
        "bm25_candidates": [],
    }
    pre_usage = _sum_usage(rewriter_usage, selector_usage)

    # 0 candidates → 先試 BM25 fallback、真不行才走 cold path
    if not candidate_ids and bm25_index is not None:
        bm25_hits = bm25_search(bm25_index, bm25_faq_ids or [], rewritten_question, top_k=5)
        if bm25_hits:
            candidate_ids = bm25_hits
            debug["bm25_fallback_used"] = True
            debug["bm25_candidates"] = bm25_hits
            debug["candidate_ids_requested"] = bm25_hits

    if not candidate_ids:
        cold_answer = (
            "目前 FAQ 沒有這題的明確答案，建議洽詢相關部門。\n\n"
            f"_（檢索器回報：{reasoning}）_"
        )
        return {
            "pre_usage": pre_usage,
            "debug": debug,
            "cold_answer": cold_answer,
            "answerer_system_blocks": None,
            "answerer_messages": [],
        }

    # 載入候選 FAQ 全文
    faq_text, found_ids = load_faq_by_ids(vault_dir, candidate_ids)
    debug["candidate_ids_found"] = found_ids

    if not faq_text:
        return {
            "pre_usage": pre_usage,
            "debug": debug,
            "cold_answer": "檢索器找到候選 FAQ ID 但檔案載入失敗，請洽系統管理員。",
            "answerer_system_blocks": None,
            "answerer_messages": [],
        }

    # Step C 準備：answerer 帶歷史（為語氣連貫），question 用原始問題
    answerer_system_blocks = build_system_blocks(
        ANSWERER_INSTRUCTIONS,
        "<SELECTED_FAQS>\n" + faq_text + "\n</SELECTED_FAQS>",
    )
    answerer_messages = []
    if history:
        for h in history:
            answerer_messages.append({"role": "user", "content": h["question"]})
            answerer_messages.append({"role": "assistant", "content": h["answer"]})
    answerer_messages.append({"role": "user", "content": question})

    return {
        "pre_usage": pre_usage,
        "debug": debug,
        "cold_answer": None,
        "answerer_system_blocks": answerer_system_blocks,
        "answerer_messages": answerer_messages,
    }


def ladder_query(
    client: Any,
    model: str,
    vault_dir: Path,
    question: str,
    navigation_text: str | None = None,
    history: list[dict] | None = None,
    bm25_index: Any = None,
    bm25_faq_ids: list[str] | None = None,
) -> tuple[str, dict, dict]:
    """
    執行完整 6-step ladder（含對話脈絡）。回傳 (answer, total_usage, debug_info)。

    history: 近 N 對 Q&A，格式 [{"question": str, "answer": str}, ...]（舊到新）。
      若非空，會先跑 Rewriter 把當前問題改寫成 standalone 再進 selector；
      answerer 會額外帶歷史對話訊息為語氣連貫。

    debug_info 含:
      - signal_terms / candidate_ids_requested / candidate_ids_found / reasoning
      - rewritten_question: rewriter 改寫後（無歷史時等於原問題）
      - original_question
    """
    sel = _select_candidates(
        client, model, vault_dir, question,
        navigation_text, history, bm25_index, bm25_faq_ids,
    )
    debug = sel["debug"]
    if sel["cold_answer"] is not None:
        return sel["cold_answer"], sel["pre_usage"], debug

    final_answer, answerer_usage = llm_call(
        client,
        model,
        sel["answerer_system_blocks"],
        sel["answerer_messages"],
        max_tokens=2048,
    )
    total = _sum_usage(sel["pre_usage"], answerer_usage)
    return final_answer, total, debug


def ladder_query_stream(
    client: Any,
    model: str,
    vault_dir: Path,
    question: str,
    navigation_text: str | None = None,
    history: list[dict] | None = None,
    bm25_index: Any = None,
    bm25_faq_ids: list[str] | None = None,
) -> Any:
    """ladder_query 的 streaming 版本。generator，依序 yield：
      ("meta", debug)                  selector 完成（debug 含 candidate_ids_found）
      ("delta", text_piece)            answerer 逐塊吐出的答案
      ("done", (answer, usage, debug)) 結束（answer 為完整答案、usage 為總用量）

    cold path（無候選 / 載入失敗）：meta → 單一 delta（整段訊息）→ done。
    """
    sel = _select_candidates(
        client, model, vault_dir, question,
        navigation_text, history, bm25_index, bm25_faq_ids,
    )
    debug = sel["debug"]
    yield ("meta", debug)

    if sel["cold_answer"] is not None:
        yield ("delta", sel["cold_answer"])
        yield ("done", (sel["cold_answer"], sel["pre_usage"], debug))
        return

    pieces: list[str] = []
    answerer_usage = {"input": 0, "cached_input": 0, "output": 0, "total": 0}
    for kind, payload in call_stream(
        client,
        model,
        sel["answerer_system_blocks"],
        sel["answerer_messages"],
        max_tokens=2048,
    ):
        if kind == "delta":
            pieces.append(payload)
            yield ("delta", payload)
        elif kind == "usage":
            answerer_usage = payload

    final_answer = "".join(pieces)
    total = _sum_usage(sel["pre_usage"], answerer_usage)
    yield ("done", (final_answer, total, debug))


def _sum_usage(a: dict, b: dict) -> dict:
    return {
        "input": a.get("input", 0) + b.get("input", 0),
        "cached_input": a.get("cached_input", 0) + b.get("cached_input", 0),
        "output": a.get("output", 0) + b.get("output", 0),
        "total": a.get("total", 0) + b.get("total", 0),
    }
