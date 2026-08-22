"""Operational health probes (rag_quality package)."""

from __future__ import annotations

from typing import Any


def default_healthy() -> dict[str, Any]:
    return {
        "ok": True,
        "db": "ok",
        "chroma": "ok",
        "model": "ok",
        "reason": None,
    }


def probe_ollama_health(
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Probe local Ollama; returns health dict (never raises for outage)."""
    try:
        import urllib.request

        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= getattr(resp, "status", 200) < 300:
                return {
                    "ok": True,
                    "db": "skipped",
                    "chroma": "skipped",
                    "model": "ok",
                    "reason": None,
                    "endpoint": base_url,
                }
            return {
                "ok": False,
                "db": "skipped",
                "chroma": "skipped",
                "model": "down",
                "reason": f"ollama status={getattr(resp, 'status', '?')}",
            }
    except Exception as exc:
        return {
            "ok": False,
            "db": "skipped",
            "chroma": "skipped",
            "model": "down",
            "reason": f"ollama unavailable: {type(exc).__name__}: {exc}",
        }
