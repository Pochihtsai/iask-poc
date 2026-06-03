"""SQLite schema + helpers for iASK 2.0 PoC web."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name  TEXT NOT NULL UNIQUE,
  first_seen    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
  id            TEXT PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id),
  started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS queries (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id     TEXT NOT NULL REFERENCES conversations(id),
  user_id             INTEGER NOT NULL REFERENCES users(id),
  question            TEXT NOT NULL,
  rewritten_question  TEXT,
  answer              TEXT NOT NULL,
  candidates          TEXT,
  pages_read          TEXT,
  signal_terms        TEXT,
  reasoning           TEXT,
  model               TEXT,
  tokens_in           INTEGER,
  tokens_cached       INTEGER,
  tokens_out          INTEGER,
  cost_usd            REAL,
  latency_ms          INTEGER,
  feedback            TEXT,
  created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_queries_user    ON queries(user_id);
CREATE INDEX IF NOT EXISTS idx_queries_created ON queries(created_at);
CREATE INDEX IF NOT EXISTS idx_queries_conv    ON queries(conversation_id);

CREATE TABLE IF NOT EXISTS link_rules (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  keyword     TEXT NOT NULL,
  match_field TEXT NOT NULL DEFAULT 'either',   -- 'question' / 'answer' / 'either'
  link_name   TEXT NOT NULL,
  link_url    TEXT NOT NULL,
  note        TEXT,
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    # Migration: 為早期建立、缺欄位的舊 DB 補上 rewritten_question
    cur = conn.execute("PRAGMA table_info(queries)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "rewritten_question" not in existing_cols:
        conn.execute("ALTER TABLE queries ADD COLUMN rewritten_question TEXT")
    conn.commit()
    conn.close()


def get_or_create_user(conn: sqlite3.Connection, display_name: str) -> int:
    cur = conn.execute("SELECT id FROM users WHERE display_name = ?", (display_name,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO users (display_name) VALUES (?)", (display_name,))
    conn.commit()
    return cur.lastrowid


def create_conversation(conn: sqlite3.Connection, user_id: int) -> str:
    conv_id = str(uuid.uuid4())
    conn.execute("INSERT INTO conversations (id, user_id) VALUES (?, ?)", (conv_id, user_id))
    conn.commit()
    return conv_id


def get_conversation(conn: sqlite3.Connection, conv_id: str) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT c.id, c.user_id, c.started_at, u.display_name "
        "FROM conversations c JOIN users u ON u.id = c.user_id WHERE c.id = ?",
        (conv_id,),
    )
    return cur.fetchone()


def save_query(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    user_id: int,
    question: str,
    answer: str,
    candidates: list[str],
    pages_read: list[str],
    signal_terms: list[str],
    reasoning: str,
    model: str,
    tokens_in: int,
    tokens_cached: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: int,
    rewritten_question: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO queries
          (conversation_id, user_id, question, rewritten_question, answer,
           candidates, pages_read, signal_terms, reasoning, model,
           tokens_in, tokens_cached, tokens_out, cost_usd, latency_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conversation_id, user_id, question, rewritten_question, answer,
            json.dumps(candidates, ensure_ascii=False),
            json.dumps(pages_read, ensure_ascii=False),
            json.dumps(signal_terms, ensure_ascii=False),
            reasoning, model, tokens_in, tokens_cached, tokens_out,
            cost_usd, latency_ms,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_history(conn: sqlite3.Connection, conversation_id: str) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT id, question, answer, created_at FROM queries "
        "WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    )
    return list(cur.fetchall())


def list_all_queries(
    conn: sqlite3.Connection,
    *,
    user_name: str | None = None,
    since: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[sqlite3.Row]:
    sql = """
      SELECT q.id, q.conversation_id, u.display_name, q.question, q.answer,
             q.model, q.cost_usd, q.latency_ms, q.tokens_in, q.tokens_cached,
             q.tokens_out, q.feedback, q.created_at,
             q.candidates, q.signal_terms
      FROM queries q JOIN users u ON u.id = q.user_id
      WHERE 1=1
    """
    params: list[Any] = []
    if user_name:
        sql += " AND u.display_name = ?"
        params.append(user_name)
    if since:
        sql += " AND q.created_at >= ?"
        params.append(since)
    sql += " ORDER BY q.id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    return list(conn.execute(sql, params).fetchall())


# ---------- link_rules CRUD ----------

def list_rules(conn: sqlite3.Connection, enabled_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM link_rules"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY id ASC"
    return list(conn.execute(sql).fetchall())


def create_rule(
    conn: sqlite3.Connection,
    *,
    enabled: bool,
    keyword: str,
    match_field: str,
    link_name: str,
    link_url: str,
    note: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO link_rules
          (enabled, keyword, match_field, link_name, link_url, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (1 if enabled else 0, keyword, match_field, link_name, link_url, note),
    )
    conn.commit()
    return cur.lastrowid


def update_rule(
    conn: sqlite3.Connection,
    rule_id: int,
    *,
    enabled: bool,
    keyword: str,
    match_field: str,
    link_name: str,
    link_url: str,
    note: str | None = None,
) -> bool:
    cur = conn.execute(
        """
        UPDATE link_rules
        SET enabled = ?, keyword = ?, match_field = ?,
            link_name = ?, link_url = ?, note = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (1 if enabled else 0, keyword, match_field, link_name, link_url, note, rule_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> bool:
    cur = conn.execute("DELETE FROM link_rules WHERE id = ?", (rule_id,))
    conn.commit()
    return cur.rowcount > 0


def find_applicable_rules(
    conn: sqlite3.Connection, question: str, answer: str,
) -> list[dict]:
    """回傳所有 enabled 且 keyword 比對成功的規則（保留 list 順序 = id ASC）。"""
    rules = list_rules(conn, enabled_only=True)
    q = (question or "").lower()
    a = (answer or "").lower()
    out: list[dict] = []
    for r in rules:
        kw = (r["keyword"] or "").strip().lower()
        if not kw:
            continue
        field = r["match_field"] or "either"
        if field == "question":
            hit = kw in q
        elif field == "answer":
            hit = kw in a
        else:
            hit = (kw in q) or (kw in a)
        if hit:
            out.append({k: r[k] for k in r.keys()})
    return out


def get_query_full(conn: sqlite3.Connection, qid: int) -> sqlite3.Row | None:
    cur = conn.execute(
        "SELECT q.id, q.conversation_id, q.user_id, q.question, q.rewritten_question, "
        "q.answer, q.candidates, q.pages_read, q.signal_terms, q.reasoning, "
        "q.model, q.tokens_in, q.tokens_cached, q.tokens_out, "
        "q.cost_usd, q.latency_ms, q.feedback, q.created_at, "
        "u.display_name "
        "FROM queries q JOIN users u ON u.id = q.user_id WHERE q.id = ?",
        (qid,),
    )
    return cur.fetchone()
