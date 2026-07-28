"""Validation for user-configurable outbound HTTP endpoints."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException

from app.config import settings


async def validate_ai_base_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlsplit(url.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise HTTPException(
            status_code=400, detail="自定义 API 地址必须是有效的 HTTP(S) URL"
        )
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=400, detail="自定义 API 地址不能包含用户名或密码"
        )
    if parsed.fragment:
        raise HTTPException(
            status_code=400, detail="自定义 API 地址不能包含 URL fragment"
        )

    allowed_hosts = settings.allowed_ai_hosts | settings.allowed_private_ai_hosts
    if hostname not in allowed_hosts:
        raise HTTPException(status_code=400, detail="自定义 API 主机未列入服务端白名单")
    if parsed.scheme != "https" and hostname not in settings.allowed_private_ai_hosts:
        raise HTTPException(status_code=400, detail="公网自定义 API 地址必须使用 HTTPS")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        literal_ip = ipaddress.ip_address(hostname)
        addresses = {literal_ip}
    except ValueError:
        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise HTTPException(
                status_code=400, detail="自定义 API 主机无法解析"
            ) from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}

    if not addresses:
        raise HTTPException(status_code=400, detail="自定义 API 主机没有可用地址")
    if hostname not in settings.allowed_private_ai_hosts and any(
        not address.is_global for address in addresses
    ):
        raise HTTPException(status_code=400, detail="自定义 API 地址解析到非公网地址")

    normalized_netloc = hostname
    if parsed.port:
        normalized_netloc = (
            f"[{hostname}]:{parsed.port}"
            if ":" in hostname
            else f"{hostname}:{parsed.port}"
        )
    return urlunsplit(
        (parsed.scheme, normalized_netloc, parsed.path.rstrip("/"), parsed.query, "")
    )
