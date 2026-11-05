"""注册与个人信息字段校验。"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
STUDENT_NO_RE = re.compile(r"^[A-Za-z0-9]{4,20}$")
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class ValidationError(Exception):
    def __init__(self, errors: dict[str, str], message: str = "请检查填写内容"):
        """保存字段级错误与整体提示。"""
        super().__init__(message)
        self.errors = errors
        self.message = message


def _text(data: dict, key: str) -> str:
    """取字符串字段并去除首尾空白，非法类型返回空串。"""
    value = data.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def password_problem(password: str) -> str | None:
    """检查密码强度，返回具体原因；合格时返回 None。"""
    if len(password) < 8:
        return "密码至少需要 8 个字符"
    if len(password) > 64:
        return "密码不能超过 64 个字符"
    if not re.search(r"[A-Za-z]", password):
        return "密码需要包含英文字母"
    if not re.search(r"\d", password):
        return "密码需要包含数字"
    return None


def validate_registration(data: dict) -> dict:
    """校验注册数据并返回规范化后的字典，不合法时抛 ValidationError。"""
    errors: dict[str, str] = {}

    student_no = _text(data, "studentNo").upper()
    if not student_no:
        errors["studentNo"] = "请输入学号"
    elif not STUDENT_NO_RE.match(student_no):
        errors["studentNo"] = "学号为 4–20 位字母或数字"

    name = _text(data, "name")
    if not name:
        errors["name"] = "请输入姓名"
    elif not 2 <= len(name) <= 20:
        errors["name"] = "姓名长度需为 2–20 个字符"
    elif CONTROL_CHARS_RE.search(name):
        errors["name"] = "姓名包含不可用字符"

    email = _text(data, "email").lower()
    if not email:
        errors["email"] = "请输入邮箱"
    elif len(email) > 120 or not EMAIL_RE.match(email):
        errors["email"] = "请输入有效的邮箱地址"

    phone = _text(data, "phone")
    if phone and not PHONE_RE.match(phone):
        errors["phone"] = "请输入有效的 11 位手机号"

    college = _text(data, "college")
    if len(college) > 30:
        errors["college"] = "学院名称不能超过 30 个字符"

    major = _text(data, "major")
    if len(major) > 40:
        errors["major"] = "专业名称不能超过 40 个字符"

    password = data.get("password", "")
    if not isinstance(password, str):
        password = ""
    problem = password_problem(password)
    if problem:
        errors["password"] = problem

    confirm_password = data.get("confirmPassword", "")
    if not isinstance(confirm_password, str):
        confirm_password = ""
    if not confirm_password:
        errors["confirmPassword"] = "请再次输入密码"
    elif password != confirm_password:
        errors["confirmPassword"] = "两次输入的密码不一致"

    if errors:
        raise ValidationError(errors)

    return {
        "student_no": student_no,
        "name": name,
        "email": email,
        "phone": phone,
        "college": college,
        "major": major,
        "password": password,
    }


def validate_profile_update(data: dict) -> dict:
    """校验资料修改数据（不含学号与密码）。"""
    errors: dict[str, str] = {}

    name = _text(data, "name")
    if not name:
        errors["name"] = "请输入姓名"
    elif not 2 <= len(name) <= 20:
        errors["name"] = "姓名长度需为 2–20 个字符"
    elif CONTROL_CHARS_RE.search(name):
        errors["name"] = "姓名包含不可用字符"

    email = _text(data, "email").lower()
    if not email:
        errors["email"] = "请输入邮箱"
    elif len(email) > 120 or not EMAIL_RE.match(email):
        errors["email"] = "请输入有效的邮箱地址"

    phone = _text(data, "phone")
    if phone and not PHONE_RE.match(phone):
        errors["phone"] = "请输入有效的 11 位手机号"

    college = _text(data, "college")
    if len(college) > 30:
        errors["college"] = "学院名称不能超过 30 个字符"

    major = _text(data, "major")
    if len(major) > 40:
        errors["major"] = "专业名称不能超过 40 个字符"

    if errors:
        raise ValidationError(errors)

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "college": college,
        "major": major,
    }


def validate_student_no(value: object) -> str:
    """管理员更改学号/工号时使用：规范化（去空格、转大写）并校验格式。"""
    student_no = value.strip().upper() if isinstance(value, str) else ""
    if not student_no:
        raise ValidationError({"studentNo": "请输入学号/工号"})
    if not STUDENT_NO_RE.match(student_no):
        raise ValidationError({"studentNo": "学号为 4–20 位字母或数字"})
    return student_no


def validate_password_change(data: dict, *, require_current: bool) -> dict:
    """校验“修改密码 / 重置密码”的请求体。"""
    errors: dict[str, str] = {}
    current = data.get("currentPassword", "")
    if require_current and (not isinstance(current, str) or not current):
        errors["currentPassword"] = "请输入当前密码"

    new_password = data.get("newPassword", "")
    if not isinstance(new_password, str):
        new_password = ""
    problem = password_problem(new_password)
    if problem:
        errors["newPassword"] = problem

    confirm = data.get("confirmPassword", "")
    if not isinstance(confirm, str):
        confirm = ""
    if not confirm:
        errors["confirmPassword"] = "请再次输入新密码"
    elif new_password != confirm:
        errors["confirmPassword"] = "两次输入的新密码不一致"

    if errors:
        raise ValidationError(errors)

    result = {"new_password": new_password}
    if require_current:
        result["current_password"] = current
    return result
