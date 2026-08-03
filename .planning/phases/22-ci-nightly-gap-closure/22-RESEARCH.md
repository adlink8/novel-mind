# Phase 22 Gap Closure Research

## Findings

1. Branch protection is correctly configured with only `ci-gate` required and
   `enforce_admins=true`.
2. The current Nightly job is bound to `[self-hosted, linux, ollama]`; GitHub job timeout
   starts only after runner acquisition, so a missing runner can queue until platform
   cancellation without producing the signed report.
3. `alert` only sees `failure` or `cancelled`. When an upstream required job fails,
   `nightly=skipped`, so the scheduled failure may create no alert.
4. When no artifact exists, the alert fingerprint is `nightly-fail:<run-id>`, defeating
   deduplication across the same root cause.
5. The latest unit failure is a timing-sensitive frontend expectation; the same suite
   passes locally (29 files, 248 tests), so it requires repeat verification rather than
   being labeled fixed by one pass.

## Authority Rules

- A provider outage or missing runner must never produce comparable quality metrics.
- `blocked_dependency` is an honest quality result, not a qualified result.
- CI execution health and model quality qualification are separate dimensions.
- Baseline promotion accepts only a signed, schema-valid, `passed|qualified` report.
- Alert identity derives from root-cause class/report signature, never run ID alone.

## Stub-SUT Nightly Finding and Deferred Lightweight Path (2026-08-03)

**Discovery:** the current nightly benchmark does **not** call any real model for scoring.
`run_quality_evaluation` falls back to deterministic oracle-aligned stubs
(`default_stub_retrieve/default_stub_answer/default_stub_answer_judge`,
`backend/app/services/rag_quality.py:771-773`) because `run_rag_quality.py` injects no
live `retrieve_fn/answer_fn/judge_fn`. Ollama appears only as a health gate
(`probe_ollama_health`, `http://127.0.0.1:11434/api/tags`): down → `blocked_dependency`
with `metrics=null`; up → stub-qualified report. Locally verified 2026-08-03:
- Ollama down → `status=blocked_dependency`, `comparable=False`, `metrics=null`.
- Default health (Ollama-up equivalent) → `status=qualified`, `comparable=True`,
  stub metrics (e.g. `answer_faithfulness_mean=1.0`), signed report emitted.
- `tests/live/test_rag_quality_dual_model.py` passes 2/2 both ways (blocked vs stub).

**Implication:** the full Phase 22-G2 operator deployment (self-hosted Linux runner with
`linux`+`ollama` labels, Docker `db`/`chroma`, two Ollama models, `NIGHTLY_RUNNER_READ_TOKEN`
secret) is premature for the current stub-based pipeline. Only the scheduled control-plane
run matters for Phase 22's exit rule ("three consecutive scheduled green runs").

**Deferred decision (user, 2026-08-03):** do NOT implement now; revisit when real model
scoring is actually needed (Phase 29 quality qualification, or when live adapters are wired
through the Pi gateway — direction A). When acted upon, the lightweight path is:
1. `ci.yml`: change the benchmark job/preflight required labels from
   `[self-hosted, linux, ollama]` to `[self-hosted, linux]`; drop `--live-health` from the
   nightly `run_rag_quality.py` invocation (use `default_healthy()`).
2. `run_rag_quality.py` (or report writer): add an explicit `sut: "deterministic-stub"`
   marker to the emitted report so stub-qualified output can never be misread as real model
   quality, and so the fixture's `model_id` lineage is not misattributed to the stub answer.
3. Register one plain Linux self-hosted runner; set `NIGHTLY_RUNNER_READ_TOKEN`
   (`Administration: read`); optional `RAG_SIGNING_SECRET`.
4. Update `22-VALIDATION.md` and the gate ledger with the 3 consecutive scheduled green runs.

Real dual-model quality evidence stays a Phase 29 concern and should route through the
provider-neutral Pi gateway (`/api/gateway/v1/chat/completions` → `AIService`) rather than a
hard-coded Ollama health probe, keeping the product agent runtime and the quality benchmark on
one model path.
