import io
import json
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
import base64
import bcrypt
import server
from commerce import Client, CommerceError, validate_product

class API(unittest.TestCase):
    def test_local_shipping_persistence(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory, patch('server.credential_path', return_value=Path(directory)/'credentials.bin'):
            handler = SimpleNamespace(path='/api/shipping-settings')
            settings = {'save': True, 'shippingAddress': 'Test shipping address', 'returnAddress': 'Test return address', 'shippingAddressId': '123', 'returnAddressId': '456'}
            server.Handler.action(handler, settings)
            loaded = server.Handler.action(handler, {})['settings']
            self.assertEqual(loaded['shippingAddress'], settings['shippingAddress'])
            self.assertEqual(loaded['returnAddressId'], '456')
            with self.assertRaises(CommerceError):
                server.Handler.action(handler, dict(settings, shippingAddressId='invented'))
            self.assertEqual(server.Handler.action(handler, {})['settings'], loaded)
    def test_signature_and_token_cache(self):
        client=Client()
        secret=bcrypt.gensalt().decode()
        client.configure({'clientId':'test-client','clientSecret':secret})
        with patch('commerce.time.time',return_value=1700000000), patch('commerce.urllib.request.urlopen') as call:
            call.return_value.__enter__.return_value=io.BytesIO(json.dumps({'access_token':'test-token','expires_in':3600}).encode())
            self.assertEqual(client.access_token(),'test-token')
            self.assertEqual(client.access_token(),'test-token')
            self.assertEqual(call.call_count,1)
            from urllib.parse import parse_qs
            request=call.call_args.args[0]
            fields=parse_qs(request.data.decode())
            signature=base64.b64decode(fields['client_secret_sign'][0])
            self.assertTrue(bcrypt.checkpw(b'test-client_1700000000000',signature))
            self.assertEqual(fields['type'],['SELF'])
            self.assertEqual(request.get_header('Content-type'),'application/x-www-form-urlencoded')
    def test_error_redacts_remote_body(self):
        client=Client()
        with patch('commerce.urllib.request.urlopen',side_effect=urllib.error.HTTPError('url',403,'Forbidden',{},io.BytesIO(b'{"code":"DENIED","message":"sensitive"}'))):
            with self.assertRaises(CommerceError) as error:
                client.raw('GET','/test',auth=False)
            self.assertNotIn('sensitive',str(error.exception))
    def test_http_guards_review_and_single_use(self):
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        base=f'http://127.0.0.1:{http.server_port}'
        def post(path,data,headers=None):
            request=urllib.request.Request(base+'/api/'+path,data=json.dumps(data).encode(),headers=headers or {},method='POST')
            try:
                with urllib.request.urlopen(request) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:return e.code,json.load(e)
        try:
            self.assertEqual(post('forget',{})[0],403)
            session=json.load(urllib.request.urlopen(base+'/api/session'))
            headers={'Origin':base,'X-CSRF-Token':session['csrf'],'Content-Type':'application/json'}
            self.assertEqual(post('review',{'product':{}},headers)[0],400)
            product={'originProduct':{'name':'Test','leafCategoryId':'123','salePrice':10000,'stockQuantity':1,'images':{'representativeImage':{'url':'https://example.com/a.png'}},'detailContent':'Test','deliveryInfo':{'deliveryFee':{'baseFee':4000}},'detailAttribute':{'test':True}},'smartstoreChannelProduct':{'channelProductDisplayStatusType':'SUSPENSION'}}
            status,review=post('review',{'product':product},headers)
            self.assertEqual(status,200)
            with patch.object(server.client,'raw',return_value={'originProductNo':123}) as remote:
                self.assertEqual(remote.call_count,0)
                self.assertEqual(post('register',{'ticket':review['ticket'],'confirmation':'bad'},headers)[0],400)
                self.assertEqual(post('register',{'ticket':review['ticket'],'confirmation':'상품 등록'},headers)[0],200)
                self.assertEqual(post('register',{'ticket':review['ticket'],'confirmation':'상품 등록'},headers)[0],400)
                self.assertEqual(remote.call_count,1)
                self.assertEqual(remote.call_args.args[0:2],('POST','/v2/products'))
        finally:
            http.shutdown()
            http.server_close()
if __name__=='__main__':unittest.main()
