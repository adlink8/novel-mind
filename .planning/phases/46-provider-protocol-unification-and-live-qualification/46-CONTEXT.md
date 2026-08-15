# Phase 46: Provider Protocol Unification and Live Qualification - Context

**Gathered:** 2026-08-12
**Status:** Ready for planning
**Source:** User-confirmed TDD seams and live repository inspection

<domain>
## Phase Boundary

Complete and qualify the text-generation providers exposed by the settings page: OpenAI,
Anthropic, Google AI Studio Gemini, Ollama and custom
OpenAI-compatible. The phase owns model catalog discovery, saved configuration validation,
connection tests, text chat/stream invocation through Pi/Agent and non-Agent backend
consumers, and provider usage/capability truth.

Image-generation providers, embedding providers, adding new vendors beyond these five,
account synchronization and purchasing provider credentials are outside this phase. The
registry may declare those capabilities unsupported; it must not imply support.
</domain>

<decisions>
## Implementation Decisions

### Provider and protocol authority

- **D-46-01:** The backend provider registry is the single authority for canonical IDs,
  aliases, labels, credential kind, default Base URL, catalog protocol, generation transport,
  pagination and declared capabilities. Frontend labels/defaults are projections, not a
  second hardcoded catalog.
- **D-46-02:** The supported set is exactly `openai`, `anthropic`, `gemini`,
  `ollama` and `custom`. `custom` means an OpenAI-compatible HTTP contract;
  arbitrary unrelated response shapes are not silently guessed.
- **D-46-03:** Model discovery follows the provider's native directory response and pagination,
  normalizes a stable model ID/display name/capability set, caps pages/items/bytes/time, detects
  token loops and revalidates every outbound URL against the SSRF policy.
- **D-46-04:** Create, update, test, discovery and invocation use the same canonical provider
  validation. Provider/Base URL/credential combinations that cannot satisfy the declared
  protocol are rejected before persistence or network use.

### Runtime deployment authority

- **D-46-05:** A run token and novel identify the owner for Pi/Agent. The owner’s active default
  `AIModelConfig` resolves the logical gateway deployment; no global or environment fallback
  is permitted in that path.
- **D-46-06:** Non-Agent text-generation consumers migrate to the same owner-scoped deployment
  resolver. Explicit model choice is allowed only when it references an active model owned by
  that user. Missing, multiple-default, inactive or unsafe configuration fails closed.
- **D-46-07:** The legacy static catalog and routing preference in `ai_router.py` cannot remain
  a runtime model selector. The settings page does not restore “智能路由策略”.
- **D-46-08:** LiteLLM may remain the protocol adapter where its native provider implementation
  is covered by contract tests; adapter ownership must still come from the registry.

### Credentials, evidence and qualification

- **D-46-09:** API keys/OAuth tokens are write-only, encrypted or OS-protected according to the
  existing boundary, never returned to the renderer, committed, printed or included in failure
  evidence. Provider errors are redacted and mapped to stable classes.
- **D-46-10:** Unit/contract tests prove protocol shaping but do not prove live availability.
  Phase 46 requires separate catalog, direct test and Pi run evidence for each provider.
- **D-46-11:** Real cloud calls require operator-supplied credentials and explicit execution
  authorization. A missing credential, inaccessible service or unavailable Ollama daemon is an
  honest `BLOCKED`/`PARTIAL` row, not permission to substitute a mock or spend money.
- **D-46-12:** Usage and cost records carry owner, provider, model, request lineage, token counts,
  latency and terminal error. Unknown prices or capabilities remain `unknown`; no estimated
  value is presented as provider truth.

### the agent's Discretion

- Define the internal `ProviderProfile`, catalog page and deployment resolver types.
- Choose bounded page/item/response limits and stable provider error vocabulary.
- Select the smallest migration order for legacy consumers while preserving their domain gates.
- Choose redacted evidence file formats and credential injection hooks for the qualification CLI.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

- `.planning/ROADMAP.md` - Phase 46 scope, dependencies and success criteria.
- `.planning/AGENT-RUNTIME-CONTRACT.md` - Pi authority and fail-closed tool/run boundary.
- `.planning/phases/44-desktop-transport-credentials-and-offline-behavior/44-CONTEXT.md` - credential and offline behavior decisions.
- `backend/app/services/provider_catalog.py` - current five-provider discovery foundation.
- `backend/app/api/models.py` - owner-scoped model CRUD/discovery/test API.
- `backend/app/api/gateway.py` - current run-token-to-owner deployment seam.
- `backend/app/services/ai_service.py` - current invocation adapter.
- `backend/app/services/ai_router.py` and its `rg` call sites - known static/global authority gap.
- `frontend/src/components/settings/models-section.tsx` - current provider configuration UI.
- `agent-service/src/agent/provider.ts` and `session-factory.ts` - Pi gateway context headers.
</canonical_refs>

<deferred>
## Deferred Ideas

- Adding Azure OpenAI, AWS Bedrock, OpenRouter or other providers requires a new requirement and
  protocol profile; they are not implied by “custom”.
- Image and embedding provider unification remain separate because their schemas, capability
  gates and cost models differ from text generation.
- Provider account management, billing setup, credential purchase and production rollout are
  operator/external work.
</deferred>

---

*Phase: 46-provider-protocol-unification-and-live-qualification*
*Context gathered: 2026-08-12*
