"""Two-layer rule tests over explicitly SYNTHETIC papers, never model quality scores."""
from copy import deepcopy
import json

import pytest

from research_copilot import CopilotService
from research_copilot.comparison import ComparisonService
from research_copilot.comparison_conditions import CONDITION_LABELS
from research_copilot.comparison_materials import ComparisonCatalog
from research_copilot.reading_history import ReadingHistory
from research_copilot.reading_schema import empty_content, MODULE_FIELDS
from research_copilot.source_audit import card_fingerprint


@pytest.fixture
def comparison_setup(service,pdf_bytes,tmp_path):
    history=ReadingHistory(service.db);audits=[];made=[]
    def make(label,*,obj='flora',metric='Height',kind='experimental',second_unit=False,absent_context=False,existing_doc=None,unknown_dim=None,assignment_label='greenhouse',not_applicable_future=False):
        context=f'task=plant; object={obj}; version=v1; split=70/30; metric={metric}; definition=stem height; unit=mm; aggregation=mean; protocol=greenhouse; role=treatment; baseline=control; assignment={assignment_label}.'
        text=f'SYNTHETIC paper {label}. Background plant growth. Prior gap fixed light. Motivation test shade. Question effect of shade. Contribution variable exposure. Method alternate light. Validation greenhouse. Growth 12 mm. Context: {context} Second validation chamber. Second result 19 mm. Future work explicitly not applicable in this fixture.'
        doc=existing_doc or service.import_pdf(pdf_bytes([text],title='SYNTHETIC '+label),label+'.pdf')['data']
        page=service.get_pages(doc['document_version_id'])[0]['raw_text']
        chunk=service.db.rows('SELECT * FROM chunks WHERE document_version_id=?',(doc['document_version_id'],))[0]
        c={**{k:doc[k] for k in ('paper_id','document_version_id','file_sha256','source_file','page_count')},
            'schema_version':'research-reading-v1','prompt_version':'synthetic-comparison-fixture','planner_version':'synthetic','retrieval_version':'synthetic',
            'pipeline':{k:doc[k] for k in ('file_sha256','parser_version','chunker_version','index_version')},'model_id':'SYNTHETIC-NO-MODEL',
            'execution_mode':'test_double','human_review':'not_performed','card_status':'draft','raw_outputs':{},'modules':{},'structured_content':empty_content(),'evidence_references':{}}
        def add(field,statement,quote,unit=None,attr=None):
            module=next(m for m,fs in MODULE_FIELDS.items() if field in fs);fid=module+':f'+str(len(c['structured_content']['facts'])+1);eid=fid+':E1'
            a=page.index(quote);b=a+len(quote)
            c['structured_content']['facts'][fid]={'fact_id':fid,'field':field,'text':statement,'kind':'author_statement','purpose':'value',
                'attribute':attr,'validation_unit_id':unit,'association_status':'not_established','evidence_ids':[eid],'review_status':'unreviewed'}
            c['evidence_references'][eid]={**{k:doc[k] for k in ('paper_id','document_version_id','file_sha256','parser_version','chunker_version','index_version')},
                'evidence_id':eid,'source_id':'E1','chunk_id':chunk['chunk_id'],'pdf_page':1,'quote':quote,'quote_char_start':a,'quote_char_end':b,
                'quote_found':True,'locator_valid':True,'semantic_support':'synthetic_not_real_review','review_status':'unreviewed'}
            f=c['structured_content']['fields'][field];f.update(status='reported',reason_code='supported_in_scope');f['fact_ids'].append(fid)
            return fid
        for field,quote in [('background','Background plant growth.'),('prior_work_gaps','Prior gap fixed light.'),('motivation','Motivation test shade.'),
                            ('research_questions','Question effect of shade.'),('claimed_contributions','Contribution variable exposure.'),('method','Method alternate light.')]:
            add(field,quote,quote)
        anchor=add('validation','Validation greenhouse.','Validation greenhouse.','validation:u1')
        result=add('findings','Growth 12 mm.','Growth 12 mm. Context: '+context,'validation:u1',
            {'role':'metric','name_raw':metric,'value_raw':'12','unit_raw':'mm','definition_raw':'stem height'})
        c['structured_content']['validation_units']['validation:u1']={'unit_id':'validation:u1','type':kind,'anchor_fact_id':anchor,'review_status':'unreviewed'}
        if second_unit:
            anchor2=add('validation','Second validation chamber.','Second validation chamber.','validation:u2')
            add('findings','Second result 19 mm.','Second result 19 mm.','validation:u2',{'role':'metric','name_raw':None,'value_raw':'19','unit_raw':'mm','definition_raw':None})
            c['structured_content']['validation_units']['validation:u2']={'unit_id':'validation:u2','type':'case_study','anchor_fact_id':anchor2,'review_status':'unreviewed'}
        if not_applicable_future:
            fid=add('future_work','Synthetic applicability evidence.','Future work explicitly not applicable in this fixture.')
            c['structured_content']['facts'][fid]['purpose']='applicability'
            c['structured_content']['fields']['future_work'].update(status='not_applicable',reason_code='not_applicable',fact_ids=[],rationale_fact_id=fid)
        mapping={'research_task':'plant','validation_object':obj,'object_version':'v1','subset_split':'70/30','metric_name':metric,'metric_definition':'stem height',
                 'metric_unit':'mm','aggregation':'mean','protocol':'greenhouse','model_role':'treatment','baseline_context':'control','result_assignment':assignment_label}
        dims={k:{'value':v,'status':'reported','fact_ids':[result],'evidence_ids':c['structured_content']['facts'][result]['evidence_ids']} for k,v in mapping.items()}
        if unknown_dim:dims[unknown_dim]['status']='uncertain'
        c['comparison_contexts']=[] if absent_context else [{'context_id':'synthetic-context','unit_id':'validation:u1','result_fact_id':result,'attribution_status':'source_checked','dimensions':dims}]
        c=history.create(c)
        a={'schema_version':'source-audit-v1',**{k:c[k] for k in ('card_id','card_version','paper_id','document_version_id','file_sha256')},
            'card_fingerprint':card_fingerprint(c),'reviewer_type':'development_assistant','human_review':'not_performed','items':[],'citations':[]}
        for fid,f in c['structured_content']['facts'].items():
            a['items'].append({'item_id':fid,'research_module':fid.split(':')[0],'current_field':f['field'],'original_statement':f['text'],
                'evidence_ids':f['evidence_ids'],'support_status':'supported','assessment_and_issues':'SYNTHETIC test assumption, not real source assessment',
                'user_review_status':'pending','comparison_candidate':{'layer1':'pending','layer2':'pending'}})
            a['citations'].extend({**c['evidence_references'][e],'item_id':fid,'locatable':True,'support_status':'supported','user_review_status':'pending'} for e in f['evidence_ids'])
        path=tmp_path/(c['card_id']+'.json');path.write_text(json.dumps(a),encoding='utf-8');audits.append(path)
        selection={**{k:c[k] for k in ('paper_id','document_version_id','card_id','card_version')},'source_kind':'saved_card','validation_unit_id':None,'context_id':None}
        made.append((doc,c,selection,path));return made[-1]
    def engine():return ComparisonService(ComparisonCatalog(service,audit_paths=audits,allow_test_doubles=True))
    return make,engine,made


@pytest.mark.parametrize('count',[2,3])
def test_two_or_three_papers_preserve_scope_and_layers(comparison_setup,count):
    make,engine,_=comparison_setup
    selections=[make(str(i))[2] for i in range(count)]
    r=engine().compare_papers(selections)
    assert r['status']=='ready_with_warnings' and len(r['columns'])==count and r['ranking'] is None
    assert len(r['numeric_condition_checks'])==count*(count-1)//2
    for i,col in enumerate(r['columns']):
        assert col['selection']==selections[i] and col['execution_mode']=='test_double'
        for ref in col['evidence'].values():
            assert ref['paper_id']==selections[i]['paper_id'] and ref['document_version_id']==selections[i]['document_version_id']
            assert ref['evidence_key'].startswith(selections[i]['card_id']+'::')
    rows={r['dimension']:r for r in r['layers']['research_logic']}
    assert rows['background']['cells'][0]['facts'][0]['text']!=rows['motivation']['cells'][0]['facts'][0]['text']
    assert 'claimed_contributions' in rows and 'conclusions' not in rows


@pytest.mark.parametrize('count',[0,1,4])
def test_invalid_count_does_not_create_snapshot(comparison_setup,count):
    make,engine,_=comparison_setup;e=engine()
    r=e.compare_papers([make(str(i))[2] for i in range(count)])
    assert r['error']['code']=='comparison_count' and not e.history.list()


def test_duplicate_paper_refused(comparison_setup):
    make,engine,_=comparison_setup;s=make('A')[2]
    assert engine().compare_papers([s,s])['error']['code']=='comparison_duplicate_paper'


@pytest.mark.parametrize('change,code',[('card_id','comparison_missing_card'),('card_version','comparison_version_mismatch'),('document_version_id','comparison_version_mismatch'),('paper_id','comparison_version_mismatch')])
def test_wrong_card_or_version_refused(comparison_setup,change,code):
    make,engine,_=comparison_setup;a=make('A')[2];b=make('B')[2]
    b[change]=999 if change=='card_version' else 'missing-other-source'
    r=engine().compare_papers([a,b]);assert r['error']['code']==code


@pytest.mark.parametrize('kind',['theoretical','case_study','simulation'])
def test_validation_types_do_not_require_same_metrics(comparison_setup,kind):
    make,engine,_=comparison_setup;a=make('A',kind=kind,absent_context=True)[2];b=make('B')[2]
    r=engine().compare_papers([a,b]);assert r['status']=='ready_with_warnings'
    assert r['columns'][0]['validation_units']['validation:u1']['type']==kind
    assert r['numeric_condition_checks'][0]['status']=='unknown'


def test_complete_source_checked_conditions_can_align_without_ranking(comparison_setup):
    make,engine,_=comparison_setup
    selections=[{**make(x)[2],'context_id':'synthetic-context'} for x in ['A','B']]
    r=engine().compare_papers(selections)
    p=r['numeric_condition_checks'][0]
    assert p['status']=='aligned' and p['direct_numeric_comparison_conditions_met']
    assert r['ranking'] is None and all(c['human_review']=='not_performed' for c in r['columns'])


def test_local_experiment_labels_need_not_equal_across_papers(comparison_setup):
    make,engine,_=comparison_setup;a=make('A',assignment_label='TableAlpha')[2];b=make('B',assignment_label='TableBeta')[2]
    r=engine().compare_papers([{**s,'context_id':'synthetic-context'} for s in (a,b)])
    assert r['numeric_condition_checks'][0]['status']=='aligned'


def test_uncertain_unit_anchor_prevents_confirmed_result_assignment(comparison_setup):
    make,engine,made=comparison_setup;a=make('A')[2];b=make('B')[2]
    c=made[0][1];path=made[0][3];audit=json.loads(path.read_text())
    anchor=c['structured_content']['validation_units']['validation:u1']['anchor_fact_id']
    for ref in audit['citations']:
        if ref['item_id']==anchor:ref['support_status']='partial'
    path.write_text(json.dumps(audit))
    r=engine().compare_papers([{**s,'context_id':'synthetic-context'} for s in (a,b)])
    pair=r['numeric_condition_checks'][0]
    assert pair['status']=='partially_aligned'
    assignment=next(x for x in pair['checks'] if x['dimension']=='result_assignment')
    assert assignment['status']=='unknown'


@pytest.mark.parametrize('other', ['dataset','metric'])
def test_known_different_conditions_do_not_block_qualitative_layer(comparison_setup,other):
    make,engine,_=comparison_setup
    a=make('A')[2];b=make('B',obj='fauna' if other=='dataset' else 'flora',metric='Width' if other=='metric' else 'Height')[2]
    r=engine().compare_papers([{**s,'context_id':'synthetic-context'} for s in [a,b]])
    assert r['qualitative_comparison']['available'] and r['numeric_condition_checks'][0]['status']=='not_aligned'
    assert not r['numeric_condition_checks'][0]['direct_numeric_comparison_conditions_met']


def test_unknown_conditions_not_defaulted_to_equal(comparison_setup):
    make,engine,made=comparison_setup;a=make('A')[2];b=make('B')[2]
    # Explicitly remove confirmation from an otherwise identical context.
    path=made[1][3];audit=json.loads(path.read_text());audit['citations'][0]['support_status']='partial';path.write_text(json.dumps(audit))
    r=engine().compare_papers([a,b]);assert r['numeric_condition_checks'][0]['status']=='unknown'
    assert r['columns'][1]['fields']['background']['facts'][0]['citations'][0]['source_support_status']=='partial'
    assert not r['columns'][1]['fields']['background']['facts'][0]['source_checked_candidate']


@pytest.mark.parametrize('unknown_dim',['object_version','metric_definition','baseline_context'])
def test_one_unknown_dimension_prevents_alignment_even_with_equal_values(comparison_setup,unknown_dim):
    make,engine,_=comparison_setup
    a=make('A')[2];b=make('B',unknown_dim=unknown_dim)[2]
    r=engine().compare_papers([{**s,'context_id':'synthetic-context'} for s in (a,b)])
    assert r['numeric_condition_checks'][0]['status']=='partially_aligned'
    assert not r['numeric_condition_checks'][0]['direct_numeric_comparison_conditions_met']


def test_comparison_ui_selection_change_and_history_restore(comparison_setup,monkeypatch):
    from streamlit.testing.v1 import AppTest
    import research_copilot.comparison_ui as ui
    make,engine,_=comparison_setup;a=make('A',second_unit=True);b=make('B');c=make('C')
    catalog=engine().catalog
    monkeypatch.setattr(ui,'make_catalog',lambda service,include_fixtures=False:catalog)
    app=AppTest.from_string('from research_copilot.comparison_ui import render_comparison\nrender_comparison(None)').run(timeout=20)
    assert not app.exception and app.button(key='comparison_generate').disabled
    app.multiselect(key='comparison_papers').set_value([a[2]['paper_id'],b[2]['paper_id']]).run(timeout=20)
    app.button(key='comparison_generate').click().run(timeout=20)
    assert not app.exception
    active=dict(app.session_state['comparison_active'])
    assert len(engine().load(active['id'],active['version'])['columns'])==2
    app.selectbox(key='comparison_unit_'+a[2]['card_id']).set_value('validation:u2').run(timeout=20)
    assert any('当前选择已变化' in w.value for w in app.warning)
    app.button(key='comparison_generate').click().run(timeout=20)
    assert not app.exception
    app.multiselect(key='comparison_papers').set_value([a[2]['paper_id'],b[2]['paper_id'],c[2]['paper_id']]).run(timeout=20)
    assert any('当前选择已变化' in w.value for w in app.warning)
    app.button(key='comparison_generate').click().run(timeout=20)
    assert not app.exception
    newer=dict(app.session_state['comparison_active'])
    assert len(engine().load(newer['id'],newer['version'])['columns'])==3
    restarted=AppTest.from_string('from research_copilot.comparison_ui import render_comparison\nrender_comparison(None)').run(timeout=20)
    restarted.selectbox(key='comparison_history').set_value(newer['id']+'@'+str(newer['version'])).run(timeout=20)
    assert not restarted.exception
    assert any('第一层' in h.value for h in restarted.subheader)


def test_partial_support_remains_partial_without_upgrading_user_review(comparison_setup):
    make,engine,made=comparison_setup;a=make('A')[2];b=make('B')[2]
    path=made[0][3];audit=json.loads(path.read_text());eid=audit['citations'][0]['evidence_id']
    audit['citations'][0]['support_status']='background_only';path.write_text(json.dumps(audit))
    r=engine().compare_papers([a,b]);col=r['columns'][0]
    ref=col['evidence'][a['card_id']+'::'+eid]
    assert ref['source_support_status']=='background_only' and ref['user_review_status']=='pending'


def test_invalid_quote_stops_new_comparison_and_marks_old_snapshot_stale(comparison_setup,service):
    make,engine,made=comparison_setup;a=make('A')[2];b=make('B')[2];e=engine();r=e.compare_papers([a,b])
    c=made[0][1];ref=next(iter(c['evidence_references'].values()))
    with service.db.connection() as con:con.execute("UPDATE pages SET raw_text=replace(raw_text,?,?) WHERE document_version_id=?",(ref['quote'],'changed original source',c['document_version_id']))
    assert e.compare_papers([a,b])['error']['code']=='comparison_invalid_evidence'
    assert e.load(r['comparison_id'],r['comparison_version'])['stale']


def test_new_file_version_and_unready_index_make_old_view_stale(comparison_setup,service,pdf_bytes):
    make,engine,_=comparison_setup;a=make('A');b=make('B');e=engine();r=e.compare_papers([a[2],b[2]])
    service.import_pdf(pdf_bytes(['A different SYNTHETIC file version with enough searchable words. '*8],title='SYNTHETIC new edition'),'A-v2.pdf',paper_id=a[0]['paper_id'])
    old=e.load(r['comparison_id'],1)
    assert old['stale'] and any(x['code']=='newer_document' for x in old['stale_reasons'])
    service.db.update('documents','document_version_id',a[0]['document_version_id'],index_status='stale')
    assert e.compare_papers([a[2],b[2]])['error']['code']=='comparison_source_not_ready'


def test_new_card_marks_old_snapshot_stale_without_overwriting_it(comparison_setup):
    make,engine,made=comparison_setup;a=make('A');b=make('B');e=engine();r=e.compare_papers([a[2],b[2]])
    frozen=e.history.get(r['comparison_id'],1)
    newer=make('A',existing_doc=a[0]);assert newer[1]['card_version']==2
    loaded=engine().load(r['comparison_id'],1)
    assert loaded['stale'] and any(x['code']=='newer_card' for x in loaded['stale_reasons'])
    assert e.history.get(r['comparison_id'],1)==frozen


def test_stale_snapshot_never_retains_effective_numeric_alignment(comparison_setup):
    make,engine,_=comparison_setup;a=make('A');b=make('B');e=engine()
    r=e.compare_papers([{**s[2],'context_id':'synthetic-context'} for s in (a,b)])
    assert r['numeric_condition_checks'][0]['status']=='aligned'
    make('A',existing_doc=a[0])
    loaded=engine().load(r['comparison_id'],1)
    p=loaded['numeric_condition_checks'][0]
    assert loaded['stale'] and p['snapshot_status']=='aligned' and p['status']=='unknown'
    assert not p['direct_numeric_comparison_conditions_met']


def test_restart_and_append_versions_preserve_previous_snapshot(comparison_setup):
    make,engine,_=comparison_setup;s=[make(x)[2] for x in ['A','B']]
    e=engine();r=e.compare_papers(s);old=e.history.get(r['comparison_id'],1)
    second=engine().compare_papers(s,comparison_id=r['comparison_id'])
    assert second['comparison_version']==2 and engine().history.get(r['comparison_id'],1)==old
    assert engine().load(r['comparison_id'],1)['columns'][0]['selection']==s[0]


def test_validation_selection_filters_facts_without_assigning_other_results(comparison_setup):
    make,engine,_=comparison_setup;a=make('A',second_unit=True)[2];b=make('B')[2]
    r=engine().compare_papers([{**a,'validation_unit_id':'validation:u2'},b])
    facts=r['columns'][0]['fields']['findings']['facts']
    assert len(facts)==1 and '19' in facts[0]['text'] and all(f['validation_unit_id']=='validation:u2' for f in facts)
    bad=engine().compare_papers([{**a,'validation_unit_id':'validation:u2','context_id':'synthetic-context'},b])
    assert bad['error']['code']=='comparison_context_mismatch'


def test_not_applicable_keeps_its_evidence_in_the_comparison_cell(comparison_setup):
    make,engine,_=comparison_setup;a=make('A',not_applicable_future=True)[2];b=make('B')[2]
    r=engine().compare_papers([a,b]);cell=r['columns'][0]['fields']['future_work']
    assert cell['status']=='not_applicable' and cell['fact_ids']==[]
    assert cell['facts'][0]['purpose']=='applicability' and cell['facts'][0]['citations']


def test_condition_evidence_cannot_be_borrowed_from_another_card(comparison_setup,service):
    make,engine,made=comparison_setup;a=make('A');b=make('B');c=deepcopy(a[1])
    # A new synthetic version explicitly carries a malformed foreign citation.
    c['comparison_contexts'][0]['dimensions']['research_task']['evidence_ids']=[next(iter(b[1]['evidence_references']))+'-foreign']
    c=ReadingHistory(service.db).create(c)
    selection={**a[2],'card_id':c['card_id'],'card_version':c['card_version'],'context_id':'synthetic-context'}
    r=engine().compare_papers([selection,b[2]])
    assert r['error']['code']=='comparison_context_evidence_mismatch'


def test_default_catalog_refuses_mock_as_real_source(comparison_setup,service):
    make,_,_=comparison_setup;a=make('A')[2];b=make('B')[2]
    r=ComparisonService(ComparisonCatalog(service)).compare_papers([a,b])
    assert r['error']['code']=='comparison_test_double'
