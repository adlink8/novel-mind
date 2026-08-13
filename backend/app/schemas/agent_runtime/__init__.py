"""
Agent Runtime 与 Artifact Contract 的严格 wire 模型（25.2-03 / D-09..D-14）。

本包是原 `agent_runtime.py` 单文件的包化拆分，公共 import 面保持零改动：
`from app.schemas.agent_runtime import X` 仍然可用。子模块按族分组：
base（基类+统一信封）、skills（技能注册）、runs（SkillRun）、artifacts（产物行）、
artifacts_phase27_31 / artifacts_phase32_34 / artifacts_derivative（领域信封）。
"""

from __future__ import annotations

from app.schemas.agent_runtime.base import (
    StrictAgentRuntimeModel,
    NormalizationTrail,
    CitedAnswerArtifact,
    ExternalEvidenceSource,
    ExternalEvidenceClaim,
    ExternalEvidenceArtifact,
)

from app.schemas.agent_runtime.skills import (
    SkillVersionRegister,
    SkillRegistryView,
    SkillVersionView,
    SkillVersionStatusUpdate,
    SkillRuntimeManifest,
    ConnectorRuntimeManifest,
)

from app.schemas.agent_runtime.runs import (
    SkillRunCreate,
    SkillRunView,
    SkillRunAccepted,
    SkillRunFinalize,
    ToolRunSummary,
    RouteSkillRequest,
)

from app.schemas.agent_runtime.chapter_batch import (
    ChapterBatchCreate,
    ChapterBatchChapterView,
    ChapterBatchView,
)

from app.schemas.agent_runtime.artifacts import (
    ArtifactView,
    ArtifactRevisionView,
)

from app.schemas.agent_runtime.artifacts_phase27_31 import (
    WorldModelCandidateArtifact,
    ChapterAnalysisArtifact,
    StoryArcArtifact,
    EvaluatedSkillRunLineage,
    EvaluatedArtifactLineage,
    SkillEvaluationArtifact,
    VisualBibleArtifact,
    SceneCandidateArtifact,
)

from app.schemas.agent_runtime.artifacts_phase32_34 import (
    SceneSpecArtifact,
    PromptArtifact,
    IllustrationRevisionPayload,
    IllustrationRevisionArtifact,
    IllustrationAnchorProposalRange,
    IllustrationAnchorProposalCopy,
    IllustrationAnchorProposalPayload,
    IllustrationAnchorProposalArtifact,
)

from app.schemas.agent_runtime.artifacts_derivative import (
    CanonForkProposalPayload,
    CanonDeltaPayload,
    CanonForkProposalArtifact,
    DerivativeEditProposalPayload,
    DerivativeEditProposalArtifact,
    BranchSuggestionPayload,
    ContinuityReportPayload,
    DraftPayload,
    DraftArtifact,
    BranchIllustrationVisualVersionRef,
    BranchIllustrationSourceSnapshotRef,
    BranchIllustrationCandidateAssetRef,
    BranchIllustrationIdentityRow,
    BranchIllustrationSourceRef,
    BranchIllustrationRevisionPayload,
    BranchVisualBibleArtifact,
    ExportPreparationSourceSnapshotRef,
    ExportPreparationBaseRevisionRef,
    ExportPreparationPayload,
    ExportPreparationArtifact,
)

__all__ = [
    # 基类 + 统一信封基建（base）
    "StrictAgentRuntimeModel",
    "NormalizationTrail",
    "CitedAnswerArtifact",
    "ExternalEvidenceSource",
    "ExternalEvidenceClaim",
    "ExternalEvidenceArtifact",
    # 技能注册 / 目录 / 版本（skills）
    "SkillVersionRegister",
    "SkillRegistryView",
    "SkillVersionView",
    "SkillVersionStatusUpdate",
    "SkillRuntimeManifest",
    "ConnectorRuntimeManifest",
    # SkillRun 族（runs）
    "SkillRunCreate",
    "SkillRunView",
    "SkillRunAccepted",
    "SkillRunFinalize",
    "ToolRunSummary",
    "RouteSkillRequest",
    "ChapterBatchCreate",
    "ChapterBatchChapterView",
    "ChapterBatchView",
    # 产物行（artifacts）
    "ArtifactView",
    "ArtifactRevisionView",
    # Phase 27-31 领域信封
    "WorldModelCandidateArtifact",
    "ChapterAnalysisArtifact",
    "StoryArcArtifact",
    "EvaluatedSkillRunLineage",
    "EvaluatedArtifactLineage",
    "SkillEvaluationArtifact",
    "VisualBibleArtifact",
    "SceneCandidateArtifact",
    # Phase 32-34 领域信封
    "SceneSpecArtifact",
    "PromptArtifact",
    "IllustrationRevisionPayload",
    "IllustrationRevisionArtifact",
    "IllustrationAnchorProposalRange",
    "IllustrationAnchorProposalCopy",
    "IllustrationAnchorProposalPayload",
    "IllustrationAnchorProposalArtifact",
    # Phase 35-39 领域信封（derivative）
    "CanonForkProposalPayload",
    "CanonDeltaPayload",
    "CanonForkProposalArtifact",
    "DerivativeEditProposalPayload",
    "DerivativeEditProposalArtifact",
    "BranchSuggestionPayload",
    "ContinuityReportPayload",
    "DraftPayload",
    "DraftArtifact",
    "BranchIllustrationVisualVersionRef",
    "BranchIllustrationSourceSnapshotRef",
    "BranchIllustrationCandidateAssetRef",
    "BranchIllustrationIdentityRow",
    "BranchIllustrationSourceRef",
    "BranchIllustrationRevisionPayload",
    "BranchVisualBibleArtifact",
    "ExportPreparationSourceSnapshotRef",
    "ExportPreparationBaseRevisionRef",
    "ExportPreparationPayload",
    "ExportPreparationArtifact",
]
