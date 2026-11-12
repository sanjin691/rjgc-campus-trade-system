"""校园二手商品交易系统 · 用户与商品管理

仅实现指定功能：
- 用户注册
- 用户登录 / 退出
- 用户信息查看与修改
- 商品创建、修改、本人商品查询及图片管理

启动：python app.py

分层：本文件只负责“组装与路由”——
业务规则在 user_service.py，数据访问在 database.py，
HTTP 基础设施在 server.py / web.py。
"""

from __future__ import annotations

import argparse
import hmac
import json
import re
import sqlite3
import sys
import threading
import traceback
import webbrowser
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

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
from errors import ApiError
from security import SESSION_COOKIE, digest_token
from server import RequestHandler
from account_service import AccountService, SessionTicket
from admin_service import UserAdminService
from validators import ValidationError
from web import (
    ROUTES,
    Request,
    Response,
    Route,
    clear_session_cookie,
    error,
    ok,
    parse_cookies,
    route,
    session_cookie,
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "data" / "user_management.db"
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
    "dependencies": ["商品管理、交易管理两个子系统均依赖本子系统的账号与角色"],
}


# --------------------------------------------------------------------------- 应用


class Application:
    def __init__(self, db_path: str | Path = DEFAULT_DB):
        """初始化数据库、两个仓储、用户服务与商品服务。"""
        initialize_database(db_path)
        self.users = UserStore(db_path)
        self.sessions = SessionStore(db_path)
        self.account_service = AccountService(self.users, self.sessions)
        self.admin_service = UserAdminService(self.users, self.sessions)
        self.goods = goods.GoodsService(db_path)

    def dispatch(self, request: Request) -> Response:
        """请求入口：认证 → 路由 → 统一异常映射。"""
        request.cookies = parse_cookies(request.headers.get("Cookie", ""))
        self._authenticate(request)

        try:
            response = self._route_request(request)
        # 异常在这里统一翻译成状态码：422 / 409 / 4xx / 500，业务函数只负责抛
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
            # 顺序固定：先认证（401）→ 再授权（403）→ 最后校验 CSRF（403）
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

    def session_response(self, ticket: SessionTicket, message: str) -> Response:
        """把登录票据转换成带 Set-Cookie 的响应（Cookie 属于 HTTP 层）。"""
        # Cookie 属于 HTTP 层，所以留在这里，不放进 user_service
        max_age = int((ticket.expires_at - datetime.now(timezone.utc)).total_seconds())
        return ok(
            {
                "user": ticket.user,
                "csrfToken": ticket.csrf_token,
                "createdAt": ticket.created_at,
            },
            message=message,
            headers=[("Set-Cookie", session_cookie(ticket.token, max_age=max_age))],
        )




# --------------------------------------------------------------------------- 用户接口




@route("POST", r"/api/auth/register")
def api_register(app: Application, request: Request) -> Response:
    """注册：交给 UserService 校验与写库，这里只负责包装响应。"""
    user = app.account_service.register(request.body)
    return ok(
        {"studentNo": user["studentNo"], "name": user["name"]},
        message="注册成功，请登录",
        status=201,
    )


@route("POST", r"/api/auth/login")
def api_login(app: Application, request: Request) -> Response:
    """登录：交给 UserService 校验并建立会话，这里只负责写 Cookie。"""
    ticket = app.account_service.login(request.body, request.ip)
    return app.session_response(ticket, "登录成功")


@route("GET", r"/api/auth/me", auth=True)
def api_me(app: Application, request: Request) -> Response:
    """返回当前登录用户与 CSRF 令牌。"""
    assert request.session and request.user
    return ok({"user": request.user, "csrfToken": request.session["csrfToken"]})


@route("POST", r"/api/auth/logout", auth=True)
def api_logout(app: Application, request: Request) -> Response:
    """退出登录：删除当前会话并清除 Cookie。"""
    app.account_service.logout(request.token_hash)
    return ok(None, message="已退出登录", headers=[("Set-Cookie", clear_session_cookie())])


@route("PATCH", r"/api/users/me", auth=True)
def api_update_profile(app: Application, request: Request) -> Response:
    """本人修改资料（不含学号/工号与角色）。"""
    assert request.user
    user = app.account_service.update_profile(request.user["id"], request.body)
    return ok({"user": user}, message="个人信息已保存")


@route("PATCH", r"/api/users/me/password", auth=True)
def api_change_password(app: Application, request: Request) -> Response:
    """本人修改密码：校验当前密码，改完保留当前会话、踢掉其他设备。"""
    assert request.user
    app.account_service.change_password(request.user["id"], request.token_hash, request.body)
    return ok(None, message="密码已修改，其他设备已退出登录")


@route("GET", r"/api/admin/users", auth=True, admin_only=True)
def api_admin_users(app: Application, request: Request) -> Response:
    """管理员查看全部用户。"""
    users = app.admin_service.list_users()
    return ok({"items": users, "total": len(users)})


@route("PATCH", r"/api/admin/users/(?P<user_id>\d+)", auth=True, admin_only=True)
def api_admin_update_user(app: Application, request: Request) -> Response:
    """管理员修改任意用户资料，可一并更正学号/工号。"""
    user = app.admin_service.update_user(int(request.params["user_id"]), request.body)
    return ok({"user": user}, message="用户信息已保存")


@route("POST", r"/api/admin/users/(?P<user_id>\d+)/password", auth=True, admin_only=True)
def api_admin_reset_password(app: Application, request: Request) -> Response:
    """管理员重置密码：不需要原密码，重置后该用户全部会话失效。"""
    target = app.admin_service.reset_password(int(request.params["user_id"]), request.body)
    return ok(
        {"studentNo": target["studentNo"]},
        message="密码已重置，请通知该用户使用新密码登录",
    )


@route("DELETE", r"/api/admin/users/(?P<user_id>\d+)", auth=True, admin_only=True)
def api_admin_delete_user(app: Application, request: Request) -> Response:
    """管理员删除账号：不能删自己，也不能删最后一名管理员。"""
    assert request.user
    target = app.admin_service.delete_user(
        int(request.user["id"]), int(request.params["user_id"])
    )
    return ok(None, message=f"已删除用户 {target['studentNo']}")


@route("POST", r"/api/admin/users", auth=True, admin_only=True)
def api_admin_create_user(app: Application, request: Request) -> Response:
    """管理员在后台新增管理员账号。"""
    user = app.admin_service.create_admin(request.body)
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
