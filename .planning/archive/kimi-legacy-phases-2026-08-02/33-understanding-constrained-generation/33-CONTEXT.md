# Phase 33 Context: Understanding-Constrained Generation

## Scope

Design the authorized generation contract that would combine user creative settings, a declared original-canon cutoff, auditable original evidence, and available understanding-state inputs.

## Current boundary

This phase is not executable in the current authorization state. Model/provider calls, paid budget, external transport, real novel-91/NM data operations, and any Narrative Memory promotion or active-pointer mutation remain out of scope. The existing `/api/fanfiction/{id}/continue` `501` response is the correct fail-closed behavior.

## Required unblockers

- Explicit authorization for model/provider invocation and budgeted transport.
- Correct source data and verified NM candidate inputs for the target novel.
- A price authority for non-zero cost accounting.
- Explicit confirmation that the work remains candidate-only and does not alter active pointers or Reader Chat cutover.

## Safe preparation

Only local, deterministic schemas, context-package fixtures, policy checks, and evaluator design may proceed before those unblockers are supplied.
