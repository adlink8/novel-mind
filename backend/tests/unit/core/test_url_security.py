"""Unit tests for app.core.url_security.validate_ai_base_url.

Covers every rejection branch and the normalization return path with a
mocked DNS resolver so no external service is contacted.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

from app.config import settings
from app.core.url_security import validate_ai_base_url


@pytest.fixture(autouse=True)
def _allowed_hosts(monkeypatch):
    """Tight, deterministic whitelist independent of the environment .env."""
    monkeypatch.setattr(settings, "ai_allowed_hosts", "api.example.com,api.other.com")
    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "localhost,127.0.0.1")
    monkeypatch.setattr(settings, "debug", False)


async def test_empty_url_returns_none():
    assert await validate_ai_base_url("") is None
    assert await validate_ai_base_url(None) is None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://api.example.com/v1",
        "ws://api.example.com/v1",
        "file:///etc/passwd",
        "api.example.com/v1",  # no scheme → hostname still parsed, but scheme missing
        "http://",  # no hostname
    ],
)
async def test_rejects_non_http_scheme_or_missing_host(url):
    with pytest.raises(HTTPException) as exc:
        await validate_ai_base_url(url)
    assert exc.value.status_code == 400


async def test_rejects_username_password():
    with pytest.raises(HTTPException) as exc:
        await validate_ai_base_url("https://user:pass@api.example.com/v1")
    assert exc.value.status_code == 400


async def test_rejects_fragment():
    with pytest.raises(HTTPException) as exc:
        await validate_ai_base_url("https://api.example.com/v1#section")
    assert exc.value.status_code == 400


async def test_rejects_host_not_in_whitelist():
    with pytest.raises(HTTPException) as exc:
        await validate_ai_base_url("https://attacker.example/v1")
    assert exc.value.status_code == 400


async def test_rejects_public_http_for_non_private_host():
    # api.example.com is in allowed_ai_hosts but NOT in allowed_private_ai_hosts
    # → plain http to a public host must be rejected.
    with pytest.raises(HTTPException) as exc:
        await validate_ai_base_url("http://api.example.com/v1")
    assert exc.value.status_code == 400


async def test_literal_ip_non_global_rejected(monkeypatch):
    # 10.0.0.5 is a private (non-global) literal IP, and is NOT in the private
    # host list → rejected as resolving to a non-public address.
    monkeypatch.setattr(settings, "ai_allowed_hosts", "api.example.com,10.0.0.5")
    with pytest.raises(HTTPException) as exc:
        await validate_ai_base_url("https://10.0.0.5/v1")
    assert exc.value.status_code == 400


async def test_dns_failure_rejected():
    with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror):
        with pytest.raises(HTTPException) as exc:
            await validate_ai_base_url("https://api.example.com/v1")
        assert exc.value.status_code == 400


async def test_empty_address_set_rejected():
    with patch.object(socket, "getaddrinfo", return_value=[]):
        with pytest.raises(HTTPException) as exc:
            await validate_ai_base_url("https://api.example.com/v1")
        assert exc.value.status_code == 400


async def test_resolved_private_address_for_public_host_rejected():
    # A public host that resolves to a non-global (private) IP is rejected.
    with patch.object(
        socket,
        "getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
    ):
        with pytest.raises(HTTPException) as exc:
            await validate_ai_base_url("https://api.example.com/v1")
        assert exc.value.status_code == 400


async def test_private_host_plain_http_allowed():
    # localhost is in allowed_private_ai_hosts → plain http is permitted.
    with patch.object(
        socket,
        "getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 11434))
        ],
    ):
        result = await validate_ai_base_url("http://localhost:11434/v1/")
        assert result == "http://localhost:11434/v1"


async def test_https_public_host_normalized():
    with patch.object(
        socket,
        "getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    ):
        result = await validate_ai_base_url("https://api.example.com/v1/")
        assert result == "https://api.example.com/v1"


async def test_https_public_host_with_query_preserved():
    with patch.object(
        socket,
        "getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    ):
        result = await validate_ai_base_url("https://api.example.com/v1/?a=1&b=2")
        assert result == "https://api.example.com/v1?a=1&b=2"


async def test_port_preserved_in_netloc():
    with patch.object(
        socket,
        "getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 8443))
        ],
    ):
        result = await validate_ai_base_url("https://api.example.com:8443/v1/")
        assert result == "https://api.example.com:8443/v1"


async def test_literal_global_ip_accepted(monkeypatch):
    # Literal public IP in the allowed list passes without DNS.
    monkeypatch.setattr(settings, "ai_allowed_hosts", "api.example.com,93.184.216.34")
    result = await validate_ai_base_url("https://93.184.216.34/v1/")
    assert result == "https://93.184.216.34/v1"
