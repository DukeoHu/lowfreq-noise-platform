"""最小鉴权与操作人识别。

内网共享口令即可：配置 LOWFREQ_ACCESS_TOKEN 后，除健康检查、接口文档与根路径外，
HTTP 请求必须携带口令（Authorization: Bearer / X-Access-Token / ?token=），
WebSocket 由路由从 ?token= 校验。未配置口令时鉴权关闭，行为与之前一致。

操作人来自 X-Actor 请求头（WebSocket 为 ?actor=），写入审计日志；未提供时记为 local。
"""

from __future__ import annotations

import hmac
from contextvars import ContextVar
from urllib.parse import unquote

from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ACTOR_HEADER = "x-actor"
ACTOR_DEFAULT = "local"
ACTOR_MAX_LENGTH = 64

# 健康检查用于探活，文档与根路径用于排障，均不要求口令
PUBLIC_PATHS = {"/", "/api/health", "/docs", "/redoc", "/openapi.json"}

_actor: ContextVar[str] = ContextVar("lowfreq_actor", default=ACTOR_DEFAULT)


def sanitize_actor(value: object) -> str:
    """HTTP 头只允许 ASCII，中文操作人由前端做百分号编码后再解码。"""
    text = value.strip() if isinstance(value, str) else ""
    text = unquote(text).strip()
    text = "".join(ch for ch in text if ch.isprintable())[:ACTOR_MAX_LENGTH]
    return text or ACTOR_DEFAULT


def current_actor() -> str:
    return _actor.get()


def token_matches(supplied: str, expected: str) -> bool:
    """口令为空表示服务端未开启鉴权。"""
    if not expected:
        return True
    return hmac.compare_digest(supplied or "", expected)


def extract_token(headers: Headers, query: QueryParams) -> str:
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    header_token = headers.get("x-access-token", "").strip()
    if header_token:
        return header_token
    return query.get("token", "").strip()


class AccessTokenMiddleware:
    """校验共享口令，并把操作人放进 ContextVar 供审计读取。"""

    def __init__(self, app: ASGIApp, token: str = "") -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        query = QueryParams(scope.get("query_string", b""))
        if scope["type"] == "websocket":
            actor = sanitize_actor(query.get("actor") or headers.get(ACTOR_HEADER))
        else:
            actor = sanitize_actor(headers.get(ACTOR_HEADER))

        reset = _actor.set(actor)
        try:
            if (
                self.token
                and scope["type"] == "http"
                and scope.get("method") != "OPTIONS"
                and scope.get("path") not in PUBLIC_PATHS
                and not token_matches(extract_token(headers, query), self.token)
            ):
                response = JSONResponse(
                    {"detail": "未授权：请在「隐私与校准」页填写访问口令"},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
        finally:
            _actor.reset(reset)
