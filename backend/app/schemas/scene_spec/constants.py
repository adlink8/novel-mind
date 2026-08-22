"""Scene Spec / Prompt Revision 词汇表常量（Phase 32-01, REQ-VIS-03）。

原 ``scene_spec.py`` 单文件的包化拆分产物。本模块只承载契约中闭集的
词汇表常量；镜像 ORM 词表，保证 schema/model/migration 字节一致。
"""

SCENE_SPEC_SCHEMA_VERSION = "scene-spec.v1"
PROMPT_SCHEMA_VERSION = "prompt-revision.v1"
SCENE_SPEC_ARTIFACT_KIND = "scene_spec"
PROMPT_ARTIFACT_KIND = "prompt_revision"

# Mirrors the ORM vocabulary so schema/model/migration stay byte-identical.
SPEC_DETAIL_KINDS = (
    "subject",
    "action",
    "setting",
    "composition",
    "style",
    "continuity",
)
SPEC_SOURCES = ("evidence", "visual_bible", "user_interpretation")
SPEC_CONSTRAINT_SCOPES = (
    "costume",
    "era",
    "identity",
    "style",
    "physical",
    "continuity",
)
SPEC_UNCERTAINTY_REASONS = (
    "missing_evidence",
    "conflicting_claim",
    "future_spoiler",
    "ambiguous_reference",
)
SPEC_REVIEW_ACTIONS = ("approve", "reject", "supersede", "needs_relink")
SPEC_REVIEW_STATES = (
    "candidate",
    "approved",
    "rejected",
    "superseded",
    "needs_relink",
)
SPEC_ACTOR_SOURCES = ("human", "machine")
# Ordered canonical prompt sections (RESEARCH A2 / D-32-02). Provider adapters
# may render these sections but never add or reorder canon.
SPEC_SECTION_ORDER = (
    "subject",
    "action",
    "setting",
    "composition",
    "style",
    "continuity",
    "negative_constraints",
    "uncertainties",
)
