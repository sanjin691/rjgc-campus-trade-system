"""HTTP 无关的基础类型：请求、响应、统一格式、路由注册与 Cookie 工具。

这些是“框架层”的东西：任何业务模块都可以 import，但它们本身不认识用户或商品。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable

from security import SESSION_COOKIE

#: 是否给 Cookie 加 Secure 标记（生产环境走 HTTPS 时设为 1）
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"


def parse_cookies(header_value: str) -> dict[str, str]:
    """把 Cookie 请求头解析成字典，忽略空键。"""
    cookies: dict[str, str] = {}
    for part in header_value.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


@dataclass
class Request:
    """一次请求的完整上下文，由 HTTP 层填充、业务层只读。"""

    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    raw_body: bytes
    ip: str
    cookies: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    user: dict | None = None
    session: dict | None = None
    token_hash: str | None = None
    invalid_session_cookie: bool = False
    body: dict = field(default_factory=dict)


@dataclass
class Response:
    """一次响应的完整描述，由业务层构造、HTTP 层输出。"""

    status: int = 200
    payload: dict | None = None
    headers: list[tuple[str, str]] = field(default_factory=list)
    content: bytes | None = None
    content_type: str = 'application/json; charset=utf-8'


def ok(data=None, message: str | None = None, status: int = 200, headers=None) -> Response:
    """构造成功响应：{ok: True, message?, data}。"""
    payload: dict = {"ok": True}
    if message:
        payload["message"] = message
    payload["data"] = data
    return Response(status=status, payload=payload, headers=list(headers or []))


def error(status: int, message: str, errors: dict[str, str] | None = None) -> Response:
    """构造失败响应：{ok: False, message, errors?}。"""
    payload: dict = {"ok": False, "message": message}
    if errors:
        payload["errors"] = errors
    return Response(status=status, payload=payload)


@dataclass
class Route:
    """一条路由：方法、正则、处理函数、是否需要登录/管理员。"""

    method: str
    pattern: re.Pattern[str]
    handler: Callable[..., Response]
    auth: bool = False
    admin_only: bool = False


ROUTES: list[Route] = []


def route(method: str, pattern: str, auth: bool = False, admin_only: bool = False):
    """路由装饰器：把方法、正则与处理函数登记进 ROUTES。"""
    compiled = re.compile(pattern)

    def decorator(handler):
        """登记路由并原样返回处理函数。"""
        # 路由在 import 时登记进全局表，_route_request 按正则匹配
        ROUTES.append(Route(method, compiled, handler, auth, admin_only))
        return handler

    return decorator


def session_cookie(token: str, max_age: int | None) -> str:
    """构造会话 Cookie（HttpOnly、SameSite=Lax，可选 Secure）。"""
    # HttpOnly：JS 读不到令牌；SameSite=Lax：跨站请求不携带
    parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts)


def clear_session_cookie() -> str:
    """构造用于清除会话的 Cookie。"""
    parts = [
        f"{SESSION_COOKIE}=",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
    ]
    if COOKIE_SECURE:
        parts.append("Secure")
    return "; ".join(parts)
