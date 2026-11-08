"""Contract tests only; actual local-engine output is separately inspected."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from research_copilot.prose import reading_prose
from research_copilot.source_reading import build,make_unit,fingerprint
from research_copilot.web import Workbench
from research_copilot.errors import CopilotError

def test_pdf_line_wrap_cleanup_retains_semantic_boundaries_and_symbols():
    text='We pro-\npose a single-\npoint method. It may not generalize [3].\n\nFor x = 12.5, 20% at 4 ms applies only here.'
    out=reading_prose(text)
    assert out=='We propose a single-point method. It may not generalize [3].\n\nFor x = 12.5, 20% at 4 ms applies only here.'
    assert reading_prose('X-\nCANIDS uses x-\ny [2].')=='X-CANIDS uses x-y [2].'

class LocalClient:
    provider='argos';config_data={'local':True,'config_revision':'synthetic-engine'}
    def __init__(self):self.calls=[];self.inputs=[]
    def public_info(self):return {'provider':'argos','config_revision':'synthetic-engine','input_normalizer':'pdf-prose-v1'}
    def translate(self,text,*args):
        self.inputs.append(text);self.calls.append({'status':'returned','provider':'argos'})
        return {'translation':'本方法可能改善结果，但不能保证泛化 [3]。','input_text':reading_prose(text),'input_normalizer':'pdf-prose-v1',
            'engine':'synthetic contract fixture, not quality evidence','terminology_notes':[], 'usage':{'input_characters':len(text)},'cost_kind':'local_no_api_fee'}

@pytest.fixture
def material(service,pdf_bytes):
    pieces=['Our method may improve the results,','but does not guarantee generalization [3].']
    d=service.import_pdf(pdf_bytes([s+' More source context. '*20 for s in pieces]),'SYNTHETIC.pdf')['data']
    pages=service.get_pages(d['document_version_id']);spans=[{'pdf_page':p['pdf_page'],'char_start':0,'char_end':len(s),'text':p['raw_text'][:len(s)]} for p,s in zip(pages,pieces)]
    b=build(service,d);u=make_unit(d,{'section':'Synthetic paragraph','spans':spans},'test_fixture')
    b['fields']['research_questions']['units']=[u];b['fields']['motivation']['units']=[deepcopy(u)]
    app=Workbench(service.root);b=app.source_reading.save(d,b,'test_fixture');return app,d,b,u

def test_cross_page_paragraph_is_one_translation_not_two_unrelated_fragments(material):
    app,d,b,u=material;c=LocalClient()
    result=app.source_reading.translate_local(d,b,app.translation,c,field='research_questions')
    assert c.inputs==[u['original_text']]
    tr=result['book']['fields']['research_questions']['units'][0]['translation']
    assert tr['source_fingerprint']==fingerprint(u['original_text']) and len(tr['parts'])==1
    assert len(tr['parts'][0]['source_ranges'])==2
    assert result['book']['fields']['motivation']['units'][0]['translation']==tr
    assert b['fields']['research_questions']['units'][0]['translation'] is None
    saved=app.store.records('translation')[0]
    assert saved['source_text']==u['original_text'] and len(saved['source_ranges'])==2
    assert result['book']['source_reading_version']==2 and saved['review_status']=='unreviewed'

def test_full_source_cache_reused_without_requests_and_without_new_version(material):
    app,d,b,u=material;c=LocalClient()
    one=app.source_reading.translate_local(d,b,app.translation,c,field='research_questions')
    two=app.source_reading.translate_local(d,one['book'],app.translation,c,field='research_questions')
    assert len(c.calls)==1 and two['new_requests']==0 and two['book']['id']==one['book']['id']
    fresh=app.source_reading.load(d,fresh=True)
    # Explicit source cache reuse is tested independently on the original unit.
    item=app.translation.translate_passage(d,u,c)
    assert item['cache_hit'] and item['new_requests']==0 and len(c.calls)==1

def test_local_automatic_path_cannot_send_to_cloud_or_cross_version(material):
    app,d,b,u=material;c=LocalClient();c.provider='deepseek'
    with pytest.raises(CopilotError):app.source_reading.translate_local(d,b,app.translation,c)
    assert not c.calls
    c.provider='argos';bad=deepcopy(u);bad['sources'][1]['document_version_id']='wrong'
    with pytest.raises(CopilotError):app.translation.translate_passage(d,bad,c)
    assert not c.calls and not app.store.records('translation')

def test_local_batch_error_keeps_saved_book_in_successful_transport_envelope(material,monkeypatch):
    app,d,b,u=material;c=LocalClient()
    def reject(*args):raise CopilotError('provider_quota','synthetic local resource limit','translation')
    c.translate=reject
    monkeypatch.setattr(app,'translation_client',lambda *args:c)
    result=app.post('/api/source-reading/local-translate',{'version':d['document_version_id'],'snapshot_id':b['id'],'field':'research_questions'})
    assert 'error' not in result and result['translation_error']['code']=='provider_quota'
    assert result['book']['source_reading_version']==2 and result['book']['fields']['research_questions']['units'][0]['original_text']==u['original_text']
