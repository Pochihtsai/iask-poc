"""iASK 2.0 PoC web — FastAPI 前台 + Basic Auth 後台。

啟動：
  . ~/.iask_openrouter_key
  export IASK_ADMIN_USER=admin
  export IASK_ADMIN_PASS=changeme
  cd poc
  python3 -m uvicorn web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from pathlib import Path
from typing import Optional

import yaml
from fastapi import Request

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from web.db import (
    create_conversation,
    create_rule,
    delete_rule,
    find_applicable_rules,
    get_conn,
    get_conversation,
    get_or_create_user,
    get_query_full,
    init_db,
    list_all_queries,
    list_history,
    list_rules,
    save_query,
    update_rule,
)
from web.ladder_adapter import VAULT_DIR, LadderEngine


HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "iask.db"
STATIC_DIR = HERE / "static"

MODEL = os.environ.get("IASK_MODEL", "google/gemini-2.5-flash-lite")
ADMIN_USER = os.environ.get("IASK_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("IASK_ADMIN_PASS")
# Teams Outgoing Webhook security token（base64）— 空字串 = 跳過 HMAC 驗證（僅限初期 dev）
TEAMS_SECRET = os.environ.get("IASK_TEAMS_SECRET", "")

if not ADMIN_PASS:
    print("[WARN] IASK_ADMIN_PASS 未設定。後台會拒絕所有登入。"
          "請 export IASK_ADMIN_PASS=...")

# ---------------------------------------------------------- init

init_db(DB_PATH)
engine = LadderEngine(model=MODEL)
print(f"[boot] model={MODEL}  vault nav layer={len(engine.nav_text):,} chars  db={DB_PATH}")

app = FastAPI(title="iASK 2.0 PoC")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

basic_auth = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(basic_auth)) -> str:
    if not ADMIN_PASS:
        raise HTTPException(503, "Admin not configured")
    correct_user = secrets.compare_digest(credentials.username, ADMIN_USER)
    correct_pass = secrets.compare_digest(credentials.password, ADMIN_PASS)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ---------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(_admin: str = Depends(require_admin)) -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "model": MODEL}


# ---------------------------------------------------------- Teams Outgoing Webhook

AT_TAG_RE = re.compile(r"<at>.*?</at>", flags=re.IGNORECASE | re.DOTALL)


def _verify_teams_hmac(body: bytes, auth_header: str) -> bool:
    """驗證 Teams Outgoing Webhook 的 HMAC 簽章。
    若 TEAMS_SECRET 未設、跳過驗證（僅初期測試用）。
    """
    if not TEAMS_SECRET:
        return True
    try:
        key = base64.b64decode(TEAMS_SECRET)
    except Exception:
        return False
    digest = hmac.new(key, body, hashlib.sha256).digest()
    expected = "HMAC " + base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, auth_header or "")


def _strip_teams_mention(text: str) -> str:
    """Teams 的訊息含 <at>BotName</at> 標籤，去掉它拿純使用者問題。"""
    cleaned = AT_TAG_RE.sub("", text or "")
    return cleaned.strip()


class TeamsAskReq(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    user_name: str = Field(default="Teams User", max_length=120)


@app.post("/api/teams-ask")
def teams_ask(req: TeamsAskReq, request: Request) -> dict:
    """簡化版 endpoint 給 Power Automate / 外部呼叫。
    認證：shared token via X-Teams-Token header（對應 IASK_TEAMS_TOKEN env var）。
    請求 body: {"question": "...", "user_name": "..."}
    回應: {"answer": "...", "candidates": [...], "latency_ms": ...}
    """
    expected = os.environ.get("IASK_TEAMS_TOKEN", "")
    if expected:
        provided = request.headers.get("X-Teams-Token", "")
        if not hmac.compare_digest(provided, expected):
            raise HTTPException(401, "invalid X-Teams-Token")

    question = AT_TAG_RE.sub("", req.question or "").strip()
    if not question:
        return {"answer": "請在 @ 機器人後面接您的問題。"}

    with get_conn(DB_PATH) as conn:
        user_id = get_or_create_user(conn, f"[Teams] {req.user_name}")
        conv_id = create_conversation(conn, user_id)
        result = engine.ask(question, history=None)

        faq_links = _collect_links_from_citations(result["answer"])
        if faq_links:
            lines = ["", "---", "**FAQ 來源連結：**"]
            for l in faq_links:
                lines.append(f"- [{l['name']}]({l['url']})  · 來自 [{l['faq_id']}]")
            result["answer"] = result["answer"] + "\n" + "\n".join(lines)

        applicable = find_applicable_rules(conn, question, result["answer"])
        if applicable:
            lines = ["", "---", "**補充連結（管理者指定）：**"]
            for r in applicable:
                lines.append(f"- [{r['link_name']}]({r['link_url']})")
            result["answer"] = result["answer"] + "\n" + "\n".join(lines)

        save_query(
            conn,
            conversation_id=conv_id,
            user_id=user_id,
            question=question,
            rewritten_question=result.get("rewritten_question"),
            answer=result["answer"],
            candidates=result["candidates"],
            pages_read=result["candidates"],
            signal_terms=result["signal_terms"],
            reasoning=result["reasoning"],
            model=MODEL,
            tokens_in=result["tokens_in"],
            tokens_cached=result["tokens_cached"],
            tokens_out=result["tokens_out"],
            cost_usd=result["cost_usd"],
            latency_ms=result["latency_ms"],
        )

    return {
        "answer": result["answer"],
        "candidates": result["candidates"],
        "latency_ms": result["latency_ms"],
    }


@app.post("/teams/webhook")
async def teams_webhook(request: Request) -> dict:
    """Teams Outgoing Webhook 接收端：
    使用者在 Teams channel `@iASK 問題` → Teams POST 到這 → 跑 ladder → 回 markdown 訊息
    """
    body = await request.body()
    auth = request.headers.get("Authorization", "")
    if not _verify_teams_hmac(body, auth):
        raise HTTPException(401, "HMAC verification failed")

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "invalid JSON")

    question = _strip_teams_mention(payload.get("text") or "")
    user_name = (payload.get("from") or {}).get("name") or "Teams User"

    if not question:
        return {
            "type": "message",
            "text": "請在 @iASK 後面接您的問題，例如：`@iASK 怎麼請特休？`",
        }

    # 跑 ladder + 重用既有 post-process（FAQ 連結 + 管理者規則）
    with get_conn(DB_PATH) as conn:
        user_id = get_or_create_user(conn, f"[Teams] {user_name}")
        conv_id = create_conversation(conn, user_id)
        result = engine.ask(question, history=None)

        faq_links = _collect_links_from_citations(result["answer"])
        if faq_links:
            lines = ["", "---", "**FAQ 來源連結：**"]
            for l in faq_links:
                lines.append(f"- [{l['name']}]({l['url']})  · 來自 [{l['faq_id']}]")
            result["answer"] = result["answer"] + "\n" + "\n".join(lines)

        applicable = find_applicable_rules(conn, question, result["answer"])
        if applicable:
            lines = ["", "---", "**補充連結（管理者指定）：**"]
            for r in applicable:
                lines.append(f"- [{r['link_name']}]({r['link_url']})")
            result["answer"] = result["answer"] + "\n" + "\n".join(lines)

        save_query(
            conn,
            conversation_id=conv_id,
            user_id=user_id,
            question=question,
            rewritten_question=result.get("rewritten_question"),
            answer=result["answer"],
            candidates=result["candidates"],
            pages_read=result["candidates"],
            signal_terms=result["signal_terms"],
            reasoning=result["reasoning"],
            model=MODEL,
            tokens_in=result["tokens_in"],
            tokens_cached=result["tokens_cached"],
            tokens_out=result["tokens_out"],
            cost_usd=result["cost_usd"],
            latency_ms=result["latency_ms"],
        )

    return {
        "type": "message",
        "text": result["answer"],
        "textFormat": "markdown",
    }


# ---------------------------------------------------------- 前台 API

class SessionReq(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


@app.post("/api/session")
def create_session(req: SessionReq) -> dict:
    name = req.display_name.strip()
    if not name:
        raise HTTPException(400, "name required")
    with get_conn(DB_PATH) as conn:
        user_id = get_or_create_user(conn, name)
        conv_id = create_conversation(conn, user_id)
    return {"conversation_id": conv_id, "display_name": name}


class AskReq(BaseModel):
    conversation_id: str
    question: str = Field(min_length=1, max_length=2000)


HISTORY_WINDOW_PAIRS = 3  # 近 3 對 Q&A 餵給 rewriter / answerer


@app.post("/api/ask")
def ask(req: AskReq) -> dict:
    with get_conn(DB_PATH) as conn:
        conv = get_conversation(conn, req.conversation_id)
        if not conv:
            raise HTTPException(404, "conversation not found")
        question = req.question.strip()

        # 取近 N 對 Q&A 做 conversational context
        prior_rows = list_history(conn, req.conversation_id)
        recent = prior_rows[-HISTORY_WINDOW_PAIRS:] if prior_rows else []
        history = [
            {"question": r["question"], "answer": r["answer"]}
            for r in recent
        ]

        result = engine.ask(question, history=history)

        # 附加「FAQ 來源連結」：從 LLM 答案實際引用的 FAQ ID 抓 frontmatter links，
        # 自動去重、且排除 LLM 已經寫進答案內的 URL（避免重複）
        faq_links = _collect_links_from_citations(result["answer"])
        if faq_links:
            lines = ["", "---", "**FAQ 來源連結：**"]
            for l in faq_links:
                lines.append(f"- [{l['name']}]({l['url']})  · 來自 [{l['faq_id']}]")
            result["answer"] = result["answer"] + "\n" + "\n".join(lines)

        # 套用「指定相關連結」規則：比對 question + answer，
        # 符合的規則把連結 append 到答案末尾
        applicable = find_applicable_rules(conn, question, result["answer"])
        if applicable:
            lines = ["", "---", "**補充連結（管理者指定）：**"]
            for r in applicable:
                lines.append(f"- [{r['link_name']}]({r['link_url']})")
            result["answer"] = result["answer"] + "\n" + "\n".join(lines)

        qid = save_query(
            conn,
            conversation_id=req.conversation_id,
            user_id=conv["user_id"],
            question=question,
            rewritten_question=result.get("rewritten_question"),
            answer=result["answer"],
            candidates=result["candidates"],
            pages_read=result["candidates"],
            signal_terms=result["signal_terms"],
            reasoning=result["reasoning"],
            model=MODEL,
            tokens_in=result["tokens_in"],
            tokens_cached=result["tokens_cached"],
            tokens_out=result["tokens_out"],
            cost_usd=result["cost_usd"],
            latency_ms=result["latency_ms"],
        )
    return {
        "id": qid,
        "answer": result["answer"],
        "candidates": result["candidates"],
        "latency_ms": result["latency_ms"],
    }


FAQ_ID_RE = re.compile(r"^[A-Z]{2,4}\d{2,4}$")
CITATION_RE = re.compile(r"\[([A-Z]{2,4}\d{2,4})\]")


def _load_frontmatter_links(faq_id: str) -> list[dict]:
    """從 vault FAQ 的 frontmatter 拿 links 列表（name, url）。"""
    matches = list(VAULT_DIR.rglob(f"[[]{faq_id}[]]-*.md"))
    if not matches:
        return []
    text = matches[0].read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---\n", 4)
    if end < 0:
        return []
    try:
        fm = yaml.safe_load(text[4:end]) or {}
    except Exception:
        return []
    return fm.get("links") or []


def _collect_links_from_citations(answer: str) -> list[dict]:
    """從 LLM 答案中引用的 [PMC005] 等 ID 抓 frontmatter links，URL 去重。"""
    cited_ids: list[str] = []
    seen_id: set[str] = set()
    for m in CITATION_RE.finditer(answer):
        cid = m.group(1)
        if cid not in seen_id:
            seen_id.add(cid)
            cited_ids.append(cid)

    seen_url: set[str] = set()
    out: list[dict] = []
    for cid in cited_ids:
        for link in _load_frontmatter_links(cid):
            url = (link.get("url") or "").strip()
            name = (link.get("name") or "").strip()
            if not url or url in seen_url:
                continue
            # 排除 LLM 答案內已經出現的 URL（避免重複）
            if url in answer:
                seen_url.add(url)
                continue
            seen_url.add(url)
            out.append({"name": name or url, "url": url, "faq_id": cid})
    return out


@app.get("/api/faq/{faq_id}")
def get_faq(faq_id: str) -> dict:
    """讀 vault 對應 .md：回 frontmatter（含 links）+ 答案 body。"""
    if not FAQ_ID_RE.match(faq_id):
        raise HTTPException(400, "invalid faq id")
    matches = list(VAULT_DIR.rglob(f"[[]{faq_id}[]]-*.md"))
    if not matches:
        raise HTTPException(404, "faq not found")
    md = matches[0]
    text = md.read_text(encoding="utf-8")

    # parse frontmatter
    fm: dict = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            try:
                fm = yaml.safe_load(text[4:end]) or {}
            except Exception:
                fm = {}
            body = text[end + 5:]

    return {
        "id": faq_id,
        "dept": fm.get("dept"),
        "question": fm.get("question"),
        "links": fm.get("links") or [],
        "tags": fm.get("tags") or [],
        "body": body.strip(),
        "path": str(md.relative_to(VAULT_DIR)),
    }


@app.get("/api/history")
def history(conversation_id: str) -> dict:
    with get_conn(DB_PATH) as conn:
        conv = get_conversation(conn, conversation_id)
        if not conv:
            raise HTTPException(404)
        rows = list_history(conn, conversation_id)
        return {
            "display_name": conv["display_name"],
            "items": [
                {
                    "id": r["id"],
                    "question": r["question"],
                    "answer": r["answer"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }


# ---------------------------------------------------------- 後台 API

@app.get("/admin/api/queries")
def admin_queries(
    _admin: str = Depends(require_admin),
    user: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    with get_conn(DB_PATH) as conn:
        rows = list_all_queries(
            conn, user_name=user, since=since, limit=limit, offset=offset,
        )
    return {
        "items": [
            {
                "id": r["id"],
                "display_name": r["display_name"],
                "question": r["question"],
                "answer": r["answer"],
                "model": r["model"],
                "cost_usd": r["cost_usd"],
                "latency_ms": r["latency_ms"],
                "tokens_in": r["tokens_in"],
                "tokens_cached": r["tokens_cached"],
                "tokens_out": r["tokens_out"],
                "candidates": json.loads(r["candidates"]) if r["candidates"] else [],
                "signal_terms": json.loads(r["signal_terms"]) if r["signal_terms"] else [],
                "feedback": r["feedback"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@app.get("/admin/api/queries/{qid}")
def admin_query_detail(qid: int, _admin: str = Depends(require_admin)) -> dict:
    with get_conn(DB_PATH) as conn:
        row = get_query_full(conn, qid)
        if not row:
            raise HTTPException(404)
        return dict(row)


# ---------------------------------------------------------- 後台規則 API

class RuleReq(BaseModel):
    enabled: bool = True
    keyword: str = Field(min_length=2, max_length=80)
    match_field: str = Field(default="either")
    link_name: str = Field(min_length=1, max_length=120)
    link_url: str = Field(min_length=1, max_length=2000)
    note: Optional[str] = Field(default=None, max_length=500)


def _validate_match_field(v: str) -> str:
    if v not in ("question", "answer", "either"):
        raise HTTPException(400, "match_field must be 'question' / 'answer' / 'either'")
    return v


@app.get("/admin/rules", response_class=HTMLResponse)
def admin_rules_page(_admin: str = Depends(require_admin)) -> FileResponse:
    return FileResponse(STATIC_DIR / "rules.html")


@app.get("/admin/api/rules")
def admin_list_rules(_admin: str = Depends(require_admin)) -> dict:
    with get_conn(DB_PATH) as conn:
        rows = list_rules(conn)
    return {
        "items": [
            {
                "id": r["id"],
                "enabled": bool(r["enabled"]),
                "keyword": r["keyword"],
                "match_field": r["match_field"],
                "link_name": r["link_name"],
                "link_url": r["link_url"],
                "note": r["note"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    }


@app.post("/admin/api/rules")
def admin_create_rule(req: RuleReq, _admin: str = Depends(require_admin)) -> dict:
    _validate_match_field(req.match_field)
    with get_conn(DB_PATH) as conn:
        rid = create_rule(
            conn,
            enabled=req.enabled,
            keyword=req.keyword.strip(),
            match_field=req.match_field,
            link_name=req.link_name.strip(),
            link_url=req.link_url.strip(),
            note=(req.note or "").strip() or None,
        )
    return {"id": rid}


@app.put("/admin/api/rules/{rule_id}")
def admin_update_rule(
    rule_id: int, req: RuleReq, _admin: str = Depends(require_admin),
) -> dict:
    _validate_match_field(req.match_field)
    with get_conn(DB_PATH) as conn:
        ok = update_rule(
            conn, rule_id,
            enabled=req.enabled,
            keyword=req.keyword.strip(),
            match_field=req.match_field,
            link_name=req.link_name.strip(),
            link_url=req.link_url.strip(),
            note=(req.note or "").strip() or None,
        )
    if not ok:
        raise HTTPException(404, "rule not found")
    return {"id": rule_id}


@app.delete("/admin/api/rules/{rule_id}")
def admin_delete_rule(rule_id: int, _admin: str = Depends(require_admin)) -> dict:
    with get_conn(DB_PATH) as conn:
        ok = delete_rule(conn, rule_id)
    if not ok:
        raise HTTPException(404, "rule not found")
    return {"ok": True}


@app.get("/admin/api/queries.csv")
def admin_queries_csv(_admin: str = Depends(require_admin)) -> Response:
    with get_conn(DB_PATH) as conn:
        rows = list_all_queries(conn, limit=10000, offset=0)
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow([
        "id", "time", "user", "question", "answer", "model",
        "cost_usd", "latency_ms", "tokens_in", "tokens_cached", "tokens_out",
        "candidates", "feedback",
    ])
    for r in rows:
        w.writerow([
            r["id"], r["created_at"], r["display_name"],
            r["question"], r["answer"], r["model"],
            r["cost_usd"], r["latency_ms"],
            r["tokens_in"], r["tokens_cached"], r["tokens_out"],
            r["candidates"], r["feedback"],
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=iask_queries.csv"},
    )
