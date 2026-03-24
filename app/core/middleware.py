from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


logger = logging.getLogger(__name__)


class BodyTooLarge(Exception):
    pass


class RequestTimeoutMiddleware:
    def __init__(self, app: ASGIApp, *, timeout_seconds: int = 30):
        self.app = app
        self.timeout_seconds = max(int(timeout_seconds), 1)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await asyncio.wait_for(self.app(scope, receive, send), timeout=self.timeout_seconds)
        except TimeoutError:
            response = JSONResponse(status_code=504, content={"detail": "Request timed out"})
            await response(scope, receive, send)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, default_limit: int, webhook_limit: int):
        self.app = app
        self.default_limit = int(default_limit)
        self.webhook_limit = int(webhook_limit)

    def _max_for_path(self, path: str) -> int:
        if path == "/billing/webhook" or path == "/api/v1/billing/webhook":
            return self.webhook_limit
        return self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        max_bytes = self._max_for_path(path)
        headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        content_length_raw = headers.get("content-length")
        if content_length_raw:
            try:
                if int(content_length_raw) > max_bytes:
                    response = JSONResponse(status_code=413, content={"detail": "Request body too large"})
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                received += len(chunk)
                if received > max_bytes:
                    raise BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            response = JSONResponse(status_code=413, content={"detail": "Request body too large"})
            await response(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts_seconds: int = 31_536_000, enable_hsts: bool = False):
        self.app = app
        self.hsts_seconds = hsts_seconds
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"strict-origin-when-cross-origin"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append((b"permissions-policy", b"camera=(), microphone=(), geolocation=()"))
                headers.append((b"cache-control", b"no-store"))
                if self.enable_hsts:
                    value = f"max-age={int(self.hsts_seconds)}; includeSubDomains; preload".encode("latin1")
                    headers.append((b"strict-transport-security", value))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        start = time.perf_counter()
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        status_code_box = {"value": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code_box["value"] = int(message.get("status", 500))
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("latin1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "request_completed request_id=%s method=%s path=%s status=%s duration_ms=%s",
            request_id,
            method,
            path,
            status_code_box["value"],
            elapsed_ms,
        )
