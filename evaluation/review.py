"""Export a task as local Markdown; hides assistant judgments to avoid gold leakage."""
import argparse,json
from pathlib import Path
from .run import load_release,ROOT
from .io import ReadOnlyService,EvaluationError,offline_only

def packet(dataset,root,task_id):
    _,cases,tasks,observations=load_release(dataset,root)
    t=next((t for t in tasks if t['task_id']==task_id),None)
    if not t:raise EvaluationError('Unknown task')
    c=next(c for c in cases if c['case_id']==t['case_id']);o=observations[t['observation_id']];r=o['record']
    lines=[f"# {task_id} · {c['case_id']}",f"Annotation: {t['annotation_type']} · unit: {t['unit']}",
        'Status: pending_human_review. No suggested gold label. Assistant judgments intentionally hidden.',
        f"Unit fingerprint: {t['unit_fingerprint']}",f"Question/field: {c['input_question_or_field']}",'']
    def cite(ref,record):
        lines.extend([f"PDF: {record['source_file']} · {record['file_sha256']} · physical page {ref['pdf_page']}",
            f"Offsets: [{ref.get('quote_char_start',ref['char_start'])}, {ref.get('quote_char_end',ref['char_end'])})",'','> '+ref['quote'].replace('\n','\n> '),''])
    if o['kind']=='qa':
        claims=r['claims'] if t['unit'] not in {x['claim_id'] for x in r['claims']} else [x for x in r['claims'] if x['claim_id']==t['unit']]
        for claim in claims:
            lines.extend([claim['claim_id']+': '+claim['text'],''])
            for ref in claim['citations']:cite(ref,r)
        notes=o.get('canonical_model_response',r.get('candidate_output') or {}).get('unanswered',r.get('unanswered',[]))
        lines.extend(['Original model unknown-scope notes (not endorsed):',*['- '+str(v) for v in notes],''])
        if t['annotation_type']=='retrieval':lines.extend(['Retrieval target labeling: read the full PDF before inspecting ranking. Mark exact physical page, start/end and verbatim quote. No target is pre-certified.',''])
    elif o['kind']=='reading':
        content=r['structured_content'];unit=t['unit']
        if unit in content['facts']:ids=[unit]
        elif unit in content['fields']:ids=content['fields'][unit]['fact_ids']
        elif ' / ' in str(c['field_selector']):ids=[i for field in ('author_limitations','future_work') for i in content['fields'][field]['fact_ids']]
        else:ids=[]
        for fid in ids:
            f=content['facts'][fid];lines.extend([fid+': '+f['text'],f"Saved validation unit: {f.get('validation_unit_id')}",''])
            for eid in f['evidence_ids']:cite(r['evidence_references'][eid],r)
        for edge in content['relations']:
            if edge['relation_id']==unit:
                lines.extend([edge['text'],''])
                for eid in edge['evidence_ids']:cite(r['evidence_references'][eid],r)
        if not ids and unit in content['fields']:lines.extend(['Saved field state (not proof of paper absence):',json.dumps(content['fields'][unit],ensure_ascii=False,indent=2),''])
        if unit=='raw_validation_candidate':
            raw=r['raw_outputs']['validation:extract'];lines.extend(['Raw model extraction — not published truth:','```json',json.dumps(raw,ensure_ascii=False,indent=2),'```'])
            wanted={e['source_id'] for f in raw['facts'] for e in f.get('evidence',[])}
            for source in r['modules']['validation']['sources']:
                if source['source_id'] in wanted:cite({**source,'quote':source['chunk_text']},r)
    else:
        for col in r['columns']:
            lines.extend([f"## {col['source_file']} · card v{col['selection']['card_version']}"])
            fields=('method',) if t['unit']=='method_columns' else ('validation','findings')
            for field in fields:
                for f in col['fields'][field]['facts']:
                    lines.extend([f['text'],f"Validation unit: {f.get('validation_unit_id')}"])
                    for ref in f['citations']:cite(ref,col)
    lines.extend(['## Your independent review','Label: ______','Rationale and scope checked: ______','Exact supporting/conflicting source: ______',
        'Reviewer ID/date: ______','If unable to judge, use the uncertainty label rather than guessing.'])
    return '\n'.join(lines)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',type=Path,default=ROOT/'evaluation');p.add_argument('--task',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with offline_only():text=packet(a.dataset,ROOT,a.task)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as f:f.write(text)
    print(str(a.output))

if __name__=='__main__':main()
