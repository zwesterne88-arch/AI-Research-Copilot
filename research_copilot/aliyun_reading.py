"""Paragraph/page/document reading over existing parser text and workspace cache.

Job creation/resume is explicit. GET, Streamlit rerun and library switches never
start remote work. Checkpoints and translations are append-only workspace rows.
"""
from copy import deepcopy
from pathlib import Path
import json,re,sqlite3,threading,uuid
from .aliyun_translation import AliyunProvider,configuration,usage,sha,units
from .errors import CopilotError
from .source_reading import make_unit,geometry,fingerprint
from .translation import TranslationService,passage_cache_key
from .job_control import TaskLock,StopToken
from .prose import standalone_formula

class AliyunReading:
    def __init__(self,service,store,provider_factory=None):
        self.service,self.store=service,store;self.translation=TranslationService(service,store)
        self.provider_factory=provider_factory or (lambda doc,consent:AliyunProvider(store,doc,consent,'aliyun'))
        self.documents={};self.jobs={};self.lock=threading.RLock()
    def blocks(self,version):
        doc=self.service.get_document(version)
        if version in self.documents:return deepcopy(self.documents[version])
        try:layout=geometry(str(self.service.store.check(doc['file_sha256'])),doc['parser_version'])
        except (OSError,ValueError,ImportError):layout={}
        result=[];references=False;section='正文'
        for page in self.service.get_pages(version):
            raw=page['raw_text'];number=page['pdf_page'];g=layout.get(number,{})
            if g.get('raw_hash')!=fingerprint(raw):g={}
            cuts={0,len(raw)}|set(g.get('cuts',[]))
            for m in re.finditer(r'\n\s*\n',raw):cuts.update((m.start(),m.end()))
            headings=[]
            for m in re.finditer(r'(?m)^\s*((?:\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{2,90})|Abstract|Introduction|References|Bibliography|Conclusion|Conclusions|Appendix[^\n]*)\s*$',raw):
                cuts.update((m.start(),m.end()));headings.append((m.start(),m.end(),m.group(1).strip()))
            # Real IEEE/Elsevier parser text can join a heading and its paragraph,
            # or space out ABSTRACT. Preserve exact heading offsets separately.
            for m in re.finditer(r'(?im)^[ \t]*(?:(?:[IVX]+\.|\d+(?:\.\d+)*\.?)\s+)?(?:A[ \t]*B[ \t]*S[ \t]*T[ \t]*R[ \t]*A[ \t]*C[ \t]*T|INTRODUCTION|RELATED WORK|BACKGROUND|METHODOLOGY|METHODS|EXPERIMENTS|RESULTS|DISCUSSION|CONCLUSIONS?|REFERENCES|BIBLIOGRAPHY)(?=\s|$)',raw):
                if any(lo<=m.start()<hi for lo,hi,_ in headings):continue
                cuts.update((m.start(),m.end()));headings.append((m.start(),m.end(),m.group().strip()))
            for a,b in zip(sorted(cuts),sorted(cuts)[1:]):
                value=raw[a:b];start=a+len(value)-len(value.lstrip());end=a+len(value.rstrip())
                if start>=end:continue
                text=raw[start:end];kind='paragraph';level=0;reason=None
                heading=next((name for lo,hi,name in headings if lo<=start and end<=hi),None)
                if heading:
                    section=heading;kind='heading';level=1+min(2,heading.split()[0].rstrip('.').count('.')) if re.match(r'\d',heading) else 1
                    if re.fullmatch(r'References|Bibliography',heading,re.I):references=True
                    elif heading.startswith('Appendix'):references=False
                elif re.match(r'^(?:Fig(?:ure)?\.?|Table)\s*\d',text,re.I):kind='caption'
                elif references:kind='reference';reason='参考文献名称与编号保留原文。'
                elif any(lo<=start and end<=hi for lo,hi in g.get('excluded',[])):kind='page_furniture';reason='页眉页脚保留原文。'
                elif standalone_formula(text):kind='formula';reason='公式保留原样。'
                elif len(re.findall(r'\d',text))>len(text)*0.34 or len(re.findall(r'\S[ \t]{3,}\S',text))>=3:kind='table';reason='表格行列需原页核查，保留原文。'
                unit=make_unit(doc,{'section':section,'spans':[{'pdf_page':number,'char_start':start,'char_end':end,'text':text}]},'parsed_pdf_reading_block',block_kind=kind,heading_level=level)
                unit.update(block_id=unit['unit_id'],pdf_page=number,source_only=bool(reason),source_only_reason=reason)
                result.append(unit)
        self.documents[version]=result;return deepcopy(result)
    def cached(self,unit,client,glossary=None):
        try:return self.store.cached(passage_cache_key(unit,client.public_info(),glossary))
        except (sqlite3.Error,ValueError):raise CopilotError('translation_storage','无法读取持久化翻译缓存，未发送新请求。','storage') from None
    def glossary(self,version):
        saved=self.store.records('glossary',version);return saved[0]['terms'] if saved else {}
    def page(self,version,page):
        doc=self.service.get_document(version)
        if not isinstance(page,int) or not 1<=page<=doc['page_count']:raise CopilotError('selection_page','PDF物理页码无效。','translation')
        client=self.provider_factory(doc,False);result=[];glossary=self.glossary(version)
        for unit in self.blocks(version):
            if unit['pdf_page']!=page:continue
            saved=self.cached(unit,client,glossary) if not unit['source_only'] else None
            result.append({**unit,'translation':saved['translation'] if saved else None,'translation_id':saved['id'] if saved else None,
                           'translation_status':'original_retained' if unit['source_only'] else 'cached' if saved else 'not_translated'})
        return {'blocks':result,'page':page,'version':version,'usage':usage(self.store),'page_count':doc['page_count']}
    def plan(self,version,scope='page',page=None,block_id=None):
        doc=self.service.get_document(version);all_blocks=self.blocks(version)
        if scope=='page':selected=[b for b in all_blocks if b['pdf_page']==page]
        elif scope=='block':selected=[b for b in all_blocks if b['block_id']==block_id]
        elif scope=='document':selected=all_blocks
        else:raise CopilotError('translation_scope','请选择段落、当前页或全文。','translation')
        if not selected:raise CopilotError('translation_scope','所选范围没有可读取的解析文本；原PDF保留。','translation')
        client=self.provider_factory(doc,False);original=0;new=0;saved=0;requests=0;cached_blocks=0;glossary=self.glossary(version)
        for unit in selected:
            if unit['source_only']:continue
            prepared=client.preview(unit['original_text'],glossary);original+=prepared['original_characters']
            if self.cached(unit,client,glossary):saved+=prepared['characters'];cached_blocks+=1
            else:
                new+=prepared['new_characters'];saved+=prepared['cache_saved_characters']
                requests+=sum(not c['cached'] for c in prepared['chunks'])
        material={'version':version,'file_sha256':doc['file_sha256'],'scope':scope,'page':page,'block_id':block_id,'blocks':[b['block_id'] for b in selected],'config':client.public_info(),'glossary':glossary}
        return {'plan_id':sha(material),'scope':scope,'page':page,'block_id':block_id,'version':version,'paper_title':doc['title'],
                'block_ids':material['blocks'],'total_blocks':len(selected),'translatable_blocks':sum(not b['source_only'] for b in selected),
                'source_only_blocks':sum(b['source_only'] for b in selected),'cached_blocks':cached_blocks,'original_characters':original,
                'estimated_new_characters':new,'cache_saved_characters':saved,'requests_upper':requests,'retry_reserved_upper':new*3,
                'usage':usage(self.store)}
    def _save(self,job):
        item={k:v for k,v in deepcopy(job).items() if k not in ('id','kind','document_version_id','cache_key')};item['revision']=job.get('revision',0)+1;job['revision']=item['revision']
        self.store.add('aliyun_job',job['version'],item);return item
    def get_job(self,job_id):
        records=[r for r in self.store.records('aliyun_job') if r['job_id']==job_id]
        if not records:raise CopilotError('translation_job','未找到该翻译任务。','translation')
        job=max(records,key=lambda r:r['revision']);live=self.jobs.get(job_id)
        if job['status']=='running' and not (live and live['thread'].is_alive()):
            try:lease=TaskLock(self.service.root,job_id)
            except CopilotError:pass  # Another process owns the running task.
            else:lease.close();job['status']='interrupted'
        return job
    def history(self,version):
        by={}
        for job in self.store.records('aliyun_job',version):
            if job['job_id'] not in by or by[job['job_id']]['revision']<job['revision']:by[job['job_id']]=job
        return [self.get_job(job['job_id']) for job in sorted(by.values(),key=lambda j:j['created_at'],reverse=True)[:8]]
    def start(self,version,scope,page,block_id,plan_id,confirmed,consent):
        prepared=self.plan(version,scope,page,block_id)
        if confirmed is not True or prepared['plan_id']!=plan_id:raise CopilotError('translation_plan_changed','请重新查看并确认翻译字符、缓存和范围计划。','authorization')
        cfg=configuration(self.store.path.parent)
        if prepared['estimated_new_characters']:
            if not cfg.configured:raise CopilotError('aliyun_config','请先在本机配置阿里云AccessKey并启用翻译。','translation')
            if not consent:raise CopilotError('provider_consent','请确认本论文必要文本可发送给阿里云。','authorization')
            if prepared['retry_reserved_upper']>prepared['usage']['remaining_characters']:raise CopilotError('aliyun_quota','本次含有限重试的保守预算超过本地剩余上限，未发送。','budget')
        job={'job_id':uuid.uuid4().hex,'version':version,'scope':scope,'page':page,'block_id':block_id,'block_ids':prepared['block_ids'],
             'glossary':self.glossary(version),
             'config':self.provider_factory(self.service.get_document(version),False).public_info(),'status':'paused','completed':[],
             'failed':{},'retained':[],'total':prepared['total_blocks'],'new_requests':0,'created_at':__import__('research_copilot.repository',fromlist=['now']).now()}
        self._save(job);return self.resume(job['job_id'],consent)
    def resume(self,job_id,consent,fields=None):
        with self.lock:
            old=self.jobs.get(job_id)
            if old and old['thread'].is_alive():return self.get_job(job_id)
            job=self.get_job(job_id);doc=self.service.get_document(job['version']);client=self.provider_factory(doc,consent)
            if job['config']!=client.public_info():raise CopilotError('translation_job_config','翻译配置已变化，请重新预览范围后创建任务。','translation')
            if fields and (job['scope']!='reading' or any(f not in job['selected_fields'] for f in fields)):raise CopilotError('reading_fields','续跑模块不属于本次精读范围。','authorization')
            lease=TaskLock(self.service.root,job_id)
            try:
                if job['scope']=='reading':
                    formulas={u['unit_id'] for u in job['units'] if standalone_formula(u['original_text'])}
                    for unit in job['units']:
                        if unit['unit_id'] in formulas:unit.update(source_only=True,source_only_reason='公式保留原样。')
                    # Older reports may have sent equations through the translator.
                    # Append a corrected report with the exact source notation.
                    job['completed']=[i for i in job['completed'] if i not in formulas]
                    for i in job['block_ids']:
                        if i in formulas:
                            if i not in job['retained']:job['retained'].append(i)
                            job['failed'].pop(i,None)
                    for value in job['book']['fields'].values():
                        for unit in value['units']:
                            if unit['unit_id'] in formulas:unit.update(translation=None,source_only=True,source_only_reason='公式保留原样。')
                allowed={u['unit_id'] for f in (fields or job.get('selected_fields',[])) for u in job.get('book',{}).get('fields',{}).get(f,{}).get('units',[])} if job['scope']=='reading' else set(job['block_ids'])
                job['run_ids']=[i for i in job['block_ids'] if i in allowed and i not in job['completed'] and i not in job['retained']]
                for i in job['run_ids']:job['failed'].pop(i,None)
                job.pop('message',None)
                job['run_id']=uuid.uuid4().hex;stop=StopToken(threading.Event(),self.store,job_id,job['run_id']);job['status']='running'
                client.cancel_event=stop
                if job['scope']=='reading':client.budget_scope={'id':job_id,'limit':job['character_budget']};job['stage']='translating'
                self._save(job)
                thread=threading.Thread(target=self._run,args=(job,client,stop,lease),daemon=True)
                self.jobs[job_id]={'thread':thread,'stop':stop};thread.start();return deepcopy(job)
            except BaseException:lease.close();raise
    def pause(self,job_id):
        job=self.get_job(job_id)
        if job['status']=='running':self.store.add('translation_job_stop',job['version'],{'job_id':job_id,'run_id':job.get('run_id')})
        live=self.jobs.get(job_id)
        if live:live['stop'].set()
        return self.get_job(job_id)
    def _run(self,job,client,stop,lease):
        try:
            doc=self.service.get_document(job['version']);blocks={b['block_id']:b for b in (job['units'] if job['scope']=='reading' else self.blocks(job['version']))}
            for block_id in job.get('run_ids',job['block_ids']):
                if stop.is_set():job['status']='paused';break
                if block_id in job['completed'] or block_id in job['retained']:continue
                block=blocks[block_id]
                if block.get('source_only'):job['retained'].append(block_id);self._save(job);continue
                call_start=len(client.calls)
                try:
                    result=self.translation.translate_passage(doc,block,client,job.get('glossary',{}))
                    job['completed'].append(block_id)
                    if result.get('cache_hit'):job.setdefault('cache_hit_units',[]).append(block_id)
                    if job['scope']=='reading':
                        from .source_reading import passage_payload
                        for value in job['book']['fields'].values():
                            for unit in value['units']:
                                if unit['unit_id']==block_id:unit['translation']=passage_payload(unit,result)
                except CopilotError as exc:
                    job['failed'][block_id]=exc.as_dict()
                    if exc.code in ('aliyun_network_permission','aliyun_tls','aliyun_proxy','aliyun_dns','aliyun_connection','aliyun_transport','translation_stopped','reading_character_budget','aliyun_quota','aliyun_config','aliyun_auth','aliyun_permission','aliyun_service','translation_storage','provider_consent'):
                        job['status']='paused';break
                finally:job['new_requests']+=len(client.calls)-call_start
                self._save(job)
                if len(job['failed'])>=3:job['status']='paused';job['message']='连续段落失败，已暂停，避免继续消耗额度。';break
            else:job['status']='completed' if len(job['completed'])+len(job['retained'])==job['total'] else 'finished_with_errors' if job['failed'] else 'paused'
        except Exception:
            job['status']='interrupted';job['message']='任务中断，已完成的缓存保留；可检查配置后继续。'
        finally:
            try:
                if job['scope']=='reading':
                    from .source_reading import SourceReading
                    final_status=job['status'];job['stage']='saving';job['status']='running';self._save(job)
                    try:job['book']=SourceReading(self.service,self.store).save(doc,job['book'],'one_click_chinese_reading')
                    except (CopilotError,sqlite3.Error,OSError):
                        job['status']='interrupted';job['stage']='storage_failed';job['message']='中文卡保存未完成，已完成分段缓存保留；请检查本地存储后继续。'
                    else:
                        job['status']=final_status;job['stage']='completed' if final_status=='completed' else 'paused' if final_status=='paused' else 'partial'
                self._save(job)
            finally:lease.close()
