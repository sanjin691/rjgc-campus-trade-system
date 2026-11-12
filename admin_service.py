"""管理员的账号管控：查看、编辑、重置密码、删除、新增管理员。

与普通用户的自助服务分开，便于独立演进和测试：
管理端规则（如“不能删最后一名管理员”）变化时，不需要碰登录与改密逻辑。
"""

from __future__ import annotations

from database import SessionStore, UserStore
from errors import ApiError
from validators import (
    ValidationError,
    validate_password_change,
    validate_profile_update,
    validate_registration,
    validate_student_no,
)


class UserAdminService:
    """管理员能做的 5 件事：看列表、改资料、重置密码、删用户、加管理员。"""

    def __init__(self, users: UserStore, sessions: SessionStore) -> None:
        self.users = users
        self.sessions = sessions

    def list_users(self) -> list[dict]:
        """查看全部用户（管理员在前）。"""
        return self.users.list_all_users()

    def update_user(self, target_id: int, body: dict) -> dict:
        """修改任意用户资料，可一并更正学号/工号。"""
        target = self.users.get_public_user(target_id)
        if not target:
            raise ApiError(404, "用户不存在")

        data = validate_profile_update(body)
        taken = self.users.account_taken(email=data["email"], exclude_user_id=target_id)
        if "email" in taken:
            raise ValidationError({"email": "该邮箱已被其他用户使用"})

        new_student_no: str | None = None
        raw_student_no = body.get("studentNo")
        if isinstance(raw_student_no, str) and raw_student_no.strip():
            candidate = validate_student_no(raw_student_no)
            if candidate != target["studentNo"]:
                if "studentNo" in self.users.account_taken(student_no=candidate):
                    raise ValidationError({"studentNo": "该学号或工号已被使用"})
                new_student_no = candidate

        return self.users.update_user(target_id, data, student_no=new_student_no)

    def reset_password(self, target_id: int, body: dict) -> dict:
        """重置指定用户的密码：不需要原密码，重置后其全部会话失效。"""
        target = self.users.get_public_user(target_id)
        if not target:
            raise ApiError(404, "用户不存在")
        data = validate_password_change(body, require_current=False)
        # 管理员不知道也不需要原密码：直接覆盖哈希
        self.sessions.update_password(target_id, data["new_password"])
        self.sessions.delete_user_sessions(target_id)
        return target

    def delete_user(self, operator_id: int, target_id: int) -> dict:
        """删除账号：不能删自己，也不能删掉最后一名管理员。"""
        # 两道保护：不能删自己，也不能删最后一名管理员
        if target_id == operator_id:
            raise ApiError(400, "不能删除当前登录的账号")
        target = self.users.get_public_user(target_id)
        if not target:
            raise ApiError(404, "用户不存在")
        if target["role"] == "admin" and self.users.count_admins() <= 1:
            raise ApiError(400, "至少要保留一名管理员")
        self.users.delete_user(target_id)
        return target

    def create_admin(self, body: dict) -> dict:
        """新增管理员账号（复用注册校验，角色固定为 admin）。"""
        data = validate_registration(body)
        errors: dict[str, str] = {}
        taken = self.users.account_taken(student_no=data["student_no"], email=data["email"])
        if "studentNo" in taken:
            errors["studentNo"] = "该学号或工号已注册"
        if "email" in taken:
            errors["email"] = "该邮箱已被使用"
        if errors:
            raise ValidationError(errors)
        # 复用注册校验，角色固定为 admin，避免注册接口被用来提权
        return self.users.create_user(data, role="admin")
