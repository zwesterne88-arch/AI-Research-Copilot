"""Human labels are explicit, provenance bound, and never inferred from audits."""
from .io import EvaluationError,fingerprint,read_jsonl

LABELS={
    'fact_support':{'fully_supported','partially_supported','unrelated','contradicted','insufficient_evidence','unable_to_judge'},
    'field_completeness':{'complete','partial','missing','not_applicable','uncertain'},
    'qa_completeness':{'complete','partial','missing','not_applicable','uncertain'},
    'refusal_reasonableness':{'appropriate','inappropriate','not_applicable','uncertain'},
    'attribution':{'correct','incorrect','not_applicable','uncertain'},
    'comparison_juxtaposition':{'correct','incorrect','not_applicable','uncertain'},
    'numeric_accuracy':{'correct','incorrect','not_applicable','uncertain'},
    'retrieval':{'relevant_targets_confirmed','no_answer_in_reviewed_scope','unable_to_judge'}
}

def load_annotations(paths,tasks):
    by_id={t['task_id']:t for t in tasks}; accepted=[]; excluded=[];seen=set()
    for path in paths:
        for a in read_jsonl(path):
            tid=a.get('task_id')
            if tid not in by_id or tid in seen:raise EvaluationError('Unknown or duplicate annotation task: '+str(tid))
            seen.add(tid);task=by_id[tid]
            if a.get('status')=='pending_human_review':excluded.append({'task_id':tid,'reason':'pending_human_review'});continue
            reviewer=a.get('reviewer') or {}
            if reviewer.get('type')!='human' or reviewer.get('independent_of_generation') is not True:
                excluded.append({'task_id':tid,'reason':'not_independent_human'});continue
            if a.get('status')!='completed' or not reviewer.get('reviewer_id') or not a.get('reviewed_at'):
                raise EvaluationError('Completed human label requires identity and review date')
            if a.get('unit_fingerprint')!=task['unit_fingerprint']:
                raise EvaluationError('Annotation is stale or belongs to another output')
            kind=task['annotation_type']
            if a.get('label') not in LABELS[kind]:raise EvaluationError('Invalid annotation label')
            if not isinstance(a.get('rationale'),str) or not a['rationale'].strip():raise EvaluationError('Human rationale is required')
            if kind=='retrieval' and a['label']=='relevant_targets_confirmed':
                targets=a.get('targets')
                if not isinstance(targets,list) or not targets:raise EvaluationError('Recall requires nonempty human target spans')
                keys=[]
                for t in targets:
                    if not all(isinstance(t.get(k),int) and not isinstance(t.get(k),bool) for k in ('pdf_page','char_start','char_end')) or not (t['pdf_page']>0 and 0<=t['char_start']<t['char_end']) or not t.get('quote'):
                        raise EvaluationError('Retrieval targets require physical page, offsets and quote')
                    keys.append((t['pdf_page'],t['char_start'],t['char_end']))
                if len(keys)!=len(set(keys)):raise EvaluationError('Duplicate target evidence inflates recall')
            accepted.append({**a,'annotation_type':kind,'case_id':task['case_id'],'observation_id':task['observation_id']})
    return accepted,excluded

def ratio(numerator,denominator,**extra):
    return {'value':numerator/denominator if denominator else None,'display':f'{numerator}/{denominator}' if denominator else 'N/A',
            'numerator':numerator,'denominator':denominator,**extra}

def human_metrics(annotations):
    definitions={
        'claim_full_support':('fact_support',{'fully_supported'},{'fully_supported','partially_supported','unrelated','contradicted','insufficient_evidence'}),
        'field_complete':('field_completeness',{'complete'},{'complete','partial','missing'}),
        'answer_complete':('qa_completeness',{'complete'},{'complete','partial','missing'}),
        'refusal_appropriate':('refusal_reasonableness',{'appropriate'},{'appropriate','inappropriate'}),
        'attribution_correct':('attribution',{'correct'},{'correct','incorrect'}),
        'comparison_juxtaposition_correct':('comparison_juxtaposition',{'correct'},{'correct','incorrect'}),
        'numeric_error_rate':('numeric_accuracy',{'incorrect'},{'correct','incorrect'})}
    result={}
    for name,(kind,positive,eligible) in definitions.items():
        rows=[a for a in annotations if a['annotation_type']==kind]; scores=[a for a in rows if a['label'] in eligible]
        result[name]=ratio(sum(a['label'] in positive for a in scores),len(scores),eligible_labels=sorted(eligible),excluded_count=len(rows)-len(scores),
            label_distribution={v:sum(a['label']==v for a in rows) for v in sorted(LABELS[kind])})
    result['automatic_cross_paper_semantics']={'value':None,'display':'N/A','denominator':0,'reason':'Not an implemented or accepted product capability'}
    return result

def retrieval_scores(targets,ranked,k):
    """Exact span coverage at CONTEXT passage rank, not lexical BM25 rank.

    A union across overlapping passages is allowed, gaps are not. MRR is the
    first passage prefix at which any complete target span has been covered.
    """
    def covered(t,items):
        end=t['char_start']
        for s in sorted((x for x in items if x['pdf_page']==t['pdf_page']),key=lambda x:x['char_start']):
            if s['char_start']>end:break
            end=max(end,s['char_end'])
            if end>=t['char_end']:return True
        return False
    if not targets:return {'recall':None,'reciprocal_rank':None,'target_count':0}
    hits=sum(covered(t,ranked[:k]) for t in targets)
    first=next((i for i in range(1,len(ranked)+1) if any(covered(t,ranked[:i]) for t in targets)),None)
    return {'recall':hits/len(targets),'reciprocal_rank':1/first if first and first<=k else 0.0,'target_count':len(targets),'hit_count':hits,'first_complete_target_rank':first}
