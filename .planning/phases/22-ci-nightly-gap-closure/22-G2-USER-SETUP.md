# Phase 22-G2 Operator Setup

Status: **Incomplete — external Runner authority required**

The hosted Nightly control plane is implemented. A real quality run additionally requires
the following repository/operator setup; no model or provider call is made until all checks
pass.

## 1. Register the provider-capable Runner

1. In GitHub repository settings, register a Linux self-hosted Actions Runner.
2. Assign the custom labels `linux` and `ollama` (GitHub adds `self-hosted`).
3. Install Docker and ensure the repository user can run `docker compose`.
4. Start Ollama and provision the two models required by the locked benchmark fixture.
5. Keep the Runner online and idle before the scheduled window.

Verification:

```powershell
gh api repos/adlink8/novel-mind/actions/runners
```

At least one runner must report `status=online`, `busy=false`, with labels
`self-hosted`, `linux`, and `ollama`.

## 2. Add read-only Runner discovery authority

Create a fine-grained GitHub token scoped only to this repository with repository
`Administration: read` permission. Store it as the `quality-benchmark` environment secret
`NIGHTLY_RUNNER_READ_TOKEN`.

The token is used only by the hosted preflight to call the GitHub Runner inventory API. Do
not place the token in source files, logs, issue comments, or this document.

## 3. Optional stable report-signing secret

Add `RAG_SIGNING_SECRET` to the `quality-benchmark` environment. If absent, the workflow
uses the run-scoped GitHub token; this remains signed and promotion-compatible inside the
same run, but a dedicated secret gives stable cross-run verification authority.

## 4. Completion evidence

After the implementation PR is merged, inspect the next `schedule` event. The run must:

- finish `Nightly runner authority preflight` on `ubuntu-latest`;
- either run the provider benchmark or record `blocked_dependency` without queue starvation;
- upload `nightly-control-report` and `nightly-rag-report`;
- expose `metrics=null` when dependency-blocked;
- skip baseline promotion unless `promotable=true`.

Phase 22 remains open until three consecutive real scheduled runs are green.
