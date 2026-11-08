"""用户管理子系统 API 集成测试。

运行：python -m unittest tests.test_api -v
"""

from __future__ import annotations

import http.client
import json
import shutil
import sys
import threading
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_server  # noqa: E402


class ApiClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, body=None, *, csrf=True):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.cookie:
            headers["Cookie"] = f"user_session={self.cookie}"
        if csrf and self.csrf and method in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["X-CSRF-Token"] = self.csrf
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        set_cookie = response.getheader("Set-Cookie")
        connection.close()

        if set_cookie and set_cookie.startswith("user_session="):
            self.cookie = set_cookie.split(";", 1)[0].split("=", 1)[1]
        data = json.loads(raw.decode("utf-8")) if raw else None
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            token = data["data"].get("csrfToken")
            if token:
                self.csrf = token
        return response.status, data

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.request("POST", path, body, **kwargs)

    def patch(self, path, body=None, **kwargs):
        return self.request("PATCH", path, body, **kwargs)

    def register(self, **overrides):
        suffix = uuid.uuid4().hex[:8]
        payload = {
            "studentNo": f"9{suffix[:9]}",
            "name": f"测试用户{suffix[:3]}",
            "email": f"test-{suffix}@campus.edu.cn",
            "phone": "",
            "college": "计算机学院",
            "major": "软件工程",
            "password": "Campus@2026",
            "confirmPassword": "Campus@2026",
        }
        payload.update(overrides)
        return self.post("/api/auth/register", payload, csrf=False), payload

    def login(self, account: str, password: str):
        return self.post(
            "/api/auth/login",
            {"account": account, "password": password},
            csrf=False,
        )


class UserManagementApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_root = PROJECT_ROOT / "data" / "test-runs"
        cls.test_root = test_root
        test_root.mkdir(parents=True, exist_ok=True)
        cls.temp_dir = test_root / f"run-{uuid.uuid4().hex[:10]}"
        cls.temp_dir.mkdir(parents=True, exist_ok=True)
        cls.server = create_server("127.0.0.1", 0, cls.temp_dir / "test.db")
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.temp_dir, ignore_errors=True)
        if cls.test_root.exists() and not any(cls.test_root.iterdir()):
            cls.test_root.rmdir()

    def client(self) -> ApiClient:
        return ApiClient("127.0.0.1", self.port)

    def create_logged_in_user(self):
        client = self.client()
        (status, data), payload = client.register()
        self.assertEqual(status, 201, data)
        status, data = client.login(payload["studentNo"], payload["password"])
        self.assertEqual(status, 200, data)
        return client, data["data"]["user"], payload

    def create_admin_client(self):
        client = self.client()
        status, data = client.login("ADMIN001", "Admin@123")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["data"]["user"]["role"], "admin")
        return client

    # --------------------------------------------------------------- 基础
    def test_health_homepage_and_static_files(self):
        client = self.client()
        status, data = client.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request("GET", "/")
        response = connection.getresponse()
        html = response.read().decode("utf-8")
        headers = dict(response.getheaders())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Content-Security-Policy", headers)
        self.assertIn("校园二手商品交易系统", html)

        for path in ("/static/css/style.css", "/static/js/app.js", "/static/favicon.svg"):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            connection.request("GET", path)
            asset = connection.getresponse()
            asset.read()
            connection.close()
            self.assertEqual(asset.status, 200, path)

    def test_registration_and_login(self):
        client = self.client()
        (status, data), payload = client.register()
        self.assertEqual(status, 201)
        self.assertEqual(data["message"], "注册成功，请登录")
        self.assertFalse(client.cookie)

        status, data = client.login(payload["studentNo"], payload["password"])
        self.assertEqual(status, 200)
        user = data["data"]["user"]
        self.assertEqual(user["studentNo"], payload["studentNo"].upper())
        self.assertEqual(user["name"], payload["name"])
        self.assertEqual(user["role"], "user")
        self.assertTrue(client.csrf)
        self.assertTrue(client.cookie)

        status, me = client.get("/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(me["data"]["user"]["email"], payload["email"])

    def test_registration_validation_and_duplicates(self):
        client = self.client()
        (status, data), _ = client.register(
            email="bad-email",
            password="123",
            confirmPassword="456",
        )
        self.assertEqual(status, 422)
        for field in ("email", "password", "confirmPassword"):
            self.assertIn(field, data["errors"])

        (status, data), payload = client.register()
        self.assertEqual(status, 201)

        duplicate = self.client()
        (status, data), _ = duplicate.register(
            studentNo=payload["studentNo"],
            email=f"other-{uuid.uuid4().hex[:6]}@campus.edu.cn",
        )
        self.assertEqual(status, 422)
        self.assertIn("studentNo", data["errors"])

        duplicate_email = self.client()
        (status, data), _ = duplicate_email.register(
            studentNo=f"8{uuid.uuid4().hex[:9]}",
            email=payload["email"],
        )
        self.assertEqual(status, 422)
        self.assertIn("email", data["errors"])

    def test_wrong_account_or_password(self):
        client = self.client()
        status, data = client.login("no-such-account", "Campus@2026")
        self.assertEqual(status, 401)
        self.assertEqual(data["message"], "账号或密码错误")

        (status, _), payload = client.register()
        self.assertEqual(status, 201)
        status, data = client.login(payload["studentNo"], "WrongPassword1")
        self.assertEqual(status, 401)
        self.assertEqual(data["message"], "账号或密码错误")
        self.assertFalse(client.cookie)

    # --------------------------------------------------------------- 登录状态
    def test_profile_update_and_logout(self):
        client, user, payload = self.create_logged_in_user()
        status, data = client.patch(
            "/api/users/me",
            {
                "name": "修改后的姓名",
                "email": user["email"],
                "phone": "13800138000",
                "college": "软件学院",
                "major": "人工智能",
            },
        )
        self.assertEqual(status, 200)
        updated = data["data"]["user"]
        self.assertEqual(updated["name"], "修改后的姓名")
        self.assertEqual(updated["phone"], "13800138000")
        self.assertEqual(updated["college"], "软件学院")

        status, me = client.get("/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(me["data"]["user"]["major"], "人工智能")

        status, data = client.post("/api/auth/logout")
        self.assertEqual(status, 200)
        self.assertFalse(client.cookie)
        status, _ = client.get("/api/auth/me")
        self.assertEqual(status, 401)

    def test_profile_validation_and_email_conflict(self):
        first, _, _ = self.create_logged_in_user()
        second, second_user, _ = self.create_logged_in_user()

        status, data = first.patch(
            "/api/users/me",
            {
                "name": "A",
                "email": "not-an-email",
                "phone": "123",
                "college": "",
                "major": "",
            },
        )
        self.assertEqual(status, 422)
        for field in ("name", "email", "phone"):
            self.assertIn(field, data["errors"])

        status, data = first.patch(
            "/api/users/me",
            {
                "name": "正常姓名",
                "email": second_user["email"],
                "phone": "",
                "college": "",
                "major": "",
            },
        )
        self.assertEqual(status, 422)
        self.assertIn("email", data["errors"])

        self.assertEqual(second.get("/api/auth/me")[0], 200)

    def test_csrf_required_and_unauthenticated_access(self):
        client, _, _ = self.create_logged_in_user()
        status, data = client.patch(
            "/api/users/me",
            {
                "name": "测试姓名",
                "email": "csrf@campus.edu.cn",
                "phone": "",
                "college": "",
                "major": "",
            },
            csrf=False,
        )
        self.assertEqual(status, 403)
        self.assertEqual(data["message"], "请求校验失败，请刷新页面后重试")

        anonymous = self.client()
        status, _ = anonymous.get("/api/auth/me")
        self.assertEqual(status, 401)
        status, _ = anonymous.patch("/api/users/me", {"name": "无权限"})
        self.assertEqual(status, 401)

    # --------------------------------------------------------------- 管理员
    def test_normal_user_cannot_access_admin_api(self):
        client, user, _ = self.create_logged_in_user()

        status, data = client.get("/api/admin/users")
        self.assertEqual(status, 403)
        self.assertEqual(data["message"], "只有管理员可以执行此操作")

        status, data = client.patch(
            f"/api/admin/users/{user['id']}",
            {
                "name": "越权修改",
                "email": user["email"],
                "phone": "",
                "college": "",
                "major": "",
            },
        )
        self.assertEqual(status, 403)

    def test_admin_can_list_all_users(self):
        user_client, user, _ = self.create_logged_in_user()
        admin = self.create_admin_client()

        status, data = admin.get("/api/admin/users")
        self.assertEqual(status, 200)
        items = data["data"]["items"]
        self.assertEqual(data["data"]["total"], len(items))
        ids = {item["id"] for item in items}
        self.assertIn(user["id"], ids)
        self.assertTrue(any(item["role"] == "admin" for item in items))
        self.assertTrue(all("password_hash" not in item for item in items))

        self.assertEqual(user_client.get("/api/admin/users")[0], 403)

    def test_admin_can_edit_any_user_information(self):
        user_client, user, _ = self.create_logged_in_user()
        admin = self.create_admin_client()

        status, data = admin.patch(
            f"/api/admin/users/{user['id']}",
            {
                "name": "管理员修改后的姓名",
                "email": user["email"],
                "phone": "13900001111",
                "college": "软件学院",
                "major": "人工智能",
            },
        )
        self.assertEqual(status, 200)
        updated = data["data"]["user"]
        self.assertEqual(updated["name"], "管理员修改后的姓名")
        self.assertEqual(updated["phone"], "13900001111")
        self.assertEqual(updated["college"], "软件学院")
        self.assertEqual(updated["studentNo"], user["studentNo"])

        status, me = user_client.get("/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(me["data"]["user"]["name"], "管理员修改后的姓名")

    def test_admin_edit_validation_and_email_conflict(self):
        first, first_user, _ = self.create_logged_in_user()
        second, second_user, _ = self.create_logged_in_user()
        admin = self.create_admin_client()

        status, data = admin.patch(
            f"/api/admin/users/{first_user['id']}",
            {
                "name": "A",
                "email": "not-an-email",
                "phone": "123",
                "college": "",
                "major": "",
            },
        )
        self.assertEqual(status, 422)
        for field in ("name", "email", "phone"):
            self.assertIn(field, data["errors"])

        status, data = admin.patch(
            f"/api/admin/users/{first_user['id']}",
            {
                "name": "正常姓名",
                "email": second_user["email"],
                "phone": "",
                "college": "",
                "major": "",
            },
        )
        self.assertEqual(status, 422)
        self.assertIn("email", data["errors"])

        status, data = admin.patch(
            "/api/admin/users/999999",
            {
                "name": "不存在",
                "email": "nobody@campus.edu.cn",
                "phone": "",
                "college": "",
                "major": "",
            },
        )
        self.assertEqual(status, 404)

        self.assertEqual(second.get("/api/auth/me")[0], 200)

    def test_admin_can_create_admin_account(self):
        admin = self.create_admin_client()
        suffix = uuid.uuid4().hex[:8]
        payload = {
            "studentNo": f"ADM{suffix[:7]}",
            "name": f"新管理员{suffix[:2]}",
            "email": f"admin-{suffix}@campus.edu.cn",
            "phone": "13800002222",
            "college": "信息中心",
            "major": "",
            "password": "Admin@2026",
            "confirmPassword": "Admin@2026",
        }

        status, data = admin.post("/api/admin/users", payload)
        self.assertEqual(status, 201, data)
        created = data["data"]["user"]
        self.assertEqual(created["role"], "admin")
        self.assertEqual(created["studentNo"], payload["studentNo"].upper())
        self.assertNotIn("passwordHash", created)
        self.assertNotIn("password_hash", created)

        # 新管理员可以用初始密码登录，并具备管理权限
        new_admin = self.client()
        status, data = new_admin.login(payload["studentNo"], payload["password"])
        self.assertEqual(status, 200, data)
        self.assertEqual(data["data"]["user"]["role"], "admin")
        self.assertEqual(new_admin.get("/api/admin/users")[0], 200)

        # 学号与邮箱不能重复
        status, data = admin.post("/api/admin/users", payload)
        self.assertEqual(status, 422)
        self.assertIn("studentNo", data["errors"])
        self.assertIn("email", data["errors"])

        # 字段校验复用注册规则
        status, data = admin.post(
            "/api/admin/users",
            {**payload, "studentNo": "", "email": "bad", "password": "123", "confirmPassword": "456"},
        )
        self.assertEqual(status, 422)
        for field in ("studentNo", "email", "password", "confirmPassword"):
            self.assertIn(field, data["errors"])

        # 普通用户不能新增管理员
        user_client, _, _ = self.create_logged_in_user()
        status, _ = user_client.post("/api/admin/users", {**payload, "studentNo": f"X{suffix[:7]}",
                                                          "email": f"x-{suffix}@campus.edu.cn"})
        self.assertEqual(status, 403)

        # 未登录不能新增管理员
        anonymous = self.client()
        status, _ = anonymous.post("/api/admin/users", payload, csrf=False)
        self.assertEqual(status, 401)

    def test_subsystem_catalog_and_placeholders(self):
        client = self.client()

        status, data = client.get("/api/subsystems")
        self.assertEqual(status, 200, data)
        items = {item["key"]: item for item in data["data"]["items"]}
        self.assertEqual(set(items), {"user", "goods", "trade"})
        self.assertEqual(items["user"]["status"], "available")
        self.assertEqual(items['goods']['status'], 'available')
        for key in ("trade",):
            self.assertEqual(items[key]["status"], "planned")
            self.assertEqual(items[key]["statusLabel"], "规划中")
            # 另外两个子系统只占位，内容为空
            self.assertEqual(items[key]["features"], [])
            self.assertEqual(items[key]["plannedSchema"], "")

        for path in ("/api/goods", "/api/goods/123"):
            status, data = client.get(path)
            self.assertEqual(status, 401, path)
            self.assertFalse(data["ok"])

        status, data = client.post("/api/trade/apply", {"goodsId": 1})
        self.assertEqual(status, 501)
        self.assertIn("交易管理子系统", data["message"])

        status, _ = client.request("DELETE", "/api/trade/1", {})
        self.assertEqual(status, 501)

    def test_change_own_password(self):
        client, user, payload = self.create_logged_in_user()
        second = self.client()
        status, _ = second.login(payload["studentNo"], payload["password"])
        self.assertEqual(status, 200)

        # 当前密码错误
        status, data = client.patch(
            "/api/users/me/password",
            {"currentPassword": "Wrong@2026", "newPassword": "Newpass@2026",
             "confirmPassword": "Newpass@2026"},
        )
        self.assertEqual(status, 422)
        self.assertIn("currentPassword", data["errors"])

        # 新密码太弱
        status, data = client.patch(
            "/api/users/me/password",
            {"currentPassword": payload["password"], "newPassword": "123",
             "confirmPassword": "123"},
        )
        self.assertEqual(status, 422)
        self.assertIn("newPassword", data["errors"])

        # 正常修改：当前会话保留，其它设备被踢下线
        status, data = client.patch(
            "/api/users/me/password",
            {"currentPassword": payload["password"], "newPassword": "Newpass@2026",
             "confirmPassword": "Newpass@2026"},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(client.get("/api/auth/me")[0], 200)
        self.assertEqual(second.get("/api/auth/me")[0], 401)

        # 旧密码失效，新密码可登录
        fresh = self.client()
        self.assertEqual(fresh.login(payload["studentNo"], payload["password"])[0], 401)
        self.assertEqual(fresh.login(payload["studentNo"], "Newpass@2026")[0], 200)

    def test_admin_reset_password_and_delete_user(self):
        admin = self.create_admin_client()
        user_client, user, payload = self.create_logged_in_user()

        status, data = admin.post(
            f"/api/admin/users/{user['id']}/password",
            {"newPassword": "Reset@2026", "confirmPassword": "Reset@2026"},
        )
        self.assertEqual(status, 200, data)
        # 重置后该用户所有会话失效
        self.assertEqual(user_client.get("/api/auth/me")[0], 401)
        fresh = self.client()
        self.assertEqual(fresh.login(payload["studentNo"], payload["password"])[0], 401)
        self.assertEqual(fresh.login(payload["studentNo"], "Reset@2026")[0], 200)

        # 普通用户不能重置或删除他人
        other_client, _, _ = self.create_logged_in_user()
        status, _ = other_client.post(
            f"/api/admin/users/{user['id']}/password",
            {"newPassword": "Hack@2026", "confirmPassword": "Hack@2026"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(other_client.request("DELETE", f"/api/admin/users/{user['id']}")[0], 403)

        # 不能删除自己
        admin_id = admin.get("/api/auth/me")[1]["data"]["user"]["id"]
        status, data = admin.request("DELETE", f"/api/admin/users/{admin_id}")
        self.assertEqual(status, 400)

        # 删除用户，账号随即失效
        status, data = admin.request("DELETE", f"/api/admin/users/{user['id']}")
        self.assertEqual(status, 200, data)
        self.assertEqual(admin.request("DELETE", f"/api/admin/users/{user['id']}")[0], 404)
        self.assertEqual(fresh.get("/api/auth/me")[0], 401)

        ids = {item["id"] for item in admin.get("/api/admin/users")[1]["data"]["items"]}
        self.assertNotIn(user["id"], ids)

    def test_admin_can_change_student_no(self):
        admin = self.create_admin_client()
        _, user, payload = self.create_logged_in_user()
        new_no = "N" + uuid.uuid4().hex[:8].upper()

        status, data = admin.patch(
            f"/api/admin/users/{user['id']}",
            {"studentNo": new_no, "name": user["name"], "email": user["email"],
             "phone": "", "college": "", "major": ""},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["data"]["user"]["studentNo"], new_no)

        # 旧学号不能登录，新学号可以
        fresh = self.client()
        self.assertEqual(fresh.login(payload["studentNo"], payload["password"])[0], 401)
        self.assertEqual(fresh.login(new_no, payload["password"])[0], 200)

        # 与别人重复时拒绝
        _, other, _ = self.create_logged_in_user()
        status, data = admin.patch(
            f"/api/admin/users/{user['id']}",
            {"studentNo": other["studentNo"], "name": user["name"], "email": user["email"],
             "phone": "", "college": "", "major": ""},
        )
        self.assertEqual(status, 422)
        self.assertIn("studentNo", data["errors"])

        # 普通用户不能通过“修改本人资料”改学号
        user_client, _, user_payload = self.create_logged_in_user()
        status, data = user_client.patch(
            "/api/users/me",
            {"studentNo": "HACK0001", "name": user_payload["name"],
             "email": user_payload["email"], "phone": "", "college": "", "major": ""},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["data"]["user"]["studentNo"], user_payload["studentNo"].upper())

    def test_not_found_and_method_not_allowed(self):
        client = self.client()
        status, _ = client.get("/api/not-found")
        self.assertEqual(status, 404)
        status, _ = client.request("DELETE", "/api/health")
        self.assertEqual(status, 405)


if __name__ == "__main__":
    unittest.main(verbosity=2)
