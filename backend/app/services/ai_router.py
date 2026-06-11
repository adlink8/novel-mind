"""
AI 智能路由器 - 根据任务类型与层级自动选择最优模型

路由策略:
  用户设置全局偏好（quality / balanced / budget）后，路由器会:
  1. 根据任务类型查表确定目标层级（如 deep_analysis → quality）
  2. 从目标层级的模型池中选取第一个可用模型
  3. 若目标层级无可用模型，自动降级到其他层级

任务类型 → 层级映射:
  - deep_analysis  → quality   （深度分析需要最强模型）
  - fanfiction     → quality   （续写创作需要高质量生成）
  - style_analysis → quality   （文风分析需要细致理解）
  - extraction     → balanced  （角色/时间线提取，中等复杂度）
  - summary        → balanced  （摘要生成）
  - chapter_summary → balanced （章节摘要）
  - translation    → balanced  （翻译）
  - embedding      → budget    （向量嵌入，最便宜的即可）

降级顺序: balanced → quality → budget

使用方式:
  from app.services.ai_router import ai_router
  model = ai_router.route_task("deep_analysis")
  # model.model_id = "gpt-4o", model.provider = "openai"
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelTier:
    """单个模型层级配置"""

    name: str  # 显示名称（如 "GPT-4o"）
    model_id: str  # LiteLLM 模型标识（如 "gpt-4o"）
    provider: str  # 提供商（openai / anthropic / ollama）
    cost_per_1k: float  # 每 1K token 的大致成本（USD）


@dataclass
class RoutingConfig:
    """路由偏好配置：三个层级的模型池"""

    quality_models: List[ModelTier] = field(default_factory=list)  # 质量优先模型池
    balanced_models: List[ModelTier] = field(default_factory=list)  # 均衡模型池
    budget_models: List[ModelTier] = field(default_factory=list)  # 经济模型池


# ─────────── 预置模型池 ───────────
_DEFAULT_ROUTING = RoutingConfig(
    quality_models=[
        ModelTier(
            name="GPT-4o", model_id="gpt-4o", provider="openai", cost_per_1k=0.005
        ),
        ModelTier(
            name="Claude Opus",
            model_id="claude-3-opus-20240229",
            provider="anthropic",
            cost_per_1k=0.015,
        ),
    ],
    balanced_models=[
        ModelTier(
            name="GPT-4o-mini",
            model_id="gpt-4o-mini",
            provider="openai",
            cost_per_1k=0.00015,
        ),
        ModelTier(
            name="Claude Sonnet",
            model_id="claude-3-5-sonnet-20241022",
            provider="anthropic",
            cost_per_1k=0.003,
        ),
    ],
    budget_models=[
        ModelTier(
            name="Ollama-Qwen2",
            model_id="ollama/qwen2:7b",
            provider="ollama",
            cost_per_1k=0.0,
        ),
        ModelTier(
            name="GPT-4o-mini",
            model_id="gpt-4o-mini",
            provider="openai",
            cost_per_1k=0.00015,
        ),
    ],
)

# ─────────── 任务类型 → 推荐层级映射 ───────────
_TASK_TIER_MAP: Dict[str, str] = {
    "deep_analysis": "quality",  # 深度分析：需要最强模型
    "extraction": "balanced",  # 角色/时间线提取：中等复杂度
    "summary": "balanced",  # 摘要生成
    "embedding": "budget",  # 向量嵌入：最便宜的即可
    "fanfiction": "quality",  # 续写创作：需要高质量生成
    "chapter_summary": "balanced",  # 章节摘要
    "style_analysis": "quality",  # 文风分析
    "translation": "balanced",  # 翻译
}


class AIRouter:
    """
    AI 智能路由器。

    根据任务类型和用户偏好，从模型池中选择最优模型。
    支持降级机制：目标层级无可用模型时自动切换到其他层级。
    """

    def __init__(self, routing_preference: str = "balanced"):
        """
        初始化路由器。

        Args:
            routing_preference: 全局偏好 - "quality" / "balanced" / "budget"
        """
        self.routing_preference = routing_preference
        self.config = _DEFAULT_ROUTING
        self._available_models: Dict[str, ModelTier] = {}  # 运行时可用模型缓存
        self._load_defaults()

    def _load_defaults(self):
        """加载默认模型池到可用模型缓存"""
        for tier_list in [
            self.config.quality_models,
            self.config.balanced_models,
            self.config.budget_models,
        ]:
            for model in tier_list:
                self._available_models[model.name] = model

    def route_task(
        self,
        task_type: str,
        override_tier: Optional[str] = None,
    ) -> ModelTier:
        """
        为指定任务选择最优模型。

        Args:
            task_type: 任务类型（deep_analysis / extraction / summary / embedding / fanfiction）
            override_tier: 强制指定层级（覆盖全局偏好与任务默认层级）

        Returns:
            选中的 ModelTier 配置（包含 model_id、provider、cost_per_1k）

        选择逻辑:
        1. 确定目标层级（override_tier > 任务默认 > 全局偏好）
        2. 从目标层级取第一个模型
        3. 若目标层级为空，降级到其他层级
        4. 若所有层级都为空，返回硬编码默认值
        """
        # 确定目标层级
        if override_tier:
            tier = override_tier
        else:
            tier = _TASK_TIER_MAP.get(task_type, self.routing_preference)

        # 从目标层级中选取第一个可用模型
        tier_models = self._get_tier_models(tier)
        if tier_models:
            selected = tier_models[0]
            logger.info(
                f"[AIRouter] 任务={task_type}, 层级={tier}, 选中={selected.name}"
            )
            return selected

        # 降级：依次尝试其他层级
        for fallback_tier in ["balanced", "quality", "budget"]:
            if fallback_tier == tier:
                continue
            fallback_models = self._get_tier_models(fallback_tier)
            if fallback_models:
                selected = fallback_models[0]
                logger.warning(
                    f"[AIRouter] 层级 {tier} 无可用模型，降级到 {fallback_tier}，选中={selected.name}"
                )
                return selected

        # 最终回退：硬编码默认值
        logger.error("[AIRouter] 所有层级均无可用模型，使用硬编码默认值")
        return ModelTier(
            name="GPT-4o-mini (fallback)",
            model_id="gpt-4o-mini",
            provider="openai",
            cost_per_1k=0.00015,
        )

    def _get_tier_models(self, tier: str) -> List[ModelTier]:
        """获取指定层级的模型列表"""
        if tier == "quality":
            return self.config.quality_models
        elif tier == "balanced":
            return self.config.balanced_models
        elif tier == "budget":
            return self.config.budget_models
        return []

    def get_all_models(self) -> Dict[str, List[ModelTier]]:
        """获取所有层级的模型列表（用于前端展示）"""
        return {
            "quality": self.config.quality_models,
            "balanced": self.config.balanced_models,
            "budget": self.config.budget_models,
        }

    def update_preference(self, preference: str):
        """更新全局路由偏好"""
        if preference in ("quality", "balanced", "budget"):
            self.routing_preference = preference
            logger.info(f"[AIRouter] 全局偏好已更新: {preference}")
        else:
            raise ValueError(
                f"无效的偏好值: {preference}，可选: quality / balanced / budget"
            )


# 全局单例（默认 balanced 偏好）
ai_router = AIRouter(routing_preference="balanced")
