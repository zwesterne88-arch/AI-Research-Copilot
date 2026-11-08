"""Offline/native-wire contracts, not measurements of real translation quality."""
import json
from pathlib import Path
from threading import Thread
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.error import HTTPError
import pytest
from research_copilot.errors import CopilotError
from research_copilot.translation import TranslationService,selection,MockTranslator
from research_copilot.translation_providers import NativeProvider,provider_status,request_json
from research_copilot.workspace import Workspace

REFERENCE=[
 ('The method may improve accuracy under certain conditions [3].','在某些条件下，该方法可能提高准确率 [3]。'),
 ('The results suggest, but do not prove, a causal relationship.','结果提示可能存在因果关系，但并未证明该关系。'),
 ('For x = 12.5, the rate is 20% at 4 ms [2].','当 x = 12.5 时，在 4 ms 处该比率为 20% [2]。')]

@pytest.fixture
def setup(service,pdf_bytes,monkeypatch):
    monkeypatch.delenv('RC_LIBRETRANSLATE_URL',raising=False);monkeypatch.delenv('RC_MYMEMORY_ENABLED',raising=False)
    for name in ('RC_GOOGLE_TRANSLATE_API_KEY','RC_GOOGLE_FREE_TIER_MONTH','RC_GOOGLE_FREE_TIER_CHAR_BUDGET'):monkeypatch.delenv(name,raising=False)
    for name in ('RC_BAIDU_APP_ID','RC_BAIDU_SECRET_KEY','RC_BAIDU_PLAN','RC_BAIDU_FREE_TIER_MONTH','RC_BAIDU_FREE_TIER_CHAR_BUDGET'):monkeypatch.delenv(name,raising=False)
    doc=service.import_pdf(pdf_bytes(['SYNTHETIC REFERENCE ONLY. '+REFERENCE[2][0]+' Extra source context. '*12]),'SYNTHETIC.pdf')['data']
    store=Workspace(service.root);anchor=selection(service,doc['document_version_id'],1,REFERENCE[2][0])
    return doc,store,anchor,TranslationService(service,store)

def native(setup,monkeypatch,provider='libretranslate',transport=None,consent=True,consent_provider=None):
    monkeypatch.setenv('RC_LIBRETRANSLATE_URL','http://127.0.0.1:5000');monkeypatch.setenv('RC_MYMEMORY_ENABLED','1')
    doc,store,_,_=setup
    return NativeProvider(provider,store,doc,consent,consent_provider or provider,transport=transport or (lambda *a:{'translatedText':REFERENCE[2][1],'responseStatus':200,'responseData':{'translatedText':REFERENCE[2][1]}}))

def test_selection_native_result_and_character_units(setup,monkeypatch):
    d,s,a,e=setup;p=native(setup,monkeypatch);r=e.translate(a,client=p)
    assert a['mapping_status']=='parser_text_matched' and r['translation']==REFERENCE[2][1]
    assert r['mode']=='basic_nmt' and r['usage']['unit']=='characters' and r['review_status']=='unreviewed'
    assert not r['numeric_preservation_warnings'] and len(s.records('translation_admission'))==1

def test_unconfigured_no_attempt_no_fallback(setup):
    d,s,a,e=setup;p=NativeProvider('libretranslate',s,d)
    with pytest.raises(CopilotError,match='未配置'):e.translate(a,client=p)
    assert not s.records('translation_admission') and not p.calls

@pytest.mark.parametrize('consent,selected',[(False,'mymemory'),(True,'deepseek'),(True,'libretranslate')])
def test_provider_specific_paper_consent_precedes_transport(setup,monkeypatch,consent,selected):
    d,s,a,e=setup;p=native(setup,monkeypatch,'mymemory',lambda *a:pytest.fail('unauthorized paper sent'),consent,selected)
    with pytest.raises(CopilotError) as ex:e.translate(a,client=p)
    assert ex.value.code=='provider_consent' and not s.records('translation_admission')

def test_free_cap_exhausted_no_retry_or_paid_fallback(setup,monkeypatch):
    d,s,a,e=setup;p=native(setup,monkeypatch,'mymemory');p.reserve(5000)
    with pytest.raises(CopilotError) as ex:e.translate(a,client=p)
    assert ex.value.code=='provider_quota' and not p.calls and not s.records('translation')

def test_cache_provider_glossary_and_configuration_isolation(setup,monkeypatch):
    d,s,a,e=setup;p=native(setup,monkeypatch);e.translate(a,client=p)
    assert e.translate(a,client=p)['cache_hit'] and len(p.calls)==1
    assert not e.translate(a,{'rate':'比率'},p)['cache_hit']
    other=native(setup,monkeypatch,'mymemory');assert not e.translate(a,client=other)['cache_hit']
    monkeypatch.setenv('RC_LIBRETRANSLATE_MODEL_REVISION','updated-model');p2=native(setup,monkeypatch)
    assert not e.translate(a,client=p2)['cache_hit']

def test_context_glossary_not_falsely_sent_to_nmt(setup,monkeypatch):
    sent=[]
    def transport(url,payload):sent.append((url,payload));return {'translatedText':REFERENCE[2][1]}
    d,s,a,e=setup;p=native(setup,monkeypatch,transport=transport);r=e.translate(a,{'rate':'比率'},p)
    assert set(sent[0][1])=={'q','source','target','format'}
    assert not r['model_info']['glossary_supported'] and not r['model_info']['context_supported']

@pytest.mark.parametrize('code,expected',[(401,'provider_auth'),(403,'provider_auth'),(429,'provider_quota'),(456,'provider_quota'),(500,'provider_http'),(302,'provider_http')])
def test_transport_auth_quota_failure_no_sensitive_error(setup,monkeypatch,code,expected):
    import research_copilot.translation_providers as m
    class Opener:
        def open(self,*a,**kw):raise HTTPError('https://private?q=SECRET',code,'SECRET',{},None)
    monkeypatch.setattr(m,'build_opener',lambda *a:Opener())
    with pytest.raises(CopilotError) as ex:request_json('http://127.0.0.1:5000/translate',{'q':'synthetic'})
    assert ex.value.code==expected and 'SECRET' not in str(ex.value)

def test_timeout_attempt_kept_and_never_retried(setup,monkeypatch):
    d,s,a,e=setup
    def fail(*a):raise CopilotError('provider_timeout','Synthetic timeout','translation')
    p=native(setup,monkeypatch,transport=fail)
    with pytest.raises(CopilotError):e.translate(a,client=p)
    assert len(p.calls)==1 and len(s.records('translation_admission'))==1 and len(s.records('translation_failure'))==1

def test_mymemory_single_short_segment_and_quota_response(setup,monkeypatch):
    p=native(setup,monkeypatch,'mymemory',lambda *a:{'responseStatus':403,'quotaFinished':True})
    with pytest.raises(CopilotError) as ex:p.translate('x'*501,'en','zh-CN')
    assert ex.value.code=='provider_length' and not p.calls
    with pytest.raises(CopilotError) as ex:p.translate('synthetic','en','zh-CN')
    assert ex.value.code=='provider_quota' and len(p.calls)==1

def test_no_native_engine_settings_and_unknown_provider(setup):
    status=provider_status(False,setup[1])
    assert not status['automatic_fallback'] and status['default']=='mock'
    assert all(not p['configured'] for p in status['providers'] if p['id'] in ('libretranslate','mymemory'))
    assert not next(p for p in status['providers'] if p['id']=='deepseek')['enabled']
    with pytest.raises(CopilotError):NativeProvider('google-unofficial',setup[1],setup[0])

@pytest.mark.parametrize('url',['https://public.example','http://192.168.1.2:5000','http://127.0.0.1:5000@evil.example','http://127.0.0.1:5000?secret=1'])
def test_local_adapter_cannot_be_silently_redirected_to_vendor(setup,monkeypatch,url):
    monkeypatch.setenv('RC_LIBRETRANSLATE_URL',url)
    assert not NativeProvider('libretranslate',setup[1],setup[0]).config_data['configured']

def test_native_http_wire_local_synthetic_server_only(setup,monkeypatch):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body=json.dumps({'translatedText':REFERENCE[2][1]}).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setenv('RC_LIBRETRANSLATE_URL','http://127.0.0.1:'+str(server.server_port))
        p=NativeProvider('libretranslate',setup[1],setup[0]);r=setup[3].translate(setup[2],client=p)
        assert received[0]['q']==REFERENCE[2][0] and r['translation']==REFERENCE[2][1]
    finally:server.shutdown();server.server_close();thread.join()

def test_user_revision_append_only_and_scope(setup):
    from research_copilot.web import Workbench
    d,s,a,e=setup;original=e.translate(a,client=MockTranslator());app=Workbench(s.path.parent)
    saved=app.post('/api/translation-revision',{'version':d['document_version_id'],'translation_id':original['id'],'text':'用户测试修订'})
    assert saved['origin']=='user_edit' and s.records('translation')[0]['translation']==original['translation']
    assert s.cached(s.records('translation')[0].get('cache_key','missing')) is None
