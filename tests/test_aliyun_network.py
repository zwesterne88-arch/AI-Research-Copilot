"""Real official SDK + loopback HTTP; external sockets remain forbidden."""
import json,socket,ssl,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from darabonba.core import DaraCore
from darabonba.exceptions import RetryError
from Tea.exceptions import UnretryableException
from requests.exceptions import ProxyError,ReadTimeout,ConnectionError
from alibabacloud_alimt20181012.client import Client
from alibabacloud_tea_openapi.models import Config as SDKConfig
from research_copilot.aliyun_network import transport_error,network_status
from research_copilot.aliyun_translation import AliyunProvider,Config,mapped_error
from research_copilot.errors import CopilotError
from research_copilot.workspace import Workspace

SECRET='synthetic-secret-never-output'
SOURCE='private synthetic paper content'

def wrapped(cause):
    return UnretryableException({'signed_request':SECRET},cause)

@pytest.mark.parametrize('cause,code,retry',[
    (RetryError('[WinError 10013] '+SECRET+' '+SOURCE),'aliyun_network_permission',False),
    (ssl.SSLCertVerificationError('certificate verify failed '+SECRET),'aliyun_tls',False),
    (ProxyError(SECRET),'aliyun_proxy',False),
    (socket.gaierror('getaddrinfo failed '+SECRET),'aliyun_dns',False),
    (ReadTimeout('timed out '+SECRET),'aliyun_temporary',True),
    (ConnectionError(SECRET),'aliyun_connection',False),
    (RetryError(SECRET),'aliyun_transport',False),
])
def test_official_exception_unwrap_is_safe(cause,code,retry):
    error=transport_error(wrapped(cause))
    assert error.code==code and error.retryable is retry
    assert error.failure_layer=='transport'
    public=json.dumps(error.as_dict())
    assert SECRET not in public and SOURCE not in public and 'signed_request' not in public

def test_cloud_10013_is_not_windows_10013():
    error=mapped_error(10013,'synthetic-request')
    assert transport_error(error) is error and error.code=='aliyun_service'

def test_connectivity_probe_is_unsigned_fixed_host_get_not_translation(monkeypatch):
    def action(request,runtime):
        assert request.protocol=='https' and request.method=='GET' and request.pathname=='/'
        assert request.headers=={'host':'mt.cn-hangzhou.aliyuncs.com'}
        assert not request.query and not request.body
        assert runtime['ignoreSSL'] is False
        assert runtime['readTimeout']==runtime['connectTimeout']==3000
        return SimpleNamespace(status_code=404)
    monkeypatch.setattr(DaraCore,'do_action',action)
    result=network_status()
    assert result['reachable'] and result['http_status']==404
    assert not result['translation_tested'] and not result['credentials_sent'] and result['source_characters']==0

def test_probe_timeout_does_not_claim_characters_were_reserved(monkeypatch):
    def fail(*a):raise ReadTimeout(SECRET)
    monkeypatch.setattr(DaraCore,'do_action',fail)
    result=network_status()
    assert not result['reachable'] and '尚未发送翻译请求' in result['message']
    assert SECRET not in json.dumps(result)

@pytest.fixture
def material(service,pdf_bytes):
    doc=service.import_pdf(pdf_bytes(['Synthetic source paragraph. '*30]),'SYNTHETIC-NETWORK.pdf')['data']
    return doc,Workspace(service.root)

def provider(material,client=None):
    doc,store=material
    return AliyunProvider(store,doc,True,'aliyun',client=client,config=Config('synthetic-id',SECRET),sleeper=lambda _:None)

def test_failed_preflight_has_zero_translation_calls_and_zero_reservations(material,monkeypatch):
    def fail(*a):raise RetryError('[WinError 10013] '+SECRET)
    monkeypatch.setattr(DaraCore,'do_action',fail)
    adapter=provider(material)
    with pytest.raises(CopilotError) as e:adapter.translate(SOURCE,'en','zh')
    assert e.value.code=='aliyun_network_permission' and e.value.failure_layer=='preflight'
    assert not e.value.retryable and adapter.client is None and not adapter.calls
    assert not material[1].records('translation_admission')
    assert not material[1].records('aliyun_chunk')

def test_real_official_sdk_http_protocol_and_cache(material,monkeypatch):
    # Synthetic credentials never leave this in-process loopback server.
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('NO_PROXY','127.0.0.1')
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length'])).decode()
            fields=parse_qs(body);received.append((dict(self.headers),fields))
            payload=json.dumps({'Code':200,'RequestId':'offline-http-fixture','Data':{'Translated':'离线协议测试 '+fields['SourceText'][0]}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        client=Client(SDKConfig(access_key_id='synthetic-id',access_key_secret=SECRET,endpoint=f'127.0.0.1:{server.server_port}',protocol='http',region_id='cn-hangzhou'))
        adapter=provider(material,client);text='Transformer may improve 12% [3].'
        first=adapter.translate(text,'en','zh');second=provider(material,client).translate(text,'en','zh')
        assert first['translation']==second['translation']=='离线协议测试 '+text
        assert len(received)==1 and len(material[1].records('translation_admission'))==1
        headers,fields=received[0];headers={k.lower():v for k,v in headers.items()}
        assert headers['x-acs-action']=='TranslateGeneral' and headers['x-acs-version']=='2018-10-12'
        assert {k:fields[k][0] for k in ('FormatType','Scene','SourceLanguage','TargetLanguage')}==dict(FormatType='text',Scene='general',SourceLanguage='en',TargetLanguage='zh')
        assert SECRET not in json.dumps(adapter.calls)
    finally:server.shutdown();server.server_close();thread.join(2)

def test_real_official_sdk_wraps_network_denial_without_leaking_or_retrying(material,monkeypatch):
    calls=[]
    def fail(request,runtime):
        calls.append(True);raise RetryError('[WinError 10013] '+SECRET+' '+SOURCE)
    monkeypatch.setattr(DaraCore,'do_action',fail)
    client=Client(SDKConfig(access_key_id='synthetic-id',access_key_secret=SECRET,endpoint='mt.cn-hangzhou.aliyuncs.com',protocol='https'))
    adapter=provider(material,client)
    with pytest.raises(CopilotError) as e:adapter.translate(SOURCE,'en','zh')
    assert e.value.code=='aliyun_network_permission' and len(calls)==1
    assert adapter.calls[0]['failure_layer']=='transport'
    assert SECRET not in json.dumps(adapter.calls) and SOURCE not in json.dumps(adapter.calls)
    # If the connection fails AFTER admission, preserve the conservative ledger.
    assert len(material[1].records('translation_admission'))==1
