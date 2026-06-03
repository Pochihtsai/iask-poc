"""
iASK 2.0 PoC chatbot — 走 OpenRouter，含 Anthropic prompt caching。

執行：
  . ~/.iask_openrouter_key                       # 把 OPENROUTER_API_KEY 載入 env
  python3 scripts/chatbot.py                     # 互動模式
  python3 scripts/chatbot.py -q "PMC 是什麼"       # 一次性提問
  python3 scripts/chatbot.py --model anthropic/claude-haiku-4.5    # 換模型
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from llm_client import build_system_blocks, call, estimate_cost_usd, make_client  # noqa: E402
from ladder_retriever import ladder_query, load_navigation_layer  # noqa: E402


ROOT = HERE.parent
VAULT_DIR = ROOT / "vault"

DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"

SYSTEM_INSTRUCTIONS = """<role>
你是 ProFederal 集團的內部知識助理（iASK 2.0），協助同仁回答關於集團管理、
行政、人資、流程的問題。回答的對象是內部同仁，必須準確、可追溯、專業。
</role>

<knowledge_source>
你的所有回答必須來自下方 <faq_knowledge_base> 內的內容。FAQ 是集團多年累積、
經部門審核的正式知識；它是這個機器人唯一的事實來源。你訓練資料中關於這家
公司的任何資訊（如果有的話）都視為過時且不可靠，一律忽略。
</knowledge_source>

<answering_protocol>
1. 找到 FAQ 中與問題相關的條目。
2. 用 FAQ 內容組合答案——你可以「串接」多個 FAQ 的事實成為流程或對照
   （例如 FAQ-A 寫「先做 X」加 FAQ-B 寫「再做 Y」，可組成「先 X、後 Y」）。
3. 你不可以引入任何 FAQ 沒寫的事實——具體數字、人名、品牌、步驟、信箱、
   門檻——即使你訓練資料知道、即使是合理推測。
4. 若 FAQ 沒有此題答案，回答：
   「目前 FAQ 沒有這題的明確答案，建議洽詢 {部門}。」
   並選一個最合理的部門。不要勉強拼湊答案。
</answering_protocol>

<citation_contract>
答案結尾必須列出所引用的 FAQ ID，格式：`來源：[PMC005]、[CFC008]`。
答案中提到的每個事實都應追溯得到對應 FAQ。**寧可多引、不要漏引。**
若 FAQ 內含 BPM 或 SharePoint URL，保留為可點擊 Markdown 連結。
</citation_contract>

<format>
只用：中英文字、阿拉伯數字、標準中英標點、Markdown 標準語法
（`#` 標題、`-` 條列、`|表格|`、`**粗體**`、反引號程式碼）。
不用 emoji 或其他 Unicode 裝飾符號。流程順序用「然後」「接著」表達。
繁體中文（台灣用語），不用香港或大陸用語。
直接給答案，不寒暄、不重複問題、結尾不問「還有其他問題嗎？」。
</format>

<department_codes>
- CFC: 財務（會計、出納、報帳）
- GAC: 行政（庶務、會議室、識別證、文具）
- HQ: 總部統一治理
- IPC: 採購（採購契約、供應商管理、選商驗收）
- KMC: 知識管理
- LCC: 法務
- PCC: 公司治理
- PMC: 專案管理（任務、提案、Pipeline、CEM）
- TMC: 人資（CB 薪酬、ER 員工關係、HIR 招募 等子類）
</department_codes>

<faq_structure>
每個 FAQ 是一個 Markdown 區塊，含 frontmatter（id、dept、question、links）
與 `# 問題`、`## Answer`、`## Links` 三段。
</faq_structure>
"""


def load_vault(vault_dir: Path) -> tuple[str, int]:
    """讀整個 vault，回傳串接好的 markdown 與檔數。"""
    files = sorted(vault_dir.rglob("*.md"))
    chunks = []
    for f in files:
        rel = f.relative_to(vault_dir)
        chunks.append(f"<!-- FILE: {rel} -->\n{f.read_text(encoding='utf-8')}\n")
    return "\n---\n".join(chunks), len(files)


def fmt_usage(usage: dict, model: str) -> str:
    cost = estimate_cost_usd(model, usage)
    return (
        f"in_fresh={usage['input']:,}  cached={usage['cached_input']:,}  "
        f"out={usage['output']:,}  ≈${cost:.4f}"
    )


def interactive(client, model: str, system_blocks: list[dict]) -> None:
    history: list[dict] = []
    print("iASK 2.0 PoC — 輸入問題，Ctrl+C 或輸入 /q 離開")
    print(f"模型: {model}\n")
    while True:
        try:
            q = input("你 > ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not q:
            continue
        if q.lower() in ("/q", "quit", "exit"):
            break
        history.append({"role": "user", "content": q})
        try:
            answer, usage = call(client, model, system_blocks, history)
        except Exception as e:
            print(f"[錯誤] {e}\n", file=sys.stderr)
            history.pop()
            continue
        history.append({"role": "assistant", "content": answer})
        print(f"\niASK > {answer}\n")
        print(f"   [{fmt_usage(usage, model)}]\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--question", help="單次提問模式")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--vault", default=str(VAULT_DIR))
    parser.add_argument("--ladder", action="store_true",
                        help="使用 Karpathy 6-step ladder 檢索（兩階段 LLM 呼叫，省 context）")
    args = parser.parse_args()

    vault_dir = Path(args.vault)
    if not vault_dir.exists():
        print(f"vault 不存在：{vault_dir}", file=sys.stderr)
        return 2

    client = make_client()

    if args.ladder:
        print(f"模式：6-step ladder (Karpathy)")
        nav_text = load_navigation_layer(vault_dir)
        print(f"  navigation layer: {len(nav_text):,} 字元\n")
        if args.question:
            answer, usage, debug = ladder_query(client, args.model, vault_dir, args.question, navigation_text=nav_text)
            print(f"{answer}\n")
            print(f"[{fmt_usage(usage, args.model)}]")
            print(f"[signal_terms={debug['signal_terms']}]")
            print(f"[candidates={debug['candidate_ids_found']}]")
            return 0
        # Ladder 互動模式
        print("iASK 2.0 PoC ladder 模式 — 輸入問題，/q 離開")
        while True:
            try:
                q = input("你 > ").strip()
            except (KeyboardInterrupt, EOFError):
                print(); break
            if not q: continue
            if q.lower() in ("/q", "quit", "exit"): break
            try:
                answer, usage, debug = ladder_query(client, args.model, vault_dir, q, navigation_text=nav_text)
            except Exception as e:
                print(f"[錯誤] {e}\n", file=sys.stderr); continue
            print(f"\niASK > {answer}\n")
            print(f"   [{fmt_usage(usage, args.model)}]")
            print(f"   [候選 {len(debug['candidate_ids_found'])} 篇: {debug['candidate_ids_found']}]\n")
        return 0

    # Full-context 模式（原行為）
    print(f"模式：full-context")
    print(f"載入 vault: {vault_dir}")
    vault_text, n_files = load_vault(vault_dir)
    print(f"  {n_files} 個檔，{len(vault_text):,} 字元\n")

    system_blocks = build_system_blocks(SYSTEM_INSTRUCTIONS, vault_text)

    if args.question:
        answer, usage = call(client, args.model, system_blocks, [{"role": "user", "content": args.question}])
        print(f"{answer}\n")
        print(f"[{fmt_usage(usage, args.model)}]")
        return 0

    interactive(client, args.model, system_blocks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
