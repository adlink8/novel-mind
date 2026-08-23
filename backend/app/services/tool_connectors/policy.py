"""Fail-closed URL, schema, and byte-limit policy for restricted Tools."""

from __future__ import annotations

import ipaddress
import json
import socket
from urllib.parse import urlsplit


MAX_TIMEOUT_SECONDS = 10.0
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024


class ConnectorPolicyError(ValueError):
    pass


def validate_target_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ConnectorPolicyError("Tool URL must use HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ConnectorPolicyError("Tool URL must contain a host and no userinfo")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"}:
        raise ConnectorPolicyError("localhost targets are forbidden")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    ):
        raise ConnectorPolicyError("private or local network targets are forbidden")


def validate_resolved_host(host: str, *, resolver=socket.getaddrinfo) -> None:
    """Reject DNS answers that could turn a public hostname into an SSRF target."""
    try:
        answers = resolver(host, None)
    except OSError as exc:
        raise ConnectorPolicyError("Tool host could not be resolved") from exc
    for answer in answers:
        address = ipaddress.ip_address(answer[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_unspecified
            or address.is_multicast
        ):
            raise ConnectorPolicyError(
                "Tool host resolves to a private or local address"
            )


def validate_json_schema(schema: dict) -> None:
    if not isinstance(schema, dict) or schema.get("type") not in {None, "object"}:
        raise ConnectorPolicyError("Tool schemas must describe JSON objects")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or any(
        not isinstance(key, str) for key in properties
    ):
        raise ConnectorPolicyError("Tool schema properties must be an object")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(key not in properties for key in required):
        raise ConnectorPolicyError(
            "Tool schema required fields must be declared properties"
        )


def validate_json_payload(payload: dict, schema: dict) -> None:
    validate_json_schema(schema)
    properties = schema.get("properties", {})
    missing = [key for key in schema.get("required", []) if key not in payload]
    if missing:
        raise ConnectorPolicyError(
            f"Tool request is missing required fields: {missing}"
        )
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(payload) - set(properties))
        if unknown:
            raise ConnectorPolicyError(
                f"Tool request contains unknown fields: {unknown}"
            )
    for key, rule in properties.items():
        if key not in payload or not isinstance(rule, dict) or "type" not in rule:
            continue
        expected = rule["type"]
        value = payload[key]
        valid = {
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "null": value is None,
        }.get(expected, True)
        if not valid:
            raise ConnectorPolicyError(f"Tool request field {key!r} has the wrong type")


def json_bytes(payload: dict) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise ConnectorPolicyError("Tool request exceeds the byte limit")
    return raw
