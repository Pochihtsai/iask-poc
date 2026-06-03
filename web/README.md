# iASK 2.0 PoC Web

FastAPI + SQLite + 純 HTML/JS 前後台。前台單輪問答（每題獨走 ladder），後台 Basic Auth 看所有紀錄。

## 啟動

```bash
# 1. 安裝依賴（首次）
pip3 install -r requirements.txt

# 2. 載入 OpenRouter key
. ~/.iask_openrouter_key

# 3. 設定後台密碼
export IASK_ADMIN_USER=admin
export IASK_ADMIN_PASS=changeme   # 改成你自己的

# 4. 啟動（在 poc/ 目錄下執行，因為 import 用 web.app）
cd poc
python3 -m uvicorn web.app:app --host 127.0.0.1 --port 8000

# 5. 開瀏覽器
# 前台： http://localhost:8000/
# 後台： http://localhost:8000/admin
```

啟動成功的訊號（看到這四行才算 OK）：

```
INFO:     Started server process [...]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

## 對外暴露（Cloudflare Tunnel）

預設 `--host 127.0.0.1` 只接受本機連線；要對外時請另外做 tunnel，不要直接改成 `0.0.0.0` 暴露給 LAN。

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:8000
# 它會印出一個 https://xxx.trycloudflare.com 的對外 URL
```

> ⚠️ 對外後請務必設一個強密碼到 `IASK_ADMIN_PASS`，OpenRouter API key 也要小心，避免被惡意流量燒額度。

## 常見啟動失敗

以下兩種已實測，看到完全相同的訊息直接照修法做：

| terminal 訊息（節錄） | 原因 | 修法 |
|---|---|---|
| `ModuleNotFoundError: No module named 'web'`（含 traceback） | 沒在 `poc/` 目錄下跑 | 啟動指令一定要先 `cd poc` |
| 一行 `OPENROUTER_API_KEY 未設定 — 請執行: . ~/.iask_openrouter_key` 然後秒退 | 沒 source key 檔（或在另一個 shell source、新 shell 沒帶到） | 同一個 terminal 重 source `. ~/.iask_openrouter_key` 再啟動 |

若 terminal 訊息不是這兩種，請把末段約 20 行（含任何 traceback）貼給維護者診斷，不要猜。

## 環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `IASK_MODEL` | `google/gemini-2.5-flash-lite` | OpenRouter 上的模型 ID |
| `IASK_ADMIN_USER` | `admin` | 後台 Basic Auth 帳號 |
| `IASK_ADMIN_PASS` | — | 後台 Basic Auth 密碼（**必設**，否則後台拒絕登入） |
| `OPENROUTER_API_KEY` | — | 透過 `. ~/.iask_openrouter_key` 載入 |

## 架構

```
web/
├── app.py               FastAPI 路由 + Basic Auth
├── db.py                SQLite schema + helpers
├── ladder_adapter.py    包既有 scripts/ladder_retriever.py
├── iask.db              runtime 產生（不入 git）
└── static/
    ├── index.html       前台
    ├── admin.html       後台
    ├── style.css        共用樣式
    ├── app.js           前台邏輯
    └── admin.js         後台邏輯
```

既有 `scripts/{chatbot,ladder_retriever,llm_client}.py` 不動，由 `ladder_adapter.py` import 使用。

## API

| Method | Path | 用途 | Auth |
|---|---|---|---|
| GET | `/` | 前台 | 無 |
| GET | `/admin` | 後台 | Basic |
| POST | `/api/session` | 名稱換 conversation_id | 無 |
| POST | `/api/ask` | 問問題 | 無 |
| GET | `/api/history?conversation_id=...` | 本次對話歷史 | 無 |
| GET | `/admin/api/queries` | 列表（含 user/since filter） | Basic |
| GET | `/admin/api/queries/{id}` | 單筆 ladder debug | Basic |
| GET | `/admin/api/queries.csv` | 匯出 CSV | Basic |
| GET | `/healthz` | 健康檢查 | 無 |

## SQLite schema

3 張表：`users`、`conversations`、`queries`。`queries` 含 ladder debug 欄位（candidates / signal_terms / reasoning）與 metric（tokens / cost / latency）。

## 限制（MVP 不做）

- 單輪：每題獨走 ladder，無多輪指代消解
- 無 streaming：等 ~5-15s 拿完整答案
- 無 user auth：名字 = ID，可造假；後台數據不能當審計
- 無 HTTPS：對外請走 Cloudflare Tunnel 或 reverse proxy
