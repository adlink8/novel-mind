"""Provider Prompt Adapters (Phase 32-03, REQ-VIS-03).

D-32-01..D-32-04: ``SceneSpec`` is the canonical candidate Artifact; a provider
prompt is a derived ``PromptRevision`` candidate rendered by a provider adapter.
This module owns:

- the ``PromptAdapter`` protocol — the provider-neutral → provider-specific
  adapter contract. Input is only a canonical ``SceneSpecContract``; output
  carries the ordered canonical sections, negative constraints, adapter id /
  version and replayable ``input_hash``/``prompt_hash``. Adapter branching is
  never written back into the SceneSpec (D-32-01) and unsupported detail fails
  closed (D-32-02);
- deterministic adapter implementations: the default mock adapter (byte-identical
  to the Phase 32-02 derivation) and a configured block adapter. Provider tokens
  stay in the rendered prompt string only;
- ``compile_prompt`` — the pure, replayable compilation. It revalidates the
  SceneSpec contract and the derived revision so a prompt is exactly
  reproducible from its SceneSpec;
- ``PromptArtifact`` — the immutable compile result (revision + deterministic
  lineage envelope, D-32-03);
- ``PromptRevisionService`` — the owner-scoped compile/preview/persist/edit/diff
  seam with server-side gates: only owner/novel-scoped SceneSpecs compile,
  prompt preview never persists and never calls a provider (D-32-04), and a
  human edit produces an explicit new candidate revision with the diff retained.

拆分说明（refactor split）：本模块是 facade —— 显式 re-export 全部公共符号，
``from app.services.prompt_compiler.adapters import X`` 的 import surface 不变。
实现按关注点拆到同目录模块，依赖单向（facade → 子模块 → leaf）：
- ``_adapter_errors`` —— 失败即关闭的错误词表（leaf，无项目内依赖）；
- ``_adapter_core`` —— 纯编译核心：adapter 契约/确定性子类/注册表/
  ``adapter_config_hash``/``compile_prompt``/``PromptArtifact``（无 DB、
  不反向 import 服务层）；
- ``_adapter_service`` —— ``PromptRevisionService`` 及请求/结果值对象。
"""

from __future__ import annotations

from app.services.prompt_compiler._adapter_core import (
    ADAPTER_REGISTRY,
    MOCK_PROMPT_ADAPTER_ID,
    MOCK_PROMPT_ADAPTER_VERSION,
    PROMPT_SCHEMA_HASH,
    BlockPromptAdapter,
    MockPromptAdapter,
    PromptAdapter,
    PromptArtifact,
    _RedactMixin,
    adapter_config_hash,
    compile_prompt,
    get_adapter,
)
from app.services.prompt_compiler._adapter_errors import (
    PromptCompileError,
    PromptRevisionConflict,
    PromptRevisionNotFound,
    PromptRevisionServiceError,
)
from app.services.prompt_compiler._adapter_service import (
    EditedPromptRevision,
    PersistedPromptRevision,
    PromptCompileRequest,
    PromptEditInput,
    PromptRevisionService,
)

# Re-exported public surface (split facade). Declared so ruff's F401 treats the
# re-export imports as intentional — identical to the rag_quality package.
__all__ = [
    "ADAPTER_REGISTRY",
    "MOCK_PROMPT_ADAPTER_ID",
    "MOCK_PROMPT_ADAPTER_VERSION",
    "PROMPT_SCHEMA_HASH",
    "BlockPromptAdapter",
    "MockPromptAdapter",
    "PromptAdapter",
    "PromptArtifact",
    "_RedactMixin",
    "adapter_config_hash",
    "compile_prompt",
    "get_adapter",
    "PromptCompileError",
    "PromptRevisionConflict",
    "PromptRevisionNotFound",
    "PromptRevisionServiceError",
    "EditedPromptRevision",
    "PersistedPromptRevision",
    "PromptCompileRequest",
    "PromptEditInput",
    "PromptRevisionService",
]
