"""HTTP 服务器：解析请求、输出响应、提供静态文件。

只依赖 web.py 的基础类型，不包含任何业务规则；
业务逻辑通过 ``self.application.dispatch(request)`` 进入。
"""

from __future__ import annotations

import json
import mimetypes
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from web import Request, Response, error

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_ROOT = STATIC_DIR.resolve()
MAX_BODY_BYTES = 64 * 1024
MAX_GOODS_BODY_BYTES = 9 * 1024 * 1024

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
        # 商品接口要传图片，单独放宽到 9MB；其余接口仍然 64KB
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
        # resolve 之后再判断是否仍在 static 目录内，防止 ../ 目录穿越
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

    # 所有 JSON 响应统一附加安全响应头（CSP、nosniff 等）
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

