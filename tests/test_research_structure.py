"""Structural recall tests; synthetic fixtures are not model quality labels."""
from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from research_copilot.research_structure import structure,focus_sources,reading_sources,research_preview
from research_copilot.evidence import validate_source_locations

def fixture(texts):
    doc={'paper_id':'synthetic-paper','document_version_id':'synthetic-v1','file_sha256':'synthetic-sha',
         'parser_version':'synthetic-parser','chunker_version':'synthetic-chunker','index_version':'synthetic-index','page_count':len(texts)}
    pages=[{'pdf_page':i+1,'raw_text':t} for i,t in enumerate(texts)];chunks=[]
    for page in pages:
        for a in range(0,len(page['raw_text']),400):
            b=min(a+500,len(page['raw_text']))
            chunks.append({**doc,'chunk_id':str(page['pdf_page'])+'-'+str(a),'pdf_page':page['pdf_page'],'char_start':a,'char_end':b,'chunk_text':page['raw_text'][a:b]})
    return SimpleNamespace(db=SimpleNamespace(rows=lambda *a:chunks),get_pages=lambda v:pages),doc

def test_abstract_concrete_problem_without_fixed_intent_keywords():
    text='Abstract\nThe concentration of dissolved oxygen in deep lakes remains difficult to estimate during winter. '+('Additional ecological measurements. '*8)+'\n1 Introduction\n'+('Aquatic context. '*40)
    s,d=fixture([text]);refs=focus_sources(s,d,'research_questions')
    assert refs[0]['section_hint']=='Abstract' and 'oxygen' in refs[0]['chunk_text']
    assert all(x['candidate_status']=='unreviewed_relevance_not_fact_support' for x in refs)

def test_motivation_introduction_reserved_separate_from_background():
    s,d=fixture(['Abstract\n'+('An ecological study. '*8)+'\nIntroduction\nContext and background. However winter sampling is sparse; this uncertainty prevents conservation planning. '+('Details. '*80)])
    assert focus_sources(s,d,'motivation')[0]['section_hint']=='Introduction'
    preview=research_preview(s,d)
    assert set(preview['fields'])=={'research_questions','background','prior_work_gaps','motivation'}
    assert preview['model_extraction']=='not_run' and preview['human_review']=='unreviewed'

@pytest.mark.parametrize('lead',['Our contributions are threefold:','The contributions of this paper are:','The main contributions are summarized as follows:'])
def test_author_list_preserves_order_and_exact_quotes(lead):
    s,d=fixture(['Introduction\n'+lead+'\n(1) First mechanism.\n(2) Second mechanism.\n(3) Third experiment.\nThe rest of this paper is organized below.\nMethods\nDo not swallow methods.'])
    items=research_preview(s,d)['contributions']
    assert len(items)==3 and [x['author_number'] for x in items]==['1','2','3']
    assert all(x['source_type']=='A_author_enumerated' and x['chinese_summary'] is None for x in items)
    assert 'Do not' not in items[-1]['original_text']
    for x in items:
        for ref in x['sources']:validate_source_locations(s,d,[dict(ref,source_id='E1')])

def test_cross_page_and_chunk_list_retains_all_parts():
    s,d=fixture(['Introduction\nOur contributions are threefold:\n(1) '+('Ecology mechanism. '*40),
                 'continuation of first contribution.\n(2) Independent field trial.\n(3) Open data release.\nConclusion\nEnd.'])
    items=research_preview(s,d)['contributions']
    assert len(items)==3 and {r['pdf_page'] for r in items[0]['sources']}=={1,2}
    assert len(items[0]['sources'])>=3

def test_explicit_contribution_heading_and_inline_items():
    s,d=fixture(['Introduction\nBackground.\n2 Contributions\n(1) New assay. (2) Validation cohort. (3) Open protocol.\n3 Methods\nOther.'])
    assert [x['original_text'] for x in structure(s,d)['contributions']]==['New assay.','Validation cohort.','Open protocol.']

def test_chinese_headings_and_ordinal_bullets():
    s,d=fixture(['摘要\n'+('植物的缺氧环境需要长期观测。'*12)+'\n引言\n现有观测不足。\n主要贡献\n首先，建立新型植物观测方法。\n其次，验证观测协议。\n最后，发布原始数据。\n方法\n实验设计。'])
    assert len(structure(s,d)['contributions'])==3
    assert focus_sources(s,d,'motivation')[0]['section_hint']=='Introduction'
    assert not focus_sources(s,d,'claimed_contributions','Abstract')

def test_no_list_no_abstract_is_explicitly_empty():
    s,d=fixture(['Methods\nA short protocol with no author contribution list.'])
    assert structure(s,d)['contributions']==[]
    assert focus_sources(s,d,'motivation','Introduction')==[]
    assert focus_sources(s,d,'research_questions','Abstract')==[]

def test_section_priority_does_not_mutate_saved_or_synthesize_fact():
    s,d=fixture(['Abstract\n'+('Hard measurement problem. '*12)+'\nIntroduction\n'+('Context. '*50)])
    diagnostics={};sources=reading_sources(s,d,'framing',[],None,diagnostics,retriever=lambda *a,**k:[])
    assert sources[0]['section_hint']=='Abstract' and diagnostics['section_reserved_sources']>0
    assert not any('fact_id' in x for x in sources)

def test_scope_validation_still_rejects_wrong_version():
    s,d=fixture(['Abstract\n'+('Concrete observation. '*10)+'\nIntroduction\nContext.'])
    bad=focus_sources(s,d,'research_questions')[0]|{'document_version_id':'other'}
    from research_copilot.errors import CopilotError
    with pytest.raises(CopilotError):reading_sources(s,d,'framing',[],None,{},retriever=lambda *a,**k:[bad])

def test_real_p05_p07_offline_lists_and_immutable_missing_fields():
    if not (Path(__file__).resolve().parents[1]/'data/library.sqlite3').is_file():pytest.skip('Private paper library is not distributed')
    from evaluation.io import ReadOnlyService
    service=ReadOnlyService(Path(__file__).resolve().parents[1]/'data')
    for prefix,version in [('aeefa68',5),('59363af',8)]:
        doc=next(d for d in service.list_documents() if d['file_sha256'].startswith(prefix))
        p=research_preview(service,doc)
        assert len(p['contributions'])==3 and all(x['sources'][0]['pdf_page']==2 for x in p['contributions'])
        assert p['fields']['research_questions'][0]['section_hint']=='Abstract'
        record=json.loads(service.db.one('SELECT record_json FROM reading_cards WHERE document_version_id=? AND card_version=?',(doc['document_version_id'],version))['record_json'])
        assert record['structured_content']['fields']['research_questions']['fact_ids']==[]
        assert not any(x['field']=='research_questions' for x in record['raw_outputs']['framing:extract']['facts'])
