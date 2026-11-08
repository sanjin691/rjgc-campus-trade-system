"""Product rule, HTTP authorization and transaction tests, using temporary data."""
import base64
import io
import http.client
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from PIL import Image
from test_api import ApiClient
from app import create_server
from database import connect
from security import digest_token
from goods_validation import validate_fields, validate_images
from validators import ValidationError


def fields(**changes):
    result = {'name': '高等数学教材', 'category': '图书教材', 'price': '18.50',
              'condition': '轻微使用痕迹', 'description': '无缺页，有少量笔记', 'images': []}
    result.update(changes)
    return result


def picture(width=20, height=20):
    data = io.BytesIO()
    Image.new('RGB', (width, height), 'red').save(data, format='PNG')
    return {'name': 'book.png', 'data': base64.b64encode(data.getvalue()).decode()}


class RuleTests(unittest.TestCase):
    def test_price_notation_and_boundaries(self):
        for value in ['0.01', '99999.99', '1', '1.5', ' 1.50 ']:
            self.assertIn('price_cents', validate_fields(fields(price=value), creating=True)[0])
        for value in ['0', '-1', '+1', '1e2', '1.000', '100000', '', 1]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_fields(fields(price=value), creating=True)

    def test_unicode_fields_and_options(self):
        validate_fields(fields(name='😀' * 50, description='文' * 500), creating=True)
        for change in [{'name': ' '}, {'name': '文'*51}, {'description': '文'*501},
                       {'category': []}, {'condition': '未知'}, {'publisherId': 1}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                validate_fields(fields(**change), creating=True)

    def test_strict_image_decoding(self):
        good = picture()
        self.assertEqual(validate_images([good])[0]['mime'], 'image/png')
        for item in [dict(good, name='x.jpg'), dict(good, data=' '+good['data']),
                     dict(good, data='data:image/png;base64,'+good['data']),
                     dict(good, data='bad_'), dict(good, data=base64.b64encode(b'bad image').decode())]:
            with self.subTest(item=item['name']), self.assertRaises(ValidationError):
                validate_images([item])

    def test_image_count_and_duplicate_ids(self):
        for values in [{'images': [picture()]*4}, {'keepImageIds': [1,1]}, {'keepImageIds': [True]}]:
            with self.assertRaises(ValidationError):
                validate_fields(fields(**values), creating=False)

    def test_pixel_limit_before_load(self):
        with self.assertRaises(ValidationError):
            validate_images([picture(5001,4000)])

    def test_exact_image_byte_limit(self):
        item = picture()
        raw = base64.b64decode(item['data'])
        # PNG readers permit trailing bytes; retain a valid, decodable PNG.
        item['data'] = base64.b64encode(raw + b'\0'*(2097152-len(raw))).decode()
        self.assertEqual(len(validate_images([item])[0]['content']),2097152)
        item['data'] = base64.b64encode(raw + b'\0'*(2097153-len(raw))).decode()
        with self.assertRaises(ValidationError):validate_images([item])


class GoodsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = create_server('127.0.0.1', 0, Path(cls.temp.name)/'test.db')
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def client(self):
        c = ApiClient('127.0.0.1', self.server.server_address[1])
        (_, _), payload = c.register()
        self.assertEqual(c.login(payload['studentNo'], payload['password'])[0],200)
        return c

    def test_create_update_and_remove_images(self):
        c=self.client()
        status, response=c.post('/api/goods', fields(images=[picture()]))
        self.assertEqual(status,201,response)
        original=response['data'];gid=original['id']
        status,response=c.patch(f'/api/goods/{gid}',fields(price='80'))
        self.assertEqual(status,200,response)
        result=response['data']
        self.assertEqual(result['price'],'80.00');self.assertEqual(result['images'],[])
        for key in ['id','publisherId','createdAt']:self.assertEqual(result[key],original[key])
        self.assertEqual(c.get('/api/goods')[1]['data']['items'][0]['id'],gid)

    def test_permissions_and_illegal_fields(self):
        owner,other=self.client(),self.client()
        gid=owner.post('/api/goods',fields())[1]['data']['id']
        self.assertEqual(other.patch(f'/api/goods/{gid}',fields())[0],403)
        self.assertEqual(other.get(f'/api/goods/{gid}')[0],403)
        self.assertEqual(owner.post('/api/goods',fields(publisherId=3))[0],422)
        self.assertEqual(owner.patch(f'/api/goods/{gid}',fields(id=gid))[0],422)
        self.assertEqual(owner.patch('/api/goods/999999',fields())[0],404)
        self.assertEqual(owner.post('/api/goods',fields(),csrf=False)[0],403)
        anonymous=ApiClient('127.0.0.1',self.server.server_address[1])
        self.assertEqual(anonymous.post('/api/goods',fields())[0],401)

    def test_old_image_ownership_and_rollback(self):
        c=self.client()
        original=c.post('/api/goods',fields(images=[picture()]))[1]['data'];gid=original['id']
        foreign=c.post('/api/goods',fields(images=[picture()]))[1]['data']['images'][0]['id']
        self.assertEqual(c.patch(f'/api/goods/{gid}',fields(keepImageIds=[foreign]))[0],422)
        store=self.server.RequestHandlerClass.application.goods.store
        # Fail after field update/deletion: SQLite must roll back old image and text.
        with patch.object(store,'insert_images',side_effect=RuntimeError('injected failure')):
            self.assertEqual(c.patch(f'/api/goods/{gid}',fields(name='不应生效'))[0],500)
            self.assertEqual(c.post('/api/goods',fields())[0],500)
        self.assertEqual(c.get(f'/api/goods/{gid}')[1]['data'],original)
        self.assertEqual(len(c.get('/api/goods')[1]['data']['items']),2)

    def test_invalid_image_does_not_write(self):
        c=self.client()
        bad=dict(picture(),data='broken')
        self.assertEqual(c.post('/api/goods',fields(images=[picture(),bad]))[0],422)
        self.assertEqual(c.get('/api/goods')[1]['data']['items'],[])

    def test_three_maximum_images_and_private_image(self):
        c, other = self.client(), self.client()
        item=picture();raw=base64.b64decode(item['data'])
        item['data']=base64.b64encode(raw+b'\0'*(2097152-len(raw))).decode()
        status,response=c.post('/api/goods',fields(images=[item]*3))
        self.assertEqual(status,201,response)
        images=response['data']['images'];self.assertEqual(len(images),3)
        for client,status in [(c,200),(other,403)]:
            connection=http.client.HTTPConnection(c.host,c.port)
            connection.request('GET',images[0]['url'],headers={'Cookie':f'user_session={client.cookie}'})
            result=connection.getresponse();content=result.read();connection.close()
            self.assertEqual(result.status,status)
            if status==200:
                self.assertEqual(len(content),2097152)
                self.assertEqual(result.getheader('Content-Type'),'image/png')

    def test_expired_session_cannot_save(self):
        c=self.client()
        gid=c.post('/api/goods',fields())[1]['data']['id']
        with connect(self.server.RequestHandlerClass.application.goods.store.path) as conn:
            conn.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE token_hash=?",(digest_token(c.cookie),))
        self.assertEqual(c.patch(f'/api/goods/{gid}',fields(name='不应修改'))[0],401)

    def test_request_framing_limits(self):
        def raw(path,headers):
            conn=http.client.HTTPConnection('127.0.0.1',self.server.server_address[1],timeout=3)
            conn.putrequest('POST',path)
            for name,value in headers:conn.putheader(name,value)
            conn.endheaders()
            response=conn.getresponse();response.read();conn.close()
            return response.status
        self.assertEqual(raw('/api/goods',[]),411)
        self.assertEqual(raw('/api/goods',[('Content-Length','1'),('Content-Length','2')]),400)
        self.assertEqual(raw('/api/goods',[('Transfer-Encoding','chunked')]),400)
        self.assertEqual(raw('/api/goods',[('Content-Length','-1')]),400)
        self.assertEqual(raw('/api/goods',[('Content-Length',str(9*1024*1024+1))]),413)
        self.assertEqual(raw('/api/auth/login',[('Content-Length',str(64*1024+1))]),413)
        self.assertEqual(raw('/api/goods',[('Content-Encoding','gzip'),('Content-Length','0')]),415)

    def test_account_with_products_cannot_be_deleted(self):
        c=self.client();gid=c.post('/api/goods',fields())[1]['data']['id']
        user_id=c.get('/api/auth/me')[1]['data']['user']['id']
        admin=ApiClient(c.host,c.port);admin.login('ADMIN001','Admin@123')
        status,response=admin.request('DELETE',f'/api/admin/users/{user_id}')
        self.assertEqual(status,409,response)
        self.assertIn('关联商品',response['message'])
        self.assertEqual(c.get(f'/api/goods/{gid}')[0],200)
