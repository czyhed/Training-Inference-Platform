"""Request-scoped identity shared by HTTP routes and record writers.

The application gateway injects ``X-Control-User-Id`` for every request.
The HTTP middleware decodes that header once (the workbench sends it URL
encoded, for example ``password%3Axshipai``) and stores the result in this
context variable.  Route modules use :func:`current_identity_id` when they
write an owner or audit field, so every record carries the same server-side
identity instead of a browser supplied value or a hard-coded placeholder.

Local development without the gateway may still fall back to ``X-User-Id``;
that fallback belongs to the middleware, never to this module.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping
from urllib.parse import unquote


request_identity: ContextVar[dict[str, Any] | None] = ContextVar(
    "request_identity", default=None
)

DEFAULT_IDENTITY = "平台管理员"


def decode_identity_header(value: str | None) -> str:
    """Decode the URL-encoded identity value injected by the gateway."""
    return unquote(value or "").strip()


def identity_from_headers(headers: Mapping[str, str]) -> dict[str, Any] | None:
    """Build the canonical identity dict from trusted gateway headers."""
    user_id = decode_identity_header(headers.get("x-control-user-id"))
    username = decode_identity_header(headers.get("x-control-username"))
    if not user_id and not username:
        return None
    roles = (headers.get("x-control-roles") or "").strip()
    return {
        "id": user_id or username,
        "uid": user_id or username,
        "sub": user_id or username,
        "name": username or user_id,
        "roles": [role for role in roles.split(",") if role],
    }


def current_identity() -> dict[str, Any] | None:
    return request_identity.get()


def current_identity_id(default: str = DEFAULT_IDENTITY) -> str:
    """Return the decoded identity used for owner/audit fields."""
    user = request_identity.get() or {}
    return str(
        user.get("sub")
        or user.get("uid")
        or user.get("id")
        or user.get("name")
        or default
    )
