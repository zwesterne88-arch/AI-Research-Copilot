"""Supplemental research-logic retrieval. Candidates never mutate reading facts."""
import re
from .evidence import validate_source_locations
from .qa_retrieval import retrieve_context
from .errors import CopilotError

VERSION='field-candidate-multipath-v1'
FIELDS={
 'research_questions':['research objective problem statement aim address challenge','we propose our goal this paper solve'],
 'motivation':['research gap motivation need existing limitations therefore address','however we propose to overcome'],
 'prior_work_gaps':['prior work existing methods limitation challenge however'],
 'background':['research background introduction problem context'],
 'claimed_contributions':['our contributions propose novel design framework'],
 'method':['method mechanism algorithm procedure architecture workflow'],
 'validation':['evaluation experimental theoretical analysis validation setup'],
 'findings':['results findings evidence comparison'],
 'conclusions':['conclusion findings implication'],
 'author_limitations':['limitation discussion constraint future work'],
 'future_work':['future work further research limitations']}
INTENT=re.compile(r'\b(research gap|motivated by|aims? to|we (?:propose|aim|introduce|present|develop)|our (?:goal|objective)|to (?:address|overcome|solve)|this (?:paper|study|work)|however|therefore)\b',re.I)
REEXTRACTION_PROMPT='''Extract only one requested general research field from selected evidence. Background=context; gap=authors\' stated prior-work shortcoming; motivation=why the study is needed; question=the specific objective, not a method name; mechanism=how steps operate. Split independently verifiable subclaims. Do not invent a causal chain, certify novelty, or mix validations. If unsupported retain uncertain. Cite only the supplied source IDs. User-selected relevance is not human confirmation of any generated statement.'''

def find_field_evidence(service,doc,field,section=None):
    if field not in FIELDS:raise CopilotError('invalid_field','请选择通用科研字段。','retrieval')
    from .research_structure import focus_sources
    logic_fields={'research_questions','background','prior_work_gaps','motivation','claimed_contributions'}
    if section and field not in logic_fields:raise CopilotError('invalid_field_section','该字段保留原检索路径，请使用通用查找。','retrieval')
    priority=focus_sources(service,doc,field,section) if field in logic_fields else []
    sources=retrieve_context(service,doc,FIELDS[field],question='',max_sources=40)
    # Separate bounded opening/author-intent path before final candidate ranking.
    # It does not change the historical RAG retriever or its saved ID contracts.
    chunks=service.db.rows('SELECT * FROM chunks WHERE paper_id=? AND document_version_id=? AND file_sha256=? ORDER BY pdf_page,char_start',(doc['paper_id'],doc['document_version_id'],doc['file_sha256']))
    extras=[]
    for c in chunks:
        for match in INTENT.finditer(c['chunk_text']):
            if field in ('research_questions','motivation','prior_work_gaps','background') and c['pdf_page']>min(4,doc['page_count']):continue
            a=max(0,match.start()-140);b=min(len(c['chunk_text']),a+600)
            raw=c['chunk_text'][a:b];lead=len(raw)-len(raw.lstrip());raw=raw.strip();a+=lead
            extras.append({**{k:c[k] for k in ('chunk_id','paper_id','document_version_id','file_sha256','pdf_page','parser_version','chunker_version','index_version')},'char_start':c['char_start']+a,'char_end':c['char_start']+a+len(raw),'chunk_text':raw,'retrieval_reason':'author_intent_context','section_hint':None})
    # Reserve slots for both paths; merely prepending every opening match can
    # starve a later explicit research gap. Ranking is a relevance hint only.
    score=lambda s:sum(len(re.findall(p,s['chunk_text'],re.I))*w for p,w in [(r'research gap|motivated by|our objective|our goal',8),(r'we propose|to address|to overcome|aims? to',4),(r'therefore|however',1)])
    extras=sorted(extras,key=lambda s:(-score(s),s['pdf_page'],s['char_start']))
    core=[s for s in sources if s.get('retrieval_reason')!='section_context']
    closing=[s for s in sources if s.get('retrieval_reason')=='section_context']
    ordered=extras[:4]+core[:4]+extras[4:]+core[4:]+closing if field in ('research_questions','motivation','prior_work_gaps') else sources+extras
    out=[];seen=set()
    for s in (priority if section else priority+ordered):
        key=(s['pdf_page'],s['char_start'],s['char_end'])
        if key in seen or len(out)>=12:continue
        if any(s['pdf_page']==x['pdf_page'] and abs(s['char_start']-x['char_start'])<180 for x in out):continue
        seen.add(key);out.append({**s,'source_id':'E'+str(len(out)+1),'candidate_status':'unreviewed_relevance_not_fact_support'})
    validate_source_locations(service,doc,out)
    return {'field':field,'strategy':VERSION+'+section-priority','section_filter':section,'queries':FIELDS[field],'candidates':out,'new_model_calls':0,'scope':'current_paper_and_version_only','notice':'候选相关性尚待核查，不自动填入原阅读卡。'}
