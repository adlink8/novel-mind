# Owner Agent settings TDD log

## Slice 1 — GET defaults

- RED: `backend/venv/Scripts/python.exe -m pytest tests/test_agent_settings.py -q`
  failed with `404 == 200` because `/api/settings/agent` did not exist.
- GREEN: added the independent `AgentSettings` model/schema/service and GET route;
  the same command passed (`1 passed`).

## Slice 2 — PUT persistence

- RED: `backend/venv/Scripts/python.exe -m pytest tests/test_agent_settings_put.py -q`
  failed with `405 == 200` because the PUT route did not exist.
- GREEN: added the typed PUT upsert and owner binding persistence; the combined
  settings tests passed (`5 passed`).

## Slice 3 — fail-closed model bindings

- RED: `backend/venv/Scripts/python.exe -m pytest tests/test_agent_settings_binding_fail_closed.py -q`
  rejected both cases as `400`, not the stable `422` validation response.
- GREEN: translated binding validation errors at the public API boundary to 422;
  the same command passed (`2 passed`).

## Slice 4 — strict typed inputs

- RED: `backend/venv/Scripts/python.exe -m pytest tests/test_agent_settings_types.py -q`
  accepted the string `"true"` as a boolean and returned 200.
- GREEN: changed boolean and model-id schema fields to Pydantic strict types;
  the same command passed (`1 passed`).
