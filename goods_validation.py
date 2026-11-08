"""Shared product rules and bounded, strict image decoding."""
import base64
import binascii
import io
import re
from decimal import Decimal
from pathlib import PurePath
from PIL import Image, UnidentifiedImageError
from validators import ValidationError

CATEGORIES = ['图书教材', '数码电子', '生活用品', '服饰配件', '运动用品', '其他']
CONDITIONS = ['全新', '几乎全新', '轻微使用痕迹', '明显使用痕迹']
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_ENCODED = 4 * ((MAX_IMAGE_BYTES + 2) // 3)
FIELDS = {'name', 'category', 'price', 'condition', 'description', 'images', 'keepImageIds'}


def validate_fields(data, *, creating):
    errors, result = {}, {}
    unknown = set(data) - FIELDS
    if unknown:
        errors['fields'] = '不允许设置字段：' + ', '.join(sorted(unknown))
    if creating and 'keepImageIds' in data:
        errors['keepImageIds'] = '创建商品不能引用旧图片'
    for key, maximum in [('name', 50), ('description', 500)]:
        value = data.get(key)
        value = value.strip() if isinstance(value, str) else ''
        if not 1 <= len(value) <= maximum or '\x00' in value:
            errors[key] = f'请填写1～{maximum}个字符'
        result[key] = value
    for key, options in [('category', CATEGORIES), ('condition', CONDITIONS)]:
        value = data.get(key)
        if value not in options:
            errors[key] = '请选择预设选项'
        result[key] = value
    price = data.get('price')
    # Strings preserve submitted notation: 1.000 must not silently become 1.0.
    if not isinstance(price, str) or len(price) > 32 or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', price.strip()):
        errors['price'] = '请输入普通十进制价格，最多两位小数'
    else:
        amount = Decimal(price.strip())
        if not Decimal('0.01') <= amount <= Decimal('99999.99'):
            errors['price'] = '价格须在0.01～99999.99元之间'
        else:
            result['price_cents'] = int(amount * 100)
    images, keep = data.get('images', []), data.get('keepImageIds', [])
    if not isinstance(images, list) or not isinstance(keep, list):
        errors['images'] = '图片及保留图片编号必须为数组'
    elif len(images) + len(keep) > 3:
        errors['images'] = f'最多选择3张图片，当前选择了{len(images) + len(keep)}张'
    elif any(type(i) is not int or i <= 0 for i in keep) or len(set(keep)) != len(keep):
        errors['images'] = '旧图片编号不合法或重复'
    if errors:
        raise ValidationError(errors)
    return result, keep, images


def validate_images(items):
    result = []
    for index, item in enumerate(items, 1):
        try:
            if not isinstance(item, dict) or set(item) != {'name', 'data'}:
                raise ValueError('图片须包含文件名和标准Base64内容')
            name, encoded = item['name'], item['data']
            extension = PurePath(name).suffix.lower() if isinstance(name, str) else ''
            expected = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG'}.get(extension)
            if not expected:
                raise ValueError('仅支持.jpg、.jpeg、.png')
            if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_ENCODED:
                raise ValueError('图片内容为空或超过2 MiB')
            raw = base64.b64decode(encoded, validate=True)
            if base64.b64encode(raw).decode('ascii') != encoded:
                raise ValueError('须使用规范的标准Base64编码')
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError('图片超过2 MiB')
            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
                if image.format != expected:
                    raise ValueError('图片实际格式与扩展名不符')
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    raise ValueError('图片总像素不能超过2000万')
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
            result.append({'content': raw, 'mime': 'image/jpeg' if expected == 'JPEG' else 'image/png',
                           'width': width, 'height': height})
        except (ValueError, TypeError, binascii.Error, UnidentifiedImageError, OSError,
                SyntaxError, Image.DecompressionBombError) as exc:
            raise ValidationError({f'images[{index}]': f'第{index}张图片：{exc}'}) from exc
    return result
