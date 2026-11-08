"""Official Google protocol and allowance guards, always with synthetic transport."""
from datetime import datetime,timezone
from copy import deepcopy
import json
import pytest
from research_copilot.translation_providers import NativeProvider,provider_status
from research_copilot.source_reading import build,make_unit
from research_copilot.web import Workbench
from research_copilot.errors import CopilotError
from research_copilot.prose import reading_prose

TEXT='Our method may improve results, but does not guarantee generalization [3].'
@pytest.fixture
def material(service,pdf_bytes,monkeypatch):
    for name in ('RC_GOOGLE_TRANSLATE_API_KEY','RC_GOOGLE_FREE_TIER_MONTH','RC_GOOGLE_FREE_TIER_CHAR_BUDGET'):monkeypatch.delenv(name,raising=False)
    d=service.import_pdf(pdf_bytes([TEXT+' Extra original context. '*25]),'SYNTHETIC.pdf')['data']
    app=Workbench(service.root);p={'section':'Synthetic','spans':[{'pdf_page':1,'char_start':0,'char_end':len(TEXT),'text':TEXT}]}
    u=make_unit(d,p,'synthetic_not_quality_gold');b=build(service,d);b['fields']['research_questions']['units']=[u]
    b=app.source_reading.save(d,b,'synthetic_fixture')
    return app,d,b,u

def enable(monkeypatch,budget=5000,month=None):
    monkeypatch.setenv('RC_GOOGLE_TRANSLATE_API_KEY','synthetic-key-not-a-real-credential')
    monkeypatch.setenv('RC_GOOGLE_FREE_TIER_MONTH',month or datetime.now(timezone.utc).strftime('%Y-%m'))
    monkeypatch.setenv('RC_GOOGLE_FREE_TIER_CHAR_BUDGET',str(budget))

def client(material,consent=True,consent_provider='google',transport=None):
    app,d,_,_=material
    return NativeProvider('google',app.store,d,consent,consent_provider,transport or (lambda *a,**kw:pytest.fail('unexpected Google network attempt')))

def test_missing_google_credentials_never_sends_or_reserves(material):
    c=client(material)
    with pytest.raises(CopilotError) as error:c.translate(TEXT,'en','zh-CN')
    assert error.value.code=='provider_unconfigured' and not c.calls
    assert not material[0].store.records('translation_admission')

@pytest.mark.parametrize('month,budget',[('2000-01',5000),(None,0),(None,500001)])
def test_free_allowance_requires_current_explicit_month_and_valid_cap(material,monkeypatch,month,budget):
    enable(monkeypatch,budget,month);c=client(material)
    with pytest.raises(CopilotError):c.translate(TEXT,'en','zh-CN')
    assert not c.calls and not material[0].store.records('translation_admission')

@pytest.mark.parametrize('consent,provider',[(False,'google'),(True,'deepseek')])
def test_google_has_its_own_paper_consent(material,monkeypatch,consent,provider):
    enable(monkeypatch);c=client(material,consent,provider)
    with pytest.raises(CopilotError) as e:c.translate(TEXT,'en','zh-CN')
    assert e.value.code=='provider_consent' and not c.calls

def test_official_post_header_and_whole_passage_cache_do_not_leak_key(material,monkeypatch):
    enable(monkeypatch);app,d,b,u=material;seen=[]
    def transport(url,payload,headers=None):
        seen.append((url,payload,headers));return {'data':{'translations':[{'translatedText':'本方法可能改善结果，但不能保证泛化 [3]。'}]}}
    c=client(material,transport=transport)
    one=app.translation.translate_passage(d,u,c);two=app.translation.translate_passage(d,u,c)
    assert len(seen)==1 and seen[0][0]=='https://translation.googleapis.com/language/translate/v2'
    assert seen[0][1]=={'q':reading_prose(TEXT),'source':'en','target':'zh-CN','format':'text','model':'nmt'}
    assert seen[0][2]=={'X-goog-api-key':'synthetic-key-not-a-real-credential'}
    assert 'synthetic-key' not in json.dumps(one) and 'synthetic-key' not in json.dumps(provider_status(False,app.store))
    assert one['provider']=='google' and two['cache_hit'] and two['new_requests']==0
    admissions=app.store.records('translation_admission');assert len(admissions)==1 and admissions[0]['period']==datetime.now(timezone.utc).strftime('%Y-%m')

def test_monthly_cap_rejects_before_transport_and_never_resets_ledger(material,monkeypatch):
    enable(monkeypatch,100);c=client(material);c.reserve(100)
    with pytest.raises(CopilotError) as e:c.translate(TEXT,'en','zh-CN')
    assert e.value.code=='provider_quota' and not c.calls and len(material[0].store.records('translation_admission'))==1

def test_field_plan_scope_confirmation_and_complete_google_version(material,monkeypatch):
    enable(monkeypatch);app,d,b,u=material
    c=client(material,transport=lambda *a,**kw:{'data':{'translations':[{'translatedText':'本方法可能改善结果，但不能保证泛化 [3]。'}]}})
    monkeypatch.setattr(app,'translation_client',lambda *a:c)
    data={'version':d['document_version_id'],'snapshot_id':b['id'],'field':'research_questions','consent':True,'consent_provider':'google'}
    p=app.post('/api/source-reading/field-plan',data)
    assert p['requests']==1 and p['characters']==len(TEXT)
    with pytest.raises(CopilotError):app.post('/api/source-reading/google-field',data)
    with pytest.raises(CopilotError):app.post('/api/source-reading/google-field',dict(data,confirm_plan=True,plan_id='wrong'))
    assert not c.calls
    result=app.post('/api/source-reading/google-field',dict(data,confirm_plan=True,plan_id=p['plan_id']))
    assert result['translation_error'] is None and result['book']['source_reading_version']==2
    tr=result['book']['fields']['research_questions']['units'][0]['translation']
    assert tr['provider']=='google' and tr['human_review']=='unreviewed'
    assert b['fields']['research_questions']['units'][0]['translation'] is None

def test_field_budget_checked_before_any_google_call(material,monkeypatch):
    enable(monkeypatch,1);app,d,b,u=material
    data={'version':d['document_version_id'],'snapshot_id':b['id'],'field':'research_questions','consent':True,'consent_provider':'google'}
    p=app.post('/api/source-reading/field-plan',data)
    with pytest.raises(CopilotError) as e:app.post('/api/source-reading/google-field',dict(data,confirm_plan=True,plan_id=p['plan_id']))
    assert e.value.code=='provider_quota' and not app.store.records('translation_admission')
