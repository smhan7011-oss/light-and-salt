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
from commerce import Client, CommerceError, validate_product, ImageAnalysisClient, AI_FIELDS

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

class ImageAnalysis(unittest.TestCase):
    def result(self):
        return {'fields':{name:{'value':'','imageIndex':None,'evidence':''} for name in AI_FIELDS},
                'points':[{'text':'편리한 개별 포장','imageIndex':1,'evidence':'사진에서 낱개 포장 확인'},
                          {'text':'표기된 용량 100 g','imageIndex':1,'evidence':'라벨에 100 g'},
                          {'text':'이동 중 휴대할 수 있는 구성','imageIndex':1,'evidence':'한 손에 들어오는 크기의 개별 포장'}],
                'warnings':[]}
    def request(self):
        return {'name':'테스트 상품','consent':True,'images':[{'base64':base64.b64encode(b'\xff\xd8\xffmock-jpeg').decode()}]}
    def test_excludes_unsupported_fields_and_points(self):
        result=self.result()
        result['fields']['maker']={'value':'추정 회사','imageIndex':None,'evidence':''}
        result['fields']['ingredient']={'value':'추정 함량','imageIndex':2,'evidence':'없는 사진'}
        result['fields']['usage']={'value':'가'*181,'imageIndex':1,'evidence':'긴 라벨'}
        result['points'][2]['imageIndex']=9
        parsed=ImageAnalysisClient.validate_analysis(result,1)
        self.assertEqual(parsed['fields']['maker']['value'],'')
        self.assertEqual(parsed['fields']['ingredient']['value'],'')
        self.assertEqual(parsed['fields']['usage']['value'],'')
        self.assertEqual(len(parsed['points']),2)
        self.assertIn('maker',parsed['missing'])
    def test_analysis_request_and_label_preservation(self):
        client=ImageAnalysisClient();client.key='test-ai-key'
        result=self.result()
        result['fields']['material']={'value':'면 100%','imageIndex':1,'evidence':'라벨 원문: 면 100%'}
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result,ensure_ascii=False)}}]}
        with patch('commerce.urllib.request.urlopen') as call:
            call.return_value.__enter__.return_value=io.BytesIO(json.dumps(response).encode())
            parsed=client.analyze(self.request())
            self.assertEqual(parsed['fields']['material']['value'],'면 100%')
            request=call.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.openai.com/v1/chat/completions')
            body=json.loads(request.data)
            self.assertTrue(body['response_format']['json_schema']['strict'])
            self.assertEqual(body['messages'][1]['content'][1]['type'],'image_url')
            self.assertNotIn('clientSecret',request.data.decode())
    def test_requires_separate_key_and_photo_consent(self):
        client=ImageAnalysisClient();client.key=''
        with patch('commerce.urllib.request.urlopen') as call:
            with self.assertRaises(CommerceError):client.analyze(self.request())
            client.key='test-ai-key'
            with self.assertRaises(CommerceError):client.analyze(dict(self.request(),consent=False))
            self.assertEqual(call.call_count,0)
    def test_ai_remote_error_redacts_body(self):
        client=ImageAnalysisClient();client.key='test-ai-key'
        with patch('commerce.urllib.request.urlopen',side_effect=urllib.error.HTTPError('url',401,'Unauthorized',{},io.BytesIO(b'sensitive-key-text'))):
            with self.assertRaises(CommerceError) as error:client.analyze(self.request())
            self.assertNotIn('sensitive',str(error.exception))
            self.assertIn('401',str(error.exception))
    def test_ai_429_distinguishes_credit_and_rate_limit(self):
        client=ImageAnalysisClient();client.key='test-ai-key'
        for code,expected in [('insufficient_quota','크레딧'),('rate_limit_exceeded','일시적인 요청 한도')]:
            error=urllib.error.HTTPError('url',429,'Too Many Requests',{},io.BytesIO(json.dumps({'error':{'code':code,'message':'do not expose'}}).encode()))
            with patch('commerce.urllib.request.urlopen',side_effect=error):
                with self.assertRaises(CommerceError) as raised:client.analyze(self.request())
                self.assertIn(expected,str(raised.exception))
                self.assertNotIn('do not expose',str(raised.exception))
    def test_truncated_output_not_applied(self):
        client=ImageAnalysisClient();client.key='test-ai-key'
        response={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}
        with patch('commerce.urllib.request.urlopen') as call:
            call.return_value.__enter__.return_value=io.BytesIO(json.dumps(response).encode())
            with self.assertRaises(CommerceError):client.analyze(self.request())

if __name__=='__main__':unittest.main()
