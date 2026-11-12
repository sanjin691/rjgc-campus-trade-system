"""HTTP 业务异常。

``dispatch`` 捕获它，把 ``status`` 与 ``message`` 翻译成统一响应；
业务层（如 user_service.py）只负责抛出，不关心响应长什么样。
"""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        """保存状态码与错误信息。"""
        super().__init__(message)
        self.status = status
        self.message = message
