"""Owner identity comes only through the authenticated private dashboard proxy."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, WebSocket
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.android_config import AndroidConfig


class ControlAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    bridge_token: SecretStr
    owner_login: str = Field(min_length=3)
    public_origin: str
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    timezone: str = "UTC"
    hermes_url: str | None = None

    @field_validator("trusted_proxy_ips")
    @classmethod
    def exact_private_proxies(cls, values: list[str]) -> list[str]:
        for value in values:
            address = ipaddress.ip_address(value)
            if not address.is_private or address.is_unspecified or address.is_multicast:
                raise ValueError("Trust only exact private proxy addresses")
        return values

    @field_validator("bridge_token")
    @classmethod
    def strong_token(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value.get_secret_value()):
            raise ValueError("Use a URL-safe dashboard bridge credential of at least 32 characters")
        return value

    @field_validator("owner_login")
    @classmethod
    def proxy_safe_login(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._+-]{2,254}", value):
            raise ValueError("Use the exact Tailscale login without spaces or control characters")
        return value

    @field_validator("public_origin", "hermes_url")
    @classmethod
    def private_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        u = urlsplit(value)
        if (
            u.scheme != "https"
            or not u.hostname
            or not u.hostname.endswith(".ts.net")
            or u.username
            or u.password
            or u.query
            or u.fragment
        ):
            raise ValueError("Use a private Tailscale HTTPS address")
        return value.rstrip("/")

    @field_validator("public_origin")
    @classmethod
    def origin_without_path(cls, value: str) -> str:
        if urlsplit(value).path not in {"", "/"}:
            raise ValueError("The dashboard origin must not include a path")
        return value


def read_access(config: AndroidConfig) -> ControlAccess | None:
    if config.dashboard_config_file is None:
        return None
    return ControlAccess.model_validate(json.loads(config.dashboard_config_file.read_text()))


def authenticate(connection: Request | WebSocket) -> tuple[Any, ControlAccess]:
    service = getattr(connection.app.state, "call_service", None)
    access = read_access(service.config) if service and hasattr(service, "control") else None
    if access is None:
        raise HTTPException(404, "Control panel is not enabled")
    supplied = connection.headers.get("x-control-bridge", "")
    login = connection.headers.get("tailscale-user-login", "")
    if not secrets.compare_digest(
        supplied.encode(), access.bridge_token.get_secret_value().encode()
    ):
        raise HTTPException(401, "Use the private dashboard address")
    if not secrets.compare_digest(
        login.casefold().encode(), access.owner_login.casefold().encode()
    ):
        raise HTTPException(403, "This dashboard belongs to another Tailscale user")
    assert service is not None
    return service.control, access


def csrf_token(access: ControlAccess) -> str:
    body = f"{int(time.time())}.{secrets.token_hex(16)}"
    signature = hmac.new(
        access.bridge_token.get_secret_value().encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    return body + "." + signature


def valid_csrf_cookie(token: str, access: ControlAccess) -> bool:
    try:
        timestamp, nonce, signature = token.split(".")
        age = time.time() - int(timestamp)
        expected = hmac.new(
            access.bridge_token.get_secret_value().encode(),
            f"{timestamp}.{nonce}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return 0 <= age < 43200 and secrets.compare_digest(signature, expected)
    except (ValueError, TypeError):
        return False


def check_csrf(
    connection: Request | WebSocket, access: ControlAccess, *, websocket: bool = False
) -> None:
    if connection.headers.get("origin") != access.public_origin:
        raise HTTPException(403, "Open this action from the private dashboard")
    token = connection.cookies.get("__Host-calling-csrf", "")
    if not valid_csrf_cookie(token, access) or (
        not websocket
        and not secrets.compare_digest(token, connection.headers.get("x-csrf-token", ""))
    ):
        raise HTTPException(403, "Your dashboard session expired. Reload the page.")


def require_control(request: Request, response: Response) -> Any:
    response.headers["Cache-Control"] = "no-store"
    control, access = authenticate(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_csrf(request, access)
    return control
