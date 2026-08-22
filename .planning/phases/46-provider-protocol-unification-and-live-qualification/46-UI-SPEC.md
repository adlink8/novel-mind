# Phase 46 UI Specification - Provider Configuration and Qualification

**Status:** Ready for implementation planning
**Surface:** Existing `/settings` AI model section only

## Product Rule

Reuse the current settings visual language and model modal. Do not restore the intelligent
routing section and do not add a parallel provider-management page.

## Model Modal States

| State | Required behavior |
|---|---|
| Provider profiles loading | Keep form stable; provider field disabled with a compact loading label |
| Profiles unavailable | Use the existing five-item manual fallback and show “供应商目录暂不可用”; saving still requires backend validation |
| Provider selected | Fill backend-provided default Base URL and credential label/requirement; never expose a stored secret |
| Discovering models | Disable duplicate discovery/save, preserve entered URL, show bounded inline progress |
| Discovery success | Show searchable model select with normalized ID and optional capability/status metadata; manual ID remains available |
| Discovery failed | Show redacted, actionable category (`凭据无效` / `地址不安全` / `服务不可用` / `响应不兼容`); keep manual entry without claiming connectivity |
| Saved, unqualified | Display “已配置，未实测” rather than connected/green |
| Qualified | Display last checked provider/model/time and catalog/direct/Pi result derived from backend evidence |
| Partial/blocked | Name the missing step or credential without exposing provider response bodies or secret fragments |

## Layout and Interaction

- Provider → Base URL → credential → “获取模型列表” → model selection/manual ID → save.
- Ollama credential is optional; cloud API keys remain password inputs. Empty password on edit
  means “keep existing”, not erase.
- The first saved model may become owner default; later models require explicit “设为默认”.
- Model cards show provider, model ID, default/active state and qualification state. They do not
  show quality/economy routing tiers.
- Keyboard focus returns to the invoking button after modal close; errors use `role=alert` and
  discovery results remain reachable by keyboard.

## Responsive and Desktop Constraints

- At 763px Electron window width the modal remains within viewport and action buttons wrap
  without horizontal scrolling.
- At 390px test width fields stack; provider/model selectors and errors remain fully readable.
- Loading and status changes do not move the bottom desktop navigation or cover save controls.

## Verification

- React tests cover all table states and backend-derived profile text.
- Browser/Electron Playwright covers one successful injected fixture and one live unavailable
  provider path, console error count zero, keyboard operation and 763px/390px screenshots.
- No test or screenshot may include a real key/token.
