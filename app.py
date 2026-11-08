"""校园二手商品交易系统 · 用户与商品管理

仅实现指定功能：
- 用户注册
- 用户登录 / 退出
- 用户信息查看与修改
- 商品创建、修改、本人商品查询及图片管理

启动：python app.py
"""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

import goods
from goods_store import GoodsAccessError
import trade
from database import (
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_STUDENT_NO,
    SessionStore,
    UserStore,
    initialize_database,
)
from security import (
    SESSION_COOKIE,
    digest_token,
    hash_password,
    iso_utc,
    new_csrf_token,
    new_session_token,
    session_expiry,
    verify_password,
)
from validators import (
    ValidationError,
    validate_password_change,
    validate_profile_update,
    validate_registration,
    validate_student_no,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DB = BASE_DIR / "data" / "user_management.db"
MAX_BODY_BYTES = 64 * 1024
MAX_GOODS_BODY_BYTES = 9 * 1024 * 1024
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
APP_NAME = "校园二手商品交易系统"
APP_SUBTITLE = "用户与商品管理（V1.4）"

#: 已实现的子系统；商品管理、交易管理见 goods.py / trade.py（本期仅预留位置）。
USER_SUBSYSTEM = {
    "key": "user",
    "name": "用户管理子系统",
    "status": "available",
    "statusLabel": "已实现",
    "summary": "负责注册、登录、退出与个人信息管理，并提供管理员账号管控。",
    "features": ["用户注册", "用户登录", "用户信息管理"],
    "routePrefix": "/api/auth",
    "module": "app.py",
    "plannedSchema": "",
    "dependencies": ["商品管理子系统、交易管理子系统均依赖本子系统的账号与角色"],
}
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


# --------------------------------------------------------------------------- 基础类型


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


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        """保存状态码与错误信息。"""
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- 路由


@dataclass
class Route:
    method: str
    pattern: re.Pattern[str]
    handler: Callable[["Application", Request], Response]
    auth: bool = False
    admin_only: bool = False


ROUTES: list[Route] = []


def route(method: str, pattern: str, auth: bool = False, admin_only: bool = False):
    """路由装饰器：把方法、正则与处理函数登记进 ROUTES。"""
    compiled = re.compile(pattern)

    def decorator(handler):
        """登记路由并原样返回处理函数。"""
        ROUTES.append(Route(method, compiled, handler, auth, admin_only))
        return handler

    return decorator


# --------------------------------------------------------------------------- 登录限流


class LoginThrottle:
    """同一 IP + 账号连续失败 5 次后暂停 10 分钟。"""

    def __init__(self, max_failures: int = 5, window_seconds: int = 600):
        """设置最大失败次数与统计窗口（秒）。"""
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        """返回还需等待的秒数；0 表示当前可尝试登录。"""
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.failures.get(key, []) if now - t < self.window_seconds]
            if len(attempts) >= self.max_failures:
                self.failures[key] = attempts
                return max(1, int(self.window_seconds - (now - attempts[0])))
            if attempts:
                self.failures[key] = attempts
            else:
                self.failures.pop(key, None)
            return 0

    def record_failure(self, key: str) -> None:
        """记录一次登录失败，并清理窗口外的旧记录。"""
        now = time.monotonic()
        with self.lock:
            attempts = [t for t in self.failures.get(key, []) if now - t < self.window_seconds]
            attempts.append(now)
            self.failures[key] = attempts

    def reset(self, key: str) -> None:
        """登录成功后清除该键的失败记录。"""
        with self.lock:
            self.failures.pop(key, None)


# --------------------------------------------------------------------------- 应用


class Application:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        """初始化数据库、两个仓储与登录限流器。"""
        initialize_database(db_path)
        self.users = UserStore(db_path)
        self.sessions = SessionStore(db_path)
        self.throttle = LoginThrottle()
        self.goods = goods.GoodsService(db_path)

    def dispatch(self, request: Request) -> Response:
        """请求入口：认证 → 路由 → 统一异常映射。"""
        request.cookies = parse_cookies(request.headers.get("Cookie", ""))
        self._authenticate(request)

        try:
            response = self._route_request(request)
        except ValidationError as exc:
            response = error(422, exc.message, exc.errors)
        except ApiError as exc:
            response = error(exc.status, exc.message)
        except GoodsAccessError as exc:
            response = error(exc.status, str(exc))
        except sqlite3.IntegrityError:
            response = error(409, "学号或邮箱已存在")
        except Exception:
            traceback.print_exc()
            response = error(500, "服务器内部错误，请稍后再试")

        if response.status == 401 and request.invalid_session_cookie:
            response.headers.append(("Set-Cookie", clear_session_cookie()))
        return response

    def _authenticate(self, request: Request) -> None:
        """按 Cookie 中的令牌恢复登录态；失效时打标记以便清除 Cookie。"""
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return
        token_hash = digest_token(token)
        session = self.sessions.find_session(token_hash)
        if session:
            request.session = session
            request.user = session["user"]
            request.token_hash = token_hash
        else:
            request.invalid_session_cookie = True

    def _route_request(self, request: Request) -> Response:
        """匹配路由、校验来源/内容类型/鉴权，然后调用业务函数。"""
        path_matches = [r for r in ROUTES if r.pattern.fullmatch(request.path)]
        if not path_matches:
            raise ApiError(404, "接口不存在")
        matched = next((r for r in path_matches if r.method == request.method), None)
        if matched is None:
            allowed = ", ".join(sorted({r.method for r in path_matches}))
            raise ApiError(405, f"请求方法不支持（允许：{allowed}）")

        match = matched.pattern.fullmatch(request.path)
        request.params = match.groupdict() if match else {}

        self._check_origin(request)
        self._check_content_type(request)
        request.body = self._parse_json_body(request)

        if matched.auth:
            if not request.user:
                raise ApiError(401, "请先登录")
            if matched.admin_only and request.user.get("role") != "admin":
                raise ApiError(403, "只有管理员可以执行此操作")
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                supplied = request.headers.get("X-CSRF-Token", "")
                expected = request.session.get("csrfToken", "") if request.session else ""
                if not supplied or not expected or not hmac.compare_digest(supplied, expected):
                    raise ApiError(403, "请求校验失败，请刷新页面后重试")
        elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise ApiError(415, "请求内容必须为 JSON 格式")

        return matched.handler(self, request)

    @staticmethod
    def _check_origin(request: Request) -> None:
        """校验 Origin 与 Host 一致，作为 CSRF 的第二道防线。"""
        origin = request.headers.get("Origin", "")
        if not origin or origin == "null":
            return
        host = request.headers.get("Host", "")
        try:
            origin_host = urlparse(origin).netloc
        except ValueError:
            origin_host = ""
        if not host or origin_host != host:
            raise ApiError(403, "请求来源校验失败")

    @staticmethod
    def _check_content_type(request: Request) -> None:
        """写操作必须是 application/json（DELETE 除外）。"""
        if request.method not in {"POST", "PUT", "PATCH"}:
            return
        content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json" and request.raw_body:
            raise ApiError(415, "请求内容必须为 JSON 格式")

    @staticmethod
    def _parse_json_body(request: Request) -> dict:
        """把请求体解析为 dict，非法 JSON 抛 400。"""
        if not request.raw_body:
            return {}
        try:
            parsed = json.loads(request.raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "请求内容不是有效的 JSON")
        if not isinstance(parsed, dict):
            raise ApiError(400, "请求内容必须为 JSON 对象")
        return parsed

    def _issue_session(self, request: Request, user_id: int) -> Response:
        """生成令牌与 CSRF、写会话表并下发 Cookie。"""
        token = new_session_token()
        csrf = new_csrf_token()
        created_at = iso_utc()
        expires = session_expiry()
        self.sessions.create_session(user_id, digest_token(token), csrf, iso_utc(expires))
        user = self.users.get_public_user(user_id)
        max_age = int((expires - datetime.now(timezone.utc)).total_seconds())
        response = ok(
            {"user": user, "csrfToken": csrf, "createdAt": created_at},
            headers=[("Set-Cookie", session_cookie(token, max_age=max_age))],
        )
        return response


# --------------------------------------------------------------------------- 用户接口


@route("POST", r"/api/auth/register")
def api_register(app: Application, request: Request) -> Response:
    """注册：校验字段 → 查重 → 写库，返回 201。"""
    data = validate_registration(request.body)
    errors: dict[str, str] = {}
    taken = app.users.account_taken(student_no=data["student_no"], email=data["email"])
    if "studentNo" in taken:
        errors["studentNo"] = "该学号已注册"
    if "email" in taken:
        errors["email"] = "该邮箱已被使用"
    if errors:
        raise ValidationError(errors)

    user = app.users.create_user(data)
    return ok(
        {"studentNo": user["studentNo"], "name": user["name"]},
        message="注册成功，请登录",
        status=201,
    )


@route("POST", r"/api/auth/login")
def api_login(app: Application, request: Request) -> Response:
    """登录：限流检查 → 查用户 → 校验密码 → 签发会话。"""
    account = request.body.get("account", "")
    password = request.body.get("password", "")
    errors: dict[str, str] = {}
    if not isinstance(account, str) or not account.strip():
        errors["account"] = "请输入学号或邮箱"
    if not isinstance(password, str) or not password:
        errors["password"] = "请输入密码"
    if errors:
        raise ValidationError(errors)

    account = account.strip()
    throttle_key = f"{request.ip}|{account.lower()}"
    retry_after = app.throttle.retry_after(throttle_key)
    if retry_after:
        minutes = max(1, (retry_after + 59) // 60)
        raise ApiError(429, f"登录尝试次数过多，请在约 {minutes} 分钟后再试")

    row = app.users.get_user_for_login(account)
    if row is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        app.throttle.record_failure(throttle_key)
        raise ApiError(401, "账号或密码错误")

    if not verify_password(password, row["password_hash"]):
        app.throttle.record_failure(throttle_key)
        raise ApiError(401, "账号或密码错误")

    app.throttle.reset(throttle_key)
    response = app._issue_session(request, int(row["id"]))
    response.payload["message"] = "登录成功"
    return response


@route("GET", r"/api/auth/me", auth=True)
def api_me(app: Application, request: Request) -> Response:
    """返回当前登录用户与 CSRF 令牌。"""
    assert request.session and request.user
    return ok({"user": request.user, "csrfToken": request.session["csrfToken"]})


@route("POST", r"/api/auth/logout", auth=True)
def api_logout(app: Application, request: Request) -> Response:
    """退出登录：删除当前会话并清除 Cookie。"""
    if request.token_hash:
        app.sessions.delete_session(request.token_hash)
    return ok(None, message="已退出登录", headers=[("Set-Cookie", clear_session_cookie())])


@route("PATCH", r"/api/users/me", auth=True)
def api_update_profile(app: Application, request: Request) -> Response:
    """本人修改资料（不含学号/工号与角色）。"""
    assert request.user
    data = validate_profile_update(request.body)
    taken = app.users.account_taken(
        email=data["email"], exclude_user_id=request.user["id"]
    )
    if "email" in taken:
        raise ValidationError({"email": "该邮箱已被其他用户使用"})
    user = app.users.update_user(request.user["id"], data)
    return ok({"user": user}, message="个人信息已保存")


@route("PATCH", r"/api/users/me/password", auth=True)
def api_change_password(app: Application, request: Request) -> Response:
    """本人修改密码：必须校验当前密码，改完保留当前会话、踢掉其他设备。"""
    assert request.user
    data = validate_password_change(request.body, require_current=True)
    stored = app.sessions.get_password_hash(int(request.user["id"]))
    if not stored or not verify_password(data["current_password"], stored):
        raise ValidationError({"currentPassword": "当前密码不正确"})

    app.sessions.update_password(int(request.user["id"]), data["new_password"])
    app.sessions.delete_user_sessions(int(request.user["id"]), keep_token_hash=request.token_hash)
    return ok(None, message="密码已修改，其他设备已退出登录")


@route("GET", r"/api/admin/users", auth=True, admin_only=True)
def api_admin_users(app: Application, request: Request) -> Response:
    """管理员查看全部用户。"""
    users = app.users.list_all_users()
    return ok({"items": users, "total": len(users)})


@route("PATCH", r"/api/admin/users/(?P<user_id>\d+)", auth=True, admin_only=True)
def api_admin_update_user(app: Application, request: Request) -> Response:
    """管理员修改任意用户资料，可一并更正学号/工号。"""
    target_id = int(request.params["user_id"])
    target = app.users.get_public_user(target_id)
    if not target:
        raise ApiError(404, "用户不存在")

    data = validate_profile_update(request.body)
    taken = app.users.account_taken(email=data["email"], exclude_user_id=target_id)
    if "email" in taken:
        raise ValidationError({"email": "该邮箱已被其他用户使用"})

    # 管理员可以修改学号/工号（普通用户不行）：先校验再落库，避免改一半
    new_student_no: str | None = None
    raw_student_no = request.body.get("studentNo")
    if isinstance(raw_student_no, str) and raw_student_no.strip():
        candidate = validate_student_no(raw_student_no)
        if candidate != target["studentNo"]:
            if "studentNo" in app.users.account_taken(student_no=candidate):
                raise ValidationError({"studentNo": "该学号或工号已被使用"})
            new_student_no = candidate

    user = app.users.update_user(target_id, data, student_no=new_student_no)
    return ok({"user": user}, message="用户信息已保存")


@route("POST", r"/api/admin/users/(?P<user_id>\d+)/password", auth=True, admin_only=True)
def api_admin_reset_password(app: Application, request: Request) -> Response:
    """管理员重置指定用户的密码（忘记密码的兜底手段），并强制其重新登录。"""
    target_id = int(request.params["user_id"])
    target = app.users.get_public_user(target_id)
    if not target:
        raise ApiError(404, "用户不存在")

    data = validate_password_change(request.body, require_current=False)
    app.sessions.update_password(target_id, data["new_password"])
    app.sessions.delete_user_sessions(target_id)
    return ok(
        {"studentNo": target["studentNo"]},
        message="密码已重置，请通知该用户使用新密码登录",
    )


@route("DELETE", r"/api/admin/users/(?P<user_id>\d+)", auth=True, admin_only=True)
def api_admin_delete_user(app: Application, request: Request) -> Response:
    """管理员删除账号；会话随外键级联删除，且不允许删自己或删掉最后一名管理员。"""
    assert request.user
    target_id = int(request.params["user_id"])
    if target_id == int(request.user["id"]):
        raise ApiError(400, "不能删除当前登录的账号")
    target = app.users.get_public_user(target_id)
    if not target:
        raise ApiError(404, "用户不存在")
    if target["role"] == "admin" and app.users.count_admins() <= 1:
        raise ApiError(400, "至少要保留一名管理员")

    app.users.delete_user(target_id)
    return ok(None, message=f"已删除用户 {target['studentNo']}")


@route("POST", r"/api/admin/users", auth=True, admin_only=True)
def api_admin_create_user(app: Application, request: Request) -> Response:
    """管理员在后台新增管理员账号（复用注册校验，角色固定为 admin）。"""
    data = validate_registration(request.body)
    errors: dict[str, str] = {}
    taken = app.users.account_taken(student_no=data["student_no"], email=data["email"])
    if "studentNo" in taken:
        errors["studentNo"] = "该学号或工号已注册"
    if "email" in taken:
        errors["email"] = "该邮箱已被使用"
    if errors:
        raise ValidationError(errors)

    user = app.users.create_user(data, role="admin")
    return ok({"user": user}, message="管理员账号已创建", status=201)


# --------------------------------------------------------------------------- 子系统占位


@route("GET", r"/api/subsystems")
def api_subsystems(app: Application, request: Request) -> Response:
    """三个子系统的清单：本期只有用户管理子系统可用，其余为规划中的占位。"""
    items = [USER_SUBSYSTEM, goods.meta(), trade.meta()]
    return ok({"items": items, "total": len(items)})


def _register_planned_subsystem(meta: dict) -> None:
    """为尚未实现的子系统注册占位路由：保留接口位置，明确返回 501。"""
    pattern = re.compile(rf"{meta['routePrefix']}(?:/.*)?")

    def handler(app: Application, request: Request, _meta: dict = meta) -> Response:
        """占位子系统：统一返回 501 并说明实现位置。"""
        raise ApiError(
            501,
            f"{_meta['name']}尚未实现（{_meta['statusLabel']}）：本期只交付用户管理子系统，"
            f"实现位置见 {_meta['module']}。",
        )

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        ROUTES.append(Route(method, pattern, handler))


_register_planned_subsystem(trade.meta())


@route('GET', r'/api/goods', auth=True)
def api_goods_list(app, request):
    return ok({'items': app.goods.store.list(request.user['id'])})


@route('GET', r'/api/goods/(?P<id>[0-9]+)', auth=True)
def api_goods_detail(app, request):
    return ok(app.goods.store.get(int(request.params['id']), request.user['id']))


@route('POST', r'/api/goods', auth=True)
def api_goods_create(app, request):
    return ok(app.goods.save(request.user['id'], request.body), status=201)


@route('PATCH', r'/api/goods/(?P<id>[0-9]+)', auth=True)
def api_goods_update(app, request):
    return ok(app.goods.save(request.user['id'], request.body, int(request.params['id'])))


@route('GET', r'/api/goods/images/(?P<id>[0-9]+)', auth=True)
def api_goods_image(app, request):
    content, mime = app.goods.store.image(int(request.params['id']), request.user['id'])
    return Response(content=content, content_type=mime)


@route('DELETE', r'/api/goods/(?P<id>[0-9]+)', auth=True)
def api_goods_delete(app, request):
    app.goods.delete(request.user['id'], int(request.params['id']))
    return ok(None, message='商品已删除')


@route("GET", r"/api/health")
def api_health(app: Application, request: Request) -> Response:
    """健康检查，供部署探活使用。"""
    return ok({"status": "ok"})


# --------------------------------------------------------------------------- Cookie


def session_cookie(token: str, max_age: int | None) -> str:
    """构造会话 Cookie（HttpOnly、SameSite=Lax，可选 Secure）。"""
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


# --------------------------------------------------------------------------- HTTP 服务


SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    ),
]

STATIC_ROOT = STATIC_DIR.resolve()


class RequestHandler(BaseHTTPRequestHandler):
    application: Application
    protocol_version = "HTTP/1.1"
    server_version = "CampusTradeUser/1.0"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802
        """把 GET 请求交给统一的 _handle 处理。"""
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        """把 HEAD 请求交给统一的 _handle 处理。"""
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        """把 POST 请求交给统一的 _handle 处理。"""
        self._handle("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        """把 PATCH 请求交给统一的 _handle 处理。"""
        self._handle("PATCH")

    def do_PUT(self) -> None:  # noqa: N802
        """把 PUT 请求交给统一的 _handle 处理。"""
        self._handle("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        """把 DELETE 请求交给统一的 _handle 处理。"""
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        """不支持跨域预检，直接返回 405。"""
        self._send_json(error(405, "不支持跨域请求"))

    def _handle(self, method: str) -> None:
        """区分 API 与静态资源，并做方法白名单。"""
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
        except ValueError:
            self._send_json(error(400, "请求地址不正确"))
            return

        if path.startswith("/api/"):
            if method == "HEAD":
                self._send_json(error(405, "请求方法不支持"))
                return
            self._handle_api(method, path, parsed.query)
        elif method in {"GET", "HEAD"}:
            self._serve_static(path, include_body=method != "HEAD")
        else:
            self._send_json(error(405, "请求方法不支持"))

    def _handle_api(self, method: str, path: str, query_string: str) -> None:
        """读取并限制请求体大小，组装 Request 后交给 dispatch。"""
        raw_body = b""
        self.connection.settimeout(10)
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) > 1:
            self.close_connection = True
            self._send_json(error(400, '不支持的请求长度或传输形式'))
            return
        if self.headers.get('Content-Encoding', 'identity').lower() != 'identity':
            self.close_connection = True
            self._send_json(error(415, '不接受压缩请求体'))
            return
        if method in {'POST', 'PATCH', 'PUT'} and self.headers.get('Content-Length') is None:
            self.close_connection = True
            self._send_json(error(411, '请求必须声明长度'))
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            self.close_connection = True
            self._send_json(error(400, "请求格式不正确"))
            return
        if length < 0:
            self.close_connection = True
            self._send_json(error(400, "请求格式不正确"))
            return
        limit = MAX_GOODS_BODY_BYTES if ((method == 'POST' and path == '/api/goods') or
                  (method == 'PATCH' and re.fullmatch(r'/api/goods/[0-9]+', path))) else MAX_BODY_BYTES
        if length > limit:
            self.close_connection = True
            self._send_json(error(413, "请求内容过大"))
            return
        if length:
            try:
                raw_body = self.rfile.read(length)
            except (TimeoutError, OSError):
                self.close_connection = True
                self._send_json(error(408, '请求体读取超时或中断'))
                return
            if len(raw_body) != length:
                self.close_connection = True
                self._send_json(error(400, '请求体长度不符'))
                return

        request = Request(
            method=method,
            path=path,
            query=parse_qs(query_string, keep_blank_values=True),
            headers={key: value for key, value in self.headers.items()},
            raw_body=raw_body,
            ip=self.client_address[0] if self.client_address else "",
        )
        self._send_json(self.application.dispatch(request))

    def _serve_static(self, path: str, include_body: bool = True) -> None:
        """从 static/ 目录提供页面与静态资源，阻止目录穿越。"""
        if path in {"/", "/index.html"}:
            relative = "index.html"
        elif path.startswith("/static/"):
            relative = path[len("/static/") :]
        else:
            self._send_static_not_found(include_body)
            return
        if not relative or relative.endswith("/"):
            self._send_static_not_found(include_body)
            return

        try:
            candidate = (STATIC_ROOT / relative).resolve()
        except (OSError, ValueError):
            self._send_static_not_found(include_body)
            return
        if not candidate.is_relative_to(STATIC_ROOT) or not candidate.is_file():
            self._send_static_not_found(include_body)
            return

        content_type, _ = mimetypes.guess_type(candidate.name)
        if candidate.suffix == ".js":
            content_type = "text/javascript"
        if candidate.suffix in {".html", ".css", ".js", ".svg", ".json"}:
            content_type = f"{content_type}; charset=utf-8"
        try:
            content = candidate.read_bytes()
        except OSError:
            self._send_static_not_found(include_body)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        for key, value in SECURITY_HEADERS:
            self.send_header(key, value)
        self.end_headers()
        if include_body:
            self.wfile.write(content)

    def _send_static_not_found(self, include_body: bool = True) -> None:
        """返回 404 页面。"""
        body = "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>404</title><p>页面不存在，<a href=\"/\">返回首页</a></p></html>".encode("utf-8")
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in SECURITY_HEADERS:
            self.send_header(key, value)
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _send_json(self, response: Response) -> None:
        """输出 JSON 响应并附加安全响应头。"""
        body = response.content if response.content is not None else json.dumps(
            response.payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        try:
            status = HTTPStatus(response.status).value
        except ValueError:
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Cookie")
        for key, value in response.headers:
            self.send_header(key, value)
        for key, value in SECURITY_HEADERS:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """只记录 /api/ 访问日志，避免静态资源刷屏。"""
        if self.path.startswith("/api/"):
            sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), format % args))


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    db_path: str | Path = DEFAULT_DB,
) -> ThreadingHTTPServer:
    """创建应用与线程化 HTTP 服务，返回可 serve_forever 的实例。"""
    application = Application(db_path=db_path)
    handler = type("BoundRequestHandler", (RequestHandler,), {"application": application})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    """解析命令行参数、准备数据库、打印访问信息并启动服务。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=f"{APP_NAME} · {APP_SUBTITLE}")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 数据库路径")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    try:
        server = create_server(args.host, args.port, args.db)
    except OSError as exc:
        print(f"无法启动服务：{exc}", file=sys.stderr)
        print("请确认端口未被占用，或使用 --port 指定其他端口。", file=sys.stderr)
        raise SystemExit(1) from exc

    actual_port = server.server_address[1]
    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{display_host}:{actual_port}"

    print("=" * 56)
    print(f"  {APP_NAME} · {APP_SUBTITLE}")
    print("=" * 56)
    print(f"  访问地址   {url}")
    print(f"  数据库     {Path(args.db).resolve()}")
    print(f"  管理员     {DEFAULT_ADMIN_STUDENT_NO} / {DEFAULT_ADMIN_PASSWORD}")
    print("  普通用户   请先注册账号")
    print("  停止服务   Ctrl + C")
    print("=" * 56, flush=True)

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
