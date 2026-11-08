"""Local browser workbench reusing the existing services. No frontend credentials."""
import argparse,hashlib,json,mimetypes,secrets,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,unquote
from . import CopilotService
from .errors import CopilotError
from .workspace import Workspace,UsageBudget
from .translation import TranslationService,MockTranslator,selection
from .field_evidence import find_field_evidence
from .llm import LLMConfig,DeepSeekClient
from .reading import StructuredReading
from .rag import PaperQA
from .comparison import ComparisonService
from .comparison_materials import ComparisonCatalog
from .repository import now
from .api_settings import APISettings, isolated
from .provider_client import ProviderClient

ROOT=Path(__file__).resolve().parents[1]

class UsageClient:
    is_test_double=False
    def __init__(self,app,sid,doc,nonce):
        self.config=LLMConfig.from_environment();self.calls=[];self.app,self.sid,self.doc,self.nonce=app,sid,doc,nonce
        if self.config.provider!='deepseek' or self.config.model!='deepseek-flash' or self.config.base_url!='https://api.deepseek.com' or self.config.max_output_tokens>2000:raise CopilotError('usage_config','当前预算仅适用于已验证DeepSeek Flash配置和≤2000输出token。','budget')
    def complete_json(self,stage,system,user):
        key=hashlib.sha256(json.dumps([self.nonce,stage,system,user],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        budget=UsageBudget(self.app.store,self.sid,self.doc['document_version_id'],stage,key,self.app.paid_enabled)
        client=DeepSeekClient(self.config,budget)
        try:return client.complete_json(stage,system,user)
        finally:self.calls.extend(client.calls)

class Workbench:
    def __init__(self,root,paid_enabled=False,*,annotation_dir=None):
        self.service=CopilotService(root);self.store=Workspace(root);self.paid_enabled=paid_enabled;self.settings=APISettings(root)
        self.annotation_dir=Path(annotation_dir) if annotation_dir else ROOT/'evaluation/labels'
        self.translation=TranslationService(self.service,self.store);self.mock=MockTranslator()
        from .aliyun_reading import AliyunReading
        from .reading_provider import configured_provider
        self.aliyun_reading=AliyunReading(self.service,self.store,provider_factory=lambda doc,consent:configured_provider(self.settings,self.store,doc,consent))
        from .source_reading import SourceReading
        self.source_reading=SourceReading(self.service,self.store)
        from .chinese_reading import ChineseReading
        self.chinese_reading=ChineseReading(self.source_reading,self.aliyun_reading)
        self.catalog=ComparisonCatalog(self.service,audit_paths=[ROOT/'verification/stage4_3/source_audit.json'])
        self.comparison=ComparisonService(self.catalog)
    def cards(self,version):
        if not self.service.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='reading_cards'"):return []
        return [json.loads(r['record_json']) for r in self.service.db.rows('SELECT record_json FROM reading_cards WHERE document_version_id=? ORDER BY card_version DESC',(version,))]
    def client(self,data,doc):
        return ProviderClient(self.settings.llm(),self.store,doc)
    def source_card(self,version,card_id=None):
        cards=self.cards(version)
        card=next((c for c in cards if c['card_id']==card_id),None) if card_id else next((c for c in cards if c['card_status']=='draft'),None)
        if card_id and not card:raise CopilotError('card_scope','历史卡不属于当前论文版本。','evidence')
        return card
    def translation_client(self,data,doc,provider):
        if provider=='aliyun':
            from .aliyun_translation import AliyunProvider
            return AliyunProvider(self.store,doc,data.get('consent'),data.get('consent_provider'))
        if provider in ('baidu','google','deepl','azure','argos','libretranslate','mymemory'):
            from .translation_providers import NativeProvider
            return NativeProvider(provider,self.store,doc,data.get('consent'),data.get('consent_provider'))
        if provider=='mock':return self.mock
        if provider=='deepseek':return self.client(data,doc)
        raise CopilotError('provider_unknown','未知Provider；没有发送请求。','translation')
    def get(self,path,q):
        version=q.get('version',[''])[0]
        if path=='/api/settings':return self.settings.public()
        if path=='/api/usage':
            from .usage_report import report
            return report(self.service,self.store,q)
        if path=='/api/tasks' and isolated(self.store.path.parent):return {'tasks':[],'batch':{'items':[]}}

        if path=='/api/aliyun/network':
            from .aliyun_network import network_status
            return network_status()
        if path=='/api/chinese-reading':
            return self.chinese_reading.state(self.service.get_document(version),self.source_card(version,q.get('card_id',[None])[0]),q.get('job_id',[None])[0])
        if path=='/api/aliyun/page':return self.aliyun_reading.page(version,int(q.get('page',['1'])[0]))
        if path=='/api/aliyun/jobs':return {'jobs':self.aliyun_reading.history(version)}
        if path=='/api/aliyun/job':return self.aliyun_reading.get_job(q['id'][0])
        if path=='/api/library':
            docs=self.service.list_documents()
            for d in docs:
                cards=self.cards(d['document_version_id']);d['reading_cards']=[{k:c[k] for k in ('card_id','card_version','card_status','execution_mode')} for c in cards];d['cloud_consent']='on_action'
            return {'documents':docs,'paid_enabled':self.paid_enabled}
        if path=='/api/translation-providers':
            from .translation_providers import provider_status
            return provider_status(self.paid_enabled,self.store)
        if path=='/api/source-reading':
            doc=self.service.get_document(version);card=self.source_card(version,q.get('card_id',[None])[0])
            return {'book':self.source_reading.load(doc,card,q.get('snapshot_id',[None])[0]),'versions':[{'id':r['id'],'version':r['source_reading_version'],'base_card_id':r.get('base_card_id'),'operation':r['operation']} for r in self.store.records('source_reading',version)]}
        if path=='/api/paper':
            from .research_structure import research_preview
            doc=self.service.get_document(version)
            has_qa=self.service.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='qa_runs'")
            qa=[json.loads(r['result_json']) for r in self.service.db.rows('SELECT result_json FROM qa_runs WHERE document_version_id=? ORDER BY created_at DESC',(version,))] if has_qa else []
            return {'document':doc,'cards':self.cards(version),'qa':qa,
                'research_structure':research_preview(self.service,doc),'research_summaries':self.store.records('research_summary',version),'translation_revisions':self.store.records('translation_revision',version),
                'supplements':self.store.records('field_evidence_candidate',version),'notes':self.store.records('note',version),'glossaries':self.store.records('glossary',version),'translations':self.store.records('translation',version)}
        if path=='/api/comparisons':return {'snapshots':self.comparison.history.list()}
        if path=='/api/comparison':return self.comparison.load(q['id'][0],int(q.get('version',['1'])[0]))
        if path=='/api/session':return self.store.session(q['id'][0])
        if path=='/api/tasks':
            if not (ROOT/'evaluation/annotation_tasks.jsonl').is_file() or not (ROOT/'evaluation/private/observations.json').is_file():
                return {'tasks':[],'batch':{'id':'local','items':[]}}
            from evaluation.io import read_jsonl,read_json
            from .quality_review import BATCH_ID,BATCH,task_material
            tasks=read_jsonl(ROOT/'evaluation/annotation_tasks.jsonl');obs=read_json(ROOT/'evaluation/private/observations.json')
            labels=self.store.records('annotation')
            drafts=self.store.records('annotation_draft')
            for t in tasks:
                t['record']=obs[t['observation_id']]['record'];t['canonical_response']=obs[t['observation_id']].get('canonical_model_response');t['labels']=[l for l in labels if l['task_id']==t['task_id']]
                t['draft']=next((d for d in drafts if d['task_id']==t['task_id'] and d['unit_fingerprint']==t['unit_fingerprint']),None)
                t['review_material']=task_material(t,t['record'])
            return {'tasks':tasks,'batch':{'id':BATCH_ID,'items':[{'task_id':tid,'title':title,'instruction':instruction} for tid,title,instruction in BATCH]}}
        raise CopilotError('not_found','接口不存在。','request')
    def post(self,path,data):
        if path=='/api/settings':return self.settings.save(data)
        if path in ('/api/annotation','/api/annotation-draft') and isolated(self.store.path.parent):raise CopilotError('not_found','当前账号没有评测任务。','request')
        if path=='/api/translate':
            provider=data.get('provider') or self.settings.read()['translation_provider']
            data={**data,'provider':provider,'consent':True,'consent_provider':provider}
        version=data.get('version','');doc=self.service.get_document(version) if version else None
        if path in ('/api/chinese-reading/begin','/api/chinese-reading/plan','/api/chinese-reading/start'):
            card=self.source_card(version,data.get('card_id'))
            if path.endswith('/begin'):return self.chinese_reading.begin(doc,card,data)
            if path.endswith('/plan'):return self.chinese_reading.plan(doc,card,data.get('snapshot_id'),data.get('fields'))
            return self.chinese_reading.start(doc,card,data)
        if path=='/api/chinese-reading/pause':return self.chinese_reading.pause(doc,data['job_id'])
        if path=='/api/chinese-reading/resume':return self.chinese_reading.resume(doc,data['job_id'],data.get('fields'))
        if path=='/api/aliyun/plan':return self.aliyun_reading.plan(version,data.get('scope','page'),data.get('page'),data.get('block_id'))
        if path=='/api/aliyun/start':return self.aliyun_reading.start(version,data.get('scope','page'),data.get('page'),data.get('block_id'),data.get('plan_id'),data.get('confirm_plan'),data.get('consent') is True)
        if path=='/api/aliyun/pause':return self.aliyun_reading.pause(data['job_id'])
        if path=='/api/aliyun/resume':return self.aliyun_reading.resume(data['job_id'],data.get('consent') is True)
        if path in ('/api/source-reading/retrieve','/api/source-reading/plan','/api/source-reading/translate','/api/source-reading/range','/api/source-reading/local-translate','/api/source-reading/field-plan','/api/source-reading/google-field','/api/source-reading/cloud-field'):
            from .source_reading import plan,make_unit,FIELDS,catalog,overlap
            card=self.source_card(version,data.get('card_id'))
            book=self.source_reading.load(doc,card,data.get('snapshot_id'),fresh=path.endswith('/retrieve'))
            if path.endswith(('/field-plan','/google-field','/cloud-field')):
                from .source_reading import fingerprint
                from .prose import reading_prose,request_segments
                from .translation_providers import settings
                provider=data.get('provider','google')
                if provider not in ('aliyun','google','baidu','deepl','azure'):raise CopilotError('provider_unknown','请选择已接入的官方翻译服务。','translation')
                if provider=='aliyun':
                    from .aliyun_translation import status
                    config=status(self.store)
                else:config=settings(provider,self.store.path.parent)
                field=data.get('field')
                if field not in FIELDS:raise CopilotError('invalid_field','请选择需要翻译的字段。','translation')
                units=book['fields'][field]['units']
                material={'version':doc['document_version_id'],'snapshot_id':book.get('id'),'card_id':book.get('base_card_id'),'field':field,'provider':provider,'glossary':data.get('glossary',{}),'sources':[u['sources'] for u in units],'transport_limit':config.get('request_character_limit')}
                prepared={'plan_id':fingerprint(material),'field':field,'requests':sum(len(request_segments(reading_prose(u['original_text']),config['request_character_limit'],6000)) for u in units) if provider=='baidu' else len(units),'characters':sum(len(reading_prose(u['original_text'])) for u in units),'provider':provider,'source_units':[u['unit_id'] for u in units]}
                if provider=='aliyun':
                    client=self.translation_client({},doc,'aliyun');previews=[client.preview(u['original_text'],data.get('glossary')) for u in units]
                    prepared.update(characters=sum(p['new_characters'] for p in previews),requests=sum(sum(not c['cached'] for c in p['chunks']) for p in previews))
                    prepared['requests_upper']=prepared['requests']*2
                if path.endswith('/field-plan'):return prepared
                if data.get('confirm_plan') is not True or data.get('plan_id')!=prepared['plan_id']:raise CopilotError('translation_plan_changed','请重新查看并确认翻译范围、次数及字符计划。','authorization')
                client=self.translation_client(data,doc,provider);config=client.config_data
                needs_remote=provider!='aliyun' or prepared['requests']>0
                if needs_remote and not config['configured']:raise CopilotError('provider_unconfigured',config['reason'],'translation')
                if needs_remote and not client.consent:raise CopilotError('provider_consent','请确认本论文发送给所选服务商。','authorization')
                used=sum(r['characters'] for r in self.store.records('translation_admission') if r['provider']==provider and r.get('period')==config['allowance_month'])
                if used+prepared['characters']*(2 if provider=='aliyun' else 1)>config['monthly_character_cap']:raise CopilotError('provider_quota','计划超过本应用字符预算，未发送任何请求。','translation')
                current=book;new_requests=0;error=None
                for unit in units:
                    try:
                        result=self.source_reading.translate(doc,current,unit['unit_id'],provider,self.translation,client,data.get('glossary'),plan(current,unit['unit_id'],provider,data.get('glossary'))['plan_id'])
                        current=result['book'];new_requests+=result['new_requests']
                    except CopilotError as exc:error=exc.as_dict();break
                return {'book':current,'translation_error':error,'new_requests':new_requests,'requests_planned':prepared['requests']}
            if path.endswith('/local-translate'):
                client=self.translation_client({},doc,'argos')
                result=self.source_reading.translate_local(doc,book,self.translation,client,data.get('glossary'),data.get('field'))
                result['translation_error']=result.pop('error')
                return result
            if path.endswith('/range'):
                field=data.get('field')
                if field not in FIELDS:raise CopilotError('invalid_field','请选择原文所属字段。','evidence')
                anchor=selection(self.service,version,data['page'],data['text'],data.get('rects'))
                if anchor['mapping_status']!='parser_text_matched':raise CopilotError('source_range_unmapped','这段选区尚未唯一映射到原文，请重新选择完整段落。','evidence')
                p={'section':'用户选定原文范围','spans':[{'pdf_page':anchor['pdf_page'],'char_start':anchor['char_start'],'char_end':anchor['char_end'],'text':anchor['exact_quote']}],'text':anchor['exact_quote']}
                unit=make_unit(doc,p,'user_selected_original_range')
                if not any(u['unit_id']==unit['unit_id'] for u in book['fields'][field]['units']):book['fields'][field]['units'].append(unit)
                book['fields'][field].update(location='located',coverage='user_defined_range')
                return {'book':self.source_reading.save(doc,book,'append_user_source_range')}
            if path.endswith('/retrieve'):
                field=data.get('field')
                if field and field not in FIELDS:raise CopilotError('invalid_field','请选择原文所属字段。','evidence')
                if field and not book['fields'][field]['units']:
                    candidates=find_field_evidence(self.service,doc,field)['candidates'];_,paragraphs=catalog(self.service,doc)
                    found=[p for p in paragraphs if any(overlap(s,r) for s in p['spans'] for r in candidates)]
                    book['fields'][field].update(units=[make_unit(doc,p,'manual_retrieval_expanded_to_paragraph') for p in found],location='located' if found else 'not_found_in_checked_scope',coverage='partial_related_scope')
                return {'book':self.source_reading.save(doc,book,'retrieve_original_again')}
            provider=data.get('provider','mock')
            if path.endswith('/plan'):return plan(book,data['unit_id'],provider,data.get('glossary'))
            if data.get('confirm_plan') is not True:raise CopilotError('translation_plan_confirmation','请明确确认本段原文的翻译计划。','authorization')
            expected=plan(book,data['unit_id'],provider,data.get('glossary'))
            if data.get('plan_id')!=expected['plan_id']:raise CopilotError('translation_plan_changed','原文范围或引擎配置已变化，请重新查看计划。','authorization')
            client=self.translation_client(data,doc,provider)
            return self.source_reading.translate(doc,book,data['unit_id'],provider,self.translation,client,data.get('glossary'),data.get('plan_id'))
        if path=='/api/inspect-source':
            ref=data['ref'];version=ref['document_version_id'];doc=self.service.get_document(version)
            if any(ref.get(k) is not None and ref[k]!=doc[k] for k in ('paper_id','file_sha256')):
                raise CopilotError('source_scope','引用的论文或文件版本不一致，未跳转。','evidence')
            start=ref.get('quote_char_start');end=ref.get('quote_char_end')
            quote=ref.get('quote') or ref.get('chunk_text') or ref.get('text','')
            if start is not None or end is not None:
                raw=self.service.get_pages(version)[ref['pdf_page']-1]['raw_text']
                if not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=len(raw) or raw[start:end]!=ref.get('quote'):
                    raise CopilotError('source_offset','引用字符区间与原文不一致，未跳转。','evidence')
                anchor=selection(self.service,version,ref['pdf_page'],quote[:2500],ref.get('rects'))
                anchor.update(text=quote,char_start=start,char_end=end,exact_quote=raw[start:end],mapping_status='parser_text_matched')
            else:anchor=selection(self.service,version,ref['pdf_page'],quote,ref.get('rects'))
            auxiliary=None
            if data.get('card_id'):
                card=next((c for c in self.cards(version) if c['card_id']==data['card_id']),None)
                saved=card and card['evidence_references'].get(ref.get('evidence_id'))
                if not saved or any(saved.get(k)!=ref.get(k) for k in ('quote','pdf_page','file_sha256')):
                    raise CopilotError('card_source_scope','引用不属于指定卡片或文件版本。','evidence')
                material=self.catalog.get({'source_kind':'saved_card','paper_id':doc['paper_id'],
                    'document_version_id':version,'card_id':card['card_id'],'card_version':card['card_version']})
                audit=material.get('audit')
                if audit:
                    item=next((x for x in audit['citations'] if x['evidence_id']==ref['evidence_id']),None)
                    if item:auxiliary={'status':item['support_status'],'origin':'development_assistant',
                        'user_independent_review':False}
            return {'anchor':anchor,'location_status':anchor['mapping_status'],
                    'semantic_support':ref.get('source_support_status') or ref.get('semantic_support','unverifiable'),
                    'auxiliary_source_review':auxiliary,'human_review':'not_inferred_from_locator'}
        if path=='/api/selection-evidence':
            from .reading_schema import MODULE_FIELDS
            if data.get('field') not in {field for fields in MODULE_FIELDS.values() for field in fields}:
                raise CopilotError('invalid_field','请选择通用精读字段。','evidence')
            cards=self.cards(version);card=next((c for c in cards if c['card_id']==data.get('card_id')),None)
            if not card or card['card_status']!='draft':
                raise CopilotError('card_scope','补充证据必须绑定当前文件的一张已保存草稿。','evidence')
            anchor=selection(self.service,version,data['page'],data['text'],data.get('rects'))
            field=card['structured_content']['fields'][data['field']]
            return self.store.add('field_evidence_candidate',version,{
                'field':data['field'],'source':anchor,'origin':'user_pdf_selection',
                'review_status':'pending','not_applied_to_card':True,'not_a_confirmed_fact':True,
                'original_card_id':card['card_id'],'original_card_version':card['card_version'],
                'original_statements':[card['structured_content']['facts'][f]['text'] for f in field['fact_ids']],
                'operation':'append_supplemental_candidate'})
        if path=='/api/session':
            versions=data.get('versions',[])
            for v in versions:self.service.get_document(v)
            return self.store.create_session(versions,data['max_calls'],data['budget'],data.get('consent',False))
        if path=='/api/translate':
            anchor=selection(self.service,version,int(data['page']),data['text'],data.get('rects'))
            provider=data.get('provider') or ('mock' if data.get('mode')=='mock' else 'deepseek')
            client=self.translation_client(data,doc,provider)
            return self.translation.translate(anchor,data.get('glossary'),client,data.get('force',False))
        if path=='/api/translation-revision':
            original=next((x for x in self.store.records('translation',version) if x['id']==data.get('translation_id')),None)
            if not original or not isinstance(data.get('text'),str) or not 1<=len(data['text'].strip())<=10000:raise CopilotError('revision_input','译文记录或修订内容无效。','translation')
            return self.store.add('translation_revision',version,{'translation_id':original['id'],'text':data['text'].strip(),'origin':'user_edit','review_status':'unreviewed','original_preserved':True})
        if path=='/api/anchor':return selection(self.service,version,int(data['page']),data['text'],data.get('rects'))
        if path=='/api/find-field':return find_field_evidence(self.service,doc,data['field'],data.get('section'))
        if path=='/api/research-summary':
            from .research_structure import research_preview,VERSION
            item=next((x for x in research_preview(self.service,doc)['contributions'] if x['ordinal']==data.get('ordinal')),None)
            if data.get('card_id') and not any(c['card_id']==data['card_id'] for c in self.cards(version)):
                raise CopilotError('card_scope','概括建议的卡片不属于当前论文版本。','evidence')
            if not item or not isinstance(data.get('text'),str) or not 1<=len(data['text'].strip())<=2000:
                raise CopilotError('summary_input','贡献项或中文概括无效。','input')
            return self.store.add('research_summary',version,{'ordinal':item['ordinal'],'original_text':item['original_text'],'sources':item['sources'],
                'chinese_summary':data['text'].strip(),'origin':'user_edit','strategy':VERSION,'review_status':'unreviewed',
                'original_card_id':data.get('card_id'),'not_applied_to_card':True})
        if path=='/api/supplement':
            card=next((c for c in self.cards(version) if c['card_id']==data.get('card_id') and c['card_status']=='draft'),None)
            if not card:raise CopilotError('card_scope','补充证据必须绑定当前文件的一张已保存草稿。','evidence')
            result=find_field_evidence(self.service,doc,data['field'],data.get('section'));source=next((c for c in result['candidates'] if c['chunk_id']==data.get('chunk_id') and c['char_start']==data.get('char_start')),None)
            if not source:raise CopilotError('candidate_stale','候选来源已变化，请重新检索。','evidence')
            return self.store.add('field_evidence_candidate',version,{'field':data['field'],'source':source,'origin':'user_selected_candidate','review_status':'pending','not_applied_to_card':True,'original_card_id':card['card_id'],'original_card_version':card['card_version'],'operation':'append_supplemental_candidate','not_a_confirmed_fact':True})
        if path=='/api/glossary':
            glossary=data['glossary']
            if not isinstance(glossary,dict) or len(glossary)>50 or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>100 or len(v)>150 for k,v in glossary.items()):raise CopilotError('invalid_glossary','术语表格式错误。','input')
            return self.store.add('glossary',version,{'terms':glossary})
        if path=='/api/note':
            if not isinstance(data.get('text'),str) or not 1<=len(data['text'])<=5000:raise CopilotError('invalid_note','笔记需为1—5000字符。','input')
            anchor=None
            if data.get('selection'):
                a=data['selection'];anchor=selection(self.service,version,a['page'],a['text'],a.get('rects'))
            return self.store.add('note',version,{'text':data['text'],'page':anchor['pdf_page'] if anchor else data.get('page'),
                'anchor':anchor,'note_type':'term_explanation' if data.get('note_type')=='term_explanation' else 'reading_note',
                'origin':'user_note_not_model_claim'})
        if path=='/api/qa':
            client=self.client(data,doc);qa=PaperQA(self.service,{doc['file_sha256']});qa.allowed_provider=client.config.provider
            return qa.answer_question(doc['paper_id'],data['question'],document_version_id=version,allow_remote=True,client=client)
        if path=='/api/reading':
            client=self.client(data,doc);reading=StructuredReading(self.service,{doc['file_sha256']});reading.allowed_provider=client.config.provider
            return reading.generate(doc['paper_id'],document_version_id=version,allow_remote=True,client=client,regenerate=False,resume_card_id=data.get('resume_card_id'))
        if path=='/api/compare':return self.comparison.compare_papers(data['selections'])
        if path in ('/api/annotation','/api/annotation-draft'):
            from evaluation.io import read_jsonl,write_jsonl
            from evaluation.annotations import load_annotations,LABELS
            from .quality_review import validate_targets
            task=next(t for t in read_jsonl(ROOT/'evaluation/annotation_tasks.jsonl') if t['task_id']==data['task_id'])
            if data.get('unit_fingerprint',task['unit_fingerprint'])!=task['unit_fingerprint']:
                raise CopilotError('review_stale','任务版本已变化，请重新打开任务。','annotation')
            if path=='/api/annotation-draft':
                draft={k:data.get(k,'') for k in ('label','rationale','reviewer_id','review_scope')}
                if any(not isinstance(v,str) or len(v)>10000 for v in draft.values()) or (draft['label'] and draft['label'] not in LABELS[task['annotation_type']]):
                    raise CopilotError('review_draft','草稿格式或标签不符合任务约定。','annotation')
                from evaluation.io import read_json
                record=read_json(ROOT/'evaluation/private/observations.json')[task['observation_id']]['record']
                targets=validate_targets(self.service,record,data.get('targets',[]))
                return self.store.add('annotation_draft',None,{**draft,'targets':targets,'task_id':task['task_id'],'unit_fingerprint':task['unit_fingerprint'],'status':'draft','is_evaluation_label':False})
            if data.get('test_record'):
                return self.store.add('annotation_test',None,{'task_id':task['task_id'],'label':data['label'],'reason':data['rationale'],'reviewer_type':'development_assistant','excluded_from_human_metrics':True})
            if not data.get('independent_confirm'):raise CopilotError('human_confirm','请本人独立核查后确认，AI不能代填。','annotation')
            label={'task_id':task['task_id'],'unit_fingerprint':task['unit_fingerprint'],'status':'completed','reviewer':{'type':'human','reviewer_id':data.get('reviewer_id'),'independent_of_generation':True},'reviewed_at':now(),'label':data['label'],'rationale':data['rationale']}
            if 'targets' in data:
                from evaluation.io import read_json
                record=read_json(ROOT/'evaluation/private/observations.json')[task['observation_id']]['record']
                label['targets']=validate_targets(self.service,record,data['targets'])
            label['review_scope']=data.get('review_scope','')
            if task['annotation_type']=='field_completeness' and data['label']=='missing' and (not label.get('targets') or not label['review_scope'].strip()):
                raise CopilotError('review_missing_source','判为缺失时请填写检查范围并添加原文依据。','annotation')
            folder=self.annotation_dir;folder.mkdir(parents=True,exist_ok=True);file=folder/(uuid.uuid4().hex+'.jsonl');write_jsonl(file,[label])
            try:load_annotations([file],[task])
            except Exception:
                file.unlink();raise
            return self.store.add('annotation',None,{**label,'label_file':str(file)})
        raise CopilotError('not_found','接口不存在。','request')

def make_server(root,port=8520,paid_enabled=False,*,require_login=False):
    from .local_translation import ensure_local_translation
    ensure_local_translation(Path(root))
    app=Workbench(Path(root),paid_enabled);token=secrets.token_urlsafe(32)
    from .accounts import Accounts
    accounts=Accounts(root) if require_login else None
    user_apps={};user_lock=threading.RLock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,value,status=200,mime='application/json',headers=None):
            raw=json.dumps(value,ensure_ascii=False).encode() if mime=='application/json' else value
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; worker-src 'self' blob:; connect-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
            for key,value in (headers or {}).items():self.send_header(key,value)
            self.end_headers();self.wfile.write(raw)
        def route(self):
            host=self.headers.get('Host','');allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if host not in allowed:raise CopilotError('host_rejected','仅允许本机访问。','security')
            origin=self.headers.get('Origin')
            if origin and origin not in {'http://'+h for h in allowed}:raise CopilotError('origin_rejected','拒绝跨站请求。','security')
            u=urlsplit(self.path);q=parse_qs(u.query);path=unquote(u.path)
            session=accounts.resolve(self.headers.get('Cookie')) if accounts else None
            csrf=session['csrf'] if session else token
            if path=='/api/bootstrap' and self.command=='GET':
                return self.reply({'token':csrf,'paid_enabled':True,'login_required':require_login,'user':Accounts.public(session) if session else None,'validation_label':getattr(app,'validation_label',None)})
            raw=None
            if self.command=='POST':
                if not secrets.compare_digest(self.headers.get('X-Workspace-Token',''),csrf):raise CopilotError('csrf','请求令牌无效，请刷新页面。','security')
                size=int(self.headers.get('Content-Length','0'));limit=25*1024*1024 if path=='/api/upload' else 1_000_000
                if not 0<size<=limit:raise CopilotError('request_size','请求为空或过大。','input')
                raw=self.rfile.read(size)
            if accounts and path in ('/api/auth/register','/api/auth/login','/api/auth/logout') and self.command=='POST':
                data=json.loads(raw)
                if path.endswith('/logout'):
                    accounts.logout(session)
                    return self.reply({'ok':True},headers={'Set-Cookie':Accounts.cookie()})
                if path.endswith('/register'):user,cookie=accounts.register(data.get('username'),data.get('password'))
                else:user,cookie=accounts.login(data.get('username'),data.get('password'),self.client_address[0])
                return self.reply({'user':user},headers={'Set-Cookie':Accounts.cookie(cookie)})
            current=app
            if require_login and path.startswith('/api/'):
                if not session:return self.reply({'error':{'code':'login_required','message':'请先登录。'}},401)
                if not session['owner']:
                    with user_lock:
                        if session['id'] not in user_apps:
                            folder=accounts.workspace(session)
                            user_apps[session['id']]=Workbench(folder,True,annotation_dir=folder/'annotations')
                        current=user_apps[session['id']]
            if self.command=='POST':
                if path=='/api/upload':return self.reply(current.service.import_pdf(raw,q.get('name',['paper.pdf'])[0]))
                return self.reply(current.post(path,json.loads(raw)))
            if path=='/api/pdf':
                doc=current.service.get_document(q['version'][0]);return self.reply(current.service.store.check(doc['file_sha256']).read_bytes(),mime='application/pdf')
            if path.startswith('/api/'):return self.reply(current.get(path,q))
            if require_login and path in ('/','/settings','/usage') and not session:
                return self.reply(b'',302,'text/plain',{'Location':'/login'})
            path={'/login':'/login.html','/settings':'/settings.html','/usage':'/usage.html'}.get(path,path)
            file=(ROOT/'web'/('index.html' if path=='/' else path.lstrip('/'))).resolve()
            if not file.is_relative_to((ROOT/'web').resolve()) or not file.is_file():return self.reply({'error':'not found'},404)
            mime='text/javascript' if file.suffix=='.mjs' else mimetypes.guess_type(file)[0] or 'application/octet-stream'
            return self.reply(file.read_bytes(),mime=mime+('; charset=utf-8' if mime.startswith('text/') else ''))
        def do_GET(self):self.safe()
        def do_POST(self):self.safe()
        def safe(self):
            try:self.route()
            except (BrokenPipeError,ConnectionResetError):pass
            except CopilotError as e:self.reply({'error':e.as_dict()},400)
            except Exception:self.reply({'error':{'code':'request_failed','message':'操作未完成，请检查输入或本地数据；没有自动重试。'}},400)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.app=app;return server

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8520);p.add_argument('--data-dir',type=Path,default=ROOT/'data');p.add_argument('--enable-paid',action='store_true');a=p.parse_args()
    server=make_server(a.data_dir,a.port,True,require_login=True);print(f'Local research workbench http://127.0.0.1:{a.port} · account login enabled',flush=True);server.serve_forever()
if __name__=='__main__':main()
