"""商品管理子系统（预留占位，内容为空）。

本文件只占住位置：商品管理子系统尚未实现，暂不包含任何功能、接口或数据模型。
后续实现时在 app.py 中把 /api/goods/** 的占位路由换成真实处理函数，
并在本文件中补充相应的业务代码即可。
"""

from __future__ import annotations

NAME = "商品管理子系统"
KEY = "goods"
STATUS = "planned"
STATUS_LABEL = "规划中"
ROUTE_PREFIX = "/api/goods"
MODULE_FILE = "goods.py"

#: 规划功能：暂为空，待实现时补充。
FEATURES: list[str] = []


def meta() -> dict:
    """返回给 /api/subsystems 的元信息（内容为空）。"""
    return {
        "key": KEY,
        "name": NAME,
        "status": STATUS,
        "statusLabel": STATUS_LABEL,
        "routePrefix": ROUTE_PREFIX,
        "module": MODULE_FILE,
        "features": [],
        "plannedSchema": "",
    }
