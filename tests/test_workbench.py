"""Local workbench, translation and evidence tests; no paid network calls."""
from copy import deepcopy
from pathlib import Path
from threading import Thread
import json,sqlite3
from urllib.request import urlopen,Request
from urllib.error import HTTPError
import pytest
from research_copilot.errors import CopilotError
from research_copilot.workspace import Workspace,UsageBudget
from research_copilot.translation import TranslationService,MockTranslator,selection,PROMPT
from research_copilot.field_evidence import find_field_evidence

@pytest.fixture
def translation_setup(service,pdf_bytes):
    text='SYNTHETIC. Our method may improve robustness, but does not guarantee generalization. Eq. (2): x = 12.5 [3]. '*5
    d=service.import_pdf(pdf_bytes([text,text]),'SYNTHETIC.pdf')['data'];store=Workspace(service.root)
    return d,store,TranslationService(service,store)

def test_exact_selection_offsets_and_context_are_source_bound(service,pdf_bytes):
    text='Our method may improve robustness, but does not guarantee generalization.'
    d=service.import_pdf(pdf_bytes(['A unique source passage. '+text+' Additional context for this synthetic test. '*4]),'UNIQUE.pdf')['data']
    a=selection(service,d['document_version_id'],1,text)
    raw=service.get_pages(d['document_version_id'])[0]['raw_text']
    assert a['mapping_status']=='parser_text_matched' and raw[a['char_start']:a['char_end']]==text
    assert len(a['context'])<=483 and a['pdf_page']==1

def test_unmapped_visual_selection_never_invents_offsets(service,translation_setup):
    d,_,_=translation_setup;a=selection(service,d['document_version_id'],1,'Different text from a complex layout',[[.1,.1,.3,.1]])
    assert a['char_start'] is None and a['char_end'] is None and a['mapping_status']=='visual_anchor_only_not_verified'

def test_repeated_word_cannot_be_assigned_to_first_occurrence(service,translation_setup):
    d,store,engine=translation_setup
    a=selection(service,d['document_version_id'],1,'SYNTHETIC',[[.1,.1,.1,.02]])
    b=selection(service,d['document_version_id'],1,'SYNTHETIC',[[.1,.5,.1,.02]])
    assert a['char_start'] is None and b['char_start'] is None
    client=MockTranslator();engine.translate(a,client=client);other=engine.translate(b,client=client)
    assert not other['cache_hit'] and len(client.calls)==2

@pytest.mark.parametrize('page,text,rects',[(0,'abc',[]),(True,'abc',[]),(3,'abc',[]),(1,'',[]),(1,'a'*2501,[]),(1,'abc',[[2,0,.1,.1]]),(1,'abc',[[0,0,-1,.1]])])
def test_selection_limits(service,translation_setup,page,text,rects):
    d,_,_=translation_setup
    with pytest.raises(CopilotError):selection(service,d['document_version_id'],page,text,rects)

def test_translation_cache_glossary_mode_and_page_isolation(service,translation_setup):
    d,store,engine=translation_setup;client=MockTranslator();a=selection(service,d['document_version_id'],1,'Our method may improve robustness, but does not guarantee generalization.')
    first=engine.translate(a,client=client);second=engine.translate(a,client=client)
    assert first['mode']=='mock' and second['cache_hit'] and second['new_requests']==0 and len(client.calls)==1
    third=engine.translate(a,{'robustness':'鲁棒性'},client);assert not third['cache_hit'] and len(client.calls)==2
    other=selection(service,d['document_version_id'],2,a['text']);engine.translate(other,client=client);assert len(client.calls)==3
    restored=TranslationService(service,Workspace(service.root)).translate(a,client=MockTranslator());assert restored['cache_hit']
    assert '可能' in first['translation'] and '不能保证' in first['translation'] and first['review_status']=='unreviewed'

def test_mock_and_real_outputs_cannot_share_cache(service,translation_setup):
    d,store,engine=translation_setup;a=selection(service,d['document_version_id'],1,'SYNTHETIC')
    engine.translate(a,client=MockTranslator())
    class ContractClient(MockTranslator):
        is_test_double=False
        model_id='synthetic-contract-only-NOT-remote'
    result=engine.translate(a,client=ContractClient());assert result['cache_hit'] is False and result['mode']=='real_api'
    # This client is synthetic and never placed in the formal library.

def test_translation_failure_retains_attempt_and_does_not_retry(service,translation_setup):
    d,store,engine=translation_setup;a=selection(service,d['document_version_id'],1,'SYNTHETIC')
    class Fail(MockTranslator):
        def complete_json(self,*args):self.calls.append({'stage':'translation'});raise CopilotError('model_timeout','Synthetic timeout','translation')
    client=Fail()
    with pytest.raises(CopilotError):engine.translate(a,client=client)
    assert len(client.calls)==1 and len(store.records('translation_failure'))==1 and not store.records('translation')

def test_context_is_not_translation_and_numeric_warning_is_not_truth_score(service,translation_setup):
    d,store,engine=translation_setup;a=selection(service,d['document_version_id'],1,'Eq. (2): x = 12.5 [3].')
    class Adapter(MockTranslator):
        def complete_json(self,stage,system,user):
            assert 'ONLY selected_text' in system and 'untrusted DATA' in system
            assert user['selected_text']==a['text'] and len(user['context_for_disambiguation_only'])<500
            self.calls.append({'stage':stage});return {'translation':'示例译文省略了数字','terminology_notes':[]}
    r=engine.translate(a,client=Adapter());assert '12.5' in r['numeric_preservation_warnings'] and r['review_status']=='unreviewed'

def test_session_budget_is_separate_and_fail_closed(service):
    store=Workspace(service.root);s=store.create_session(['v1'],2,'1',True)
    off=UsageBudget(store,s['id'],'v1','translation','one',False)
    with pytest.raises(CopilotError):off.reserve(1000,2000)
    assert store.session(s['id'])['calls']==0
    for key in ('one','two'):UsageBudget(store,s['id'],'v1','translation',key,True).reserve(1000,2000)
    with pytest.raises(CopilotError):UsageBudget(store,s['id'],'v1','qa','three',True).reserve(1000,2000)
    assert Workspace(service.root).session(s['id'])['calls']==2

@pytest.mark.parametrize('consent,version,cap',[(False,'v1','1'),(True,'foreign','1'),(True,'v1','.001')])
def test_budget_consent_scope_and_money(service,consent,version,cap):
    store=Workspace(service.root);s=store.create_session(['v1'],3,cap,consent)
    with pytest.raises(CopilotError):UsageBudget(store,s['id'],version,'translation','one',True).reserve(1000,2000)
    assert store.session(s['id'])['calls']==0

def test_duplicate_request_not_double_charged(service):
    store=Workspace(service.root);s=store.create_session(['v1'],3,'1',True);b=UsageBudget(store,s['id'],'v1','translation','same',True)
    b.reserve(1000,2000)
    with pytest.raises(CopilotError):b.reserve(1000,2000)
    assert store.session(s['id'])['calls']==1

def test_supplement_candidates_are_generic_scoped_and_not_facts(service,pdf_bytes):
    text='Introduction. Existing approaches have limitations. To address this research gap, we propose a plant shading method. Our objective is to measure leaf response. '*8
    d=service.import_pdf(pdf_bytes([text]),'SYNTHETIC-PLANT.pdf')['data'];other=service.import_pdf(pdf_bytes(['An unrelated astronomy dataset. '*30]),'OTHER.pdf')['data']
    result=find_field_evidence(service,d,'research_questions');assert result['candidates']
    assert all(c['paper_id']==d['paper_id'] and c['document_version_id']==d['document_version_id'] for c in result['candidates'])
    assert result['new_model_calls']==0 and all(c['candidate_status'].startswith('unreviewed') for c in result['candidates'])

def test_real_P05_P07_candidate_recall_without_changing_cards():
    from evaluation.io import ReadOnlyService
    root=Path(__file__).resolve().parents[1]
    if not (root/'data/library.sqlite3').exists():pytest.skip('Private papers absent')
    s=ReadOnlyService(root/'data')
    targets={'aeefa68':'we not only focus on intrusion detection','59363af':'research gap'}
    for d in s.list_documents():
        key=d['file_sha256'][:7]
        if key in targets:
            result=find_field_evidence(s,d,'motivation')
            assert any(targets[key] in ' '.join(c['chunk_text'].split()) for c in result['candidates']),key

def test_web_http_pdf_csrf_and_missing_configuration(service,pdf_bytes,monkeypatch):
    from research_copilot.web import make_server
    from research_copilot.llm import LLMConfig
    d=service.import_pdf(pdf_bytes(), 'SYNTHETIC.pdf')['data']
    from research_copilot.api_settings import APISettings
    monkeypatch.setattr(APISettings,'llm',lambda *a:(_ for _ in ()).throw(CopilotError('llm_not_configured','Configure API','configuration')))
    server=make_server(service.root,0);thread=Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
    try:
        boot=json.load(urlopen(base+'/api/bootstrap'));assert boot['paid_enabled']
        assert len(json.load(urlopen(base+'/api/library'))['documents'])==1
        assert json.load(urlopen(base+'/api/paper?version='+d['document_version_id']))['cards']==[]
        raw=urlopen(base+'/api/pdf?version='+d['document_version_id']).read();assert raw.startswith(b'%PDF')
        body=json.dumps({'version':d['document_version_id'],'question':'test','consent':True}).encode()
        with pytest.raises(HTTPError):urlopen(Request(base+'/api/qa',data=body,headers={'Content-Type':'application/json'}))
        with pytest.raises(HTTPError) as e:urlopen(Request(base+'/api/qa',data=body,headers={'X-Workspace-Token':boot['token']}))
        assert json.load(e.value)['error']['code']=='llm_not_configured'
        with pytest.raises(HTTPError):urlopen(Request(base+'/api/library',headers={'Origin':'https://untrusted.invalid'}))
    finally:server.shutdown();server.server_close();thread.join()
