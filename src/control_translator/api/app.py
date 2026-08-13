"""FastAPI application factory and process entrypoint for the local API.

Design goals (see the parent issue for full context):

- Loopback-only by default: the app validates the incoming ``Host`` header on
  every request and the entrypoint binds uvicorn to ``127.0.0.1`` unless a
  caller explicitly overrides it.
- No implicit trust of browser origins: CORS defaults to an empty allow-list.
- Ephemeral local session token: generated fresh per process, required by
  every sensitive/state-changing route (see ``security.py``/``routes.py``),
  and never persisted to disk.
- Sanitized errors: every domain exception is mapped to an allow-listed HTTP
  response (see ``errors.py``); nothing from ``str(exc)`` is ever returned.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from ..application import ControlTranslatorService
from ..projects import MAX_SOURCE_BYTES
from . import routes
from .errors import DOMAIN_EXCEPTIONS, domain_error_handler, validation_error_handler
from .security import (
    ALLOWED_HOSTS,
    DEFAULT_ALLOWED_ORIGINS,
    SESSION_TOKEN_HEADER,
    SessionTokenAuth,
    generate_session_token,
    validate_host_header,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8756

# Refuse any request body larger than this before it reaches a route handler,
# so a misbehaving or malicious client cannot exhaust memory with an oversized
# payload against a local-only service.
MIN_UPLOAD_BODY_BYTES = ((MAX_SOURCE_BYTES + 2) // 3) * 4
MAX_BODY_BYTES = MIN_UPLOAD_BODY_BYTES + (256 * 1024)


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, max_bytes: int):
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"error": {"code": "bad_request",
                                                                         "message": "Invalid Content-Length."}})
            if declared > self._max_bytes:
                return JSONResponse(status_code=413, content={"error": {"code": "payload_too_large",
                                                                         "message": "Request body too large."}})
        body = await request.body()
        if len(body) > self._max_bytes:
            return JSONResponse(status_code=413, content={"error": {"code": "payload_too_large",
                                                                     "message": "Request body too large."}})
        return await call_next(request)


class _HostValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, allowed_hosts: frozenset[str]):
        super().__init__(app)
        self._allowed_hosts = allowed_hosts

    async def dispatch(self, request: Request, call_next):
        try:
            validate_host_header(request, self._allowed_hosts)
        except Exception:  # noqa: BLE001 - normalize to the same sanitized shape
            return JSONResponse(status_code=400, content={"error": {"code": "invalid_host",
                                                                     "message": "Host not permitted."}})
        return await call_next(request)


def create_app(
    *,
    service: ControlTranslatorService | None = None,
    session_token: str | None = None,
    allowed_hosts: frozenset[str] = ALLOWED_HOSTS,
    allowed_origins: frozenset[str] = DEFAULT_ALLOWED_ORIGINS,
    max_body_bytes: int = MAX_BODY_BYTES,
    static_assets: str | Path | None = None,
) -> FastAPI:
    """Build the local API application.

    Returns the app and, as ``app.state.session_token``, the token callers
    must present in the ``X-CT-Session-Token`` header for sensitive routes.
    """
    token = session_token or generate_session_token()
    app_service = service or ControlTranslatorService()

    app = FastAPI(
        title="control-translator local API",
        version="v1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.session_token = token

    app.add_middleware(_BodySizeLimitMiddleware, max_bytes=max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-CT-Session-Token"],
    )
    app.add_middleware(_HostValidationMiddleware, allowed_hosts=allowed_hosts)

    for exc_type in DOMAIN_EXCEPTIONS:
        app.add_exception_handler(exc_type, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, domain_error_handler)

    require_token = SessionTokenAuth(token)
    app.include_router(routes.build_router(app_service, require_token))
    if static_assets is not None:
        app.mount("/", StaticFiles(directory=str(static_assets), html=True), name="dashboard")

    return app


def main() -> None:
    """Entrypoint for the ``ct-api`` console script (and a future combined launcher)."""
    import argparse

    try:
        import uvicorn
    except ModuleNotFoundError:
        print("The 'api' extra is required to run ct-api: pip install 'control-translator[api]'",
              file=sys.stderr)
        raise SystemExit(1) from None

    parser = argparse.ArgumentParser(prog="ct-api", description="control-translator local API")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Bind address (default: {DEFAULT_HOST}; loopback only is strongly recommended)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    app = create_app()
    print(f"Local session token (send as {SESSION_TOKEN_HEADER}): {app.state.session_token}", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
