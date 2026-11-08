"""One confirmed reading task: existing source locator -> Aliyun -> saved report.

Uses the existing provider, source-bound cache, job worker and workspace. Never
invokes an LLM and never starts remote work from a read/refresh operation.
"""
from copy import deepcopy
import re,uuid
from .source_reading import FIELDS,validate,passage_payload,catalog,make_unit,sentence_focus
from .aliyun_translation import usage,sha
from .repository import now
from .errors import CopilotError
from .job_control import TaskLock
from .prose import standalone_formula

STRATEGY='one-click-chinese-reading-v1'
QUESTION_CUES=r'however|although|\bbut\b|cannot|can not|not (?:able|effective)|lack|limitation|disadvantage|research gap|difficult|impractical|struggle|addresses|investigate|we (?:explore|seek|study)|objective|\baim\b|challenge|research question|\bproblem\b|不足|难以|缺乏|待解决|研究目标|\b(?:how|why|what|whether)[^?]{4,200}\?'
GAP_CUES=r'however|although|\bbut\b|cannot|can not|not (?:able|effective)|lack|limitation|disadvantage|research gap|difficult|impractical|struggle|(?:existing|prior|previous|traditional|current)[\s\S]{0,100}limited|不足|难以|缺乏'

class ChineseReading:
    def __init__(self,sources,reader):self.sources,self.reader,self.store=sources,reader,reader.store
    def prepare(self,doc,card=None,snapshot_id=None,fields=None):
        if fields is None:fields=list(FIELDS)
        if not isinstance(fields,list) or not fields or any(f not in FIELDS for f in fields):raise CopilotError('reading_fields','请选择至少一个有效精读模块。','reading')
        fields=[f for f in FIELDS if f in fields]
        book=deepcopy(self.sources.load(doc,card,snapshot_id,fresh=not snapshot_id));validate(self.reader.service,doc,book)
        # The original locator exposes related abstract context even without an
        # author problem cue. Do not turn that fallback into a claimed question.
        question=book['fields']['research_questions']
        question['units']=[u for u in question['units'] if re.search(QUESTION_CUES,u['original_text'],re.I) or u['origin']=='user_selected_original_range']
        if question['units'] and not any(re.search(r'we (?:study|investigate|address)|(?:our|the) (?:objective|aim)|research question|\bhow\b[^?]+\?',u['original_text'],re.I) for u in question['units']):question['coverage']='partial_related_scope'
        gaps=book['fields']['prior_work_gaps'];gaps['units']=[u for u in gaps['units'] if re.search(GAP_CUES,u['original_text'],re.I) or u['origin']=='user_selected_original_range']
        _,paragraphs=catalog(self.reader.service,doc)
        if not question['units']:
            explicit=[p for p in paragraphs if re.search(r'problem (?:statement|formulation)|research (?:question|objective)|问题定义|研究目标',p['section'],re.I)]
            question['units']=[make_unit(doc,p,'explicit_author_problem_section_unreviewed') for p in explicit]
        background=book['fields']['background']
        if not any(u['origin']=='user_selected_original_range' for u in background['units']):
            abstracts=[p for p in paragraphs if p['section']=='Abstract']
            focused=[x for p in abstracts[:1] for x in sentence_focus(p,GAP_CUES+'|we propose|this paper|objective',prefix=True)]
            if focused:background['units']=[make_unit(doc,p,'background_range_before_explicit_gap') for p in focused]
        # Nonstandard section names (e.g. "Solution", "Proof", "Case study")
        # can still supply verbatim mechanism/validation evidence. These remain
        # unreviewed mappings; no inferred claims or synthesized innovations.
        cue_fields={
            'method':r'our (?:algorithm|method|framework)|we (?:combine|aggregate|compute|construct)|consists of|mechanism',
            'validation':r'we (?:evaluate|prove|test|interview)|our (?:experiment|evaluation)|theorem|simulation (?:results|setup)|case study'}
        missing=[f for f in fields if f in cue_fields and not book['fields'][f]['units']]
        if missing:
            for field in missing:
                matches=[p for p in paragraphs if re.search(cue_fields[field],p['text'],re.I) and not re.search('abstract|introduction|references',p['section'],re.I)]
                book['fields'][field].update(units=[make_unit(doc,p,'body_content_cue_unreviewed') for p in matches],coverage='partial_related_scope',location='located' if matches else 'not_found_in_checked_scope')
        uses={}
        for field,value in book['fields'].items():
            for unit in value['units']:uses.setdefault(unit['unit_id'],[]).append(field)
        for value in book['fields'].values():
            for unit in value['units']:unit['shared_fields']=list(dict.fromkeys(uses[unit['unit_id']]))
        glossary=self.reader.glossary(doc['document_version_id']);client=self.reader.provider_factory(doc,False)
        units={};original=0;characters=0;saved=0;requests=0;cache_hits=0;seen_wire=set()
        for field,value in book['fields'].items():
            value['selected']=field in fields
            value['location']='located' if value['units'] else 'not_found_in_checked_scope'
            for unit in value['units']:
                unit['translation']=None  # Old snapshots remain; Mock/Argos cannot become this report.
                if field not in fields:continue
                unit['block_id']=unit['unit_id'];unit['source_only']=False
                text=unit['original_text']
                if standalone_formula(text):
                    unit['source_only']=True;unit['source_only_reason']='公式无法可靠重建，保留原文，请对照PDF。'
                elif len(re.findall(r'\d',text))>len(text)*.34:
                    unit['source_only']=True;unit['source_only_reason']='本块可能为表格，保留原文及行列位置。'
                cached=self.reader.cached(unit,client,glossary) if not unit['source_only'] else None
                if cached:unit['translation']=passage_payload(unit,{**cached,'cache_hit':True})
                if unit['unit_id'] in units:continue
                units[unit['unit_id']]=deepcopy(unit)
                if unit['source_only']:continue
                prepared=client.preview(text,glossary);original+=prepared['original_characters']
                if cached:saved+=prepared['characters'];cache_hits+=1
                else:
                    for chunk in prepared['chunks']:
                        if chunk['cached'] or chunk['cache_key'] in seen_wire:saved+=chunk['characters']
                        else:characters+=chunk['characters'];requests+=1
                        seen_wire.add(chunk['cache_key'])
        book.update(pipeline=STRATEGY,default_language='zh',extraction_method='local_section_rhetorical_and_historical_source_mapping',llm_called=False,selected_fields=fields)
        material={'version':doc['document_version_id'],'file_sha256':doc['file_sha256'],'base_card_id':book['base_card_id'],'snapshot_id':snapshot_id,
                  'fields':fields,'units':[{k:u[k] for k in ('unit_id','original_text','sources')} for u in units.values()],
                  'provider':client.public_info(),'glossary':glossary,'strategy':STRATEGY}
        plan={'plan_id':sha(material),'version':doc['document_version_id'],'paper_title':doc['title'],'source_file':doc['source_file'],'file_sha256':doc['file_sha256'],
              'base_card_id':book['base_card_id'],'base_card_version':book['base_card_version'],'snapshot_id':snapshot_id,'fields':fields,
              'total_units':len(units),'original_characters':original,'estimated_new_characters':characters,'retry_reserved_upper':characters*3,
              'first_requests_upper':requests,'requests_with_retry_upper':requests*3,'cache_hit_units':cache_hits,'cache_saved_characters':saved,
              'source_only_units':sum(u['source_only'] for u in units.values()),'usage':usage(self.store) if client.provider=='aliyun' else {'remaining_characters':10**12},
              'field_sources':{f:len(book['fields'][f]['units']) for f in fields},'llm_called':False,'provider':client.provider}
        return plan,book,list(units.values())
    def plan(self,doc,card=None,snapshot_id=None,fields=None):return self.prepare(doc,card,snapshot_id,fields)[0]
    def begin(self,doc,card,data):
        """A deliberate one-click action includes translation of this paper's evidence."""
        lease=TaskLock(self.reader.service.root,'readingstart'+sha([doc['document_version_id'],(card or {}).get('card_id')]))
        try:
            current=self.state(doc,card);job=current['job']
            same_snapshot=not data.get('snapshot_id') or data['snapshot_id']==(current['book'] or {}).get('id')
            same_fields=not data.get('fields') or data['fields']==(job or {}).get('selected_fields')
            same_provider=not job or job['authorization']['provider']==self.reader.provider_factory(doc,False).provider
            if job and same_snapshot and same_fields and same_provider:
                if job['status'] in ('running','completed'):return current
                return self.resume(doc,job['job_id'])
            prepared_result=self.prepare(doc,card,data.get('snapshot_id'),data.get('fields'))
            prepared=prepared_result[0]
            request={**data,'plan_id':prepared['plan_id'],'confirm_plan':True,'consent':True,
                     'character_budget':prepared['usage']['remaining_characters'],'authorization_mode':'one_click'}
            return self._start(doc,card,request,prepared_result)
        finally:lease.close()
    def start(self,doc,card,data):
        lease=TaskLock(self.reader.service.root,'readingstart'+sha([doc['document_version_id'],(card or {}).get('card_id')]))
        try:return self._start(doc,card,data)
        finally:lease.close()
    def _start(self,doc,card,data,prepared_result=None):
        prepared,book,units=prepared_result or self.prepare(doc,card,data.get('snapshot_id'),data.get('fields'))
        if data.get('confirm_plan') is not True or data.get('plan_id')!=prepared['plan_id']:raise CopilotError('reading_plan_confirmation','请先确认当前论文、原文范围与字符预算。','authorization')
        budget=data.get('character_budget')
        if not isinstance(budget,int) or isinstance(budget,bool) or not 0<=budget<=prepared['usage']['remaining_characters'] or budget<prepared['retry_reserved_upper']:
            raise CopilotError('reading_character_budget','本次字符预算不足或超过本地月剩余预算，请调整模块范围后重新确认。','budget')
        if prepared['estimated_new_characters'] and data.get('consent') is not True:raise CopilotError('provider_consent','请一次性确认本论文必要原文可由阿里云处理。','authorization')
        client=self.reader.provider_factory(doc,False)
        if prepared['estimated_new_characters'] and not client.config_data['configured']:raise CopilotError('aliyun_config',client.config_data['reason'],'translation')
        # Avoid two clicks starting the same ongoing report, even before cache fills.
        active=self.state(doc,card)['job']
        if active and active['status']=='running':raise CopilotError('translation_job_running','当前论文已有精读任务运行，可查看进度或暂停。','reading')
        job={'job_id':uuid.uuid4().hex,'version':doc['document_version_id'],'scope':'reading','base_card_id':book['base_card_id'],
             'config':client.public_info(),'glossary':self.reader.glossary(doc['document_version_id']),'status':'paused','stage':'evidence_ready',
             'units':units,'block_ids':[u['unit_id'] for u in units],'book':book,'selected_fields':prepared['fields'],
             'completed':[],'failed':{},'retained':[],'cache_hit_units':[],'total':len(units),'new_requests':0,'created_at':now(),
             'character_budget':budget,'plan':prepared,
             'authorization':{'provider':client.provider,'document_version_id':doc['document_version_id'],'file_sha256':doc['file_sha256'],
                              'fields':prepared['fields'],'confirmed_at':now(),'character_budget':budget,'llm_authorized':False,
                              'mode':data.get('authorization_mode','explicit_plan')}}
        self.reader._save(job);self.reader.resume(job['job_id'],True);return self.state(doc,card,job['job_id'])
    def check(self,doc,job_id):
        job=self.reader.get_job(job_id)
        if job['scope']!='reading' or job['version']!=doc['document_version_id'] or job['authorization']['file_sha256']!=doc['file_sha256']:
            raise CopilotError('reading_job_scope','精读任务与当前论文文件版本不一致。','evidence')
        validate(self.reader.service,doc,job['book']);return job
    def resume(self,doc,job_id,fields=None):
        job=self.check(doc,job_id)
        if not job.get('authorization',{}).get('confirmed_at'):raise CopilotError('reading_authorization','任务没有已保存的论文及预算确认。','authorization')
        self.reader.resume(job_id,True,fields);return self.state(doc,None,job_id)
    def pause(self,doc,job_id):self.check(doc,job_id);self.reader.pause(job_id);return self.state(doc,None,job_id)
    def state(self,doc,card=None,job_id=None):
        if job_id:job=self.check(doc,job_id)
        else:
            rows=[r for r in self.store.records('aliyun_job',doc['document_version_id']) if r.get('scope')=='reading' and r.get('base_card_id')==(card or {}).get('card_id')]
            latest=max(rows,key=lambda r:r['created_at'],default=None)
            if not latest:return {'job':None,'book':None}
            job=self.check(doc,latest['job_id'])
        public={k:v for k,v in job.items() if k not in ('book','units','glossary','config','kind','id','document_version_id')}
        public['field_status']={}
        for field in job['selected_fields']:
            value=job['book']['fields'][field];ids={u['unit_id'] for u in value['units']};errors={i:e for i,e in job['failed'].items() if i in ids}
            translated=sum(u.get('translation',{}).get('status')=='complete' for u in value['units'] if u.get('translation'))
            public['field_status'][field]={'sources':len(ids),'translated':translated,'retained':len(ids.intersection(job['retained'])),'errors':errors,
                'status':'missing_source' if not ids else 'failed' if errors else 'complete' if ids.issubset(set(job['completed'])|set(job['retained'])) else 'pending',
                'semantic_review':'unreviewed','conflict':value.get('conflict',False),'coverage':value.get('coverage')}
        used=sum(r['characters'] for r in self.store.records('translation_admission') if r.get('task_id')==job['job_id'] and r.get('provider')==job['authorization']['provider'])
        public.update(submitted_characters=used,remaining_characters=max(0,job['character_budget']-used))
        return {'job':public,'book':job['book']}
