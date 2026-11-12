"""普通用户的自助服务：注册、登录、退出、改资料、改密码。

与管理员操作分开，是因为两类操作的变化原因不同：
自助规则（密码策略、资料字段）和账号管控规则（谁能删谁）通常不会同时变。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime

from database import SessionStore, UserStore
from errors import ApiError
from security import (
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
)

#: 账号不存在时用来做假校验的哈希，保证“账号不存在”与“密码错误”耗时一致
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


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


@dataclass
class SessionTicket:
    """一次登录的结果：原始令牌交给 HTTP 层写 Cookie，摘要已经入库。"""

    user: dict
    token: str
    csrf_token: str
    created_at: str
    expires_at: datetime


class AccountService:
    """普通用户能做的 5 件事：注册、登录、退出、改资料、改密码。"""

    def __init__(
        self,
        users: UserStore,
        sessions: SessionStore,
        throttle: LoginThrottle | None = None,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.throttle = throttle or LoginThrottle()

    def register(self, body: dict) -> dict:
        """注册：校验字段、检查学号与邮箱是否被占用，然后写库。"""
        data = validate_registration(body)
        errors: dict[str, str] = {}
        taken = self.users.account_taken(student_no=data["student_no"], email=data["email"])
        if "studentNo" in taken:
            errors["studentNo"] = "该学号已注册"
        if "email" in taken:
            errors["email"] = "该邮箱已被使用"
        if errors:
            raise ValidationError(errors)
        return self.users.create_user(data)

    def login(self, body: dict, ip: str) -> SessionTicket:
        """登录：限流检查 → 查用户 → 校验密码 → 建立会话。"""
        account = body.get("account", "")
        password = body.get("password", "")
        errors: dict[str, str] = {}
        if not isinstance(account, str) or not account.strip():
            errors["account"] = "请输入学号或邮箱"
        if not isinstance(password, str) or not password:
            errors["password"] = "请输入密码"
        if errors:
            raise ValidationError(errors)

        account = account.strip()
        throttle_key = f"{ip}|{account.lower()}"
        # 先查限流：被锁定时直接返回，不查数据库，省资源也防爆破
        retry_after = self.throttle.retry_after(throttle_key)
        if retry_after:
            minutes = max(1, (retry_after + 59) // 60)
            raise ApiError(429, f"登录尝试次数过多，请在约 {minutes} 分钟后再试")

        row = self.users.get_user_for_login(account)
        if row is None:
            # 账号不存在也做一次哈希：让两种失败的提示与耗时一致，防枚举
            verify_password(password, DUMMY_PASSWORD_HASH)
            self.throttle.record_failure(throttle_key)
            raise ApiError(401, "账号或密码错误")

        if not verify_password(password, row["password_hash"]):
            self.throttle.record_failure(throttle_key)
            raise ApiError(401, "账号或密码错误")

        self.throttle.reset(throttle_key)
        return self._issue_session(int(row["id"]))

    def logout(self, token_hash: str | None) -> None:
        """退出登录：删除当前会话。"""
        if token_hash:
            self.sessions.delete_session(token_hash)

    def update_profile(self, user_id: int, body: dict) -> dict:
        """本人修改资料：只允许改姓名/邮箱/手机/学院/专业。"""
        data = validate_profile_update(body)
        taken = self.users.account_taken(email=data["email"], exclude_user_id=user_id)
        if "email" in taken:
            raise ValidationError({"email": "该邮箱已被其他用户使用"})
        return self.users.update_user(user_id, data)

    def change_password(self, user_id: int, token_hash: str | None, body: dict) -> None:
        """本人修改密码：先校验当前密码，改完保留当前设备、踢掉其他设备。"""
        data = validate_password_change(body, require_current=True)
        stored = self.sessions.get_password_hash(user_id)
        if not stored or not verify_password(data["current_password"], stored):
            raise ValidationError({"currentPassword": "当前密码不正确"})
        self.sessions.update_password(user_id, data["new_password"])
        # 保留当前设备的会话，其他设备全部下线
        self.sessions.delete_user_sessions(user_id, keep_token_hash=token_hash)

    def _issue_session(self, user_id: int) -> SessionTicket:
        """生成令牌与 CSRF、写会话表，返回交给 HTTP 层的票据。"""
        token = new_session_token()
        csrf = new_csrf_token()
        created_at = iso_utc()
        expires = session_expiry()
        # 令牌只下发给浏览器一次，库里保存的是它的 SHA-256 摘要
        self.sessions.create_session(user_id, digest_token(token), csrf, iso_utc(expires))
        return SessionTicket(
            user=self.users.get_public_user(user_id),
            token=token,
            csrf_token=csrf,
            created_at=created_at,
            expires_at=expires,
        )
