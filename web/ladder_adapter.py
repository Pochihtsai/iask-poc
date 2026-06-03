"""薄包一層 ladder_query，加 latency 與 metric 收集。

單例 LadderEngine 在 app 啟動時建一次，cache navigation layer 在記憶體。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from llm_client import estimate_cost_usd, make_client  # noqa: E402
from ladder_retriever import (  # noqa: E402
    build_bm25_index,
    ladder_query,
    load_navigation_layer,
)


VAULT_DIR = ROOT / "vault"


class LadderEngine:
    """單例：app 啟動時建一次。"""

    def __init__(self, model: str):
        self.model = model
        self.client = make_client()
        self.nav_text = load_navigation_layer(VAULT_DIR)
        # BM25 fallback index — selector cold 時用
        self.bm25, self.bm25_faq_ids = build_bm25_index(VAULT_DIR)
        if self.bm25 is not None:
            print(f"  BM25 fallback index: {len(self.bm25_faq_ids)} FAQ")

    def ask(self, question: str, history: list[dict] | None = None) -> dict:
        """跑 ladder。history 為近 N 對 Q&A（list of {question, answer}）。"""
        t0 = time.monotonic()
        answer, usage, debug = ladder_query(
            self.client, self.model, VAULT_DIR, question,
            navigation_text=self.nav_text,
            history=history,
            bm25_index=self.bm25,
            bm25_faq_ids=self.bm25_faq_ids,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        cost = estimate_cost_usd(self.model, usage)
        return {
            "answer": answer,
            "candidates": debug["candidate_ids_found"],
            "candidates_requested": debug.get("candidate_ids_requested", []),
            "signal_terms": debug["signal_terms"],
            "reasoning": debug.get("reasoning", ""),
            "rewritten_question": debug.get("rewritten_question", question),
            "tokens_in": usage["input"],
            "tokens_cached": usage["cached_input"],
            "tokens_out": usage["output"],
            "cost_usd": cost,
            "latency_ms": latency_ms,
        }
