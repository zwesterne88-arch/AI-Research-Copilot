"""Export pinned genuine history into a local, auditable evaluation release.

Never invokes application generators or modifies a source database. Model
outputs remain verbatim inside observation records; assistant audits are separate.
"""
import argparse,json
from pathlib import Path
from .io import ReadOnlyDB,fingerprint,file_hash,write_json,write_jsonl,offline_only,EvaluationError

ROOT=Path(__file__).resolve().parents[1]
P05='aeefa68d33385ee11c2291d2484033aa2b1940ceea98a57f6982a581b5d3345b'
P07='59363aff640a20e4ee25bd5ca7c538850897fdc72d038e30d63afcc1c40adadf'
V5='card_17fae9c3114b4d2485339265d6ab5dcd'
V8='card_e83ba6e50d314406938eadd3af5af127'
V4='card_57339442d69941988d929a084efa72b2'
OLD_QA='qa_883bd8473a7949cba3a6f55f0b1b9adb'
NEW_QA='qa_ffdebce7ef624af08905fb21ba7bcccc'
CMP='comparison_1c646333f756408e813432fcd159b129'

def build(root=ROOT,out=None,dataset_id='research-copilot-stage5_1-development-v1'):
    root=Path(root);out=Path(out or root/'evaluation')
    names=['cases.jsonl','annotation_tasks.jsonl','dataset_manifest.json','private/observations.json','private/assistant_audits.json','private/annotation_workbook.md']
    if any((out/p).exists() for p in names):raise EvaluationError('Dataset release exists; use another --output, never overwrite annotations/history')
    db=ReadOnlyDB(root/'data/library.sqlite3');comparison_db=ReadOnlyDB(root/'data/comparisons.sqlite3')
    observations={};source_links=[]
    for table,key,col in [('qa_runs','run_id','result_json'),('reading_cards','card_id','record_json')]:
        for row in db.rows(f'SELECT {key},{col} FROM {table}'):
            record=json.loads(row[col]);oid=row[key]
            observations[oid]={'kind':'qa' if table=='qa_runs' else 'reading','record':record,
                'origin_kind':'persisted_real_api_record_with_raw_outputs' if record.get('execution_mode')=='real_api' else 'non_real_record',
                'snapshot_fingerprint':fingerprint(record)}
            source_links.append({'kind':'sqlite_row','path':'data/library.sqlite3','table':table,'key_column':key,'key':oid,'value_column':col,'sha256':fingerprint(record)})
    row=comparison_db.one('SELECT record_json FROM comparison_snapshots WHERE comparison_id=? AND comparison_version=1',(CMP,))
    record=json.loads(row['record_json'])
    observations[CMP]={'kind':'comparison','record':record,'origin_kind':'deterministic_comparison_NOT_model_generated','snapshot_fingerprint':fingerprint(record)}
    source_links.append({'kind':'comparison_row','path':'data/comparisons.sqlite3','key':CMP,'version':1,'sha256':fingerprint(record)})
    for cid,ver in [(V5,5),(V8,8)]:
        c=observations[cid]['record']
        assert c['card_status']=='draft' and c['execution_mode']=='real_api' and c['card_version']==ver
    audits={}
    for label,rel in [('P07_source_audit','verification/stage4_3/source_audit.json'),('P05_source_review','verification/stage4_5_2/private/source_review.json'),
                      ('old_retrieval_replay','verification/stage4_2_quality/retrieval_replay.json'),('qa_citation_review','verification/stage4_2_quality/citation_review.json'),
                      ('original_failure_replay','verification/stage4_5_fix/replay_summary.json')]:
        audits[label]=json.loads((root/rel).read_text(encoding='utf-8'));source_links.append({'kind':'file','path':rel,'sha256':file_hash(root/rel)})
    # Preserve the canonical unmodified API response separately from the legacy
    # application candidate whose unanswered list was changed by an old alias bug.
    raw=json.loads((root/'verification/stage4_5/private/real_api/response_06.json').read_text(encoding='utf-8'))
    observations[OLD_QA]['canonical_model_response']=json.loads(json.loads(raw['body_utf8'])['choices'][0]['message']['content'])
    observations[OLD_QA]['known_legacy_candidate_alias_issue']=True
    source_links.append({'kind':'file','path':'verification/stage4_5/private/real_api/response_06.json','sha256':file_hash(root/'verification/stage4_5/private/real_api/response_06.json')})
    old_summary=json.loads((root/'verification/stage4_2/real_model_validation.json').read_text(encoding='utf-8'))
    source_links.append({'kind':'file','path':'verification/stage4_2/real_model_validation.json','sha256':file_hash(root/'verification/stage4_2/real_model_validation.json')})
    cases=[]
    def add(cid,oid,scenario,defects,checks,field=None,dimensions=None):
        o=observations[oid];r=o['record']
        if o['kind']=='comparison':scopes=[{k:c['selection'][k] for k in ('paper_id','document_version_id','card_id','card_version')}|{'file_sha256':c['file_sha256']} for c in r['columns']]
        else:scopes=[{k:r[k] for k in ('paper_id','document_version_id','file_sha256')}]
        cases.append({'case_id':cid,'test_type':o['kind'],'source_scope':scopes,'observation_id':oid,'observation_fingerprint':fingerprint(r),
            'input_question_or_field':r.get('question') if field is None else field,'scenario':scenario,
            'actual_model_output':{'artifact':'private/observations.json','observation_id':oid,'path':'record','meaning':'genuine saved application result; raw_outputs/candidate_output retained, not a new model response'} if o['kind']!='comparison' else None,
            'actual_application_output':{'artifact':'private/observations.json','observation_id':oid},
            'original_evidence':{'artifact':'private/observations.json','observation_id':oid,'path':'record.sources' if o['kind']=='qa' else 'record.evidence_references' if o['kind']=='reading' else 'record.columns[].evidence'},
            'expected_checkpoints':checks,'evaluation_dimensions':dimensions or ['source_location','semantic_support','completeness'],
            'annotation_status':'pending_human_review','defect_ids':defects,'reproduce':'python -m evaluation.run --case '+cid+' --output verification/stage5_1/review_'+cid,
            'source_kind':o['origin_kind'],'split':'development_regression_NOT_holdout','field_selector':field})
    for c in old_summary['cases']:
        cid='QA42-'+c['case_id'];scenario=c['category'];defects=[]
        if c['case_id']=='G07':defects=['D03'];scenario='key_evidence_recall_gap'
        if c['case_id']=='G08':scenario='scoped_refusal_requires_human_confirmation'
        add(cid,c['run_id'],scenario,defects,['Only archived query translations may be reused offline','Locate published citations; independently label completeness and support','Do not infer full-paper absence from retrieval'],dimensions=['retrieval','qa_completeness','fact_support','refusal_reasonableness'])
    add('QA42-QUOTE-REJECT','qa_0a020a50161e4b18b1c99df50158d92f','unlocatable_model_quote',['D01'],['Historical invalid quote remains unpublished; transport success is not QA success'])
    g05=next(c['run_id'] for c in old_summary['cases'] if c['case_id']=='G05')
    add('QA42-COMPOSITE',g05,'locatable_citation_incomplete_support',['D02'],['Inspect C2 each metric/configuration fact independently; source ID existence is not support'])
    add('QA45-E4-OLD',OLD_QA,'evidence_present_but_false_unknown',['D04','D05'],['E4 was sent; canonical response has3 notes vs legacy stored4','No automatic conversion of free-text unknown into supported fact'])
    add('QA452-E4-NEW',NEW_QA,'evidence_used_with_citation',['D04','D05'],['E4 cited for author limitation; remain partial; no scope audit ran','Compare same paper/question but changed Prompt, one observed pair only'])
    p07=[('MOTIVATION','motivation','prior_gap_motivation_boundary','D06'),('QUESTION','research_questions','missing_research_question','D07'),
         ('METHOD','method','method_mechanism_coverage','D08'),('BASELINE','findings:f2','baseline_and_metric_attribution','D09'),
         ('VALIDATION','validation:f7','mixed_validation_conditions','D10'),('LIMITATIONS','author_limitations','author_limitations_coverage','D11')]
    for name,field,scenario,defect in p07:add('READ07-'+name,V8,scenario,[defect],['Keep original v8 and22 proposed corrections unchanged','Assistant audit remains separate from human labels'],field=field)
    for name,field,scenario,defect in [('MOTIVATION','motivation','compound_motivation_withheld','D06'),('QUESTION','research_questions','missing_research_question','D07'),
        ('METHOD','approach:f5','partially_supported_mechanism','D12'),('MIX','validation:extract','mixed_dataset_metric_candidate','D10'),
        ('RELATION','approach:relation:r2','logical_link_overreach','D13'),('LIMITATIONS','author_limitations / future_work','explicit_author_limits_and_future_work','D04')]:
        add('READ05-'+name,V5,scenario,[defect],['Inspect retained versus raw/withheld independently','No metric or experimental-field requirement for other study types'],field=field)
    add('READ05-C4',V4,'support_level_boolean_protocol_conflict',['D14'],['Preserve original true/partial conflict; current claim isolation must withhold C4'],field='metadata:review')
    add('READ05-C1',V4,'one_conflict_discards_other_valid_claims',['D15'],['Original failed batch versus retained C1-C3 in v5; never rewrite old candidate'],field='metadata:extract')
    add('COMPARE-REAL',CMP,'two_real_cards_unknown_numeric_conditions',['D10'],['P05v5/P07v8 exact binding; no fixture; no ranking; unknown is not alignment'],dimensions=['source_isolation','comparison_juxtaposition','comparability','staleness'])
    add('COMPARE-SEMANTICS',CMP,'automatic_semantic_comparison_unimplemented',[],['Do not score evidence juxtaposition as automatic semantic analysis'],dimensions=['capability_boundary'])
    tasks=[]
    def task(cid,kind,unit):
        c=next(c for c in cases if c['case_id']==cid)
        t={'task_id':'H'+str(len(tasks)+1).zfill(3),'case_id':cid,'observation_id':c['observation_id'],'annotation_type':kind,'unit':unit,
           'status':'pending_human_review','suggested_label':None,'reviewer':None,'label':None,'rationale':None,'original_evidence':c['original_evidence']}
        t['unit_fingerprint']=fingerprint({'case_id':cid,'observation':c['observation_fingerprint'],'unit':unit,'annotation_type':kind})
        tasks.append(t)
    for row in [('QA42-G01','fact_support','C1'),('QA42-G05','qa_completeness','whole_answer'),('QA42-G07','retrieval','target_evidence'),
        ('QA42-G08','refusal_reasonableness','whole_answer'),('QA42-COMPOSITE','fact_support','C2'),('QA45-E4-OLD','refusal_reasonableness','canonical_unanswered'),
        ('QA452-E4-NEW','fact_support','C8'),('QA452-E4-NEW','qa_completeness','whole_answer'),('READ07-MOTIVATION','field_completeness','motivation'),
        ('READ07-QUESTION','field_completeness','research_questions'),('READ07-METHOD','field_completeness','method'),('READ07-BASELINE','attribution','findings:f2'),
        ('READ07-VALIDATION','attribution','validation:f7'),('READ07-LIMITATIONS','field_completeness','author_limitations'),('READ05-MOTIVATION','field_completeness','motivation'),
        ('READ05-QUESTION','field_completeness','research_questions'),('READ05-METHOD','fact_support','approach:f5'),('READ05-MIX','attribution','raw_validation_candidate'),
        ('READ05-RELATION','fact_support','approach:relation:r2'),('READ05-LIMITATIONS','fact_support','findings:f6'),('COMPARE-REAL','comparison_juxtaposition','method_columns'),
        ('COMPARE-REAL','attribution','validation_result_columns')]:task(*row)
    out.mkdir(parents=True,exist_ok=True);(out/'private').mkdir(exist_ok=True)
    write_json(out/'private/observations.json',observations);write_json(out/'private/assistant_audits.json',audits)
    write_jsonl(out/'cases.jsonl',cases);write_jsonl(out/'annotation_tasks.jsonl',tasks)
    workbook=['# Human review tasks — all pending','No labels here are independent gold. Read the PDF and assigned evidence before completing any task.','']
    for t in tasks:
        c=next(c for c in cases if c['case_id']==t['case_id']);r=observations[t['observation_id']]['record']
        workbook += [f"## {t['task_id']} · {t['case_id']} · {t['annotation_type']}",f"Unit: {t['unit']} · fingerprint: {t['unit_fingerprint']}",f"Question/field: {c['input_question_or_field']}",
            'Full saved output and sources: private/observations.json / '+t['observation_id'],
            'Independent label: ______  Reason: ______  Evidence/page/offset: ______','']
        if r.get('question'):workbook += ['Published answer:',*[cl['claim_id']+': '+cl['text'] for cl in r['claims']],'Unknown scope notes:',*r.get('unanswered',[]),'']
    with (out/'private/annotation_workbook.md').open('x',encoding='utf-8') as f:f.write('\n'.join(workbook))
    manifest={'schema_version':'research-evaluation-release-v1','dataset_id':dataset_id,'new_model_calls':0,
        'cases':len(cases),'annotation_tasks':len(tasks),'independent_human_labels':0,'observations':len(observations),'source_links':source_links,
        'artifacts':{p:file_hash(out/p) for p in names if p!='dataset_manifest.json'},'development_materials_not_holdout':True,'cross_discipline_effectiveness_validated':False}
    manifest['release_fingerprint']=fingerprint(manifest)
    write_json(out/'dataset_manifest.json',manifest)
    return {'cases':len(cases),'tasks':len(tasks),'observations':len(observations),'release_fingerprint':manifest['release_fingerprint']}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--output',type=Path,default=ROOT/'evaluation');ap.add_argument('--dataset-id',default='research-copilot-stage5_1-development-v1');args=ap.parse_args()
    with offline_only():print(json.dumps(build(out=args.output,dataset_id=args.dataset_id),ensure_ascii=False))

if __name__=='__main__':main()
