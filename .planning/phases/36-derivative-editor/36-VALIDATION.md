# Phase 36 Validation — Nyquist Gate

## Fixture matrix

Two owners × two projects × two forks; chapters with plan/draft/published candidate; stale base revision; duplicate autosave request; crash after write-before-response; rollback target; malformed/oversized Markdown。

## Automated tests (planned)

```text
cd backend; pytest tests/unit/derivative_editor tests/integration/test_derivative_editor.py -q
cd backend; pytest tests/adversarial/test_derivative_owner_isolation.py -q
cd frontend; npm test -- writing derivative
cd frontend; npm run test:e2e:desktop -- derivative-editor.spec.ts
```

Map: `REQ-CRE-03` → CRUD/owner/integration; `REQ-FORK-02` → fork selection/autosave/recovery; `REQ-CRE-04` → diff/rollback/lineage. New tests are Wave 0 gaps; commands are planning contracts and were not run.

## Manual UAT

Create project with explicit fork, add chapter plan, type Markdown, wait for autosave, reload, open history/diff, provoke stale tab conflict, recover draft, rollback, and verify another owner receives 404. Check keyboard, 390px touch viewport, unsaved/conflict/error states. No implementation test run in this research turn.

## Failure policy

Stale writes must return conflict without data loss; rollback must create a new revision; any owner leak or original-space mutation blocks the phase.
