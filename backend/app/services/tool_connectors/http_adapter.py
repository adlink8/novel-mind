"""HTTP adapter seam. Production network transport is intentionally not wired here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HttpAdapterResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    final_url: str


class HttpToolAdapter(Protocol):
    async def request(self, *, method: str, url: str, body: bytes, timeout: float, max_response_bytes: int) -> HttpAdapterResponse: ...


class FakeHttpAdapter:
    """Deterministic adapter used by tests and the local dry-run API."""

    def __init__(self, response: HttpAdapterResponse):
        self.response = response
        self.calls: list[dict] = []

    async def request(self, *, method: str, url: str, body: bytes, timeout: float, max_response_bytes: int) -> HttpAdapterResponse:
        self.calls.append({"method": method, "url": url, "body": body, "timeout": timeout, "max_response_bytes": max_response_bytes})
        return self.response

