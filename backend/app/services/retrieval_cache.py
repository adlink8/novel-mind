"""RAG 检索结果缓存。

默认进程内 dict；设置 NOVELMIND_REDIS_URL 时走 Redis。
这是焦点 JD「缓存技术（Redis）」的最小可运行实现：同一查询命中缓存，
避免重复 embedding + 向量检索。分支不合并 master。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any


def _now() -> float:
    return time.monotonic()


class RetrievalCache:
    def __init__(self, redis_client: Any | None = None, ttl_s: int = 60):
        self._redis = redis_client
        self._mem: dict[str, tuple[float, str]] = {}
        self.ttl_s = ttl_s
        self.hits = 0
        self.misses = 0

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    def key(self, novel_id: int, query: str, top_k: int) -> str:
        raw = f"{int(novel_id)}|{int(top_k)}|{query.strip()}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
        return f"nm:rag:{digest}"

    def get(self, novel_id: int, query: str, top_k: int) -> list[dict[str, Any]] | None:
        cache_key = self.key(novel_id, query, top_k)
        payload = self._load(cache_key)
        if payload is None:
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def set(self, novel_id: int, query: str, top_k: int, value: list[dict[str, Any]]) -> None:
        cache_key = self.key(novel_id, query, top_k)
        blob = json.dumps(value, ensure_ascii=False)
        if self._redis is not None:
            self._redis.setex(cache_key, self.ttl_s, blob)
            return
        self._mem[cache_key] = (_now() + self.ttl_s, blob)

    def _load(self, cache_key: str) -> list[dict[str, Any]] | None:
        if self._redis is not None:
            raw = self._redis.get(cache_key)
            if not raw:
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        item = self._mem.get(cache_key)
        if not item:
            return None
        expires, blob = item
        if expires < _now():
            self._mem.pop(cache_key, None)
            return None
        return json.loads(blob)

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) if total else 0.0
        return {
            "backend": self.backend,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(hit_rate, 4),
        }


def connect_redis(url: str | None = None) -> Any | None:
    url = url if url is not None else os.getenv("NOVELMIND_REDIS_URL", "")
    if not url:
        return None
    import redis  # optional runtime dependency

    return redis.Redis.from_url(url, decode_responses=True)


retrieval_cache = RetrievalCache(redis_client=connect_redis())
