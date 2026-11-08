"""Baidu official REST/signature and guards, synthetic transport only."""
from datetime import datetime,timezone
from urllib.parse import parse_qs
import json
import pytest
from research_copilot.translation_providers import NativeProvider,baidu_signature,provider_status
from research_copilot.workspace import Workspace
from research_copilot.errors import CopilotError
from research_copilot.prose import reading_prose,request_segments

@pytest.fixture
def material(service,pdf_bytes,monkeypatch):
    for k in ('RC_BAIDU_APP_ID','RC_BAIDU_SECRET_KEY','RC_BAIDU_PLAN','RC_BAIDU_FREE_TIER_MONTH','RC_BAIDU_FREE_TIER_CHAR_BUDGET'):monkeypatch.delenv(k,raising=False)
    d=service.import_pdf(pdf_bytes(['Synthetic source context. '*30]),'SYNTHETIC.pdf')['data'];return d,Workspace(service.root)

def enable(monkeypatch,month=None,budget=5000):
    for k,v in {'RC_BAIDU_APP_ID':'synthetic-app-id','RC_BAIDU_SECRET_KEY':'synthetic-secret-not-real','RC_BAIDU_PLAN':'standard','RC_BAIDU_FREE_TIER_MONTH':month or datetime.now(timezone.utc).strftime('%Y-%m'),'RC_BAIDU_FREE_TIER_CHAR_BUDGET':str(budget)}.items():monkeypatch.setenv(k,v)

def native(material,consent=True,provider='baidu',transport=None):
    d,w=material;return NativeProvider('baidu',w,d,consent,provider,transport or (lambda *a,**kw:pytest.fail('unexpected external request')))

def test_published_baidu_signature_vector():
    assert baidu_signature('2015063000000001','apple','1435660288','12345678')=='f89f9594663708c1605f3d736d01d2d4'

def test_baidu_missing_credentials_do_not_attempt(material):
    c=native(material)
    with pytest.raises(CopilotError):c.translate('Synthetic source','en','zh-CN')
    assert not c.calls and not material[1].records('translation_admission')

@pytest.mark.parametrize('month,budget',[('2000-01',5000),(None,0),(None,50001)])
def test_standard_allowance_is_explicit_and_bounded(material,monkeypatch,month,budget):
    enable(monkeypatch,month,budget);c=native(material)
    with pytest.raises(CopilotError):c.translate('Synthetic source','en','zh-CN')
    assert not c.calls and not material[1].records('translation_admission')

def test_other_vendor_consent_cannot_authorize_baidu(material,monkeypatch):
    enable(monkeypatch);c=native(material,provider='google')
    with pytest.raises(CopilotError) as e:c.translate('Synthetic source','en','zh-CN')
    assert e.value.code=='provider_consent' and not c.calls

def test_long_original_is_segmented_at_transport_only_and_key_stays_server_side(material,monkeypatch):
    enable(monkeypatch);seen=[]
    def transport(url,body,headers=None):
        form={k:v[0] for k,v in parse_qs(body.decode(),keep_blank_values=True).items()};seen.append(form)
        assert url=='https://fanyi-api.baidu.com/api/trans/vip/translate' and headers['Content-Type']=='application/x-www-form-urlencoded'
        assert form['sign']==baidu_signature(form['appid'],form['q'],form['salt'],'synthetic-secret-not-real')
        assert form['salt'].isdecimal() and 'synthetic-secret' not in body.decode()
        return {'trans_result':[{'src':form['q'],'dst':'译文片段。'}]}
    text=('This method may improve accuracy, but does not guarantee generalization [3]. '*27).strip()
    c=native(material,transport=transport);result=c.translate(text,'en','zh-CN')
    assert len(seen)>1 and all(len(p['q'])<=1000 for p in seen)
    assert ''.join(p['q'] for p in seen)==reading_prose(text)
    assert result['translation']=='译文片段。'*len(seen) and len(c.calls)==len(seen)
    assert sum(r['characters'] for r in material[1].records('translation_admission'))==len(reading_prose(text))
    assert 'synthetic-secret' not in json.dumps(provider_status(False,material[1]))

def test_baidu_quota_error_is_not_retried_or_replaced(material,monkeypatch):
    enable(monkeypatch);c=native(material,transport=lambda *a,**kw:{'error_code':'54003','error_msg':'synthetic rate limit'})
    with pytest.raises(CopilotError) as e:c.translate('Synthetic source','en','zh-CN')
    assert e.value.code=='provider_quota' and len(c.calls)==1 and len(material[1].records('translation_admission'))==1

def test_request_segmentation_does_not_drop_any_source_characters():
    text='A complete sentence [3]. '+('Condition x = 12.5; '*90)+'Only this cohort applies.'
    pieces=request_segments(text,1000)
    assert ''.join(pieces)==text and all(len(p)<=1000 for p in pieces)
    unicode_text='α'*5000+' Formula x = 12.5 [3].'
    bounded=request_segments(unicode_text,6000,6000)
    assert ''.join(bounded)==unicode_text and all(len(p.encode())<=6000 for p in bounded)
