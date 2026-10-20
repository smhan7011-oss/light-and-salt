from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import argparse
import base64
import hashlib
import json
import secrets
import threading
import webbrowser
from commerce import Client, CommerceError, validate_product, credential_path, ImageAnalysisClient

client = Client()
ai_client = ImageAnalysisClient()
csrf = secrets.token_urlsafe(32)
lock = threading.Lock()
reviews = {}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(Path(__file__).parent / 'static'), **kwargs)
    def log_message(self, *args):
        pass
    def allowed(self):
        port = self.server.server_port
        host = self.headers.get('Host', '')
        return host in (f'127.0.0.1:{port}', f'localhost:{port}')
    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()
    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error':'허용되지 않은 호스트'})
        if self.path == '/api/session':
            return self.reply(200, {'csrf':csrf, 'configured':bool(client.client_id)})
        if self.path.startswith('/api/'):
            return self.reply(404, {'error':'경로 없음'})
        super().do_GET()
    def do_POST(self):
        port = self.server.server_port
        if not self.allowed() or self.headers.get('Origin') not in (f'http://localhost:{port}', f'http://127.0.0.1:{port}') or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),csrf):
            return self.reply(403, {'error':'허용되지 않은 요청'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length < 1 or length > 24000000:
                return self.reply(413, {'error':'요청 크기 초과'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise CommerceError('잘못된 요청')
            with lock:
                result = self.action(data)
            self.reply(200, result)
        except (CommerceError, ValueError, KeyError, TypeError) as e:
            self.reply(400, {'error':str(e) if isinstance(e, CommerceError) else '입력 형식을 확인해주세요.'})
        except Exception:
            self.reply(500, {'error':'처리에 실패했습니다. 등록 요청 후에는 판매자센터에서 결과를 확인하세요.'})
    def action(self, data):
        if self.path == '/api/ai-status':
            return {'configured':bool(ai_client.key)}
        if self.path == '/api/ai-configure':
            ai_client.configure(data)
            return {'configured':True, 'saved':bool(data.get('save'))}
        if self.path == '/api/ai-forget':
            ai_client.forget()
            return {'configured':False}
        if self.path == '/api/analyze-images':
            return ai_client.analyze(data)
        if self.path == '/api/shipping-settings':
            path = credential_path().with_name('shipping.json')
            if data.get('save'):
                values = {key: str(data.get(key, '')).strip() for key in
                          ('shippingAddress', 'returnAddress', 'shippingAddressId', 'returnAddressId')}
                if not values['shippingAddress'] or not values['returnAddress']:
                    raise CommerceError('출고지와 반품지 주소를 모두 입력하세요.')
                if any(len(value) > 500 for value in values.values()):
                    raise CommerceError('입력값이 너무 깁니다.')
                for key in ('shippingAddressId', 'returnAddressId'):
                    if values[key] and (not values[key].isascii() or not values[key].isdigit() or int(values[key]) <= 0):
                        raise CommerceError('주소 ID는 네이버에서 확인한 양의 정수만 입력하세요.')
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.tmp')
                temporary.write_text(json.dumps(values, ensure_ascii=False), encoding='utf-8')
                temporary.replace(path)
                reviews.clear()
            return {'settings': json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}}
        if self.path == '/api/connect':
            client.configure(data)
            channels = client.raw('GET', '/v1/seller/channels')
            if data.get('save'):
                client.save()
            reviews.clear()
            return {'channels':channels, 'saved':bool(data.get('save'))}
        if self.path == '/api/forget':
            client.forget()
            reviews.clear()
            return {'ok':True}
        if self.path == '/api/channels':
            return {'channels':client.raw('GET', '/v1/seller/channels')}
        if self.path == '/api/addresses':
            return {'addresses':client.raw('GET', '/v1/seller/addressbooks')}
        if self.path == '/api/categories':
            return {'categories':client.raw('GET', '/v1/categories')}
        if self.path == '/api/upload':
            raw = base64.b64decode(data['base64'], validate=True)
            if len(raw)>15000000:
                raise CommerceError('이미지는 15MB 이하로 선택하세요.')
            if raw.startswith(b'\x89PNG\r\n\x1a\n'):
                ext,mime='png','image/png'
            elif raw.startswith(b'\xff\xd8\xff'):
                ext,mime='jpg','image/jpeg'
            else:
                raise CommerceError('PNG 또는 JPEG를 선택하세요.')
            return client.upload(raw, 'product.'+ext, mime)
        if self.path == '/api/review':
            product = validate_product(data['product'])
            encoded=json.dumps(product, sort_keys=True, ensure_ascii=False).encode()
            ticket=secrets.token_urlsafe(24)
            reviews.clear()
            reviews[ticket]=(hashlib.sha256(encoded).hexdigest(), product)
            return {'ticket':ticket,'product':product}
        if self.path == '/api/register':
            if data.get('confirmation') != '상품 등록':
                raise CommerceError('확인 문구를 입력하세요.')
            item=reviews.pop(data.get('ticket',''), None)
            if not item:
                raise CommerceError('구성 변경 또는 이미 사용된 요청입니다. 다시 검토해주세요.')
            return client.raw('POST', '/v2/products', json.dumps(item[1],ensure_ascii=False).encode())
        raise CommerceError('지원하지 않는 요청')

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    if args.open:
        webbrowser.open(f'http://127.0.0.1:{args.port}')
    print(f'Light & Salt running on port {args.port}. Close this window to stop.',flush=True)
    server.serve_forever()
