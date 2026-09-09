"""RetrievalCache 单元测试：内存后端可独立跑，不依赖 Redis 进程。"""

from __future__ import annotations

import time

import pytest

from app.services.retrieval_cache import RetrievalCache

pytestmark = pytest.mark.unit


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key: str):
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str):
        assert ttl > 0
        self.store[key] = value


def test_memory_miss_then_hit():
    cache = RetrievalCache(ttl_s=30)
    assert cache.get(1, "龙影", 5) is None
    payload = [{"chunk_id": 9, "score": 0.81, "content": "龙影出鞘"}]
    cache.set(1, "龙影", 5, payload)
    assert cache.get(1, "龙影", 5) == payload
    assert cache.stats()["hits"] == 1
    assert cache.stats()["misses"] == 1
    assert cache.backend == "memory"


def test_ttl_expires():
    cache = RetrievalCache(ttl_s=0)
    cache.set(1, "q", 3, [{"chunk_id": 1}])
    time.sleep(0.01)
    cache.ttl_s = 0
    cache._mem[cache.key(1, "q", 3)] = (time.monotonic() - 1, "[]")
    assert cache.get(1, "q", 3) is None


def test_redis_backend_roundtrip():
    fake = FakeRedis()
    cache = RetrievalCache(redis_client=fake, ttl_s=15)
    cache.set(2, "query", 8, [{"chunk_id": 2, "score": 0.5}])
    assert cache.backend == "redis"
    got = cache.get(2, "query", 8)
    assert got == [{"chunk_id": 2, "score": 0.5}]
    assert "nm:rag:" in next(iter(fake.store))


def test_hit_faster_than_miss_path(monkeypatch):
    cache = RetrievalCache(ttl_s=60)
    rows = [{"chunk_id": i, "score": 1.0 - i * 0.01} for i in range(20)]

    def slow_fetch():
        time.sleep(0.02)
        return rows

    t0 = time.perf_counter()
    miss = cache.get(1, "bench", 20)
    if miss is None:
        cache.set(1, "bench", 20, slow_fetch())
    miss_ms = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    hit = cache.get(1, "bench", 20)
    hit_ms = (time.perf_counter() - t1) * 1000
    assert hit == rows
    assert hit_ms < miss_ms
    assert hit_ms < 5
