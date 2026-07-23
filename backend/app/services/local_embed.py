"""本地 embedding（与「数据分析」项目同一套逻辑）。

使用 sentence-transformers 直接加载本机 BGE 模型，不再经 Ollama HTTP。

默认模型:
  BAAI/bge-small-zh-v1.5（512 维，中文）
  路径: D:\\models\\bge-small-zh-v1.5（可被配置覆盖）

优势（相对 Ollama 批量）:
  - 无 11434 网络请求，不会 502 / hang
  - 批量 encode 快
  - 数据不出本机

接口:
  - embed(text) -> list[float]
  - embed_batch(texts, batch_size=64) -> list[list[float] | None]
  - verify_model() -> (ok, msg, dim)
  - aembed_batch(texts, batch_size=64) -> 异步封装（to_thread）
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# 禁用 HF 在线检查（纯本地模型）
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# 可由 settings 注入；此处为默认值（与数据分析一致）
DEFAULT_MODEL_PATH = r"D:\models\bge-small-zh-v1.5"
DEFAULT_EMBED_DIM = 512
DEFAULT_DEVICE = os.environ.get("NOVELMIND_EMBED_DEVICE", "cuda")

_MODEL = None
_MODEL_PATH_LOADED: str | None = None
_MODEL_DEVICE: str | None = None


def _resolve_path(model_path: str | None = None) -> str:
    path = (
        model_path
        or os.environ.get("NOVELMIND_EMBEDDING_MODEL_PATH")
        or DEFAULT_MODEL_PATH
    )
    return path


def _get_model(model_path: str | None = None, device: str | None = None):
    """懒加载模型单例。同路径复用（含 cuda→cpu 回退后不再反复加载）。"""
    global _MODEL, _MODEL_PATH_LOADED, _MODEL_DEVICE
    path = _resolve_path(model_path)
    # 运行时读 env，避免 import 时写死；settings 可 setdefault 覆盖
    requested = (
        device
        or os.environ.get("NOVELMIND_EMBED_DEVICE")
        or os.environ.get("NOVELMIND_EMBEDDING_DEVICE")
        or DEFAULT_DEVICE
    )
    # 同一路径已加载则直接复用（回退后 _MODEL_DEVICE=cpu，而 requested 仍是 cuda）
    if _MODEL is not None and _MODEL_PATH_LOADED == path:
        return _MODEL
    if not os.path.isdir(path):
        raise FileNotFoundError(
            f"本地 embedding 模型目录不存在: {path}。"
            "请确认已放置 bge-small-zh-v1.5，或设置 NOVELMIND_EMBEDDING_MODEL_PATH"
        )
    from sentence_transformers import SentenceTransformer

    try:
        _MODEL = SentenceTransformer(path, device=requested)
        _MODEL_DEVICE = requested
        logger.info("已加载本地 embedding 模型 path=%s device=%s", path, requested)
    except Exception as e:
        logger.warning("GPU/指定设备加载失败 (%s)，回退 CPU: %s", requested, e)
        _MODEL = SentenceTransformer(path, device="cpu")
        _MODEL_DEVICE = "cpu"
        logger.info("已加载本地 embedding 模型 path=%s device=cpu", path)
    _MODEL_PATH_LOADED = path
    return _MODEL


def embed(text: str, model_path: str | None = None) -> list[float]:
    """单条文本向量化。"""
    if not text or not str(text).strip():
        raise ValueError("空文本无法 embedding")
    model = _get_model(model_path)
    vec = model.encode([text], batch_size=1)[0]
    return vec.tolist()


def embed_batch(
    texts: list[str],
    batch_size: int = 64,
    model_path: str | None = None,
) -> list[Optional[list[float]]]:
    """批量向量化。空文本位置返回 None。"""
    if not texts:
        return []
    model = _get_model(model_path)
    results: list[Optional[list[float]]] = [None] * len(texts)
    indices_ok: list[int] = []
    clean_texts: list[str] = []
    for i, t in enumerate(texts):
        c = (t or "").strip()
        if c:
            indices_ok.append(i)
            clean_texts.append(c)
    if not clean_texts:
        return results
    embs = model.encode(clean_texts, batch_size=batch_size, show_progress_bar=False)
    for idx, emb in zip(indices_ok, embs):
        results[idx] = emb.tolist()
    return results


async def aembed_batch(
    texts: list[str],
    batch_size: int = 64,
    model_path: str | None = None,
) -> list[list[float]]:
    """
    异步批量向量化（线程池执行同步 encode）。

    空文本用零向量占位会污染检索，因此空文本直接报错。
    """

    def _run() -> list[list[float]]:
        out = embed_batch(texts, batch_size=batch_size, model_path=model_path)
        vectors: list[list[float]] = []
        for i, v in enumerate(out):
            if v is None:
                raise ValueError(f"第 {i} 条文本为空，无法 embedding")
            vectors.append(v)
        return vectors

    return await asyncio.to_thread(_run)


def verify_model(model_path: str | None = None) -> tuple[bool, str, Optional[int]]:
    """验证模型可用性。返回 (可用, 说明, 维度)。"""
    try:
        path = _resolve_path(model_path)
        if not os.path.isdir(path):
            return False, f"模型目录不存在: {path}", None
        vec = embed("连通性测试", model_path=path)
        return True, f"ok path={path} device={_MODEL_DEVICE}", len(vec)
    except Exception as e:
        return False, str(e), None
