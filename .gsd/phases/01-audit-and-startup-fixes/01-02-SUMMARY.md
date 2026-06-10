# Summary 01-02: 前后端契约启动修复

## Completed

- Updated frontend novel API types to match backend paginated list response.
- Updated novel store to read `res.data.items`.
- Handled nullable `author` fields in search and cards.
- Updated AI model frontend contract to submit `name`, `model_id`, `provider`, optional `api_key`, and optional `base_url`.
- Updated AI model test handling to use backend `success`, `error`, and `latency_ms`.
- Changed write/generate placeholder backend routes to return HTTP 501 instead of fake success payloads.

## Verification

- `python -m compileall app` passed in `backend`.
- `npx tsc --noEmit` passed in `frontend`.

## Next

Continue with `01-03`: smoke commands and documentation status correction.
