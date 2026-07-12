"""
评测服务 测试 — 仅测试指标计算（模型层），不依赖外部服务

服务依赖采用惰性加载，因此可以直接测试生产指标实现，不复制算法。
"""
import pytest

pytestmark = pytest.mark.unit

from app.services.eval_service import EvalService


service = EvalService()


class TestComputeMetrics:
    """测试指标计算"""

    def test_perfect_recall(self):
        gold = [1, 2, 3, 4, 5]
        recalled = [1, 2, 3, 4, 5]
        metrics = service._compute_metrics(gold, recalled, top_k=5)
        assert metrics["recall_at_k"] == 1.0
        assert metrics["precision_at_k"] == 1.0
        assert metrics["mrr"] == 1.0

    def test_zero_recall(self):
        gold = [1, 2, 3, 4, 5]
        recalled = [10, 11, 12, 13, 14]
        metrics = service._compute_metrics(gold, recalled, top_k=5)
        assert metrics["recall_at_k"] == 0.0
        assert metrics["precision_at_k"] == 0.0
        assert metrics["mrr"] == 0.0

    def test_partial_recall(self):
        gold = [1, 2, 3]
        recalled = [1, 10, 2, 20, 30]
        metrics = service._compute_metrics(gold, recalled, top_k=5)
        assert abs(metrics["recall_at_k"] - 2 / 3) < 0.001
        assert metrics["precision_at_k"] == 2 / 5

    def test_mrr_first_position(self):
        gold = [1, 2, 3]
        recalled = [1, 10, 20]
        metrics = service._compute_metrics(gold, recalled, top_k=5)
        assert metrics["mrr"] == 1.0

    def test_mrr_third_position(self):
        gold = [1, 2, 3]
        recalled = [10, 20, 1, 30, 40]
        metrics = service._compute_metrics(gold, recalled, top_k=5)
        assert abs(metrics["mrr"] - 1 / 3) < 0.001

    def test_empty_gold_returns_zero(self):
        metrics = service._compute_metrics([], [1, 2, 3], top_k=5)
        assert metrics["recall_at_k"] == 0.0

    def test_empty_recalled_returns_zero(self):
        gold = [1, 2, 3]
        recalled = []
        metrics = service._compute_metrics(gold, recalled, top_k=5)
        assert metrics["recall_at_k"] == 0.0
        assert metrics["mrr"] == 0.0

    def test_ndcg_perfect_relevance(self):
        gold = [1, 2, 3]
        recalled = [1, 2, 3]
        ndcg = service._compute_ndcg(gold, recalled, top_k=3)
        assert ndcg == 1.0

    def test_ndcg_partial_relevance(self):
        gold = [1, 2]
        recalled = [10, 1, 20, 2, 30]
        # Relevance: [0,1,0,1,0]
        # DCG = 0/1 +1/1.585 +0/2 +1/2.322 +0/2.585 ≈ 1.0624
        # IDCG = 1/1 + 1/1.585 ≈ 1.6309
        # NDCG ≈ 0.6514
        ndcg = service._compute_ndcg(gold, recalled, top_k=5)
        assert 0.64 < ndcg < 0.66

    def test_ndcg_empty_gold(self):
        ndcg = service._compute_ndcg([], [1, 2, 3], top_k=3)
        assert ndcg == 0.0

    def test_ndcg_empty_recalled(self):
        ndcg = service._compute_ndcg([1, 2], [], top_k=5)
        assert ndcg == 0.0

    def test_ndcg_with_scores(self):
        """检索置信度不能改变基于相关性判断的 NDCG。"""
        gold = [1, 2]
        recalled = [1, 10, 2, 20]
        scores = [0.9, 0.5, 0.8, 0.3]
        ndcg = service._compute_ndcg(gold, recalled, top_k=4, scores=scores)
        assert 0.0 <= ndcg <= 1.0
        assert ndcg == service._compute_ndcg(gold, recalled, top_k=4)
