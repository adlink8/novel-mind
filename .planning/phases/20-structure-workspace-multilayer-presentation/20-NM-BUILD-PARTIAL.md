# Step 2 — NM build status (PARTIAL)

**Date:** 2026-07-17  
**Novel:** 91 (owner 2)  
**version_id:** 1  
**run_id:** 1  
**status:** `partial` (`chapter_or_parent_failed`)  
**Promotion:** not attempted

## Progress snapshot

| Metric | Value |
|--------|-------|
| chapter_state completed (stages) | ~12 |
| chapter_state pending | ~500+ |
| failed stages | intermittent / healed partially |
| nodes persisted | ~12 `chapter_state` |
| arc / global | **not yet** (blocked until all chapter_state required by planner complete) |
| transport | Vertex/LiteLLM wired in CLI (uncommitted or in-progress agent files) |

## Why not full book in one session

515 chapters × multi-second LLM each ≈ multi-hour run. Operator loop used resume batches; run settles to `partial` when some chapters fail validation/package rebind.

## Resume (no promote)

```powershell
cd D:\ADLINK\Myproject\novel-mind\backend
$env:PYTHONPATH = "."
# After transport commit lands:
.\.venv\Scripts\python.exe scripts\run_narrative_memory_build.py resume --run-id 1
# or status / start with --version-id 1
```

## Product impact

Structure Workspace can load `GET /api/narrative-memory/91/versions` (1 version) and tree may show partial L2 chapter_state nodes for early chapters only until build finishes.
