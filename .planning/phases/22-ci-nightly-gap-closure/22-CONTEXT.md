# Phase 22 Gap Closure Context

## Goal

Close the difference between “CI implementation exists” and “scheduled Nightly is
operationally trustworthy”. Phase 22 is complete only after repository-controlled failures
are fixed and three consecutive scheduled observations are green.

## Current Evidence (2026-07-31)

| Run | Event | Result | Root classification |
|---|---|---|---|
| [30607067442](https://github.com/adlink8/novel-mind/actions/runs/30607067442) | schedule | failure | product/test: frontend relationship test timed out; Nightly skipped |
| [30515165945](https://github.com/adlink8/novel-mind/actions/runs/30515165945) | schedule | cancelled | environment: `self-hosted, linux, ollama` runner never acquired |
| [30424693088](https://github.com/adlink8/novel-mind/actions/runs/30424693088) | schedule | cancelled | environment: Nightly queued about 24h, then cancelled |
| [30330904855](https://github.com/adlink8/novel-mind/actions/runs/30330904855) | schedule | cancelled | environment: Nightly runner unavailable |

Open automated alerts: #24, #25, #27, #28. Their fallback fingerprint embeds the run ID,
so identical runner-unavailable failures are not deduplicated.

## Status Dimensions

- implementation_readiness: `partial` — required check and producer DAG exist; gap plans active.
- sample_data_coverage: `not_applicable` — Phase 22 validates CI control-plane behavior.
- quality_qualification: `blocked` — no completed Nightly benchmark observation.

## Exit Rule

Phase 22 remains `ACTIVE/BLOCKED-OBSERVATION` until 22-G1 and 22-G2 are verified and 22-G3
records three consecutive scheduled green runs. Manual reruns do not silently substitute
for scheduled observations.
