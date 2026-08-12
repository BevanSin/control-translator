"""Loopback-only network guards and ephemeral local session token auth.

The local API is designed to be safe to run on a developer workstation with no
external exposure: it binds to a loopback address by default, rejects any
request whose ``Host`` header does not name an approved loopback host, applies
a restrictive CORS/origin policy, and requires a per-process ephemeral session
token for every state-changing or otherwise sensitive endpoint. None of this
replaces real authentication for a multi-user or networked deployment — it
exists solely to keep a single local user's own workspace from being reachable
by an arbitrary web page or process on the same machine.
"""
from __future__ import annotations

import hmac
import secrets

from fastapi import Header, HTTPException, Request

SESSION_TOKEN_HEADER = "X-CT-Session-Token"

# Hosts (without port) an incoming request's Host header is allowed to name.
# Anything else is rejected before any route handler runs, closing off DNS
# rebinding attacks against the loopback listener.
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Origins allowed to make browser-initiated cross-origin requests. Left empty
# by default: same-origin tools (curl, a co-located desktop shell) never send
# an Origin header restricted by CORS, while an empty allow-list means no
# third-party site can read a response even if it can trigger the request.
DEFAULT_ALLOWED_ORIGINS: frozenset[str] = frozenset()


def generate_session_token() -> str:
    """Generate a fresh, high-entropy ephemeral token for this process only."""
    return secrets.token_urlsafe(32)


class SessionTokenAuth:
    """Dependency that requires a valid ``X-CT-Session-Token`` header."""

    def __init__(self, token: str):
        self._token = token

    def __call__(self, x_ct_session_token: str | None = Header(default=None, alias=SESSION_TOKEN_HEADER)) -> None:
        if not x_ct_session_token or not hmac.compare_digest(x_ct_session_token, self._token):
            raise HTTPException(status_code=401, detail="Missing or invalid session token.")


def _hostname_from_host_header(host_header: str) -> str:
    """Strip an optional ``:port`` suffix, respecting bracketed IPv6 literals."""
    if host_header.startswith("["):
        end = host_header.find("]")
        if end == -1:
            return host_header
        return host_header[: end + 1]
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header


def validate_host_header(request: Request, allowed_hosts: frozenset[str] = ALLOWED_HOSTS) -> None:
    """Reject requests whose Host header does not name an approved loopback host."""
    hostname = _hostname_from_host_header(request.headers.get("host", ""))
    if hostname not in allowed_hosts:
        raise HTTPException(status_code=400, detail="Host not permitted.")
