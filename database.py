"""SQLite 数据访问：用户表与会话表。"""

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


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(users)").fetchall()
            }
            if "role" not in columns:
                connection.execute(
                    "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
                )
            self._ensure_admin(connection)
        self.cleanup_sessions()

    @staticmethod
    def _ensure_admin(connection: sqlite3.Connection) -> None:
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

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict:
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

    def get_public_user(self, user_id: int) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return self._public_user(row) if row else None

    def get_user_for_login(self, account: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM users
                WHERE student_no = ? COLLATE NOCASE
                   OR email = ? COLLATE NOCASE
                """,
                (account, account),
            ).fetchone()

    def student_no_exists(self, student_no: str) -> bool:
        with self.connection() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM users WHERE student_no = ? COLLATE NOCASE",
                    (student_no,),
                ).fetchone()
                is not None
            )

    def email_exists(self, email: str, exclude_user_id: int | None = None) -> bool:
        query = "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE"
        params: list[object] = [email]
        if exclude_user_id is not None:
            query += " AND id <> ?"
            params.append(exclude_user_id)
        with self.connection() as connection:
            return connection.execute(query, params).fetchone() is not None

    def create_user(self, data: dict, role: str = "user") -> dict:
        now = iso_utc()
        with self.connection() as connection:
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

    def update_profile(self, user_id: int, data: dict) -> dict | None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE users
                SET name = ?, email = ?, phone = ?, college = ?, major = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    data["name"],
                    data["email"],
                    data["phone"],
                    data["college"],
                    data["major"],
                    iso_utc(),
                    user_id,
                ),
            )
        return self.get_public_user(user_id)

    def list_all_users(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM users
                ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, id ASC
                """
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def create_session(
        self, user_id: int, token_hash: str, csrf_token: str, expires_at: str
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, user_id, csrf_token, iso_utc(), expires_at),
            )

    def find_session(self, token_hash: str) -> dict | None:
        with self.connection() as connection:
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
            "user": self._public_user(row),
        }

    def delete_session(self, token_hash: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def delete_user_sessions(self, user_id: int, keep_token_hash: str | None = None) -> None:
        with self.connection() as connection:
            if keep_token_hash:
                connection.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
                    (user_id, keep_token_hash),
                )
            else:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def count_admins(self) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM users WHERE role = 'admin'"
            ).fetchone()
        return int(row["total"]) if row else 0

    def get_password_hash(self, user_id: int) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row["password_hash"] if row else None

    def update_password(self, user_id: int, password: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(password), iso_utc(), user_id),
            )

    def update_student_no(self, user_id: int, student_no: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE users SET student_no = ?, updated_at = ? WHERE id = ?",
                (student_no, iso_utc(), user_id),
            )

    def delete_user(self, user_id: int) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def cleanup_sessions(self) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso_utc(),))
