# Phase 20 — Local Services Smoke

Date: 2026-07-17  
Purpose: Bring local BE (+ FE if possible) up for analysis smoke.

## Port scan (Windows)

| Port | State before start | Owner |
|------|--------------------|--------|
| 8000 | Free | — |
| 3000 | Busy | PID **7736** `vmnat` (VMware NAT; not Next.js) |
| 3001 | Free | — |
| 3005 | Busy (pre-existing FE) | PID **56924** `node` — Next.js for `frontend/` |
| 8010 | Busy (pre-existing BE) | PID **33568** `python` — left running, not killed |

No unrelated processes were terminated.

## Backend (new)

- Command: `backend\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
- CWD: `D:\ADLINK\Myproject\novel-mind\backend`
- Env: `NO_PROXY=127.0.0.1,localhost`
- Result: **started OK**
- Bind: `http://127.0.0.1:8000`
- PID: **53720** (`python`)
- Background task_id: `019f6d8e-b42d-7cb3-8fab-c97a5cb4156e`
- Startup log: `NovelMind API 启动中...` → `服务就绪 ✓` → `Uvicorn running on http://127.0.0.1:8000`

### Health

```text
GET http://127.0.0.1:8000/api/health
HTTP 200
{"status":"ok","version":"0.1.0"}
```

(Verified with `curl.exe --noproxy "*" -s http://127.0.0.1:8000/api/health`)

## Frontend

### Attempted start (3001)

- Command: `npm run dev -- --port 3001` in `frontend/`
- Result: **failed (expected lock)** — Next.js reported another `next dev` already running:
  - Local: `http://localhost:3005`
  - PID: **56924**
  - Dir: `D:\ADLINK\Myproject\novel-mind\frontend`
- Did **not** kill PID 56924 (reuse existing FE for smoke).

### Existing FE used for smoke

| Item | Value |
|------|--------|
| URL | `http://127.0.0.1:3005/` |
| PID | **56924** (`node`) |
| Probe | HTTP **200** |

Note: Port 3000 is occupied by VMware `vmnat`, not the app; FE for this workspace is on **3005**.

## Summary for analysis smoke

| Role | URL | PID | Status |
|------|-----|-----|--------|
| Backend (smoke target) | http://127.0.0.1:8000 | 53720 | OK + `/api/health` 200 |
| Backend (pre-existing) | http://127.0.0.1:8010 | 33568 | Left running |
| Frontend | http://127.0.0.1:3005 | 56924 | OK (reused) |
| Port 3000 | — | 7736 vmnat | Not FE |

## Credentials

None written to this file. No secrets logged.

## Blockers

None for BE on 8000. FE new instance on 3001 blocked only by Next single-dev lock; existing FE on 3005 is usable.
