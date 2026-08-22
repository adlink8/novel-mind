# Phase 46: Provider Protocol Unification and Live Qualification - Research

**Researched:** 2026-08-12
**Scope:** Official provider protocols plus live repository gaps
**Confidence:** High for OpenAI/Anthropic/Gemini/Ollama and custom OpenAI-compatible discovery

## Existing Foundation (do not re-plan)

The current dirty worktree already provides:

- `GET /api/models/providers` and `POST /api/models/discover` behind authentication.
- Five provider profiles and native model-list request/response normalization.
- SSRF validation before discovery and again before owner-bound gateway invocation.
- Pi run token + novel headers, owner resolution and owner default `AIModelConfig` lookup.
- Provider-to-LiteLLM mapping through one generic invocation seam.
- Settings provider selection, backend-derived profiles, model discovery, manual fallback and
  first-model default behavior; the intelligent-routing section is removed.
- Targeted evidence already observed: backend 41 tests, frontend 119 tests, Agent 21 tests,
  type checks/lint, local health and browser settings/Ollama-failure behavior.

These facts establish a foundation only. They do not prove pagination, every legacy consumer,
or live cloud credentials.

## Official Protocol Matrix

| Provider | Catalog interface | Authentication | Generation interface owned by adapter | Important normalization |
|---|---|---|---|---|
| OpenAI | `GET {base}/models` | `Authorization: Bearer` | OpenAI chat-completions-compatible transport | `data[].id`; retain only declared text/chat-capable models when capability evidence exists |
| Anthropic | `GET {base}/models` | `x-api-key` plus `anthropic-version` | native Messages API via adapter | `data[].id`, `display_name`; pagination uses `has_more`/cursor fields |
| Gemini AI Studio | `GET {base}/models` | `x-goog-api-key` | `models/{model}:generateContent` | strip `models/`; require `generateContent` in `supportedGenerationMethods`; follow `nextPageToken` |
| Ollama | `GET {base}/api/tags` | normally none; optional proxy Bearer | `/api/chat` through adapter | `models[].model|name`; local/private host remains explicitly allowlisted only |
| Custom | `GET {base}/models` | optional Bearer | OpenAI-compatible chat completions | accept only the declared OpenAI-compatible `data[]` shape; no response-shape guessing |

Official references:

- OpenAI Models API: https://platform.openai.com/docs/api-reference/models/list
- Anthropic Models API: https://docs.anthropic.com/en/api/models-list
- Anthropic API authentication/version headers: https://docs.anthropic.com/en/api/getting-started
- Gemini Models API: https://ai.google.dev/api/models
- Ollama list local models: https://docs.ollama.com/api/tags
- Ollama chat: https://docs.ollama.com/api/chat

## Repository Gaps Found

1. `provider_catalog.py` reads one page and returns only ID/name. It does not carry a pagination
   cursor, capability truth, protocol version, endpoint parent or qualification status.
2. Canonical provider validation is used by discovery but is not yet the common create/update/
   invocation authority; aliases and unsupported provider/config combinations can drift.
3. `backend/app/services/ai_router.py` still owns static model IDs, costs and global quality/
   balanced/economy selection. Call sites include reader chat, knowledge/relationship/clue
   judging and derivative generation. Hiding its UI does not remove its runtime authority.
4. Some services choose `settings.chat_provider`, environment defaults or static model IDs.
   Owner isolation and one-default semantics therefore are not yet uniform outside Pi gateway.
5. Existing tests prove shaped requests with mocks. There is no redacted, credential-gated live
   matrix proving catalog + direct test + Pi stream for each of the five choices.
6. Existing usage records are not yet guaranteed to use one provider/model/capability/price
   authority. Static price snapshots may be stale or absent and must not become invented cost.
7. Phase 42–45 execution artifacts exist while `STATE.md` previously pointed to Phase 41. Phase
   46 records that drift without rewriting closed summaries or changing the Phase 41 decision.

## Recommended Architecture

Use two small deep modules rather than expanding API conditionals:

1. `ProviderRegistry`: immutable profiles and protocol adapters. It validates config, produces
   catalog pages, declares invocation transport/capabilities and normalizes provider outcomes.
2. `ModelDeploymentResolver`: loads an active owner-scoped model, validates it through the
   registry, decrypts credentials only at the call boundary and returns a typed deployment.

All consumers depend on the resolver, not ORM queries, process-wide settings or static tiers.
The resolver is not a new routing recommender: default or explicit owner model selection is
deterministic.

## TDD Seams

- Protocol fixtures: one request and one or more response pages per provider, malformed bodies,
  cursor loops, over-limit payloads, missing auth and unsafe redirects/URLs.
- Resolver fixtures: two owners, duplicate/missing defaults, inactive model, forged run token,
  unsafe stored URL and explicit foreign model ID.
- Consumer characterization: freeze current domain input/output before replacing router lookup;
  assert deployment provenance and unchanged domain gates.
- Live harness: process-only credential injection, secret scanner, redacted JSON result rows,
  one bounded request per catalog/test/Pi step and zero automatic retries that multiply cost.
- Closeout: browser verifies settings states; evidence checker recomputes VERIFIED/PARTIAL/
  BLOCKED from artifacts and fails when a required row/hash is absent.

## Planning Conclusion

Four plans are warranted. Protocol completion precedes resolver migration; resolver migration
precedes live qualification; live evidence precedes usage/UI closeout.
