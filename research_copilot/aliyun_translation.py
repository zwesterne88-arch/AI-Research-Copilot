"""Official TranslateGeneral adapter; source-bound callers/cache stay in translation.py."""
from dataclasses import dataclass,field
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import hashlib,importlib.util,json,os,re,sqlite3,threading,time,uuid
from .errors import CopilotError
from .prose import reading_prose
from .repository import now

ZONE=ZoneInfo('Asia/Shanghai')
VERSION='aliyun-general-v1-protected'
WIRE_LIMIT=4500
_lock=threading.RLock()
_last_request=0.0
TERMS=('Transformer','Federated Learning','Non-IID','CNN','RAG','F1-score','CIC-IDS2017')

def units(text):return len(text.encode('utf-16-le'))//2
def sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

@dataclass(frozen=True)
class Config:
    access_key_id:str=field(repr=False)
    access_key_secret:str=field(repr=False)
    enabled:bool=True
    monthly_limit:int=900000
    @property
    def configured(self):return bool(self.enabled and self.access_key_id and self.access_key_secret)

def configuration(root=None, *, use_saved=True):
    values={};root=Path(root or Path(__file__).resolve().parents[1]/'data')
    from .api_settings import APISettings,isolated
    if use_saved:
        saved=APISettings(root).profile('translation','aliyun')
        if saved:return Config(saved.get('access_key_id',''),saved.get('access_key_secret',''),True,10**12)
    if isolated(root):return Config('','')
    try:
        for line in (root.parent/'.env').read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1);values[key.strip()]=value.strip().strip('\"\'')
    except OSError:pass
    names=('ALIBABA_CLOUD_ACCESS_KEY_ID','ALIBABA_CLOUD_ACCESS_KEY_SECRET','ALIYUN_TRANSLATE_ENABLED','ALIYUN_TRANSLATE_MONTHLY_LIMIT')
    values={k:os.environ.get(k,values.get(k,'')) for k in names}
    try:limit=int(values['ALIYUN_TRANSLATE_MONTHLY_LIMIT'] or 900000)
    except ValueError:limit=0
    # The default safety margin cannot be removed accidentally by a typo.
    if not 0<=limit<=900000:limit=0
    return Config(values[names[0]],values[names[1]],values[names[2]].lower() not in ('0','false','no','off'),limit)

def period(clock=None):
    value=(clock or (lambda:datetime.now(ZONE)))()
    if value.tzinfo is None:value=value.replace(tzinfo=ZONE)
    return value.astimezone(ZONE).strftime('%Y-%m')

def usage(store,cfg=None,clock=None):
    cfg=cfg or configuration(store.path.parent);month=period(clock)
    submitted=sum(r['characters'] for r in store.records('translation_admission') if r.get('provider')=='aliyun' and r.get('period')==month)
    successful=sum(r['characters'] for r in store.records('aliyun_usage_result') if r['period']==month and r['status']=='success')
    return {'period':month,'timezone':'Asia/Shanghai','submitted_characters':submitted,'successful_characters':successful,
            'monthly_limit':cfg.monthly_limit,'remaining_characters':max(0,cfg.monthly_limit-submitted),
            'label':'本应用本月记录用量','notice':'阿里云账号实际剩余额度请以云控制台为准；本地统计包含保守预留。'}

def status(store):
    cfg=configuration(store.path.parent);sdk=importlib.util.find_spec('alibabacloud_alimt20181012') is not None
    reason='阿里云翻译已关闭，请在本机.env设置 ALIYUN_TRANSLATE_ENABLED=true。' if not cfg.enabled else '请在本机.env配置阿里云AccessKey ID和Secret；不要发送到聊天或前端。' if not cfg.configured else '请安装requirements.txt中的阿里云官方SDK。' if not sdk else '本应用月度上限配置无效或为0。' if not cfg.monthly_limit else None
    recorded=usage(store,cfg)
    verification=next((r for r in store.records('aliyun_smoke') if r.get('success') is True),None)
    return {'id':'aliyun','label':'阿里云 · 机器翻译通用版','configured':cfg.configured and sdk and bool(cfg.monthly_limit),'local':False,
            'reason':reason,'enabled':cfg.enabled,'credentials_present':bool(cfg.access_key_id and cfg.access_key_secret),'sdk_installed':sdk,
            'api':'TranslateGeneral','api_version':'2018-10-12','scene':'general','monthly_character_cap':cfg.monthly_limit,
            'allowance_month':recorded['period'],'local_reserved_characters':recorded['submitted_characters'],'local_remaining_characters':recorded['remaining_characters'],
            'cost_kind':'local_monthly_character_guard','connection_verified':bool(verification),'last_verified_at':verification.get('created_at') if verification else None,'automatic_fallback':False,**recorded,'label':'阿里云 · 机器翻译通用版'}

def prepare(text,glossary=None):
    """Protect unambiguous technical spans; every wire request is <5000 units."""
    normalized=reading_prose(text)
    if not normalized:return {'text':'','chunks':[],'characters':0,'original_characters':0,'markers':{}}
    if re.search(r'ZXQ\d{6}QXZ',normalized):raise CopilotError('aliyun_protection','原文包含保留的保护标记，请调整选段；没有发送。','translation')
    terms=sorted(set(TERMS)|set((glossary or {}).keys()),key=len,reverse=True)
    patterns=[r'\$\$[\s\S]+?\$\$',r'\$[^$\n]+\$',r'\\\([\s\S]+?\\\)',r'\\\[[\s\S]+?\\\]',
              r'\[\s*\d+(?:[\s,，\-–]+\d+)*\s*\]',r'\(\d+(?:\.\d+)?\)',
              r'(?<!\w)(?:'+'|'.join(re.escape(t) for t in terms)+r')(?!\w)',
              r'\d+(?:,\d{3})*(?:\.\d+)?(?:%|‰)?',r'[∑∫√±≤≥≈α-ωΑ-Ω]',r'\b[A-Za-z](?:_[A-Za-z0-9]+)?(?=\s*[=<>])']
    markers={}
    def protect(m):
        token=f'ZXQ{len(markers):06d}QXZ';markers[token]=m.group();return token
    wire=re.sub('|'.join(patterns),protect,normalized)
    chunks=[];a=0
    while a<len(wire):
        b=min(len(wire),a+WIRE_LIMIT)
        while units(wire[a:b])>WIRE_LIMIT:b-=1
        if b<len(wire):
            boundaries=[a+m.end() for m in re.finditer(r'\n\s*\n|[.!?。！？]\s+',wire[a:b])]
            if not boundaries:boundaries=[a+m.end() for m in re.finditer(r'\s+',wire[a:b])]
            if boundaries:b=boundaries[-1]
            for marker in re.finditer(r'ZXQ\d{6}QXZ',wire):
                if marker.start()<b<marker.end():b=marker.start();break
        if b<=a:raise CopilotError('aliyun_split','无法安全拆分本段，未截断或发送。','translation')
        segment=wire[a:b];needed={k:v for k,v in markers.items() if k in segment}
        chunks.append({'text':segment,'markers':needed,'characters':units(segment),'start':a,'end':b});a=b
    return {'text':normalized,'chunks':chunks,'characters':sum(c['characters'] for c in chunks),'original_characters':units(normalized),'markers':markers}

def restore(text,markers):
    # Permit only inserted whitespace inside an otherwise exact marker.
    for token,value in markers.items():
        expression=r'\s*'.join(map(re.escape,token));hits=list(re.finditer(expression,text))
        if len(hits)!=1:raise CopilotError('aliyun_protection','译文未完整保留公式、数值或术语标记，已保留原文，未发布为成功译文。','translation')
        text=re.sub(expression,lambda m:value,text,count=1)
    if re.search(r'ZXQ\d{6}QXZ',text):raise CopilotError('aliyun_protection','译文出现未对应的保护标记，未发布。','translation')
    return text

def mapped_error(code,request_id=None):
    code=str(code or 'unknown')
    if code in ('10009','Forbidden.RAM','Forbidden','NoPermission','AccessDenied'):kind,message,retry='aliyun_permission','RAM用户没有 alimt:TranslateGeneral 权限。',False
    elif code in ('10010','10013'):kind,message,retry='aliyun_service','阿里云机器翻译通用版未开通或账户服务不可用，请在控制台检查。',False
    elif any(v in code.lower() for v in ('invalidaccesskey','signature','unauthorized','invalidsecurity')):kind,message,retry='aliyun_auth','阿里云AccessKey无效或签名认证失败，请检查本机配置。',False
    elif code=='10008':kind,message,retry='aliyun_length','阿里云拒绝了超长文本，已保留原文。',False
    elif code in ('10005','10006'):kind,message,retry='aliyun_language','阿里云不支持该语言组合，本项目仅启用英文到简体中文。',False
    elif code in ('10001','ReadTimeout','ConnectTimeout','TimeoutError','ConnectionError') or 'throttl' in code.lower() or code in ('10002','ServiceUnavailable','500','503'):kind,message,retry='aliyun_temporary','阿里云网络超时、限流或暂时不可用；已保守预留本次额度。',True
    else:kind,message,retry='aliyun_response','阿里云没有返回成功译文，请检查服务状态；原文与已完成译文保留。',False
    error=CopilotError(kind,message,'translation',retry)
    error.vendor_code=code if re.fullmatch(r'100\d{2}|[A-Za-z][A-Za-z.]{1,70}',code) else 'unknown'
    error.request_id=request_id if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9-]{1,128}',request_id) else None
    return error

class AliyunProvider:
    provider='aliyun';is_test_double=False;model_id='aliyun-TranslateGeneral-2018-10-12'
    def __init__(self,store,doc,consent=False,consent_provider=None,*,client=None,config=None,clock=None,max_retries=1,sleeper=time.sleep):
        self.store,self.doc=store,doc;self.config=config or configuration(store.path.parent);self.client=client;self.clock=clock
        self.consent=consent is True and consent_provider=='aliyun';self.calls=[];self.max_retries=min(1,max(0,max_retries));self.sleep=sleeper
        self.budget_scope=None;self.cancel_event=None
        self.config_data={'local':False,'configured':self.config.configured and bool(self.config.monthly_limit),'config_revision':VERSION,
                          'reason':('本应用月度上限配置无效或为0。' if not self.config.monthly_limit else None) if self.config.configured else '缺少阿里云AccessKey或翻译未启用，请检查本机.env。',
                          'monthly_character_cap':self.config.monthly_limit,'allowance_month':period(clock)}
    def public_info(self):return {'provider':'aliyun','engine':'Aliyun TranslateGeneral','api':'TranslateGeneral','api_version':'2018-10-12','scene':'general','format':'text','source':'en','target':'zh','adapter':VERSION,'endpoint':'mt.cn-hangzhou.aliyuncs.com','config_revision':VERSION,'context_supported':False,'glossary_supported':'protected terms only'}
    def _sdk(self):
        if self.client is not None:return self.client
        from .aliyun_network import network_status
        connectivity=network_status()
        if not connectivity['reachable']:
            info=connectivity['error'];error=CopilotError(info['code'],connectivity['message']+' 尚未提交本段翻译，也未预留本段字符。','translation')
            error.failure_layer='preflight';raise error
        try:
            from alibabacloud_alimt20181012.client import Client
            from alibabacloud_tea_openapi.models import Config as SDKConfig
            self.client=Client(SDKConfig(access_key_id=self.config.access_key_id,access_key_secret=self.config.access_key_secret,endpoint='mt.cn-hangzhou.aliyuncs.com',region_id='cn-hangzhou',protocol='https'))
        except ImportError:raise CopilotError('aliyun_sdk','缺少阿里云官方SDK，请安装requirements.txt。','translation') from None
        return self.client
    def reserve(self,count):
        month=period(self.clock)
        try:
            with self.store.connection() as c:
                c.execute('BEGIN IMMEDIATE')
                records=c.execute("SELECT payload FROM records WHERE kind='translation_admission'").fetchall()
                used=sum(r['characters'] for item in records if (r:=json.loads(item[0])).get('provider')=='aliyun' and r.get('period')==month)
                if count+used>self.config.monthly_limit:raise CopilotError('aliyun_quota','本应用本月记录用量将超过本地上限，已阻止新请求；已有译文保留。','budget')
                scope=self.budget_scope
                if scope:
                    task_used=sum(r['characters'] for item in records if (r:=json.loads(item[0])).get('provider')=='aliyun' and r.get('task_id')==scope['id'])
                    if count+task_used>scope['limit']:raise CopilotError('reading_character_budget','已达到本次精读确认的字符预算；已完成结果保留，请重新确认范围与预算后创建任务。','budget')
                item={'id':uuid.uuid4().hex,'kind':'translation_admission','created_at':now(),'document_version_id':self.doc['document_version_id'],'file_sha256':self.doc['file_sha256'],'provider':'aliyun','api':'TranslateGeneral','period':month,'timezone':'Asia/Shanghai','characters':count,'cap':self.config.monthly_limit,'status':'conservatively_reserved'}
                if scope:item.update(task_id=scope['id'],task_character_budget=scope['limit'])
                c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(item['id'],item['kind'],item['document_version_id'],None,item['created_at'],json.dumps(item)))
                return item
        except sqlite3.Error:raise CopilotError('translation_storage','无法保存额度预留，未发送阿里云请求。','storage') from None
    def _cached(self,key):
        try:
            with self.store.connection() as c:r=c.execute("SELECT payload FROM records WHERE kind='aliyun_chunk' AND cache_key=? ORDER BY created_at DESC LIMIT 1",(key,)).fetchone()
            return json.loads(r[0]) if r else None
        except (sqlite3.Error,ValueError):raise CopilotError('translation_storage','无法读取翻译缓存，未发送新请求。','storage') from None
    def preview(self,text,glossary=None):
        prepared=prepare(text,glossary);config=self.public_info();parent=sha(prepared['text']);chunks=[]
        for i,chunk in enumerate(prepared['chunks']):
            key=sha({'doc':self.doc['document_version_id'],'parent':parent,'ordinal':i,'text':chunk['text'],'config':config,'glossary':glossary or {}})
            chunks.append({**chunk,'cache_key':key,'cached':self._cached(key)})
        return {**prepared,'chunks':chunks,'new_characters':sum(c['characters'] for c in chunks if not c['cached']),
                'cache_saved_characters':sum(c['characters'] for c in chunks if c['cached'])}
    def translate(self,text,source_lang,target_lang,context=None,glossary=None):
        if source_lang!='en' or target_lang not in ('zh','zh-CN'):raise CopilotError('aliyun_language','本期只支持英文翻译为简体中文。','translation')
        with _lock:return self._translate(text,glossary or {})
    def _translate(self,text,glossary):
        global _last_request
        prepared=self.preview(text,glossary);outputs=[];call_start=len(self.calls)
        if not prepared['chunks']:return {'translation':'','input_text':'','input_normalizer':VERSION,'provider':'aliyun','engine':'Aliyun TranslateGeneral','terminology_notes':[],'usage':{'input_characters':0},'cost_kind':'local_monthly_character_guard'}
        for chunk in prepared['chunks']:
            if chunk['cached']:outputs.append(chunk['cached']['translation']);continue
            if not self.config.enabled:raise CopilotError('aliyun_disabled','阿里云翻译已关闭；原文仍可阅读。','authorization')
            if not self.config.configured:raise CopilotError('aliyun_config','缺少阿里云AccessKey，请在本机环境变量或.env中配置。','translation')
            if not self.consent:raise CopilotError('provider_consent','请确认本论文必要文本可由阿里云处理。','authorization')
            sdk=self._sdk()
            from alibabacloud_alimt20181012.models import TranslateGeneralRequest
            from alibabacloud_tea_util.models import RuntimeOptions
            wire_text=chunk['text'];active_markers=chunk['markers'];temporary_retries=0;attempt=0
            while True:
                if self.cancel_event and self.cancel_event.is_set():raise CopilotError('translation_stopped','任务已暂停；原文、成功译文和已完成分片保留。','translation')
                delay=.021-(time.monotonic()-_last_request)
                if delay>0:self.sleep(delay)
                if self.cancel_event and self.cancel_event.is_set():raise CopilotError('translation_stopped','任务已暂停，未提交下一个请求。','translation')
                admission=self.reserve(units(wire_text));start=time.monotonic();attempt+=1
                _last_request=start
                trace={'provider':'aliyun','api':'TranslateGeneral','admission_id':admission['id'],'input_characters':units(wire_text),'status':'attempted','attempt':attempt,
                       'input_mode':'protected' if active_markers else 'original'};self.calls.append(trace)
                try:
                    response=sdk.translate_general_with_options(TranslateGeneralRequest(format_type='text',scene='general',source_language='en',target_language='zh',source_text=wire_text),RuntimeOptions(autoretry=False,max_attempts=1,connect_timeout=5000,read_timeout=20000))
                    body=response.body.to_map() if hasattr(response.body,'to_map') else response.body
                    if not isinstance(body,dict) or str(body.get('Code'))!='200':raise mapped_error(body.get('Code') if isinstance(body,dict) else 'invalid',body.get('RequestId') if isinstance(body,dict) else None)
                    translated=(body.get('Data') or {}).get('Translated')
                    if not isinstance(translated,str) or not translated.strip():raise mapped_error('invalid_translation',body.get('RequestId'))
                    request_id=body.get('RequestId');request_id=request_id if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9-]{1,128}',request_id) and request_id not in (self.config.access_key_id,self.config.access_key_secret) else None
                    trace.update(status='returned',elapsed_ms=round((time.monotonic()-start)*1000),request_id=request_id)
                    result=restore(translated,active_markers)
                    self.store.add('aliyun_usage_result',self.doc['document_version_id'],{'admission_id':admission['id'],'provider':'aliyun','period':admission['period'],'characters':units(wire_text),'status':'success','request_id':request_id})
                    cached=self.store.add('aliyun_chunk',self.doc['document_version_id'],{'provider':'aliyun','source_hash':sha(prepared['text']),'translation':result,'status':'complete','characters':units(wire_text),'api':'TranslateGeneral','api_version':'2018-10-12','source_language':'en','target_language':'zh','request_id':request_id},chunk['cache_key'])
                    outputs.append(cached['translation']);break
                except sqlite3.Error:raise CopilotError('translation_storage','远端处理后缓存写入失败，请检查本地存储；未自动重复调用。','storage') from None
                except Exception as exc:
                    from .aliyun_network import transport_error
                    error=transport_error(exc)
                    trace.update(status='failed',code=getattr(error,'vendor_code',error.code),elapsed_ms=round((time.monotonic()-start)*1000))
                    if getattr(error,'failure_layer',None):trace.update(failure_layer=error.failure_layer,cause_type=getattr(error,'cause_type',None))
                    if error.code=='aliyun_protection' and active_markers:
                        # A machine translator may alter artificial tokens. Retry this
                        # fragment once as ordinary source text, never guess a token's value.
                        wire_text=restore(chunk['text'],chunk['markers']);active_markers={};continue
                    if error.retryable and temporary_retries<self.max_retries:
                        temporary_retries+=1;self.sleep(0.4*temporary_retries);continue
                    raise error from None
        return {'translation':''.join(outputs),'input_text':prepared['text'],'input_normalizer':VERSION,'provider':'aliyun','engine':'Aliyun TranslateGeneral','terminology_notes':['阿里云通用版机器译文；公式、术语、引用与限定条件仍需对照原文核查。'],'usage':{'input_characters':sum(c['input_characters'] for c in self.calls[call_start:]),'unit':'characters'},'cost_kind':'local_monthly_character_guard','cost_amount':None}
