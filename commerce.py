"""Naver Commerce API client; never logs credentials or bearer tokens."""
import base64
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.error
import uuid

API = 'https://api.commerce.naver.com/external'

class CommerceError(Exception):
    pass

def protect(data, decrypt=False):
    if os.name != 'nt':
        raise CommerceError('인증 정보 저장은 Windows 사용자 계정 암호화(DPAPI)에서만 지원합니다. 현재 실행 중에는 메모리에만 보관됩니다.')
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(out)):
        raise CommerceError('Windows 인증 정보 암호화/복호화 실패')
    try:
        return ctypes.string_at(out.data, out.size)
    finally:
        kernel.LocalFree(out.data)

def credential_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'LightAndSaltStudio' / 'credentials.bin'

class Client:
    def __init__(self):
        self.client_id = self.secret = self.token = ''
        self.expires = 0
        if os.name == 'nt' and credential_path().exists():
            try:
                self.configure(json.loads(protect(credential_path().read_bytes(), True)))
            except Exception:
                pass
    def configure(self, data):
        client_id = data.get('clientId', '').strip()
        secret = data.get('clientSecret', '').strip()
        if not client_id or not secret:
            raise CommerceError('애플리케이션 ID와 시크릿을 모두 입력하세요.')
        self.client_id, self.secret = client_id, secret
        self.token, self.expires = '', 0
    def save(self):
        blob = protect(json.dumps({'clientId': self.client_id, 'clientSecret': self.secret}).encode())
        path = credential_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_bytes(blob)
        temp.replace(path)
    def forget(self):
        self.client_id = self.secret = self.token = ''
        self.expires = 0
        if os.name == 'nt':
            credential_path().unlink(missing_ok=True)
    def raw(self, method, path, body=None, content_type='application/json', auth=True):
        headers = {'Accept': 'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.access_token()
        if body is not None:
            headers['Content-Type'] = content_type
        req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=35) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # Do not return request headers, credentials, or full remote response bodies.
            try:
                obj = json.loads(e.read(32000))
                code = str(obj.get('code', ''))[:100]
            except Exception:
                code = ''
            raise CommerceError(f'네이버 API HTTP {e.code} {code}. 인증 정보·등록 IP·권한 및 상품 필수값을 확인하세요.') from None
        except (urllib.error.URLError, TimeoutError):
            raise CommerceError('네이버 API 연결 실패. 인터넷 연결 및 API 도메인 접근을 확인하세요. 등록 요청 중이었다면 판매자센터에서 중복 여부를 확인한 뒤 재시도하세요.') from None
    def access_token(self):
        if self.token and time.time() < self.expires:
            return self.token
        if not self.client_id:
            raise CommerceError('먼저 API 인증 정보를 설정하세요.')
        import bcrypt
        from urllib.parse import urlencode
        timestamp = int(time.time() * 1000)
        try:
            signature = base64.b64encode(bcrypt.hashpw(f'{self.client_id}_{timestamp}'.encode(), self.secret.encode())).decode()
        except ValueError:
            raise CommerceError('시크릿 형식을 확인하세요. 애플리케이션 시크릿 원문이 필요합니다.') from None
        data = urlencode({'client_id': self.client_id, 'timestamp': timestamp, 'client_secret_sign': signature, 'grant_type': 'client_credentials', 'type': 'SELF'}).encode()
        result = self.raw('POST', '/v1/oauth2/token', data, 'application/x-www-form-urlencoded', False)
        self.token = result['access_token']
        self.expires = time.time() + int(result.get('expires_in', 3600)) - 60
        return self.token
    def upload(self, data, filename, mime):
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="imageFiles"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        return self.raw('POST', '/v1/product-images/upload', body, 'multipart/form-data; boundary=' + boundary)

def validate_product(payload):
    if not isinstance(payload, dict):
        raise CommerceError('상품 JSON은 객체여야 합니다.')
    origin = payload.get('originProduct', {})
    channel = payload.get('smartstoreChannelProduct', {})
    for field in ['name', 'leafCategoryId', 'salePrice', 'stockQuantity', 'images', 'detailContent', 'deliveryInfo', 'detailAttribute']:
        if field not in origin or origin[field] in ('', None):
            raise CommerceError('필수 상품 항목 누락: ' + field)
    if not isinstance(origin['salePrice'], int) or origin['salePrice'] <= 0:
        raise CommerceError('판매가격을 확인하세요.')
    if not isinstance(origin['stockQuantity'], int) or origin['stockQuantity'] < 0:
        raise CommerceError('재고를 확인하세요.')
    if origin.get('deliveryInfo', {}).get('deliveryFee', {}).get('baseFee') != 4000:
        raise CommerceError('기본 배송비는 4,000원으로 설정해주세요.')
    if channel.get('channelProductDisplayStatusType') != 'SUSPENSION':
        raise CommerceError('첫 등록은 전시중지(SUSPENSION)로만 허용합니다. 판매자센터에서 확인 후 전시해주세요.')
    return payload
