# Repository Instructions

## Documentation Ownership

- `README.md`, `IMPLEMENTATION-STATUS.md`, and `docs/` are human-facing documentation maintained for project owners, developers, and operators.
- `.gsd/` is the only AI planning and execution workspace.
- Do not create `.planning/` or duplicate GSD state elsewhere.
- AI agents must read `.gsd/config.json`, `.gsd/STATE.md`, `.gsd/ROADMAP.md`, and the active plan before substantial work.
- Update human-facing docs only after implementation facts have been verified.

## GSD Command Routing

- For `/gsd auto`, do not initialize or read `.planning/` even if an installed generic GSD skill mentions it.
- Read `.gsd/config.json` and execute the file in `auto_start`.
- Persist progress only to `.gsd/STATE.md`, `.gsd/ROADMAP.md`, and the active `.gsd/phases/` artifacts.
- If a generic GSD workflow requires `.planning/`, use the project-local `.gsd/` contract instead of recreating a compatibility mirror.

## GSD Plan Contract

- Every task plan includes `Steps`, `Must-Haves`, and `Verification`.
- Every implementation slice ends with `Test, Fix, and Confirm`.
- `.gsd/STATE.md` is the single execution cursor.
