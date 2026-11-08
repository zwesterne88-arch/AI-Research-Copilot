from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import json,threading
import pytest
from research_copilot.aliyun_translation import AliyunProvider,Config,prepare,restore,units,usage,configuration,period
from research_copilot.workspace import Workspace
from research_copilot.errors import CopilotError

class SDK:
    def __init__(self,failures=None):self.calls=[];self.failures=list(failures or [])
    def translate_general_with_options(self,request,runtime):
        self.calls.append((request,runtime))
        if self.failures:
            code=self.failures.pop(0)
            if code is not None:return SimpleNamespace(body={'Code':code,'RequestId':'synthetic-request'})
        return SimpleNamespace(body={'Code':200,'Data':{'Translated':'中文 '+request.source_text},'RequestId':'synthetic-request'})

@pytest.fixture
def material(service,pdf_bytes,monkeypatch):
    for name in ('ALIBABA_CLOUD_ACCESS_KEY_ID','ALIBABA_CLOUD_ACCESS_KEY_SECRET','ALIYUN_TRANSLATE_ENABLED','ALIYUN_TRANSLATE_MONTHLY_LIMIT'):monkeypatch.delenv(name,raising=False)
    doc=service.import_pdf(pdf_bytes(['Synthetic source paragraph. '*30]),'SYNTHETIC.pdf')['data']
    return doc,Workspace(service.root)

def provider(material,sdk=None,limit=900000,**kwargs):
    doc,store=material;return AliyunProvider(store,doc,True,'aliyun',client=sdk or SDK(),config=Config('synthetic-id','synthetic-secret',True,limit),sleeper=lambda _:None,**kwargs)

def test_official_general_request_and_no_sdk_auto_retry(material):
    sdk=SDK();c=provider(material,sdk);r=c.translate('Hello, welcome to China.','en','zh-CN')
    req,rt=sdk.calls[0]
    assert (req.format_type,req.scene,req.source_language,req.target_language,req.source_text)==('text','general','en','zh','Hello, welcome to China.')
    assert rt.autoretry is False and rt.max_attempts==1
    assert r['provider']=='aliyun' and len(c.calls)==1
    assert 'synthetic-secret' not in json.dumps(r) and 'synthetic-id' not in json.dumps(c.public_info())

def test_empty_text_never_calls_sdk_or_uses_quota(material):
    sdk=SDK();c=provider(material,sdk)
    assert c.translate('  \n\n','en','zh')['translation']==''
    assert not sdk.calls and not material[1].records('translation_admission')

def test_unreadable_cache_or_reservation_store_blocks_before_sdk(material,monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    sdk=SDK();c=provider(material,sdk)
    @contextmanager
    def broken():raise sqlite3.OperationalError('synthetic disk fault');yield
    monkeypatch.setattr(c.store,'connection',broken)
    with pytest.raises(CopilotError) as e:c.translate('A valid source sentence.','en','zh')
    assert e.value.code=='translation_storage' and not sdk.calls

def test_write_failure_after_response_is_not_retried(material,monkeypatch):
    import sqlite3
    sdk=SDK();c=provider(material,sdk)
    original=c.store.add
    def broken(kind,*args):
        if kind=='aliyun_chunk':raise sqlite3.OperationalError('synthetic disk fault')
        return original(kind,*args)
    monkeypatch.setattr(c.store,'add',broken)
    with pytest.raises(CopilotError) as e:c.translate('A valid source sentence.','en','zh')
    assert e.value.code=='translation_storage' and len(sdk.calls)==1
    assert len(c.store.records('translation_admission'))==1

def test_long_text_splitting_is_lossless_and_strictly_below_5000():
    text=('A full natural sentence with conditions. '*250)+'\n\nNext paragraph. '+('α'*1000)
    p=prepare(text)
    assert len(p['chunks'])>1 and all(units(c['text'])<=4500<5000 for c in p['chunks'])
    assert restore(''.join(c['text'] for c in p['chunks']),p['markers'])==p['text']

def test_formulas_variables_citations_and_model_names_are_restored_exactly():
    text='Transformer uses Non-IID CIC-IDS2017. For x = 12.5, $y=α^2$ may improve F1-score by 20% [12] (3).'
    p=prepare(text);out=restore('译文 '+''.join(c['text'] for c in p['chunks']),p['markers'])
    for literal in ('Transformer','Non-IID','CIC-IDS2017','x','12.5','$y=α^2$','F1-score','20%','[12]','(3)'):assert literal in out
    with pytest.raises(CopilotError):restore('markers lost',p['markers'])

def test_grouped_numbers_and_spaced_citations_stay_single_spans():
    p=prepare('Data volumes are 450,000 and 600,000 [ 4].')
    assert set(p['markers'].values())=={'450,000','600,000','[ 4]'}

def test_changed_markers_automatically_translate_original_and_cache(material):
    class ChangesMarkers(SDK):
        def translate_general_with_options(self,request,runtime):
            self.calls.append((request,runtime))
            text='标记被改变' if 'ZXQ' in request.source_text else '数据量为450,000条。'
            return SimpleNamespace(body={'Code':200,'Data':{'Translated':text},'RequestId':'synthetic-request'})
    sdk=ChangesMarkers();client=provider(material,sdk,max_retries=0)
    result=client.translate('Data volume is 450,000.','en','zh')
    assert result['translation']=='数据量为450,000条。'
    assert len(sdk.calls)==2 and sdk.calls[1][0].source_text=='Data volume is 450,000.'
    assert [t['input_mode'] for t in client.calls]==['protected','original']
    other=provider(material,sdk);assert other.translate('Data volume is 450,000.','en','zh')['translation']==result['translation']
    assert len(sdk.calls)==2

def test_partial_failure_keeps_completed_chunks_and_resume_only_requests_missing(material):
    sdk=SDK([None,10009]);c=provider(material,sdk,max_retries=0)
    text='First sentence. '*400
    with pytest.raises(CopilotError):c.translate(text,'en','zh')
    completed=len(material[1].records('aliyun_chunk'));assert completed==1
    before=len(sdk.calls);result=c.translate(text,'en','zh')
    assert result['translation'] and len(sdk.calls)-before==len(prepare(text)['chunks'])-completed

def test_repeated_translation_and_new_provider_process_use_persistent_cache(material):
    sdk=SDK();c=provider(material,sdk);text='The method may not generalize [3].'
    one=c.translate(text,'en','zh');before=usage(material[1],c.config)
    other=provider(material,sdk);two=other.translate(text,'en','zh')
    assert one['translation']==two['translation'] and len(sdk.calls)==1 and usage(material[1],c.config)==before

def test_quota_rejects_before_call_and_reservations_are_atomic(material):
    doc,store=material;c=provider(material,limit=100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        def reserve():
            try:c.reserve(70);return True
            except CopilotError:return False
        results=list(pool.map(lambda _:reserve(),range(2)))
    assert sorted(results)==[False,True] and usage(store,c.config)['submitted_characters']==70
    with pytest.raises(CopilotError):c.translate('A long enough source text to exceed the rest of this quota.','en','zh')
    assert not c.client.calls

@pytest.mark.parametrize('code,expected',[(10009,'aliyun_permission'),(10010,'aliyun_service'),(10013,'aliyun_service'),('InvalidAccessKeyId.NotFound','aliyun_auth'),(10008,'aliyun_length'),(10005,'aliyun_language')])
def test_non_temporary_failures_are_not_retried(material,code,expected):
    sdk=SDK([code]);c=provider(material,sdk)
    with pytest.raises(CopilotError) as e:c.translate('Original source.','en','zh')
    assert e.value.code==expected and len(sdk.calls)==1

def test_limited_retry_reserves_each_attempt(material):
    sdk=SDK([10001,None]);c=provider(material,sdk);text='Original source.'
    c.translate(text,'en','zh')
    recorded=usage(material[1],c.config)
    assert len(sdk.calls)==2 and recorded['submitted_characters']==2*len(text) and recorded['successful_characters']==len(text)

def test_month_switch_uses_shanghai_and_keeps_history(material):
    before=datetime(2026,11,30,15,59,tzinfo=timezone.utc);after=datetime(2026,11,30,16,1,tzinfo=timezone.utc)
    c=provider(material,clock=lambda:before);c.reserve(50)
    assert period(lambda:before)=='2026-11' and period(lambda:after)=='2026-12'
    assert usage(material[1],c.config,lambda:after)['submitted_characters']==0
    assert usage(material[1],c.config,lambda:before)['submitted_characters']==50

def test_missing_or_disabled_credentials_leave_other_features_usable(material):
    doc,store=material;c=AliyunProvider(store,doc,True,'aliyun',client=SDK(),config=Config('',''))
    with pytest.raises(CopilotError) as e:c.translate('Original source.','en','zh')
    assert e.value.code=='aliyun_config' and not c.calls
    c=AliyunProvider(store,doc,True,'aliyun',client=SDK(),config=Config('id','secret',False))
    with pytest.raises(CopilotError):c.translate('Original source.','en','zh')
    assert not c.calls
