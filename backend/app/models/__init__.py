"""
ORM 模型统一导出包

本包包含 NovelMind 所有数据库模型（13 张表），分为 7 个业务域:

小说域:
  - Novel          : 小说主表（标题、作者、字数、状态、文风指纹）
  - Chapter        : 章节表（章节号、标题、正文、AI 摘要）

导入域:
  - ImportJob      : 导入任务表（状态机、进度、重试机制）

RAG 域:
  - TextChunk      : 文本块表（三级分块后的语义单元，用于向量检索）

分析域:
  - AnalysisResult : AI 分析结果表（摘要、人物分析、叙事结构等）
  - Character      : 人物表（名称、别名、角色类型、性格描述）
  - CharacterRelation: 人物关系表（关系类型、强度、首次出现章节）
  - TimelineEvent  : 时间线事件表（事件标题、类型、因果关联）

创作域:
  - FanFiction     : 同人文表（续写提示、风格配置、生成状态）
  - FanFictionChapter: 同人文章节表（AI 生成标记、风格评分、RAG 上下文）

基础设施域:
  - User           : 用户与认证主体
  - AIModelConfig  : AI 模型配置表（提供商、密钥、路由层级）
  - AIUsageLog     : AI 调用日志表（token 用量、费用、延迟）

使用方式:
  from app.models import Novel, Chapter, TextChunk  # 统一导入
  # 或
  from app.models.novel import Novel, Chapter       # 按模块导入
"""

from app.models.base import Base
from app.models.user import User
from app.models.novel import Novel, Chapter
from app.models.import_job import ImportJob
from app.models.analysis import (
    AnalysisResult,
    AnalysisRun,
    AnalysisVersion,
    AnalysisChapterStage,
    ModelCallAttempt,
    AnalysisBudgetLedger,
    AnalysisBudgetReservation,
)
from app.models.timeline import (
    TimelineEvent,
    MachineTimelineEvent,
    TimelineParticipant,
    TimelineEvidenceRef,
    TimelineCausalEdge,
    TimelineOverride,
    TimelineActivePointer,
    TimelinePointerJournal,
)
from app.models.character import Character, CharacterRelation
from app.models.fanfiction import FanFiction
from app.models.fanfiction_chapter import FanFictionChapter
from app.models.ai_model import AIModelConfig
from app.models.ai_usage_log import AIUsageLog
from app.models.app_setting import AppSetting
from app.models.text_chunk import TextChunk
from app.models.chunk_index_journal import ChunkIndexJournal
from app.models.chunk_build import (
    ChunkActivePointer,
    ChunkBuild,
    ChunkHierarchyNode,
)
from app.models.eval import (
    ActiveBaseline,
    BaselineCandidate,
    EvalDataset,
    EvalRun,
    EvalResult,
    QualityRun,
    RagSourceSnapshot,
    RagFixtureJob,
    RagEvalCase,
)
from app.models.knowledge import (
    KnowledgeEntityCandidate,
    KnowledgeEventCandidate,
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
    KnowledgeReviewQueue,
)
from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativePromotionJournal,
    NarrativeRefreshRun,
    NarrativeSourceWatermark,
    NarrativeSourceSnapshot,
    NarrativeSourceSnapshotItem,
    NarrativeUnit,
    NarrativeUnitEvidenceLink,
)
from app.models.relationship import (
    CharacterIdentityOverride,
    RelationshipBuildRun,
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipObservationCandidate,
    RelationshipObservationJudgment,
    RelationshipOverride,
    RelationshipProjectionAudit,
)
from app.models.reader_chat import (
    ReaderBudgetLedger,
    ReaderBudgetReservation,
    ReaderContextEvidenceRef,
    ReaderContextManifest,
    ReaderConversation,
    ReaderGenerationJob,
    ReaderMessage,
    ReaderMessageCitation,
    ReaderMessageSelection,
    ReaderModelCallAttempt,
)
from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueBudgetLedger,
    ClueBudgetReservation,
    ClueEvidenceRef,
    ClueLifecycleEvent,
    ClueLink,
    ClueModelCallAttempt,
    ClueOverride,
    CluePointerJournal,
    MachineClue,
)
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryEdge,
    NarrativeMemoryManifest,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryValidationReport,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildBudgetLedger,
    NarrativeMemoryBuildBudgetReservation,
    NarrativeMemoryBuildModelCallAttempt,
    NarrativeMemoryBuildReport,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.models.narrative_memory_rebuild import (
    NarrativeMemoryRebuildItem,
    NarrativeMemoryRebuildPlan,
    NarrativeMemoryReuseReport,
)
from app.models.narrative_memory_qualification import (
    NarrativeMemoryQualificationCaseResult,
    NarrativeMemoryQualificationReport,
    NarrativeMemoryQualificationRun,
)
from app.models.agent_runtime import (
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    NovelAgentProfile,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.models.queryplan import QueryPlanTrace
from app.models.world_model_event import (
    WorldModelCausalEdge,
    WorldModelConflict,
    WorldModelEvent,
)
from app.models.world_model_knowledge import WorldModelKnowledge
from app.models.world_model_entity import (
    WorldModelAliasReview,
    WorldModelEntity,
    WorldModelEntityLink,
    WorldModelRule,
    WorldModelRuleException,
)
# 31-34 迁移在链上但模型未注册：补齐注册使 ORM metadata 与迁移链一致
# （否则 alembic check 把四张既有表判为待删除的 drift）。
from app.models.canon_space import CanonSpaceArtifact
from app.models.canon_fork import CanonFork
from app.models.derivative_project import DerivativeProject
from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_revision import DerivativeRevision
from app.models.canon_contamination import CanonContaminationBlock
from app.models.fanfiction_revision import FanFictionRevision
from app.models.fanfiction_override import FanFictionOverride
from app.models.reader_bookmark import ReaderBookmark
from app.models.visual_bible import (
    VisualBibleReviewEvent,
    VisualBibleVersion,
    VisualClaim,
    VisualEntity,
    VisualEvidenceRef,
    VisualReferenceAsset,
)
from app.models.key_scene import (
    SceneCandidate,
    SceneCandidateSet,
    SceneEvidenceRange,
    SceneReviewDecision,
)
from app.models.scene_spec import (
    SceneSpecDetail,
    SceneSpecEvidenceRef,
    SceneSpecNegativeConstraint,
    SceneSpecUncertainty,
    SceneSpecVersion,
)
from app.models.prompt_revision import (
    PromptRevision,
    PromptRevisionReviewEvent,
)
from app.models.illustration_job import (
    IllustrationAttempt,
    IllustrationBudgetLedger,
    IllustrationBudgetReservation,
    IllustrationJob,
    IllustrationReviewEvent,
)
from app.models.illustration import (
    AssetRevision,
    ConsistencyReport,
)
from app.models.illustration_anchor import (
    IllustrationAnchor,
    IllustrationAnchorProposal,
)

__all__ = [
    "Base",
    "User",
    "Novel",
    "Chapter",
    "ImportJob",
    "AnalysisResult",
    "TimelineEvent",
    "AnalysisRun",
    "AnalysisVersion",
    "AnalysisChapterStage",
    "ModelCallAttempt",
    "AnalysisBudgetLedger",
    "AnalysisBudgetReservation",
    "MachineTimelineEvent",
    "TimelineParticipant",
    "TimelineEvidenceRef",
    "TimelineCausalEdge",
    "TimelineOverride",
    "TimelineActivePointer",
    "TimelinePointerJournal",
    "Character",
    "CharacterRelation",
    "FanFiction",
    "FanFictionChapter",
    "AIModelConfig",
    "AIUsageLog",
    "AppSetting",
    "TextChunk",
    "ChunkIndexJournal",
    "ChunkBuild",
    "ChunkActivePointer",
    "ChunkHierarchyNode",
    "EvalDataset",
    "EvalRun",
    "EvalResult",
    "RagSourceSnapshot",
    "RagFixtureJob",
    "RagEvalCase",
    "QualityRun",
    "BaselineCandidate",
    "ActiveBaseline",
    "KnowledgeExtractionRun",
    "KnowledgeEntityCandidate",
    "KnowledgeEventCandidate",
    "KnowledgeRelationCandidate",
    "KnowledgeRelationJudgment",
    "KnowledgeEvidenceRef",
    "KnowledgeReviewQueue",
    "NarrativeSourceSnapshot",
    "NarrativeSourceSnapshotItem",
    "NarrativeUnit",
    "NarrativeUnitEvidenceLink",
    "NarrativeIndexBuild",
    "NarrativeActivePointer",
    "NarrativePromotionJournal",
    "NarrativeRefreshRun",
    "NarrativeSourceWatermark",
    "RelationshipBuildRun",
    "RelationshipObservationCandidate",
    "RelationshipObservationJudgment",
    "RelationshipObservation",
    "RelationshipEvidenceLink",
    "CharacterIdentityOverride",
    "RelationshipOverride",
    "RelationshipProjectionAudit",
    "ReaderConversation",
    "ReaderMessage",
    "ReaderMessageSelection",
    "ReaderContextManifest",
    "ReaderContextEvidenceRef",
    "ReaderMessageCitation",
    "ReaderGenerationJob",
    "ReaderModelCallAttempt",
    "ReaderBudgetLedger",
    "ReaderBudgetReservation",
    "ClueAnalysisVersion",
    "ClueAnalysisRun",
    "MachineClue",
    "ClueEvidenceRef",
    "ClueLifecycleEvent",
    "ClueLink",
    "ClueOverride",
    "ClueBudgetLedger",
    "ClueBudgetReservation",
    "ClueModelCallAttempt",
    "ClueActivePointer",
    "CluePointerJournal",
    "NarrativeMemoryVersion",
    "NarrativeMemoryNode",
    "NarrativeMemoryClaim",
    "NarrativeMemoryEdge",
    "NarrativeMemorySourceLink",
    "NarrativeMemoryManifest",
    "NarrativeMemoryValidationReport",
    "NarrativeMemoryBuildRun",
    "NarrativeMemoryBuildStage",
    "NarrativeMemoryBuildBudgetLedger",
    "NarrativeMemoryBuildBudgetReservation",
    "NarrativeMemoryBuildModelCallAttempt",
    "NarrativeMemoryBuildReport",
    "NarrativeMemoryRebuildPlan",
    "NarrativeMemoryRebuildItem",
    "NarrativeMemoryReuseReport",
    "NarrativeMemoryQualificationRun",
    "NarrativeMemoryQualificationCaseResult",
    "NarrativeMemoryQualificationReport",
    "SkillRegistry",
    "SkillVersion",
    "SkillRun",
    "Artifact",
    "ArtifactRevision",
    "NovelAgentProfile",
    "ApprovalRequest",
    "QueryPlanTrace",
    "WorldModelEvent",
    "WorldModelCausalEdge",
    "WorldModelConflict",
    "WorldModelKnowledge",
    "WorldModelEntity",
    "WorldModelRule",
    "WorldModelRuleException",
    "WorldModelEntityLink",
    "WorldModelAliasReview",
    "CanonSpaceArtifact",
    "CanonFork",
    "DerivativeProject",
    "DerivativeChapter",
    "DerivativeRevision",
    "CanonContaminationBlock",
    "FanFictionRevision",
    "FanFictionOverride",
    "ReaderBookmark",
    "VisualBibleVersion",
    "VisualEntity",
    "VisualClaim",
    "VisualEvidenceRef",
    "VisualReferenceAsset",
    "VisualBibleReviewEvent",
    "SceneCandidateSet",
    "SceneCandidate",
    "SceneEvidenceRange",
    "SceneReviewDecision",
    "SceneSpecVersion",
    "SceneSpecDetail",
    "SceneSpecNegativeConstraint",
    "SceneSpecEvidenceRef",
    "SceneSpecUncertainty",
    "PromptRevision",
    "PromptRevisionReviewEvent",
    "IllustrationJob",
    "IllustrationAttempt",
    "IllustrationBudgetLedger",
    "IllustrationBudgetReservation",
    "IllustrationReviewEvent",
    "AssetRevision",
    "ConsistencyReport",
    "IllustrationAnchor",
    "IllustrationAnchorProposal",
]
