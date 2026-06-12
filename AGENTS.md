# Repository Instructions

## Documentation Ownership

- `README.md`, `IMPLEMENTATION-STATUS.md`, and `docs/` are human-facing documentation maintained for project owners, developers, and operators.
- `.planning/` is the only AI planning and execution workspace, following GSD workflow conventions.
- AI agents must read `.planning/config.json`, `.planning/STATE.md`, `.planning/ROADMAP.md`, and the active plan before substantial work.
- Update human-facing docs only after implementation facts have been verified.

## GSD Command Routing

- Read `.planning/config.json` and execute the file in `auto_start`.
- Persist progress only to `.planning/STATE.md`, `.planning/ROADMAP.md`, and the active `.planning/phases/` artifacts.
- All GSD skills operate directly on the `.planning/` directory.

## GSD Plan Contract

- Every task plan includes `Steps`, `Must-Haves`, and `Verification`.
- Every implementation slice ends with `Test, Fix, and Confirm`.
- `.planning/STATE.md` is the single execution cursor.

## Document Architecture

- System structure: `docs/architecture/` (11 docs + 5 Mermaid diagrams)
- Module boundaries: `**/README.md` in backend/app/* and frontend/src/*
- Project state: `IMPLEMENTATION-STATUS.md` (authoritative)
- Product docs: `docs/` (requirements, roadmap, competitive analysis)
