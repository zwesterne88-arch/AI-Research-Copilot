"""One-click orchestration uses a fake official SDK; no real translation quality claims."""
from copy import deepcopy
from types import SimpleNamespace
import threading,time
import pytest
from research_copilot.web import Workbench
from research_copilot.aliyun_translation import AliyunProvider,Config,prepare
from research_copilot.errors import CopilotError

TEXT=('Abstract\nA field concerns reliable measurement. However prior methods cannot estimate the target under low illumination. We propose a method for this problem.\n\n'
      'Introduction\nBetter observations are necessary because existing methods lack coverage. Therefore we aim to address this gap under limited data.\n\n'
      'Our contributions are as follows:\n(1) We design a feature mechanism.\n(2) We evaluate two cohorts.\n(3) We publish code.\n\n'
      'Methods\nOur algorithm combines a local feature extractor with an aggregation mechanism.\n\n'
      'Evaluation\nWe evaluate on two public cohorts with fixed experimental conditions.\n\n'
      'Results\nThe measured improvement is 12% only for the tested cohort [3].\n\n'
      'Conclusion\nOur method may improve robustness, but it cannot guarantee generalization. Future work will extend the protocol.')

class SDK:
    def __init__(self):self.calls=[];self.fail=None;self.wait=None
    def translate_general_with_options(self,request,runtime):
        self.calls.append(request.source_text)
        if self.wait:self.wait.wait(5)
        if self.fail and self.fail in request.source_text:return SimpleNamespace(body={'Code':19999})
        return SimpleNamespace(body={'Code':200,'Data':{'Translated':'【离线测试译文】'+request.source_text},'RequestId':'offline-unit-test'})

@pytest.fixture
def fixture(service,pdf_bytes,monkeypatch):
    monkeypatch.setenv('ALIYUN_TRANSLATE_MONTHLY_LIMIT','900000')
    doc=service.import_pdf(pdf_bytes([TEXT]),'SYNTHETIC-ONE-CLICK.pdf')['data'];app=Workbench(service.root);sdk=SDK()
    app.aliyun_reading.provider_factory=lambda d,consent:AliyunProvider(app.store,d,consent,'aliyun',client=sdk,config=Config('synthetic-id','synthetic-secret'),sleeper=lambda _:None,max_retries=0)
    return app,doc,sdk

def plan(app,d,fields=None,**extra):return app.post('/api/chinese-reading/plan',{'version':d['document_version_id'],'fields':fields,**extra})
def start(app,d,p,**extra):
    return app.post('/api/chinese-reading/start',{'version':d['document_version_id'],'fields':p['fields'],'snapshot_id':p['snapshot_id'],'plan_id':p['plan_id'],'confirm_plan':True,'consent':True,'character_budget':p['retry_reserved_upper'],**extra})
def finish(app,d,response):
    for _ in range(500):
        result=app.chinese_reading.state(d,None,response['job']['job_id'])
        if result['job']['status']!='running':
            thread=app.aliyun_reading.jobs.get(response['job']['job_id'],{}).get('thread')
            if not thread or not thread.is_alive():return result
        time.sleep(.01)
    raise AssertionError('offline reading did not finish')

def test_open_and_plan_are_local_and_source_fields_are_located(fixture):
    app,d,sdk=fixture;p=plan(app,d)
    assert all(p['field_sources'][f]>0 for f in ('research_questions','motivation','claimed_contributions','method'))
    assert app.get('/api/chinese-reading',{'version':[d['document_version_id']]})['job'] is None
    assert not sdk.calls and not app.store.records('translation_admission')

def test_one_click_begins_without_another_consent_or_budget_form(fixture):
    app,d,sdk=fixture
    out=finish(app,d,app.post('/api/chinese-reading/begin',{'version':d['document_version_id']}))
    assert out['job']['status']=='completed' and sdk.calls
    assert out['job']['authorization']['mode']=='one_click'
    count=len(sdk.calls)
    again=app.post('/api/chinese-reading/begin',{'version':d['document_version_id']})
    assert again['job']['job_id']==out['job']['job_id'] and len(sdk.calls)==count

def test_one_click_resumes_existing_report_and_clears_old_failure_message(fixture):
    app,d,sdk=fixture;sdk.fail='Our algorithm combines'
    out=finish(app,d,app.post('/api/chinese-reading/begin',{'version':d['document_version_id']}))
    old=app.aliyun_reading.get_job(out['job']['job_id']);old['message']='Old failure';app.aliyun_reading._save(old)
    count=len(sdk.calls);sdk.fail=None
    done=finish(app,d,app.post('/api/chinese-reading/begin',{'version':d['document_version_id']}))
    assert done['job']['status']=='completed' and done['job']['job_id']==old['job_id']
    assert len(sdk.calls)==count+1 and 'message' not in done['job']

def test_one_click_during_running_job_reuses_the_same_worker(fixture):
    app,d,sdk=fixture;sdk.wait=threading.Event()
    first=app.post('/api/chinese-reading/begin',{'version':d['document_version_id']})
    second=app.post('/api/chinese-reading/begin',{'version':d['document_version_id']})
    assert first['job']['job_id']==second['job']['job_id']
    sdk.wait.set();assert finish(app,d,first)['job']['status']=='completed'

def test_standalone_equations_retain_exact_operators_without_translation(fixture,service,pdf_bytes,monkeypatch):
    app,_,sdk=fixture
    doc=service.import_pdf(pdf_bytes(['Abstract\nA study describes a method.\nResults\nAccuracy = (TP + TN)/(TP + TN + FP + FN) (3)\n\nWe evaluate the method on two cohorts.']),'EQUATION.pdf')['data']
    monkeypatch.setattr('research_copilot.source_reading.geometry',lambda *_:{})
    out=finish(app,doc,app.post('/api/chinese-reading/begin',{'version':doc['document_version_id']}))
    formula=next(u for v in out['book']['fields'].values() for u in v['units'] if u['original_text'].startswith('Accuracy ='))
    assert formula['source_only'] and formula['translation'] is None
    assert formula['unit_id'] in out['job']['retained'] and not any('Accuracy' in text for text in sdk.calls)
    from research_copilot.prose import standalone_formula
    assert standalone_formula('𝑃 𝑟𝑒𝑐𝑖𝑠𝑖𝑜𝑛 = 𝑇 𝑃 ∕(𝑇 𝑃 + 𝐹 𝑃) (4)')

def test_local_network_denial_pauses_first_unit_without_reserving(fixture,monkeypatch):
    app,d,sdk=fixture
    app.aliyun_reading.provider_factory=lambda doc,consent:AliyunProvider(app.store,doc,consent,'aliyun',config=Config('synthetic-id','synthetic-secret'))
    monkeypatch.setattr('research_copilot.aliyun_network.network_status',lambda:{'reachable':False,'error':{'code':'aliyun_network_permission'},'message':'系统限制联网，未发送翻译。'})
    p=plan(app,d);out=finish(app,d,start(app,d,p))
    assert out['job']['status']=='paused' and len(out['job']['failed'])==1
    assert next(iter(out['job']['failed'].values()))['code']=='aliyun_network_permission'
    assert not app.store.records('translation_admission') and not sdk.calls
    assert not out['job']['completed'] and out['book']['fields']['research_questions']['units'][0]['original_text']

def test_single_confirmation_translates_all_modules_and_saves_chinese_report(fixture):
    app,d,sdk=fixture;p=plan(app,d);out=finish(app,d,start(app,d,p))
    assert out['job']['status']=='completed' and sdk.calls
    b=out['book'];assert b['default_language']=='zh' and not b['llm_called']
    assert all(u['translation']['provider']=='aliyun' and u['translation']['text'] for f in b['fields'].values() for u in f['units'])
    assert all(u['translation']['human_review']=='unreviewed' for f in b['fields'].values() for u in f['units'])
    contributions=b['fields']['claimed_contributions']['units'];assert [u['author_number'] for u in contributions]==['1','2','3']
    assert b['operation']=='one_click_chinese_reading' and app.store.records('source_reading',d['document_version_id'])

def test_original_evidence_not_summary_and_shared_sources_submit_once(fixture):
    app,d,sdk=fixture;p,b,units=app.chinese_reading.prepare(d)
    expected={c['text'] for u in units if not u['source_only'] for c in prepare(u['original_text'])['chunks']}
    out=finish(app,d,start(app,d,p))
    assert set(sdk.calls)==expected and len(sdk.calls)==len(set(sdk.calls))
    assert len(out['job']['completed'])==len(units)
    for f in out['book']['fields'].values():
        for u in f['units']:
            assert u['original_text']=='\n'.join(r['quote'] for r in u['sources'])
            assert u['translation']['source_fingerprint']

@pytest.mark.parametrize('change',[{'consent':False},{'confirm_plan':False},{'character_budget':0},{'plan_id':'wrong'}])
def test_no_permission_or_budget_never_sends(fixture,change):
    app,d,sdk=fixture;p=plan(app,d)
    with pytest.raises(CopilotError):start(app,d,p,**change)
    assert not sdk.calls and not app.store.records('translation_admission')

def test_one_failed_module_keeps_other_results_and_targeted_retry(fixture):
    app,d,sdk=fixture;sdk.fail='Our algorithm combines';p=plan(app,d)
    out=finish(app,d,start(app,d,p));assert out['job']['status']=='finished_with_errors'
    assert out['job']['field_status']['method']['errors']
    assert out['book']['fields']['research_questions']['units'][0]['translation']['text']
    before=len(sdk.calls);sdk.fail=None
    response=app.post('/api/chinese-reading/resume',{'version':d['document_version_id'],'job_id':out['job']['job_id'],'fields':['method']})
    done=finish(app,d,response);assert done['job']['status']=='completed' and len(sdk.calls)==before+1
    assert done['book']['source_reading_version']>out['book']['source_reading_version']

def test_cache_and_completed_report_survive_new_workbench_without_sdk(fixture):
    app,d,sdk=fixture;p=plan(app,d);out=finish(app,d,start(app,d,p));count=len(sdk.calls)
    fresh=Workbench(app.service.root);fresh.aliyun_reading.provider_factory=app.aliyun_reading.provider_factory
    saved=fresh.get('/api/chinese-reading',{'version':[d['document_version_id']]})
    assert saved['book']['id']==out['book']['id'] and saved['job']['status']=='completed'
    next_plan=plan(fresh,d);assert next_plan['estimated_new_characters']==0
    finish(fresh,d,start(fresh,d,next_plan,consent=False));assert len(sdk.calls)==count

def test_pause_between_wire_chunks_and_restart_resumes_only_uncached(fixture,monkeypatch):
    app,d,sdk=fixture
    # Use one long exact source paragraph, rather than an AI-generated summary.
    book=app.source_reading.load(d);unit=deepcopy(book['fields']['method']['units'][0])
    long_text='A long technical mechanism preserves the same semantic conditions. '*180
    original_pages=app.service.get_pages;original_doc=app.service.get_document
    monkeypatch.setattr(app.service,'get_pages',lambda v:[{'pdf_page':1,'raw_text':long_text}])
    from research_copilot.source_reading import make_unit
    u=make_unit(d,{'section':'Methods','spans':[{'pdf_page':1,'char_start':0,'char_end':len(long_text),'text':long_text}]},'synthetic_complete_source')
    book['fields']['method']['units']=[u]
    for name,value in book['fields'].items():
        if name!='method':value['units']=[]
    monkeypatch.setattr(app.source_reading,'load',lambda *a,**k:deepcopy(book))
    sdk.wait=threading.Event();p=plan(app,d,['method']);out=start(app,d,p)
    for _ in range(100):
        if sdk.calls:break
        time.sleep(.01)
    app.chinese_reading.pause(d,out['job']['job_id']);sdk.wait.set();paused=finish(app,d,out)
    assert paused['job']['status']=='paused' and len(sdk.calls)==1 and len(app.store.records('aliyun_chunk'))==1
    # Fresh service object models restart; no UI GET resumes automatically.
    from research_copilot.source_reading import SourceReading
    from research_copilot.aliyun_reading import AliyunReading
    from research_copilot.chinese_reading import ChineseReading
    reader=AliyunReading(app.service,app.store,app.aliyun_reading.provider_factory);pipeline=ChineseReading(SourceReading(app.service,app.store),reader)
    assert pipeline.state(d,None,out['job']['job_id'])['job']['status']=='paused' and len(sdk.calls)==1
    reader.resume(out['job']['job_id'],True);app.aliyun_reading=reader;app.chinese_reading=pipeline
    done=finish(app,d,out);assert done['job']['status']=='completed' and len(sdk.calls)==len(prepare(long_text)['chunks'])

def test_missing_fields_remain_missing_and_mock_is_not_promoted(fixture,monkeypatch):
    app,d,sdk=fixture;book=app.source_reading.load(d)
    for value in book['fields'].values():value['units']=[]
    monkeypatch.setattr(app.source_reading,'load',lambda *a,**k:deepcopy(book))
    p=plan(app,d,['research_questions','motivation']);out=finish(app,d,start(app,d,p))
    assert out['job']['status']=='completed' and not sdk.calls
    assert all(v['status']=='missing_source' for v in out['job']['field_status'].values())

def test_mock_translation_is_discarded_from_new_report(fixture,monkeypatch):
    app,d,sdk=fixture;book=app.source_reading.load(d)
    for f in book['fields'].values():
        for u in f['units']:u['translation']={'provider':'mock','mode':'mock','status':'complete','text':'不是真译文','source_fingerprint':__import__('research_copilot.source_reading',fromlist=['fingerprint']).fingerprint(u['original_text'])}
    monkeypatch.setattr(app.source_reading,'load',lambda *a,**k:deepcopy(book))
    p,b,_=app.chinese_reading.prepare(d)
    assert all(u['translation'] is None for f in b['fields'].values() for u in f['units'])
    out=finish(app,d,start(app,d,p));assert '不是真译文' not in str(out['book'])

def test_job_scope_cannot_be_used_to_resume_another_pdf(fixture,service,pdf_bytes):
    app,d,sdk=fixture;out=finish(app,d,start(app,d,plan(app,d)))
    other=service.import_pdf(pdf_bytes(['Another paper. '*30]),'OTHER.pdf')['data']
    with pytest.raises(CopilotError) as e:app.chinese_reading.resume(other,out['job']['job_id'])
    assert e.value.code=='reading_job_scope'

@pytest.mark.parametrize('legacy_version',[5,8])
def test_old_card_can_be_supplemented_without_llm_or_overwrite(fixture,legacy_version,monkeypatch):
    from research_copilot.reading_schema import empty_content
    app,d,sdk=fixture
    card={'card_id':'synthetic-frozen-card','card_version':legacy_version,'structured_content':empty_content(),'evidence_references':{},'raw_outputs':{},'human_review':'unreviewed'}
    old=deepcopy(card)
    monkeypatch.setattr(app,'client',lambda *a:pytest.fail('LLM must not be invoked'))
    original=app.source_reading.load(d,card);saved=app.source_reading.save(d,original,'synthetic_history')
    p=app.chinese_reading.plan(d,card,saved['id'])
    out=finish(app,d,app.chinese_reading.start(d,card,{'snapshot_id':saved['id'],'fields':p['fields'],'plan_id':p['plan_id'],'character_budget':p['retry_reserved_upper'],'confirm_plan':True,'consent':True}))
    assert card==old and out['book']['base_card_version']==legacy_version and out['book']['id']!=saved['id']
    assert app.source_reading.load(d,card,saved['id'])['source_reading_version']==saved['source_reading_version']

def test_cross_page_author_list_translates_full_multi_range_evidence(fixture,service,pdf_bytes,monkeypatch):
    app,_,sdk=fixture
    doc=service.import_pdf(pdf_bytes(['Introduction\nOur contributions are threefold:\n(1) We design a mechanism that may improve',
                                    'detection under low illumination.\n(2) We compare two cohorts.\n(3) We publish code.\nMethods\nA mechanism.']),'CROSS-PAGE.pdf')['data']
    monkeypatch.setattr('research_copilot.source_reading.geometry',lambda *_:{})
    p=plan(app,doc,['claimed_contributions']);out=finish(app,doc,start(app,doc,p));units=out['book']['fields']['claimed_contributions']['units']
    assert [u['author_number'] for u in units]==['1','2','3']
    assert [r['pdf_page'] for r in units[0]['sources']]==[1,2]
    assert len(units[0]['translation']['parts'][0]['source_ranges'])==2
    assert 'may improve detection under low illumination.' in sdk.calls[0]

def test_no_credentials_blocks_task_without_affecting_original(fixture):
    app,d,sdk=fixture
    app.aliyun_reading.provider_factory=lambda d,c:AliyunProvider(app.store,d,c,'aliyun',client=sdk,config=Config('',''))
    p=plan(app,d)
    with pytest.raises(CopilotError) as e:start(app,d,p)
    assert e.value.code=='aliyun_config' and not sdk.calls and app.service.get_pages(d['document_version_id'])

def test_task_budget_cannot_reset_on_module_resume(fixture):
    app,d,sdk=fixture;sdk.fail='Our algorithm combines';out=finish(app,d,start(app,d,plan(app,d)))
    job=out['job'];client=app.aliyun_reading.provider_factory(d,True);client.budget_scope={'id':job['job_id'],'limit':job['character_budget']}
    client.reserve(job['remaining_characters'])  # Conservative unknown request reservation, in isolated test DB.
    before=len(sdk.calls);sdk.fail=None
    result=finish(app,d,app.chinese_reading.resume(d,job['job_id'],['method']))
    assert result['job']['status']=='paused' and len(sdk.calls)==before and result['job']['remaining_characters']==0
    assert next(iter(result['job']['failed'].values()))['code']=='reading_character_budget'

def test_related_background_is_not_invented_as_question_or_motivation(fixture,monkeypatch):
    app,d,sdk=fixture;book=app.source_reading.load(d)
    for f in book['fields'].values():f['units']=[]
    from research_copilot.source_reading import make_unit
    raw='A field concerns reliable measurement under limited data.'
    monkeypatch.setattr(app.service,'get_pages',lambda v:[{'pdf_page':1,'raw_text':raw}])
    unit=make_unit(d,{'section':'Abstract','spans':[{'pdf_page':1,'char_start':0,'char_end':len(raw),'text':raw}]},'abstract_related_fallback')
    book['fields']['research_questions']['units']=[unit]
    monkeypatch.setattr(app.source_reading,'load',lambda *a,**k:deepcopy(book))
    p=plan(app,d,['research_questions','motivation']);assert p['field_sources']=={'research_questions':0,'motivation':0}
    out=finish(app,d,start(app,d,p));assert not sdk.calls and out['job']['field_status']['research_questions']['status']=='missing_source'
    assert out['book']['fields']['research_questions']['location']=='not_found_in_checked_scope'

def test_background_under_limited_data_does_not_replace_intro_motivation(fixture,service,pdf_bytes,monkeypatch):
    app,_,sdk=fixture
    doc=service.import_pdf(pdf_bytes(['Abstract\nA field studies measurement under limited data. However prior assays cannot distinguish the targets.\nIntroduction\nBetter observations are necessary because current methods lack coverage.']),'BACKGROUND.pdf')['data']
    monkeypatch.setattr('research_copilot.source_reading.geometry',lambda *_:{})
    _,book,_=app.chinese_reading.prepare(doc)
    assert book['fields']['background']['units'][0]['original_text']=='A field studies measurement under limited data.'
    assert all(u['section']=='Introduction' for u in book['fields']['motivation']['units'])
    assert all('background' not in u['shared_fields'] for u in book['fields']['motivation']['units'])
    assert not any(u['original_text']=='A field studies measurement under limited data.' for u in book['fields']['prior_work_gaps']['units'])

def test_double_start_cannot_create_two_concurrent_tasks(fixture):
    from concurrent.futures import ThreadPoolExecutor
    app,d,sdk=fixture;sdk.wait=threading.Event();p=plan(app,d)
    def launch():
        try:return start(app,d,p)
        except CopilotError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:launch(),range(2)))
    live=[r for r in results if r];assert len(live)==1
    sdk.wait.set();finish(app,d,live[0])
    assert len({j['job_id'] for j in app.store.records('aliyun_job') if j['scope']=='reading'})==1


@pytest.mark.parametrize('provider',['google','baidu','deepl','azure'])
def test_one_click_uses_selected_translation_interface(fixture,monkeypatch,provider):
    from research_copilot.reading_provider import configured_provider
    import research_copilot.translation_providers as native
    app,doc,sdk=fixture
    values={'google':{'api_key':'fake'},'baidu':{'app_id':'fake-app','secret_key':'fake'},'deepl':{'api_key':'fake'},'azure':{'api_key':'fake','region':'eastasia'}}[provider]
    app.settings.save({'kind':'translation','provider':provider,'values':values})
    calls=[]
    def request(url,payload,headers):
        calls.append(url)
        return {'google':{'data':{'translations':[{'translatedText':'synthetic translation'}]}},'baidu':{'trans_result':[{'dst':'synthetic translation'}]},'deepl':{'translations':[{'text':'synthetic translation'}]},'azure':[{'translations':[{'text':'synthetic translation'}]}]}[provider]
    monkeypatch.setattr(native,'request_json',request)
    monkeypatch.setattr(native.time,'sleep',lambda _:None)
    app.aliyun_reading.provider_factory=lambda d,c:configured_provider(app.settings,app.store,d,c)
    response=app.post('/api/chinese-reading/begin',{'version':doc['document_version_id']})
    result=finish(app,doc,response)
    assert result['job']['status']=='completed' and calls and not sdk.calls
    assert result['job']['authorization']['provider']==provider
    assert all(r['provider']==provider for r in app.store.records('translation_admission'))
    assert result['job']['submitted_characters']>0
