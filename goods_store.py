"""Product persistence; every product/image mutation is one transaction."""
from database import connect
from security import iso_utc
from validators import ValidationError

SCHEMA = '''
CREATE TABLE IF NOT EXISTS goods (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 publisher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, category TEXT NOT NULL, price_cents INTEGER NOT NULL,
 condition TEXT NOT NULL, description TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goods_images (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 goods_id INTEGER NOT NULL REFERENCES goods(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, mime TEXT NOT NULL, content BLOB NOT NULL,
 width INTEGER NOT NULL, height INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS goods_owner ON goods(publisher_id, created_at, id);
CREATE INDEX IF NOT EXISTS images_goods ON goods_images(goods_id, position);
'''


class GoodsAccessError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class GoodsStore:
    def __init__(self, path):
        self.path = path
        with connect(path) as conn:
            conn.executescript(SCHEMA)

    @staticmethod
    def owned(conn, goods_id, user_id):
        row = conn.execute('SELECT * FROM goods WHERE id=?', (goods_id,)).fetchone()
        if row is None:
            raise GoodsAccessError(404, '商品不存在')
        if row['publisher_id'] != user_id:
            raise GoodsAccessError(403, '只能访问或修改本人商品')
        return row

    @staticmethod
    def present(conn, row):
        return {'id': row['id'], 'publisherId': row['publisher_id'], 'name': row['name'],
                'category': row['category'], 'price': f"{row['price_cents'] / 100:.2f}",
                'condition': row['condition'], 'description': row['description'],
                'createdAt': row['created_at'], 'updatedAt': row['updated_at'],
                'images': [{'id': i['id'], 'url': f"/api/goods/images/{i['id']}"}
                           for i in conn.execute('SELECT id FROM goods_images WHERE goods_id=? ORDER BY position,id', (row['id'],))]}

    def list(self, user_id):
        with connect(self.path) as conn:
            return [self.present(conn, r) for r in conn.execute(
                'SELECT * FROM goods WHERE publisher_id=? ORDER BY created_at DESC,id DESC', (user_id,)).fetchall()]

    def get(self, goods_id, user_id):
        with connect(self.path) as conn:
            return self.present(conn, self.owned(conn, goods_id, user_id))

    def image(self, image_id, user_id):
        with connect(self.path) as conn:
            row = conn.execute('SELECT * FROM goods_images WHERE id=?', (image_id,)).fetchone()
            if row is None:
                raise GoodsAccessError(404, '图片不存在')
            self.owned(conn, row['goods_id'], user_id)
            return row['content'], row['mime']

    @staticmethod
    def insert_images(conn, goods_id, images, start=0):
        for position, image in enumerate(images, start):
            conn.execute('INSERT INTO goods_images(goods_id,position,mime,content,width,height) VALUES(?,?,?,?,?,?)',
                         (goods_id, position, image['mime'], image['content'], image['width'], image['height']))

    def save(self, user_id, fields, keep, images, goods_id=None):
        with connect(self.path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            now = iso_utc()
            if goods_id is None:
                cur = conn.execute('INSERT INTO goods(publisher_id,name,category,price_cents,condition,description,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                                   (user_id, fields['name'], fields['category'], fields['price_cents'], fields['condition'], fields['description'], now, now))
                goods_id = cur.lastrowid
            else:
                self.owned(conn, goods_id, user_id)
                old = {r['id'] for r in conn.execute('SELECT id FROM goods_images WHERE goods_id=?', (goods_id,))}
                if not set(keep) <= old:
                    raise ValidationError({'images': '引用的旧图片不属于当前商品'})
                conn.execute('UPDATE goods SET name=?,category=?,price_cents=?,condition=?,description=?,updated_at=? WHERE id=?',
                             (fields['name'], fields['category'], fields['price_cents'], fields['condition'], fields['description'], now, goods_id))
                for image_id in old - set(keep):
                    conn.execute('DELETE FROM goods_images WHERE id=?', (image_id,))
                for position, image_id in enumerate(keep):
                    conn.execute('UPDATE goods_images SET position=? WHERE id=?', (position, image_id))
            self.insert_images(conn, goods_id, images, len(keep))
            return self.present(conn, self.owned(conn, goods_id, user_id))
