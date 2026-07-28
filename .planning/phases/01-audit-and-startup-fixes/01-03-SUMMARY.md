# Summary 01-03: 验证脚本与文档状态修正

## Completed

- Added `docs/GSD-SMOKE-TESTS.md`.
- Re-ran backend syntax/import checks with `backend/.venv`.
- Re-ran frontend typecheck and production build.
- Ran real backend API smoke on temporary port 8003 with Docker PostgreSQL healthy:
  - health check
  - upload `test_novel.txt`
  - list novels
  - fetch uploaded novel chapters
- Updated `docs/项目状态.md`, `docs/路线图.md`, and `docs/待办清单.md` so Phase 1 is no longer overstated as fully complete.

## Verification

- `backend/.venv/Scripts/python.exe -m compileall app`: passed.
- `backend/.venv/Scripts/python.exe -c "from app.main import app; print(app.title); print(len(app.routes))"`: passed, `NovelMind API`, `31`.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed.
- API smoke via `curl.exe --noproxy "*"`: passed.
  - uploaded_id: 4
  - chapter_count: 3
  - list_total: 2
  - fetched_chapters: 3

## Notes

- Global Python lacks `sqlalchemy`; use `backend/.venv/Scripts/python.exe`.
- PowerShell `Invoke-RestMethod` returned 502 for localhost due to local proxy behavior; `curl.exe --noproxy "*"` worked.

## Result

Phase 1 “审计与启动修复” is complete.
