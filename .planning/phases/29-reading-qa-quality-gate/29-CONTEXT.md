# Phase 29 Context — Reading QA Quality Gate

## Decisions

- D-01: Freeze one-book gold set covering local, cross-chapter, global, causal, character-knowledge, world-rule, no-answer, and spoiler questions.
- D-02: Reports bind DB fingerprint, dataset version, source snapshot, commit, model/prompt/schema, and budget.
- D-03: Measure retrieval, citation correctness, faithfulness, relevance, latency, cost, abstention, fallback, and reuse separately by bucket.
- D-04: Candidate and leaf baseline use identical source, cutoff, and budget; violations block qualification.
- D-05: Verdict is only qualified_candidate or blocked; never promotion.
- D-06: Browser UAT covers Reader/Analysis Chat, citations, evidence panel, partial/failure states, desktop/mobile/accessibility, and spoiler metadata.

## Agent Consumer Contract

- Skill / mode: evaluate-reading-skill-runs.
- Inputs: frozen SkillRun/ToolRun/Artifact/Manifest/model/source/dataset.
- Official output: SkillEvaluationArtifact, with SkillRun/ToolRun, runtime/model, source/input hash,
  evidence and owner/novel/branch lineage where applicable.
- Approval: deterministic qualification only.
- Deterministic authority: immutable evaluation runner and milestone audit.
- Forbidden: mutable Agent replay as qualification evidence; shell/filesystem/default coding tools, ambient packages and direct
  database access are always forbidden.
- Canonical contract: `.planning/AGENT-RUNTIME-CONTRACT.md`; consume Phase 25.2
  ResourceLoader/Skill/Artifact and Phase 25.3 registry/policy contracts.

## the agent's Discretion

- Gold rubric and curator agreement.
- Thresholds frozen before evaluation.
- Report schema and worst-case sampling.

## Deferred Ideas (OUT OF SCOPE)

- Production A/B, promotion, active pointer.
- Replacing human UAT with one aggregate score.
- Cross-novel benchmark.

## Canonical refs

backend/app/services/narrative_memory/qualification_{fixtures,metrics,runner,verifier,
verdict}.py; backend/app/services/eval_service.py; backend/evals/;
frontend/e2e/reader-chat*.spec.ts; frontend/src/components/analysis/analysis-chat-panel.test.tsx.
[VERIFIED: repository grep]

Phase 22 0/3 remains independent and blocked. [VERIFIED: repository grep]
