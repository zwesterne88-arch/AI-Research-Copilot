"""Reproducible local evaluation; no LLM/judge, no environment-key access.

python -m evaluation.run --output verification/stage5_1/baseline
"""
import argparse,json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from .io import (EvaluationError,ReadOnlyDB,ReadOnlyService,confined,fingerprint,file_hash,
                 offline_only,read_json,read_jsonl,write_json)
from .annotations import load_annotations,human_metrics,ratio,retrieval_scores

ROOT=Path(__file__).resolve().parents[1]

def load_release(dataset,project_root):
    dataset=Path(dataset);root=Path(project_root)
    manifest=read_json(dataset/'dataset_manifest.json')
    if manifest.get('schema_version')!='research-evaluation-release-v1':raise EvaluationError('Unknown dataset schema')
    core={k:v for k,v in manifest.items() if k!='release_fingerprint'}
    if fingerprint(core)!=manifest['release_fingerprint']:raise EvaluationError('Dataset manifest changed')
    for rel,sha in manifest['artifacts'].items():
        if file_hash(confined(dataset,rel))!=sha:raise EvaluationError('Dataset artifact changed: '+rel)
    observations=read_json(dataset/'private/observations.json');cases=read_jsonl(dataset/'cases.jsonl');tasks=read_jsonl(dataset/'annotation_tasks.jsonl')
    if len({c['case_id'] for c in cases})!=len(cases) or len({t['task_id'] for t in tasks})!=len(tasks):raise EvaluationError('Duplicate case/task IDs')
    case_map={c['case_id']:c for c in cases}
    for oid,o in observations.items():
        if fingerprint(o['record'])!=o['snapshot_fingerprint']:raise EvaluationError('Observation fingerprint mismatch: '+oid)
    for c in cases:
        if c['observation_id'] not in observations or c['observation_fingerprint']!=observations[c['observation_id']]['snapshot_fingerprint']:
            raise EvaluationError('Case bound to wrong output')
        o=observations[c['observation_id']];r=o['record']
        if o['kind']!='comparison':
            if r.get('execution_mode')!='real_api':raise EvaluationError('A real-history case cannot silently use fixture/mock')
            if any(c['source_scope'][0].get(k)!=r[k] for k in ('paper_id','document_version_id','file_sha256')):raise EvaluationError('Case scope mismatch')
        elif any(col['source_kind']!='saved_card' or col['execution_mode']!='real_api' for col in r['columns']):raise EvaluationError('Comparison includes non-real material')
    for t in tasks:
        c=case_map.get(t['case_id'])
        if not c or t['observation_id']!=c['observation_id']:raise EvaluationError('Task belongs to another observation')
        expected=fingerprint({'case_id':c['case_id'],'observation':c['observation_fingerprint'],'unit':t['unit'],'annotation_type':t['annotation_type']})
        if expected!=t['unit_fingerprint']:raise EvaluationError('Task fingerprint mismatch')
    for link in manifest['source_links']:
        path=confined(root,link['path'])
        if link['kind']=='file':actual=file_hash(path)
        elif link['kind']=='sqlite_row':
            allowed={('qa_runs','run_id','result_json'),('reading_cards','card_id','record_json')}
            t,k,col=link['table'],link['key_column'],link['value_column']
            if (t,k,col) not in allowed:raise EvaluationError('Unapproved source SQL identifier')
            row=ReadOnlyDB(path).one(f'SELECT {col} FROM {t} WHERE {k}=?',(link['key'],))
            actual=fingerprint(json.loads(row[col])) if row else None
        elif link['kind']=='comparison_row':
            row=ReadOnlyDB(path).one('SELECT record_json FROM comparison_snapshots WHERE comparison_id=? AND comparison_version=?',(link['key'],link['version']))
            actual=fingerprint(json.loads(row['record_json'])) if row else None
        else:raise EvaluationError('Unknown source link')
        if actual!=link['sha256']:raise EvaluationError('Historical source changed: '+link['path'])
    return manifest,cases,tasks,observations

def check_reference(service,record,ref):
    """Validate location only, never infer scientific support from string equality."""
    from research_copilot.evidence import locate_quote
    try:
        keys=('paper_id','document_version_id','file_sha256')
        if any(ref.get(k)!=record[k] for k in keys):return False,'wrong_paper_or_version'
        service.store.check(record['file_sha256'])
        doc=service.get_document(record['document_version_id'])
        if any(doc[k]!=record[k] for k in keys):return False,'document_scope_mismatch'
        chunk=service.db.one('SELECT * FROM chunks WHERE chunk_id=?',(ref.get('chunk_id'),))
        if not chunk or any(chunk[k]!=record[k] for k in keys):return False,'missing_or_foreign_chunk'
        page=ref.get('pdf_page');a=ref.get('quote_char_start');b=ref.get('quote_char_end')
        legacy=False
        if 'quote_char_start' not in ref and 'quote_char_end' not in ref and record.get('prompt_version') in ('research-logic-evidence-qa-v1','research-logic-evidence-qa-v2-sourceids'):
            # Those historical contracts stored EXACT quote offsets in char_*.
            # Never guess offsets for newer contracts, or partly missing pairs.
            a,b=ref.get('char_start'),ref.get('char_end');legacy=True
        if any(not isinstance(x,int) or isinstance(x,bool) for x in (page,a,b)):return False,'invalid_page_or_offset_type'
        if page!=chunk['pdf_page'] or not chunk['char_start']<=a<b<=chunk['char_end']:return False,'wrong_page_or_offset'
        if any(ref.get(k)!=doc[k] or ref.get(k)!=chunk[k] for k in ('parser_version','chunker_version','index_version')):return False,'pipeline_mismatch'
        raw=service.db.one('SELECT raw_text FROM pages WHERE document_version_id=? AND pdf_page=?',(record['document_version_id'],page))
        if not raw or not isinstance(ref.get('quote'),str) or not ref['quote']:return False,'missing_quote'
        text=raw['raw_text'][a:b]
        span=locate_quote(text,ref['quote'])
        if span!=(0,len(text)):return False,'quote_not_at_declared_offset'
        return True,'legacy_exact_quote_offsets_valid' if legacy else 'locator_valid_semantics_unjudged'
    except (KeyError,TypeError,ValueError,OSError):return False,'malformed_reference'

def reference_rows(service,oid,o):
    r=o['record'];refs=[]
    if o['kind']=='qa':
        for claim in r.get('claims',[]):
            used=set()
            for i,ref in enumerate(claim['citations']):
                valid,reason=check_reference(service,r,ref)
                if ref.get('source_id') in used:valid,reason=False,'duplicate_citation_in_claim'
                used.add(ref.get('source_id'));refs.append({'observation_id':oid,'unit_id':claim['claim_id'],'reference_id':str(i)+':'+str(ref.get('source_id')),'valid':valid,'reason':reason})
    elif o['kind']=='reading' and r['card_status']=='draft':
        for eid,ref in r['evidence_references'].items():
            valid,reason=check_reference(service,r,ref);refs.append({'observation_id':oid,'unit_id':eid,'reference_id':eid,'valid':valid,'reason':reason})
    return refs

def evaluate(dataset,project_root,annotation_paths=(),case_ids=None):
    from research_copilot.qa_retrieval import retrieve_context,RETRIEVAL_VERSION
    from research_copilot.evidence import validate_source_locations
    manifest,cases,tasks,observations=load_release(dataset,project_root)
    if case_ids:
        missing=set(case_ids)-{c['case_id'] for c in cases}
        if missing:raise EvaluationError('Unknown requested case IDs: '+str(sorted(missing)))
        cases=[c for c in cases if c['case_id'] in case_ids]
    active={c['observation_id'] for c in cases};service=ReadOnlyService(Path(project_root)/'data')
    annotations,excluded=load_annotations(annotation_paths,tasks)
    annotations=[a for a in annotations if a['observation_id'] in active and a['case_id'] in {c['case_id'] for c in cases}]
    locators=[];replays={};replay_summary=[];case_results=[]
    for oid in sorted(active):
        o=observations[oid];r=o['record'];locators.extend(reference_rows(service,oid,o))
        if o['kind']=='qa' and r.get('retrieval_queries'):
            doc=service.get_document(r['document_version_id']);diag={}
            sources=retrieve_context(service,doc,r['retrieval_queries'],question=r['question'],diagnostics=diag)
            validate_source_locations(service,doc,sources)
            replays[oid]={'historical_context':r['sources'],'current_offline_context':sources,'diagnostics':diag}
            replay_summary.append({'observation_id':oid,'queries_are_archived_model_output':True,'new_translation_calls':0,'historical_retrieval_version':r['retrieval_version'],
                'current_retrieval_version':RETRIEVAL_VERSION,'historical_passages':len(r['sources']),'current_passages':len(sources),
                'historical_pages':sorted({s['pdf_page'] for s in r['sources']}),'current_pages':sorted({s['pdf_page'] for s in sources}),
                'context_equal_to_history':r['sources']==sources,'current_context_chars':diag['context_characters'],'current_locators_valid':True,
                'query_ranking_details_file':'private/replays.json','semantic_recall':'N/A without independent targets'})
    for c in cases:
        r=observations[c['observation_id']]['record']
        checks=[v for v in locators if v['observation_id']==c['observation_id']]
        case_results.append({'case_id':c['case_id'],'scenario':c['scenario'],'observation_id':c['observation_id'],
            'observed_application_status':r.get('status',r.get('card_status')),'reference_slots_checked':len(checks),
            'invalid_published_reference_slots':sum(not v['valid'] for v in checks),
            'semantic_status':'not_implemented' if c['case_id']=='COMPARE-SEMANTICS' else 'pending_human_review',
            'defect_ids':c['defect_ids'],'expected_checkpoints':c['expected_checkpoints']})
    retrieval={}
    for view in ('historical_context','current_offline_context'):
        for k in (5,10,20,40):
            values=[]
            for a in annotations:
                if a['annotation_type']!='retrieval' or a['label']!='relevant_targets_confirmed':continue
                oid=a['observation_id'];r=observations[oid]['record'];pages={p['pdf_page']:p['raw_text'] for p in service.get_pages(r['document_version_id'])}
                for target in a['targets']:
                    raw=pages.get(target['pdf_page'],'')
                    if raw[target['char_start']:target['char_end']]!=target['quote']:raise EvaluationError('Human retrieval target not in the declared file/page/offset')
                if oid not in replays:raise EvaluationError('Annotated retrieval case has no ranked context')
                values.append(retrieval_scores(a['targets'],replays[oid][view],k))
            retrieval[view+f'_recall@{k}']={'value':sum(v['recall'] for v in values)/len(values) if values else None,'display':'N/A' if not values else 'macro mean',
                'denominator':len(values),'unit':'independently labeled question','target_span_count':sum(v['target_count'] for v in values),'excluded_unlabeled':'not scored'}
            retrieval[view+f'_MRR@{k}']={'value':sum(v['reciprocal_rank'] for v in values)/len(values) if values else None,'display':'N/A' if not values else 'macro mean',
                'denominator':len(values),'rank_unit':'ordered context passage prefix; not lexical BM25 rank'}
    audits=read_json(Path(dataset)/'private/assistant_audits.json')
    p07=audits['P07_source_audit'];p05=audits['P05_source_review']
    auxiliary={'not_independent_human_labels':True,'P07_items':dict(Counter(i['support_status'] for i in p07['items'])),
        'P07_citations':dict(Counter(i['support_status'] for i in p07['citations'])),
        'P05_items':dict(Counter(i['assistant_support_status'] for i in p05['items'])),
        'no_combined_accuracy':'Different units and assistant judgments; never added to human denominators',
        'historical_G07_assistant_retrieval_diagnostic':next(c for c in audits['old_retrieval_replay']['cases'] if c['case_id']=='G07')}
    inventory={'qa':Counter(),'reading':Counter()}
    for o in observations.values():
        if o['kind']=='qa':inventory['qa'][o['record']['status']]+=1
        if o['kind']=='reading':inventory['reading'][o['record']['card_status']]+=1
    field_presence=[]
    for oid in sorted(active):
        o=observations[oid];r=o['record']
        if o['kind']=='reading' and r['card_status']=='draft':
            field_presence.append({'card_id':oid,'version':r['card_version'],'field_status_counts':dict(Counter(f['status'] for f in r['structured_content']['fields'].values())),
                'fields_with_saved_facts':sum(bool(f['fact_ids']) for f in r['structured_content']['fields'].values()),'total_fields':len(r['structured_content']['fields']),
                'facts':len(r['structured_content']['facts']),'relations':len(r['structured_content']['relations']),'citations':len(r['evidence_references']),
                'semantic_completeness':'N/A until field annotations completed'})
    paired={}
    old_id='qa_883bd8473a7949cba3a6f55f0b1b9adb';new_id='qa_ffdebce7ef624af08905fb21ba7bcccc'
    if {old_id,new_id}.issubset(active):
        a,b=(observations[x]['record'] for x in (old_id,new_id))
        paired={'same_question':a['question']==b['question'],'same_file':a['file_sha256']==b['file_sha256'],'same_sources':a['sources']==b['sources'],
            'prompts':[a['prompt_version'],b['prompt_version']],
            'E4_in_context_before_after':[any(x['source_id']=='E4' for x in r['sources']) for r in (a,b)],
            'E4_cited_before_after':[any(x['source_id']=='E4' for c in r['claims'] for x in c['citations']) for r in (a,b)],
            'claim':'One observed historical pair, not a causal quality gain or accuracy statistic'}
    comparisons=[]
    for oid in sorted(active):
        if observations[oid]['kind']!='comparison':continue
        r=observations[oid]['record']
        comparisons.append({'comparison_id':oid,'card_versions':[c['selection']['card_version'] for c in r['columns']],
            'source_kinds':[c['source_kind'] for c in r['columns']],'execution_modes':[c['execution_mode'] for c in r['columns']],
            'numeric_condition_status':[p['status'] for p in r['numeric_condition_checks']],'ranking':r['ranking'],
            'semantic_similarity_inferred':r['qualitative_comparison']['semantic_similarity_inferred'],'human_review':'not_performed'})
    result={'dataset_id':manifest['dataset_id'],'release_fingerprint':manifest['release_fingerprint'],'new_paid_calls':0,'network_disabled':True,
        'sample':{'cases':len(cases),'unique_observations':len(active),'all_archived_observations':len(observations),'case_types':dict(Counter(c['test_type'] for c in cases)),
                  'unique_paper_files':len({s['file_sha256'] for c in cases for s in c['source_scope']}),'development_regression_NOT_holdout':True},
        'inventory_application_statuses':{k:dict(v) for k,v in inventory.items()},
        'independent_human_labels':len(annotations),'pending_tasks':sum(t['case_id'] in {c['case_id'] for c in cases} and t['task_id'] not in {a['task_id'] for a in annotations} for t in tasks),
        'excluded_annotations':excluded,'deterministic_location':ratio(sum(r['valid'] for r in locators),len(locators),
            population='Published claim-citation associations and saved draft evidence IDs; deduplicated by observation ID across cases; rejects/withheld candidates not in this denominator',
            not_semantic_accuracy=True),'location_contract_counts':dict(Counter(r['reason'] for r in locators)),
        'invalid_locations':[r for r in locators if not r['valid']],
        'human_quality':human_metrics(annotations),'retrieval_quality':retrieval,'retrieval_replays':replay_summary,
        'field_presence_not_completeness':field_presence,'assistant_only_distributions':auxiliary,'E4_observed_pair':paired,'comparisons':comparisons,'cases':case_results,
        'limitations':['No independent human annotations completed','Frozen English query plans do not verify new Chinese query rewriting',
            'Selected development regressions are not representative prevalence or held-out quality','Mock injection checks do not measure real-model attack resistance',
            'Historical latencies are individual observations, not product p95 or SLA','No objective LLM accuracy, number error rate or semantic-comparison pass rate is asserted']}
    result['report_fingerprint']=fingerprint(result)
    return result,replays

def markdown(result):
    s=result['sample'];loc=result['deterministic_location']
    lines=['# Offline evaluation baseline',f"Dataset: {result['dataset_id']} · {result['release_fingerprint']}",'',
        f"Cases: {s['cases']}; unique pinned observations: {s['unique_observations']}; source files: {s['unique_paper_files']}.",
        f"New paid calls: 0. Independent human labels: {result['independent_human_labels']}; pending tasks: {result['pending_tasks']}.",'',
        f"Deterministic locator checks: {loc['display']}. This is NOT semantic correctness.",'',
        '| Human quality metric | Value | Denominator |','|---|---|---:|']
    for k,v in result['human_quality'].items():lines.append(f"| {k} | {v['display']} | {v['denominator']} |")
    lines += ['','Recall@K/MRR@K: independently annotated targets required. Current details in results.json.','',
        '## Per scenario','| Case | Historical application status | Semantic review |','|---|---|---|']
    for c in result['cases']:lines.append(f"| {c['case_id']} | {c['observed_application_status']} | {c['semantic_status']} |")
    lines += ['','## Limits',*['- '+v for v in result['limitations']],'',
        'Assistant distributions, field presence and historical statuses are observations, not human gold or a single accuracy score.',
        f"Report fingerprint: {result['report_fingerprint']}"]
    return '\n'.join(lines)+'\n'

def main():
    from research_copilot.errors import CopilotError
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dataset',type=Path,default=ROOT/'evaluation');ap.add_argument('--project-root',type=Path,default=ROOT)
    ap.add_argument('--annotations',type=Path,action='append',default=[]);ap.add_argument('--case',action='append')
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    if args.output.exists():ap.error('Output directory exists; choose a new path to preserve prior reports')
    try:
        with offline_only():result,replays=evaluate(args.dataset,args.project_root,args.annotations,args.case)
        args.output.mkdir(parents=True,exist_ok=False)
        write_json(args.output/'results.json',result);write_json(args.output/'private/replays.json',replays)
        with (args.output/'report.md').open('x',encoding='utf-8') as f:f.write(markdown(result))
        write_json(args.output/'run_metadata.json',{'written_at':datetime.now(timezone.utc).isoformat(),'report_fingerprint':result['report_fingerprint'],'new_paid_calls':0})
        print(json.dumps({'cases':result['sample']['cases'],'locators':result['deterministic_location']['display'],'independent_labels':result['independent_human_labels'],'report_fingerprint':result['report_fingerprint']},ensure_ascii=False))
    except (EvaluationError,OSError,KeyError,CopilotError) as exc:ap.exit(2,'Offline evaluation stopped: '+str(exc)+'\n')

if __name__=='__main__':main()
