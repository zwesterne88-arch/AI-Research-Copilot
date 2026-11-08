from pathlib import Path
from types import SimpleNamespace
import time,threading
import pytest
from research_copilot.aliyun_reading import AliyunReading
from research_copilot.aliyun_translation import AliyunProvider,Config
from research_copilot.workspace import Workspace
from research_copilot.errors import CopilotError

class SDK:
    def __init__(self):self.calls=[];self.wait=None
    def translate_general_with_options(self,request,runtime):
        self.calls.append(request.source_text)
        if self.wait:self.wait.wait(2)
        return SimpleNamespace(body={'Code':200,'Data':{'Translated':'【synthetic SDK, not quality evidence】'+request.source_text},'RequestId':'synthetic'})

@pytest.fixture
def fixture(service,pdf_bytes,monkeypatch):
    for k,v in {'ALIBABA_CLOUD_ACCESS_KEY_ID':'synthetic-id','ALIBABA_CLOUD_ACCESS_KEY_SECRET':'synthetic-secret','ALIYUN_TRANSLATE_ENABLED':'true','ALIYUN_TRANSLATE_MONTHLY_LIMIT':'900000'}.items():monkeypatch.setenv(k,v)
    doc=service.import_pdf(pdf_bytes(['Abstract\nA method may improve robustness. '*12+'\n\n1. Introduction\nHowever there is a gap. '*12,'2. Method\nThe proposed algorithm uses a new feature. '*18]),'SYNTHETIC.pdf')['data']
    store=Workspace(service.root);sdk=SDK()
    factory=lambda d,consent:AliyunProvider(store,d,consent,'aliyun',client=sdk,config=Config('synthetic-id','synthetic-secret'),sleeper=lambda _:None)
    reading=AliyunReading(service,store,factory);return reading,doc,sdk

def finish(reading,job):
    for _ in range(200):
        value=reading.get_job(job['job_id'])
        if value['status']!='running':return value
        time.sleep(.01)
    raise AssertionError('synthetic job did not finish')

def test_open_page_plan_and_refresh_never_start_translation(fixture):
    reading,d,sdk=fixture
    assert reading.page(d['document_version_id'],1)['blocks']
    plan=reading.plan(d['document_version_id'],'document')
    assert plan['estimated_new_characters']>0 and plan['requests_upper']>0
    reading.page(d['document_version_id'],1);assert not sdk.calls and not reading.history(d['document_version_id'])

def test_full_document_requires_confirmation_and_scope_plan(fixture):
    reading,d,sdk=fixture;p=reading.plan(d['document_version_id'],'document')
    with pytest.raises(CopilotError):reading.start(d['document_version_id'],'document',None,None,p['plan_id'],False,True)
    with pytest.raises(CopilotError):reading.start(d['document_version_id'],'document',None,None,'wrong',True,True)
    assert not sdk.calls

def test_page_translation_is_bilingual_source_bound_and_persistent(fixture):
    reading,d,sdk=fixture;version=d['document_version_id'];p=reading.plan(version,'page',1)
    job=finish(reading,reading.start(version,'page',1,None,p['plan_id'],True,True))
    assert job['status']=='completed' and job['completed']
    count=len(sdk.calls);new=AliyunReading(reading.service,reading.store,reading.provider_factory)
    page=new.page(version,1);assert any(b['translation'] for b in page['blocks']) and all(b['sources'][0]['pdf_page']==1 for b in page['blocks'])
    next_plan=new.plan(version,'page',1);assert next_plan['estimated_new_characters']==0 and next_plan['cache_saved_characters']>0
    finish(new,new.start(version,'page',1,None,next_plan['plan_id'],True,True));assert len(sdk.calls)==count

def test_pause_and_resume_never_discards_completed_chunks(fixture):
    reading,d,sdk=fixture;version=d['document_version_id'];sdk.wait=threading.Event();p=reading.plan(version,'document')
    started=reading.start(version,'document',None,None,p['plan_id'],True,True)
    for _ in range(100):
        if sdk.calls:break
        time.sleep(.01)
    reading.pause(started['job_id']);sdk.wait.set();paused=finish(reading,started)
    assert paused['status']=='paused' and paused['completed']
    before=list(paused['completed']);resumed=finish(reading,reading.resume(started['job_id'],True))
    assert resumed['status']=='completed' and set(before)<=set(resumed['completed'])

def test_other_window_reports_live_task_then_restart_reports_interruption(fixture):
    reading,d,sdk=fixture;version=d['document_version_id'];p=reading.plan(version,'document');sdk.wait=threading.Event()
    started=reading.start(version,'document',None,None,p['plan_id'],True,True)
    for _ in range(100):
        if sdk.calls:break
        time.sleep(.01)
    other=AliyunReading(reading.service,reading.store,reading.provider_factory)
    assert other.get_job(started['job_id'])['status']=='running'
    with pytest.raises(CopilotError) as e:other.resume(started['job_id'],True)
    assert e.value.code=='translation_job_running'
    count=len(sdk.calls);other.page(version,1);assert len(sdk.calls)==count
    reading.pause(started['job_id']);sdk.wait.set();finish(reading,started)
    checkpoint=reading.get_job(started['job_id']);checkpoint['status']='running';reading._save(checkpoint)
    assert other.get_job(started['job_id'])['status']=='interrupted'

def test_blocks_preserve_exact_raw_ranges_without_api(fixture):
    reading,d,sdk=fixture
    blocks=reading.blocks(d['document_version_id']);assert all(b['original_text']==reading.service.get_pages(d['document_version_id'])[b['pdf_page']-1]['raw_text'][b['sources'][0]['quote_char_start']:b['sources'][0]['quote_char_end']] for b in blocks)
    assert not sdk.calls

def test_unreliable_formula_and_reference_blocks_are_never_sent(fixture,monkeypatch):
    reading,d,sdk=fixture;version=d['document_version_id'];reading.documents.clear()
    raw='Abstract\nA research study tests generalization.\n\nx = y = z\n\nReferences\n[1] Example, title, 2020.'
    monkeypatch.setattr(reading.service,'get_pages',lambda _: [{'pdf_page':1,'raw_text':raw}])
    monkeypatch.setattr('research_copilot.aliyun_reading.geometry',lambda *_:{})
    blocks=reading.blocks(version)
    assert any(b['block_kind']=='formula' and b['source_only'] for b in blocks)
    assert any(b['block_kind']=='reference' and b['source_only'] for b in blocks)
    p=reading.plan(version,'document');finish(reading,reading.start(version,'document',None,None,p['plan_id'],True,True))
    assert all('x = y = z' not in s and '[1] Example' not in s for s in sdk.calls)

def test_inline_ieee_and_spaced_elsevier_headings_preserve_all_source_text(fixture,monkeypatch):
    reading,d,sdk=fixture;reading.documents.clear()
    raw='A B S T R A C T\nA complete abstract statement.\nI. INTRODUCTION With a specific need, we study privacy.\n2.1. Technical procedure\nA method is described here.'
    monkeypatch.setattr(reading.service,'get_pages',lambda _: [{'pdf_page':1,'raw_text':raw}])
    monkeypatch.setattr('research_copilot.aliyun_reading.geometry',lambda *_:{})
    blocks=reading.blocks(d['document_version_id']);headings=[b for b in blocks if b['block_kind']=='heading']
    assert [h['original_text'] for h in headings]==['A B S T R A C T','I. INTRODUCTION','2.1. Technical procedure']
    assert headings[-1]['heading_level']==2
    assert ''.join(b['original_text'].replace(' ','') for b in blocks)==raw.replace('\n','').replace(' ','')
    assert not sdk.calls

def test_same_source_scope_reuses_translation_despite_section_label(fixture):
    from copy import deepcopy
    reading,d,sdk=fixture;block=next(b for b in reading.blocks(d['document_version_id']) if not b['source_only'])
    client=reading.provider_factory(d,True)
    reading.translation.translate_passage(d,block,client)
    same=deepcopy(block);same['sources'][0]['section']='Different reading field label'
    before=len(sdk.calls)
    result=reading.translation.translate_passage(d,same,client)
    assert result['cache_hit'] and len(sdk.calls)==before

def test_missing_credentials_streamlit_reading_component_still_opens(fixture,monkeypatch):
    from streamlit.testing.v1 import AppTest
    reading,d,sdk=fixture
    monkeypatch.setenv('ALIBABA_CLOUD_ACCESS_KEY_ID','');monkeypatch.setenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET','')
    code=f'''from research_copilot import CopilotService
from research_copilot.aliyun_ui import render_aliyun
service=CopilotService({str(reading.service.root)!r})
render_aliyun(service,service.get_document({d['document_version_id']!r}),1)
'''
    app=AppTest.from_string(code).run(timeout=30)
    assert not app.exception
    assert any('AccessKey' in i.value for i in app.info)
    assert any(b.label=='翻译所选段落' for b in app.button)
    assert not sdk.calls

def test_web_api_plan_and_translation_share_service_cache(fixture):
    from research_copilot.web import Workbench
    reading,d,sdk=fixture;version=d['document_version_id'];app=Workbench(reading.service.root);app.aliyun_reading=reading
    assert app.get('/api/aliyun/page',{'version':[version],'page':['1']})['blocks']
    plan=app.post('/api/aliyun/plan',{'version':version,'scope':'page','page':1})
    assert not sdk.calls
    job=app.post('/api/aliyun/start',{'version':version,'scope':'page','page':1,'plan_id':plan['plan_id'],'confirm_plan':True,'consent':True})
    assert finish(reading,job)['status']=='completed'
    assert any(b['translation'] for b in app.get('/api/aliyun/page',{'version':[version],'page':['1']})['blocks'])

def test_source_first_fields_can_publish_cached_chunks_without_new_credentials(fixture,monkeypatch):
    from research_copilot.web import Workbench
    from research_copilot.source_reading import FIELDS
    reading,d,sdk=fixture;version=d['document_version_id'];app=Workbench(reading.service.root)
    block=next(b for b in reading.blocks(version) if not b['source_only'])
    reading.provider_factory(d,True).translate(block['original_text'],'en','zh')
    before=len(sdk.calls)
    book={'fields':{f:{'units':[block] if f=='research_questions' else []} for f in FIELDS},'base_card_id':None}
    monkeypatch.setattr(app.source_reading,'load',lambda *a,**k:book)
    monkeypatch.setattr(app.source_reading,'save',lambda doc,b,operation:b)
    def cached_client(data,doc,provider):
        return AliyunProvider(app.store,doc,False,'aliyun',client=sdk,config=Config('','',False,900000))
    monkeypatch.setattr(app,'translation_client',cached_client)
    body={'version':version,'field':'research_questions','provider':'aliyun'}
    p=app.post('/api/source-reading/field-plan',body);assert p['requests']==0
    out=app.post('/api/source-reading/cloud-field',{**body,'plan_id':p['plan_id'],'confirm_plan':True})
    assert out['translation_error'] is None and out['new_requests']==0 and len(sdk.calls)==before
    assert out['book']['fields']['research_questions']['units'][0]['translation']['text']
