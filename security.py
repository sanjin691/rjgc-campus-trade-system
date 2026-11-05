"""安全原语：密码哈希、会话令牌与时间工具。

本模块只做“纯计算”，不访问数据库、不感知 HTTP，因此可以被任意层复用，
也是单元测试最容易覆盖的部分：

- 密码：PBKDF2-HMAC-SHA256 加盐哈希与常量时间校验；
- 令牌：会话令牌、CSRF 令牌的生成与摘要；
- 时间：统一使用 UTC + ISO-8601（``2026-11-04T12:00:00Z``），避免时区歧义。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

#: 哈希算法标识，会写进哈希串，便于将来平滑升级算法
PBKDF2_ALGORITHM = "pbkdf2_sha256"
#: 迭代次数：越高越难暴力破解，但登录耗时也越长（26 万次约 100ms 量级）
PBKDF2_ITERATIONS = 260_000
#: 会话 Cookie 的名称
SESSION_COOKIE = "user_session"
#: 会话有效期（小时）
SESSION_HOURS = 24


def utc_now() -> datetime:
    """返回当前 UTC 时间（带时区信息，不会得到“裸”时间）。"""
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    """把时间格式化为 ISO-8601 UTC 字符串，如 ``2026-11-04T12:00:00Z``。

    不传参数时取当前时间；统一使用秒级精度，保证字符串可直接比较排序。
    """
    value = value or utc_now()
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """把 :func:`iso_utc` 生成的字符串解析回带时区的 ``datetime``。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hash_password(password: str) -> str:
    """对明文密码做加盐哈希。

    返回 ``pbkdf2_sha256$迭代次数$盐$摘要`` 形式的字符串：算法与迭代次数随哈希
    一起存储，将来调整参数时老密码仍可校验。
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """校验明文密码与存储的哈希是否匹配。

    使用存储里的算法与迭代次数重算，并用 :func:`hmac.compare_digest` 做
    **常量时间比较**，避免通过响应时间猜测摘要；哈希串格式非法时返回 ``False``。
    """
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != PBKDF2_ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    """生成随机会话令牌（32 字节，URL 安全编码），只下发给浏览器。"""
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    """生成随机 CSRF 令牌，随会话下发，写操作时回传校验。"""
    return secrets.token_urlsafe(32)


def digest_token(token: str) -> str:
    """计算令牌的 SHA-256 摘要。

    数据库只保存摘要：即使数据库泄露，攻击者也无法据此伪造 Cookie。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry() -> datetime:
    """返回会话的过期时间（当前时间 + :data:`SESSION_HOURS` 小时）。"""
    return utc_now() + timedelta(hours=SESSION_HOURS)
