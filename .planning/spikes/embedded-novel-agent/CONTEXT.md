# Embedded Novel Agent Runtime — Spike Context

**Phase:** 25.2-01 Agent Runtime Spike
**Execution override:** 2026-08-02 user-authorized (Phase 22 3/3 gate skipped; see `.planning/STATE.md`)
**Pi SDK pin:** `@earendil-works/pi-coding-agent@0.83.0`, `@earendil-works/pi-agent-core@0.83.0`, `@earendil-works/pi-ai@0.83.0` (+ `typebox@1.3.7`)

## Spike Goal

Prove the Pi SDK can be embedded in a standalone Node `agent-service/` under NovelMind
constraints: no default coding tools (D-05), custom domain tools only (D-06), fully
controlled ResourceLoader (D-18), session create/resume/cancel/retry (D-19), streaming
events, and a settled answer on the custom `SessionStorage` injection seam (A1) with the
documented fallback (in-memory Pi session + NovelMind-owned run state, D-11/D-12).

## Constraints Under Test

| ID | Constraint | Experiment |
|---|---|---|
| D-05 | Pi default coding tools (bash/read/edit/write/grep/find/ls) absent from every session | 01 |
| D-06 | Custom NovelMind domain tool registers and round-trips typed params | 02 |
| D-18 | ResourceLoader loads only allowlisted resources; zero ambient discovery | 03 |
| D-19 | create/resume/cancel/retry with stopReason surfaced | 04 |
| — | Pi events capturable for SSE serialization | 05 |
| — | Skill instructions injectable without the disabled read tool (Pitfall 3) | 07 |
| A1 | Custom SessionStorage injection seam (or fallback proof) | 06 |
| A2 | Ambient discovery surfaces fully closed | 03 |
| D-22 | No write path exercised | all |

## Capability Matrix

| # | Capability | Script | Verdict |
|---|---|---|---|
| 0 | npm re-verify (0.83.0, no postinstall) | (npm view) | see EXPERIMENTS.md |
| 1 | No default coding tools | `spikes/01-no-tools-enumeration.mjs` | see EXPERIMENTS.md |
| 2 | Custom tool roundtrip + typed params | `spikes/02-custom-tool-roundtrip.mjs` | see EXPERIMENTS.md |
| 3 | ResourceLoader allowlist closure (A2) | `spikes/03-resource-loader-allowlist.mjs` | see EXPERIMENTS.md |
| 4 | Session lifecycle (abort/continue/stopReason) | `spikes/04-session-lifecycle.mjs` | see EXPERIMENTS.md |
| 5 | Streaming events (SSE subset) | `spikes/05-streaming-events.mjs` | see EXPERIMENTS.md |
| 6 | Storage seam (A1) + fallback proof | `spikes/06-storage-injection-seam.mjs` | see EXPERIMENTS.md |
| 7 | Skill instruction injection (Pitfall 3) | `spikes/07-skill-instruction-injection.mjs` | see EXPERIMENTS.md |

## Timebox

Each experiment ≤ 30 min wall-clock; live-model assertions SKIP with reason when no dev
provider key is available. Outputs are experiments and findings — not production code. The
go/no-go and storage decision is owned by 25.2-06, not this spike.

## Package Verification Notes

- npm view re-verification recorded in EXPERIMENTS.md before install (Task 1 gate).
- `@earendil-works/pi-storage-sqlite-node@0.83.0` is [SUS] (slopcheck) — install gated by
  Task 3 human checkpoint; spike-only per D-17.
