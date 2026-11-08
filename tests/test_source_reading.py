"""Source/translation contracts; no generated result is an independent quality label."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from research_copilot.source_reading import build,validate,plan,SourceReading,make_unit,historical_diagnostics,fingerprint
from research_copilot.translation import selection,TranslationService,MockTranslator
from research_copilot.workspace import Workspace
from research_copilot.errors import CopilotError

TEXT='Our method may improve robustness, but does not guarantee generalization.'
def synthetic(texts):
    doc={'paper_id':'synthetic','document_version_id':'synthetic-v1','file_sha256':'synthetic-sha','parser_version':'synthetic','chunker_version':'synthetic','index_version':'synthetic','page_count':len(texts),'source_file':'SYNTHETIC.pdf'}
    pages=[{'pdf_page':i+1,'raw_text':t} for i,t in enumerate(texts)]
    chunks=[dict(doc,chunk_id=str(i),pdf_page=i+1,char_start=0,char_end=len(t),chunk_text=t) for i,t in enumerate(texts)]
    return SimpleNamespace(db=SimpleNamespace(rows=lambda *a:chunks),get_pages=lambda v:pages,get_document=lambda v:doc),doc

def test_abstract_problem_and_background_use_distinct_original_ranges():
    s,d=synthetic(['Abstract\nLakes support freshwater ecosystems. However winter oxygen concentrations remain difficult to estimate. These estimates may inform planning under uncertain conditions [3].\nIntroduction\nBetter observations are necessary for conservation.'])
    book=build(s,d);validate(s,d,book)
    problem=book['fields']['research_questions']['units'];background=book['fields']['background']['units']
    assert 'difficult' in problem[0]['original_text'] and background[0]['original_text']=='Lakes support freshwater ecosystems.'
    assert problem[0]['unit_id']!=background[0]['unit_id']

def test_full_gap_paragraph_conditions_not_compressed_to_summary():
    details=('However prior assays cannot distinguish the two species under low illumination. '
             'This limitation affects samples at 4 ms, even when x = 12.5 [2]. '
             'The comparison is valid only for the winter cohort. ')*15
    s,d=synthetic(['Abstract\nA botanical assay.\nIntroduction\n'+details+'\nMethods\nA new procedure.'])
    b=build(s,d);units=b['fields']['prior_work_gaps']['units']
    assert any(u['original_text']==details.strip() for u in units)
    assert len(units[0]['original_text'])>2500
    p=plan(b,units[0]['unit_id'],'mock');assert p['requests']>1
    assert ''.join(c['quote'] for c in p['chunks'])==units[0]['original_text']

def test_cross_page_contribution_order_and_every_character_retained():
    s,d=synthetic(['Introduction\nOur contributions are threefold:\n(1) We design an assay that may improve',
                  'detection under low illumination.\n(2) We compare two cohorts.\n(3) We publish code.\nMethods\nProtocol.'])
    b=build(s,d);units=b['fields']['claimed_contributions']['units'];validate(s,d,b)
    assert [u['author_number'] for u in units]==['1','2','3']
    assert [r['pdf_page'] for r in units[0]['sources']]==[1,2] and 'may improve\ndetection' in units[0]['original_text']

def test_missing_evidence_does_not_become_missing_human_review():
    s,d=synthetic(['Methods\nA procedure with no question, motivation or author limitation.'])
    b=build(s,d)
    assert not b['fields']['research_questions']['units'] and not b['fields']['author_limitations']['units']
    assert b['fields']['research_questions']['location']=='not_found_in_checked_scope'

@pytest.fixture
def setup(service,pdf_bytes):
    doc=service.import_pdf(pdf_bytes(['SYNTHETIC. '+TEXT+' A second source context. '*8]),'SYNTHETIC.pdf')['data']
    from research_copilot.web import Workbench
    app=Workbench(service.root);anchor=selection(service,doc['document_version_id'],1,TEXT)
    empty=build(service,doc)
    p={'section':'Synthetic problem statement','spans':[{'pdf_page':1,'char_start':anchor['char_start'],'char_end':anchor['char_end'],'text':TEXT}],'text':TEXT}
    unit=make_unit(doc,p,'test_fixture_original_not_a_quality_label');empty['fields']['research_questions']['units']=[unit]
    book=app.source_reading.save(doc,empty,'synthetic_test_setup')
    return app,doc,book,unit

def test_translation_consumes_exact_original_never_summary_and_appends_versions(setup):
    app,d,b,u=setup;seen=[]
    class Client(MockTranslator):
        def complete_json(self,stage,system,user):
            seen.append(user['selected_text']);return super().complete_json(stage,system,user)
    p=plan(b,u['unit_id'],'mock');result=app.source_reading.translate(d,b,u['unit_id'],'mock',app.translation,Client(),approved_plan=p['plan_id'])
    translated=result['book']['fields']['research_questions']['units'][0]
    assert seen==[TEXT] and '不能保证' in translated['translation']['text']
    assert translated['original_text']==TEXT and translated['translation']['mode']=='mock'
    assert result['book']['source_reading_version']==2 and result['book']['parent_snapshot_id']==b['id']
    assert b['fields']['research_questions']['units'][0]['translation'] is None

def test_plan_must_be_confirmed_and_scope_hash_cannot_change(setup):
    app,d,b,u=setup;data={'version':d['document_version_id'],'snapshot_id':b['id'],'unit_id':u['unit_id'],'provider':'mock'}
    with pytest.raises(CopilotError):app.post('/api/source-reading/translate',data)
    p=app.post('/api/source-reading/plan',data)
    with pytest.raises(CopilotError):app.post('/api/source-reading/translate',dict(data,confirm_plan=True,plan_id=p['plan_id'],glossary={'robustness':'鲁棒性'}))
    assert not app.store.records('translation')

def test_no_paid_or_unconfigured_provider_fallback(setup,monkeypatch):
    app,d,b,u=setup;monkeypatch.delenv('RC_LIBRETRANSLATE_URL',raising=False)
    data={'version':d['document_version_id'],'snapshot_id':b['id'],'unit_id':u['unit_id'],'provider':'deepseek','confirm_plan':True,'consent':True}
    p=app.post('/api/source-reading/plan',data)
    with pytest.raises(CopilotError) as ex:app.post('/api/source-reading/translate',dict(data,plan_id=p['plan_id']))
    assert ex.value.code=='llm_not_configured' and not app.store.records('translation')
    data['provider']='libretranslate';p=app.post('/api/source-reading/plan',data)
    result=app.post('/api/source-reading/translate',dict(data,plan_id=p['plan_id']))
    unit=result['book']['fields']['research_questions']['units'][0]
    assert result['error']['code']=='provider_unconfigured' and unit['translation']['text'] is None
    assert unit['original_text']==TEXT and result['new_requests']==0

def test_cache_restores_same_source_with_zero_new_requests(setup):
    app,d,b,u=setup;p=plan(b,u['unit_id'],'mock')
    one=app.source_reading.translate(d,b,u['unit_id'],'mock',app.translation,app.mock,approved_plan=p['plan_id'])
    two=app.source_reading.translate(d,one['book'],u['unit_id'],'mock',app.translation,MockTranslator(),approved_plan=p['plan_id'])
    assert one['new_requests']==1 and two['new_requests']==0
    restored=SourceReading(app.service,Workspace(app.service.root)).load(d,snapshot_id=two['book']['id'])
    assert restored['fields']['research_questions']['units'][0]['translation']['parts'][0]['cache_hit']

def test_reuse_existing_translation_requires_exact_original_not_a_summary(setup):
    app,d,b,u=setup;anchor=selection(app.service,d['document_version_id'],1,TEXT)
    wrong={**anchor,'text':'A high-level AI summary is not the original'}
    app.store.add('translation',d['document_version_id'],{'anchor':wrong,'provider':'mock','mode':'mock','translation':'不能显示这条摘要译文','glossary':{}})
    before=app.source_reading.load(d,snapshot_id=b['id'])
    assert before['fields']['research_questions']['units'][0]['translation'] is None
    app.translation.translate(anchor,client=MockTranslator())
    after=app.source_reading.load(d,snapshot_id=b['id'])
    assert after['fields']['research_questions']['units'][0]['translation']['origin']=='reused_exact_saved_translation'
    assert '不能保证' in after['fields']['research_questions']['units'][0]['translation']['text']

def test_snapshot_scope_and_translation_source_fingerprint_checked(setup):
    app,d,b,u=setup;bad=deepcopy(b);bad['document_version_id']='other'
    with pytest.raises(CopilotError):app.source_reading.save(d,bad,'forged')
    bad=deepcopy(b);bad['fields']['research_questions']['units'][0]['translation']={'source_fingerprint':'different'}
    with pytest.raises(CopilotError):validate(app.service,d,bad)

def test_same_source_for_two_fields_is_explicit_and_translation_updates_both(setup):
    app,d,b,u=setup;b['fields']['motivation']['units']=[deepcopy(u)]
    p=plan(b,u['unit_id'],'mock');r=app.source_reading.translate(d,b,u['unit_id'],'mock',app.translation,app.mock,approved_plan=p['plan_id'])
    assert r['book']['fields']['motivation']['units'][0]['translation']==r['book']['fields']['research_questions']['units'][0]['translation']

def test_long_exact_original_is_locatable_without_2500_character_cutoff(service,pdf_bytes):
    from research_copilot.web import Workbench
    text='Long exact synthetic paragraph. '*110
    d=service.import_pdf(pdf_bytes([text]),'SYNTHETIC-long.pdf')['data'];raw=service.get_pages(d['document_version_id'])[0]['raw_text']
    app=Workbench(service.root);ref={'document_version_id':d['document_version_id'],'paper_id':d['paper_id'],'file_sha256':d['file_sha256'],'pdf_page':1,'quote':raw,'quote_char_start':0,'quote_char_end':len(raw)}
    result=app.post('/api/inspect-source',{'ref':ref})
    assert result['anchor']['text']==raw and result['anchor']['char_end']==len(raw)

def test_real_frozen_cards_diagnose_missing_model_output_but_show_original():
    if not (Path(__file__).resolve().parents[1]/'data/library.sqlite3').is_file():pytest.skip('Private paper library is not distributed')
    from evaluation.io import ReadOnlyService
    service=ReadOnlyService(Path(__file__).resolve().parents[1]/'data')
    for prefix,version in [('aeefa68',5),('59363af',8)]:
        d=next(d for d in service.list_documents() if d['file_sha256'].startswith(prefix))
        card=json.loads(service.db.one('SELECT record_json FROM reading_cards WHERE document_version_id=? AND card_version=?',(d['document_version_id'],version))['record_json']);before=deepcopy(card)
        b=build(service,d,card);validate(service,d,b)
        assert b['fields']['research_questions']['units'] and b['fields']['motivation']['units']
        assert len(b['fields']['claimed_contributions']['units'])==3
        assert historical_diagnostics(card,'research_questions')['extraction']=='no_model_output'
        assert all(u['translation'] is None and u['human_review']=='unreviewed' for v in b['fields'].values() for u in v['units'])
        for value in b['fields'].values():
            positions=[(u['sources'][0]['pdf_page'],u['sources'][0]['quote_char_start']) for u in value['units']]
            assert positions==sorted(positions)
        assert not any('References' in u['section'] or 'CRediT' in u['original_text'] for u in b['fields']['conclusions']['units'])
        assert not any('E-mail address' in u['original_text'] for v in b['fields'].values() for u in v['units'])
        if prefix=='aeefa68':
            assert any(u['original_text'].startswith('With the gradual development') and 'detection. Currently' in u['original_text'] for u in b['fields']['motivation']['units'])
            assert any('The presented an IoV' in u['original_text'] and 'effectively reducing the risk' in u['original_text'] and {r['pdf_page'] for r in u['sources']}=={3,4} for u in b['fields']['method']['units'])
        assert card==before
