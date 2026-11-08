"""商品服务：可信身份、共用规则及整体保存。"""

from __future__ import annotations
from goods_store import GoodsStore
from goods_validation import validate_fields, validate_images

NAME = "商品管理子系统"
KEY = "goods"
STATUS = "available"
STATUS_LABEL = "已实现"
ROUTE_PREFIX = "/api/goods"
MODULE_FILE = "goods.py"

#: 规划功能：暂为空，待实现时补充。
FEATURES: list[str] = ['创建商品', '修改商品', '删除商品', '本人商品查询']


def meta() -> dict:
    """返回给 /api/subsystems 的元信息（内容为空）。"""
    return {
        "key": KEY,
        "name": NAME,
        "status": STATUS,
        "statusLabel": STATUS_LABEL,
        "routePrefix": ROUTE_PREFIX,
        "module": MODULE_FILE,
        "features": FEATURES,
        "plannedSchema": "",
    }


class GoodsService:
    def __init__(self, path):
        self.store = GoodsStore(path)

    def save(self, user_id, body, goods_id=None):
        if goods_id is not None:
            self.store.get(goods_id, user_id)
        fields, keep, raw_images = validate_fields(body, creating=goods_id is None)
        images = validate_images(raw_images)
        return self.store.save(user_id, fields, keep, images, goods_id)

    def delete(self, user_id, goods_id):
        self.store.delete(goods_id, user_id)
