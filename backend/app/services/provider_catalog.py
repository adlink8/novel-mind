"""Provider-specific model catalog adapters behind one internal contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ALIASES = {
    "openai": "openai",
    "custom": "custom",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    "ollama": "ollama",
}

_PROVIDER_PROFILES = (
    {
        "id": "openai",
        "label": "OpenAI",
        "default_base_url": "https://api.openai.com/v1",
        "credential_kind": "api_key",
        "credential_required": True,
    },
    {
        "id": "anthropic",
        "label": "Anthropic",
        "default_base_url": "https://api.anthropic.com/v1",
        "credential_kind": "api_key",
        "credential_required": True,
    },
    {
        "id": "gemini",
        "label": "Google AI Studio",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "credential_kind": "api_key",
        "credential_required": True,
    },
    {
        "id": "ollama",
        "label": "Ollama",
        "default_base_url": "http://127.0.0.1:11434",
        "credential_kind": "none",
        "credential_required": False,
    },
    {
        "id": "custom",
        "label": "OpenAI 兼容服务",
        "default_base_url": None,
        "credential_kind": "api_key",
        "credential_required": False,
    },
)


def provider_profiles() -> tuple[dict[str, Any], ...]:
    """Return settings metadata from the same authority as protocol adapters."""
    return _PROVIDER_PROFILES


@dataclass(frozen=True)
class ProviderCatalogRequest:
    provider: str
    url: str
    headers: dict[str, str]


def canonical_provider(provider: str) -> str:
    """Return the supported canonical provider or reject unknown protocols."""
    normalized = (provider or "").strip().lower()
    try:
        return _ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported model provider: {provider}") from exc


def _append_path(base_url: str, suffix: str) -> str:
    base = base_url.rstrip("/")
    normalized_suffix = "/" + suffix.strip("/")
    if base.lower().endswith(normalized_suffix.lower()):
        return base
    return base + normalized_suffix


def build_catalog_request(
    provider: str,
    *,
    base_url: str,
    api_key: str | None,
) -> ProviderCatalogRequest:
    """Build the provider's model-list request without making network calls."""
    canonical = canonical_provider(provider)
    headers = {"Accept": "application/json"}

    if canonical == "ollama":
        url = _append_path(base_url, "api/tags")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        url = _append_path(base_url, "models")
        if api_key:
            if canonical == "anthropic":
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
            elif canonical == "gemini":
                headers["x-goog-api-key"] = api_key
            else:
                headers["Authorization"] = f"Bearer {api_key}"

    return ProviderCatalogRequest(provider=canonical, url=url, headers=headers)


def _clean_model_id(value: Any, *, prefixes: tuple[str, ...] = ()) -> str:
    model_id = str(value or "").strip()
    for prefix in prefixes:
        if model_id.startswith(prefix):
            model_id = model_id[len(prefix) :]
            break
    return model_id


def normalize_catalog_response(
    provider: str,
    payload: object,
) -> list[dict[str, str]]:
    """Normalize OpenAI, Claude, Gemini and Ollama list responses."""
    canonical = canonical_provider(provider)
    if not isinstance(payload, dict):
        return []

    if canonical in {"openai", "custom", "anthropic"}:
        rows = payload.get("data")
    else:
        rows = payload.get("models")
    if not isinstance(rows, list):
        return []

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if canonical == "gemini":
            methods = row.get("supportedGenerationMethods")
            if isinstance(methods, list) and "generateContent" not in methods:
                continue
            model_id = _clean_model_id(
                row.get("baseModelId") or row.get("name"), prefixes=("models/",)
            )
            name = str(row.get("displayName") or model_id).strip()
        elif canonical == "ollama":
            model_id = _clean_model_id(row.get("model") or row.get("name"))
            name = str(row.get("name") or model_id).strip()
        else:
            model_id = _clean_model_id(row.get("id") or row.get("name"))
            name = str(
                row.get("display_name") or row.get("displayName") or model_id
            ).strip()

        if not model_id or len(model_id) > 100 or model_id in seen:
            continue
        seen.add(model_id)
        normalized.append({"id": model_id, "name": name or model_id})
    return normalized
