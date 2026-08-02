# Phase 30 Context: NM Promotion, A/B, and Cutover Decision

## Scope

Design and, only if explicitly authorized, execute the promotion journal, A/B qualification, and consumer cutover decision for Narrative Memory.

## Hard authorization boundary

User authorization for provider/paid work, Narrative Memory promotion, active-pointer changes, and
Reader Chat cutover is recorded. Authorization does not waive the signed-evaluation, cost,
rollback, owner, spoiler, citation, or CAS gates; no mutation is executed until those gates pass.

## Safe outcome

The valid current outcome is a blocked/candidate-only verdict with no mutation. The 30-01 contract
is verified and 30-03 remains a valid archive; 30-02 still requires upstream qualification and
cost authority. Any future switch must prove CAS, manifests, rollback, spoiler, owner, and citation
gates before considering a switch.
