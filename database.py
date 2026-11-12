"""SQLite 数据访问层：只负责“数据怎么存、怎么取”。

本模块不包含业务规则，也不感知 HTTP，被 ``app.py`` 调用。按聚合拆成两个仓储类：

- :class:`UserStore`    用户账号与资料（8 个公开方法）
- :class:`SessionStore` 会话令牌与登录凭据（7 个公开方法）

公共设施：

- :func:`connect`            每次操作打开一个连接，正常提交、异常回滚；
- :func:`initialize_database` 建表、补列、创建默认管理员、清理过期会话。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from security import hash_password, iso_utc

DEFAULT_ADMIN_STUDENT_NO = "ADMIN001"
DEFAULT_ADMIN_NAME = "系统管理员"
DEFAULT_ADMIN_EMAIL = "admin@campus.edu.cn"
DEFAULT_ADMIN_PASSWORD = "Admin@123"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_no    TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL COLLATE NOCASE UNIQUE,
    phone         TEXT NOT NULL DEFAULT '',
    college       TEXT NOT NULL DEFAULT '',
    major         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
"""


# --------------------------------------------------------------------------- 公共设施


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    """打开一个数据库连接，正常结束提交、异常回滚、最后关闭。

    每次操作使用独立连接，避免请求之间共享连接带来的线程安全问题；
    ``busy_timeout`` 让并发写入时最多等待 5 秒而不是立刻报错。
    """
    connection = sqlite3.connect(Path(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # 并发写入最多等 5 秒，避免立刻抛 database is locked
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
        # 正常结束提交；下面一行在异常时回滚，保证不会“改一半”
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _public_user(row: sqlite3.Row) -> dict:
    """把数据库行转成对外字典（camelCase），并去掉密码哈希。"""
    return {
        "id": row["id"],
        "studentNo": row["student_no"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "college": row["college"],
        "major": row["major"],
        "role": row["role"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def ensure_default_admin(connection: sqlite3.Connection) -> None:
    """若库中没有任何管理员，则创建默认管理员账号。

    只在初始化时调用；管理员已存在时什么也不做，因此可以安全地重复执行。
    """
    existing_admin = connection.execute(
        "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
    ).fetchone()
    if existing_admin:
        return
    now = iso_utc()
    try:
        connection.execute(
            """
            INSERT INTO users (
                student_no, name, email, phone, college, major,
                role, password_hash, created_at, updated_at
            ) VALUES (?, ?, ?, '', '', '', 'admin', ?, ?, ?)
            """,
            (
                DEFAULT_ADMIN_STUDENT_NO,
                DEFAULT_ADMIN_NAME,
                DEFAULT_ADMIN_EMAIL,
                hash_password(DEFAULT_ADMIN_PASSWORD),
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise RuntimeError(
            "默认管理员账号与现有用户冲突，请删除 data/user_management.db 后重新启动。"
        ) from exc


def initialize_database(path: str | Path) -> None:
    """准备数据库：建表、兼容旧库补列、确保管理员、清理过期会话。

    在应用启动时调用一次，可以重复执行（全部使用 ``IF NOT EXISTS`` 与幂等判断）。
    """
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "role" not in columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
        ensure_default_admin(connection)
    with connect(database_path) as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso_utc(),))


# --------------------------------------------------------------------------- 用户


class UserStore:
    """用户账号与资料的读写。

    8 个公开方法，按用途分为三组：

    - 查询：:meth:`get_public_user`、:meth:`get_user_for_login`、:meth:`account_taken`
    - 写入：:meth:`create_user`、:meth:`update_user`
    - 管理：:meth:`list_all_users`、:meth:`count_admins`、:meth:`delete_user`
    """

    def __init__(self, path: str | Path) -> None:
        """绑定数据库文件路径；建表与初始化由 :func:initialize_database 负责。"""
        self.path = Path(path)

    def get_public_user(self, user_id: int) -> dict | None:
        """按主键查询用户，返回脱敏字典；用户不存在时返回 ``None``。"""
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return _public_user(row) if row else None

    def get_user_for_login(self, account: str) -> sqlite3.Row | None:
        """按“学号/工号或邮箱”查询用户，忽略大小写。

        返回原始行（含 ``password_hash``），供调用方做密码校验；
        查不到时返回 ``None``，调用方仍需执行一次假哈希比较以防时序枚举。
        """
        with connect(self.path) as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE student_no = ? COLLATE NOCASE
                   OR email = ? COLLATE NOCASE
                """,
                (account, account),
            ).fetchone()

    def account_taken(
        self,
        *,
        student_no: str | None = None,
        email: str | None = None,
        exclude_user_id: int | None = None,
    ) -> set[str]:
        """检查学号/工号、邮箱是否已被占用。

        参数均为可选，只检查实际传入的字段；``exclude_user_id`` 用于修改资料时
        排除用户自己。返回被占用的字段名集合，例如 ``{"studentNo", "email"}``。
        """
        taken: set[str] = set()
        with connect(self.path) as connection:
            if student_no:
                query = "SELECT 1 FROM users WHERE student_no = ? COLLATE NOCASE"
                params: list[object] = [student_no]
                if exclude_user_id is not None:
                    query += " AND id <> ?"
                    params.append(exclude_user_id)
                if connection.execute(query, params).fetchone() is not None:
                    taken.add("studentNo")
            if email:
                query = "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE"
                params = [email]
                if exclude_user_id is not None:
                    query += " AND id <> ?"
                    params.append(exclude_user_id)
                if connection.execute(query, params).fetchone() is not None:
                    taken.add("email")
        return taken

    def create_user(self, data: dict, role: str = "user") -> dict:
        """写入一个新用户并返回脱敏字典。

        ``data`` 为已校验的注册数据（含明文 ``password``，在此处哈希）；
        ``role`` 默认普通用户，管理员新增管理员时传 ``"admin"``。
        """
        now = iso_utc()
        with connect(self.path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    student_no, name, email, phone, college, major,
                    role, password_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["student_no"],
                    data["name"],
                    data["email"],
                    data["phone"],
                    data["college"],
                    data["major"],
                    role,
                    hash_password(data["password"]),
                    now,
                    now,
                ),
            )
            user_id = int(cursor.lastrowid)
        return self.get_public_user(user_id)  # type: ignore[return-value]

    def update_user(
        self,
        user_id: int,
        profile: dict,
        *,
        student_no: str | None = None,
    ) -> dict | None:
        """更新用户的姓名、邮箱、手机号、学院、专业。

        传入 ``student_no`` 时一并更正学号/工号（仅管理员可用，调用方负责校验）；
        不传则保持原值。返回更新后的脱敏字典，用户不存在时返回 ``None``。
        """
        now = iso_utc()
        with connect(self.path) as connection:
            connection.execute(
                """
                UPDATE users
                SET name = ?, email = ?, phone = ?, college = ?, major = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    profile["name"],
                    profile["email"],
                    profile["phone"],
                    profile["college"],
                    profile["major"],
                    now,
                    user_id,
                ),
            )
            if student_no is not None:
                connection.execute(
                    "UPDATE users SET student_no = ?, updated_at = ? WHERE id = ?",
                    (student_no, now, user_id),
                )
        return self.get_public_user(user_id)

    def list_all_users(self) -> list[dict]:
        """返回全部用户（脱敏），管理员排在前面，其余按注册顺序。"""
        with connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, id ASC
                """
            ).fetchall()
        return [_public_user(row) for row in rows]

    def count_admins(self) -> int:
        """统计管理员数量，用于“至少保留一名管理员”的保护。"""
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM users WHERE role = 'admin'"
            ).fetchone()
        return int(row["total"]) if row else 0

    def delete_user(self, user_id: int) -> None:
        """删除用户。

        该用户的会话通过 ``sessions.user_id`` 上的 ``ON DELETE CASCADE`` 一并删除，
        因此这里不需要手工清理会话。
        """
        with connect(self.path) as connection:
            # 会话由外键 ON DELETE CASCADE 一并删除，这里不用手工清
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))


# --------------------------------------------------------------------------- 会话与凭据


class SessionStore:
    """会话令牌与登录凭据的持久化。

    密码哈希与会话同属“认证数据”（密码决定能否建立会话，会话决定请求能否通过），
    因此放在同一个仓储里，共 7 个公开方法：

    - 会话生命周期：:meth:`create_session`、:meth:`find_session`、
      :meth:`delete_session`、:meth:`delete_user_sessions`、:meth:`cleanup_sessions`
    - 密码凭据：:meth:`get_password_hash`、:meth:`update_password`
    """

    def __init__(self, path: str | Path) -> None:
        """绑定数据库文件路径；表结构由 :func:initialize_database 负责创建。"""
        self.path = Path(path)

    def create_session(
        self, user_id: int, token_hash: str, csrf_token: str, expires_at: str
    ) -> None:
        """写入一条会话记录。

        只保存令牌的 SHA-256 摘要（``token_hash``），不保存原始令牌；
        ``expires_at`` 为 ISO-8601 UTC 字符串，由调用方计算。
        """
        with connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, user_id, csrf_token, iso_utc(), expires_at),
            )

    def find_session(self, token_hash: str) -> dict | None:
        """按令牌摘要查询“未过期”的会话，并带出所属用户。

        返回 ``{"csrfToken", "createdAt", "expiresAt", "user"}``；
        令牌不存在或已过期时返回 ``None``。
        """
        with connect(self.path) as connection:
            row = connection.execute(
                """
                SELECT s.csrf_token, s.created_at, s.expires_at, u.*
                FROM sessions AS s
                JOIN users AS u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?
                """,
                (token_hash, iso_utc()),
            ).fetchone()
        if not row:
            return None
        return {
            "csrfToken": row["csrf_token"],
            "createdAt": row["created_at"],
            "expiresAt": row["expires_at"],
            "user": _public_user(row),
        }

    def delete_session(self, token_hash: str) -> None:
        """删除单条会话（退出登录时调用）。"""
        with connect(self.path) as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: int, keep_token_hash: str | None = None) -> None:
        """删除某个用户的全部会话。

        ``keep_token_hash`` 用于“本人修改密码”场景：保留当前设备的会话，
        让用户不必重新登录，同时把其他设备踢下线。
        """
        with connect(self.path) as connection:
            # 保留当前设备的令牌：本人改密后本机不用重新登录
            if keep_token_hash:
                connection.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
                    (user_id, keep_token_hash),
                )
            else:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def cleanup_sessions(self) -> None:
        """删除所有已过期会话，避免会话表随时间无限增长。"""
        with connect(self.path) as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso_utc(),))

    def get_password_hash(self, user_id: int) -> str | None:
        """读取用户当前的密码哈希，用于校验“当前密码”；用户不存在返回 ``None``。"""
        with connect(self.path) as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row["password_hash"] if row else None

    def update_password(self, user_id: int, password: str) -> None:
        """设置新密码：内部完成加盐哈希，并刷新 ``updated_at``。"""
        with connect(self.path) as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(password), iso_utc(), user_id),
            )
