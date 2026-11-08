"""Native NMT adapters. No OpenAI emulation, redirects, retries or paid fallback."""
import hashlib,json,os,socket,uuid,html,time,threading
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit,urlencode
from urllib.request import Request,build_opener,HTTPRedirectHandler,ProxyHandler
from urllib.error import HTTPError,URLError
from .errors import CopilotError
from .repository import now

ADAPTER_VERSION='native-nmt-v2-prose'
_baidu_lock=threading.Lock()
_baidu_last=0.0
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def request_json(url,payload=None,headers=None):
    req=Request(url,data=payload if isinstance(payload,bytes) else json.dumps(payload).encode() if payload is not None else None,
                headers={'Content-Type':'application/json','Accept':'application/json',**(headers or {})})
    # Loopback must not be sent through a machine-wide web proxy.
    handlers=[NoRedirect()]+([ProxyHandler({})] if urlsplit(url).hostname in ('127.0.0.1','localhost','::1') else [])
    try:
        with build_opener(*handlers).open(req,timeout=20) as response:
            raw=response.read(200001)
        if len(raw)>200000:raise ValueError('size')
        obj=json.loads(raw)
        if not isinstance(obj,(dict,list)):raise ValueError('shape')
        return obj
    except HTTPError as e:
        code='provider_auth' if e.code in (401,403) else 'provider_quota' if e.code in (429,456) else 'provider_http'
        if e.code==403:
            try:
                body=json.loads(e.read(4000));reason=str(body.get('error',{})).lower()
                if any(word in reason for word in ('limitexceeded','quotaexceeded','resource_exhausted')):code='provider_quota'
            except (ValueError,OSError):pass
        message={'provider_auth':'服务商认证失败；检查服务端配置。','provider_quota':'服务商额度或速率限制已触发；没有切换引擎。','provider_http':'服务商API异常或重定向已被拒绝；没有自动重试。'}[code]
        raise CopilotError(code,message,'translation') from None
    except (TimeoutError,socket.timeout):raise CopilotError('provider_timeout','翻译网络超时；本次尝试已记录，没有自动重试。','translation') from None
    except URLError as e:
        if isinstance(e.reason,(TimeoutError,socket.timeout)):raise CopilotError('provider_timeout','翻译网络超时；没有自动重试。','translation') from None
        raise CopilotError('provider_network','无法连接翻译服务；本地引擎请先启动，远程服务请检查网络。','translation') from None
    except (ValueError,UnicodeError):raise CopilotError('provider_response','服务商响应不是有效译文。','translation') from None

def file_config(provider,names,root=None,*,use_saved=True):
    from .api_settings import APISettings,isolated
    if root and use_saved:
        saved=APISettings(root).profile('translation',provider)
        if saved:
            mapping={'baidu':{'RC_BAIDU_APP_ID':saved.get('app_id',''),'RC_BAIDU_SECRET_KEY':saved.get('secret_key',''),'RC_BAIDU_PLAN':'standard'},'google':{'RC_GOOGLE_TRANSLATE_API_KEY':saved.get('api_key','')}}.get(provider,{})
            mapping.update({f'RC_{provider.upper()}_FREE_TIER_MONTH':datetime.now(timezone.utc).strftime('%Y-%m'),f'RC_{provider.upper()}_FREE_TIER_CHAR_BUDGET':'1000000000000'})
            return {k:mapping.get(k,'') for k in names}
    if root and isolated(root):return {k:'' for k in names}
    values={};path=Path(root).resolve().parent/('.env.'+provider) if root else Path(__file__).resolve().parents[1]/('.env.'+provider)
    try:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);values[k.strip()]=v.strip().strip('\"\'')
    except OSError:pass
    return {k:os.environ.get(k,values.get(k,'')) for k in names}

def google_config(root=None):return file_config('google',('RC_GOOGLE_TRANSLATE_API_KEY','RC_GOOGLE_FREE_TIER_MONTH','RC_GOOGLE_FREE_TIER_CHAR_BUDGET'),root)
def baidu_config(root=None):return file_config('baidu',('RC_BAIDU_APP_ID','RC_BAIDU_SECRET_KEY','RC_BAIDU_PLAN','RC_BAIDU_FREE_TIER_MONTH','RC_BAIDU_FREE_TIER_CHAR_BUDGET'),root)
def baidu_signature(appid,text,salt,secret):return hashlib.md5((appid+text+salt+secret).encode()).hexdigest()

def settings(provider,root=None):
    from .api_settings import APISettings
    saved=APISettings(root).profile('translation',provider) if root else None
    if provider in ('deepl','azure'):
        saved=saved or {};ready=bool(saved.get('api_key') and (provider!='azure' or saved.get('region')))
        return {'id':provider,'label':'DeepL' if provider=='deepl' else 'Microsoft Azure Translator','configured':ready,'credentials_present':ready,'local':False,
                'reason':None if ready else '请在接口设置中填写翻译服务凭证。','url':saved.get('base_url','https://api-free.deepl.com/v2') if provider=='deepl' else 'https://api.cognitive.microsofttranslator.com',
                'cost_kind':'provider_characters','monthly_character_cap':10**12,'allowance_month':datetime.now(timezone.utc).strftime('%Y-%m'),'config_revision':provider+'-v1',
                'context_supported':False,'glossary_supported':False,'connection_verified':False,'auto_translate':False}
    if provider=='baidu':
        cfg=baidu_config(root);month=datetime.now(timezone.utc).strftime('%Y-%m');tier=cfg['RC_BAIDU_PLAN'] or 'standard';maximum=1000000 if tier=='advanced' else 50000
        try:cap=int(cfg['RC_BAIDU_FREE_TIER_CHAR_BUDGET'])
        except ValueError:cap=0
        credentials=bool(cfg['RC_BAIDU_APP_ID'] and cfg['RC_BAIDU_SECRET_KEY']);allowance=bool(saved) or cfg['RC_BAIDU_FREE_TIER_MONTH']==month and 0<cap<=maximum and tier in ('standard','advanced')
        reason='请在本机 .env.baidu 配置百度翻译开放平台的APPID和密钥。' if not credentials else '请确认套餐、本月免费余量及本应用字符预算；没有发送请求。' if not allowance else None
        return {'id':provider,'label':'百度翻译 · 官方通用API','configured':credentials and allowance,'credentials_present':credentials,'local':False,'reason':reason,
            'url':'https://fanyi-api.baidu.com/api/trans/vip/translate','cost_kind':'operator_confirmed_baidu_free_allowance','monthly_character_cap':cap if allowance else 0,
            'allowance_month':month,'config_revision':'baidu-general-v1-'+tier,'plan':tier,'request_character_limit':6000 if tier=='advanced' else 1000,
            'context_supported':False,'glossary_supported':False,'connection_verified':False,'auto_translate':False,'external_free_balance_verified':False}
    if provider=='google':
        cfg=google_config(root);month=datetime.now(timezone.utc).strftime('%Y-%m')
        try:cap=int(cfg['RC_GOOGLE_FREE_TIER_CHAR_BUDGET'])
        except ValueError:cap=0
        allowance=bool(saved) or cfg['RC_GOOGLE_FREE_TIER_MONTH']==month and 0<cap<=500000
        configured=bool(cfg['RC_GOOGLE_TRANSLATE_API_KEY'] and allowance)
        reason='Google Cloud API key尚未在本机配置；完成结算与API启用后填写 .env.google。' if not cfg['RC_GOOGLE_TRANSLATE_API_KEY'] else '尚未确认本月项目剩余免费额度及本应用字符上限；没有发送请求。' if not allowance else None
        return {'id':provider,'label':'Google Cloud · 官方NMT','configured':configured,'credentials_present':bool(cfg['RC_GOOGLE_TRANSLATE_API_KEY']),
            'local':False,'reason':reason,'url':'https://translation.googleapis.com/language/translate/v2',
            'cost_kind':'operator_confirmed_google_free_allowance','monthly_character_cap':cap if allowance else 0,'allowance_month':month,
            'config_revision':'google-cloud-v2-standard-nmt','context_supported':False,'glossary_supported':False,
            'connection_verified':False,'auto_translate':False,'billing_required':True,'external_free_balance_verified':False}
    if provider=='argos':
        from .local_translation import profile,health
        p=profile(root);ready=health(p['port']) if p else None
        valid=bool(p and ready and ready.get('model_sha256')==p['model_sha256'])
        return {'id':provider,'label':'本地免费翻译 · Argos','configured':valid,'local':True,'managed':True,
            'reason':None if valid else '本地免费翻译未就绪，请运行安装脚本并重启工作台。',
            'url':f'http://127.0.0.1:{p["port"]}' if p else '', 'cost_kind':'local_no_api_fee','daily_character_cap':200000,
            'config_revision':p['model_sha256'] if p else 'not-installed','context_supported':False,'glossary_supported':False,
            'connection_verified':valid,'auto_translate':bool(valid and p.get('auto_translate'))}
    if provider=='libretranslate':
        url=os.environ.get('RC_LIBRETRANSLATE_URL','').rstrip('/')
        parsed=urlsplit(url)
        valid=bool(url and parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1') and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path in ('','/'))
        return {'id':provider,'label':'LibreTranslate · 本地基础翻译','configured':valid,'local':True,
            'reason':None if valid else '未配置本地引擎：设置 RC_LIBRETRANSLATE_URL=http://127.0.0.1:5000 并安装 en→zh 模型。仅接受本机地址。',
            'url':url if valid else '', 'cost_kind':'local_no_api_fee','daily_character_cap':50000,
            'config_revision':os.environ.get('RC_LIBRETRANSLATE_MODEL_REVISION','operator-unspecified'),
            'context_supported':False,'glossary_supported':False}
    if provider=='mymemory':
        enabled=os.environ.get('RC_MYMEMORY_ENABLED')=='1'
        return {'id':provider,'label':'MyMemory · 公开短段落备选','configured':enabled,'local':False,
            'reason':None if enabled else '未启用；阅读隐私条款后在服务端设置 RC_MYMEMORY_ENABLED=1，并单独确认本论文发送授权。',
            'url':'https://api.mymemory.translated.net','cost_kind':'anonymous_free_quota','daily_character_cap':5000,
            'config_revision':'anonymous-get-500bytes','context_supported':False,'glossary_supported':False}
    raise CopilotError('provider_unknown','未知翻译Provider；未发送给任何服务。','translation')

def provider_status(paid=False,store=None):
    from .llm import LLMConfig
    try:
        from .api_settings import APISettings
        profile=APISettings(store.path.parent).llm() if store else None
        deepseek_configured=bool(profile)
    except CopilotError:deepseek_configured=False
    rows=[]
    for provider in ('baidu','google','deepl','azure','argos','libretranslate','mymemory'):
        config=settings(provider,store.path.parent if store else None)
        if store:
            date=datetime.now(timezone.utc).date().isoformat()
            used=sum(r['characters'] for r in store.records('translation_admission') if r['provider']==provider and (r.get('period')==config['allowance_month'] if provider in ('google','baidu','deepl','azure') else r['date_utc']==date))
            cap=config['monthly_character_cap'] if provider in ('google','baidu','deepl','azure') else config['daily_character_cap']
            config.update(local_reserved_characters=used,local_remaining_characters=max(0,cap-used))
        rows.append({'connection_verified':False}|{k:v for k,v in config.items() if k!='url'})
    if store:
        from .aliyun_translation import status
        rows.insert(0,status(store))
    from .api_settings import APISettings
    preferred=APISettings(store.path.parent).read()['translation_provider'] if store and APISettings(store.path.parent).path.exists() else ''
    if store and not preferred:
        try:preferred=json.loads((store.path.parent/'translation_preference.json').read_text(encoding='utf-8')).get('provider','')
        except (OSError,ValueError):pass
    return {'providers':[{'id':'mock','label':'Mock · 本地交互演示','configured':True,'local':True,'cost_kind':'mock'},*rows,
        {'id':'deepseek','label':'当前大模型 · 学术精译','configured':deepseek_configured,'enabled':deepseek_configured,'local':False,
         'reason':None if deepseek_configured else '请先配置大模型接口。','cost_kind':'paid_tokens'}],
        'default':preferred if preferred in ('aliyun','baidu','google','deepl','azure') else 'aliyun' if any(p['id']=='aliyun' and p['configured'] for p in rows) else 'baidu' if any(p['id']=='baidu' and p['configured'] for p in rows) else 'google' if any(p['id']=='google' and p['configured'] for p in rows) else 'argos' if any(p['id']=='argos' and p['configured'] for p in rows) else 'mock','automatic_fallback':False,'notice':'本地额度不代表服务商实时余额；超限拒绝发送，不自动换用其他引擎。'}

class NativeProvider:
    is_test_double=False
    def __init__(self,provider,store,doc,consent=False,consent_provider=None,transport=None):
        self.config_data=settings(provider,store.path.parent);self.provider=provider;self.store=store;self.doc=doc
        self.consent=consent is True and consent_provider==provider;self.calls=[];self.transport=transport or request_json
        # No raw credential or endpoint is exposed to the browser/cache record.
        self.model_id='native-'+provider
    def public_info(self):
        c=self.config_data
        from .prose import VERSION
        return {'provider':self.provider,'engine':'DeepL Text Translation' if self.provider=='deepl' else 'Azure Translator v3' if self.provider=='azure' else 'Baidu General Translation API' if self.provider=='baidu' else 'Google Cloud Translation Basic v2 standard NMT' if self.provider=='google' else 'Argos/CTranslate2 CPU' if self.provider=='argos' else 'Argos/LibreTranslate NMT' if c['local'] else 'MyMemory TM/MT',
            'adapter':ADAPTER_VERSION,'config_revision':c['config_revision'],
            'input_normalizer':VERSION,
            'endpoint_fingerprint':hashlib.sha256(c['url'].encode()).hexdigest(),
            'context_supported':False,'glossary_supported':False,'cost_kind':c['cost_kind']}
    def reserve(self,count):
        date=datetime.now(timezone.utc).date().isoformat();period=date[:7] if self.provider in ('google','baidu','deepl','azure') else date
        cap=self.config_data['monthly_character_cap'] if self.provider in ('google','baidu','deepl','azure') else self.config_data['daily_character_cap']
        # Atomic across workbench processes; append-only, failures stay reserved.
        with self.store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            rows=c.execute("SELECT payload FROM records WHERE kind='translation_admission'").fetchall()
            used=sum(x['characters'] for r in rows if (x:=json.loads(r[0]))['provider']==self.provider and (x.get('period')==period if self.provider in ('google','baidu','deepl','azure') else x['date_utc']==date))
            if used+count>cap:raise CopilotError('provider_quota','本机今日处理字符预算已用完，稍后再试；不会切换到收费引擎。' if self.provider=='argos' else '本地字符预算不足；服务商实际剩余额度可能更低。不会切换到收费引擎。','translation')
            item={'id':uuid.uuid4().hex,'kind':'translation_admission','created_at':now(),'provider':self.provider,'date_utc':date,
                'document_version_id':self.doc['document_version_id'],'file_sha256':self.doc['file_sha256'],'characters':count,'cap':cap,'period':period,'status':'attempt_reserved'}
            if getattr(self,'budget_scope',None):item['task_id']=self.budget_scope['id']
            c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(item['id'],item['kind'],item['document_version_id'],None,item['created_at'],json.dumps(item)))
        return item
    def translate(self,text,source_lang,target_lang,context=None,glossary=None):
        if self.provider in ('google','baidu','deepl','azure'):self.config_data=settings(self.provider,self.store.path.parent)
        c=self.config_data
        if not c['configured']:raise CopilotError('provider_unconfigured',c['reason'],'translation')
        from .prose import reading_prose
        original_text=text;text=reading_prose(text)
        if not c['local'] and not self.consent:raise CopilotError('provider_consent','尚未确认当前论文发送给所选'+self.provider+'；其他服务商授权不能复用。','authorization')
        if self.provider=='mymemory' and (len(text.encode('utf-8'))>500 or '\n\n' in text):
            raise CopilotError('provider_length','MyMemory仅支持单段且不超过500 UTF-8字节；请缩小选区，不自动拆分发送。','translation')
        if self.provider=='google' and len(text)>12000:raise CopilotError('provider_length','本段超出整段翻译范围，未截断发送。','translation')
        if self.provider=='baidu':return self.translate_baidu(text,source_lang,target_lang)
        reservation=self.reserve(len(text));trace={'provider':self.provider,'input_characters':len(text),'usage_unit':'characters','status':'attempted','admission_id':reservation['id'],
            'source_consent':'local_only' if c['local'] else 'explicit_current_paper_provider','file_sha256':self.doc['file_sha256']};self.calls.append(trace)
        if self.provider=='google':
            cfg=google_config(self.store.path.parent)
            result=self.transport(c['url'],{'q':text,'source':source_lang,'target':'zh-CN' if target_lang.startswith('zh') else target_lang,'format':'text','model':'nmt'},
                headers={'X-goog-api-key':cfg['RC_GOOGLE_TRANSLATE_API_KEY']})
            items=(result.get('data') or {}).get('translations') or []
            translation=html.unescape(items[0]['translatedText']) if len(items)==1 and isinstance(items[0].get('translatedText'),str) else None
        elif self.provider in ('deepl','azure'):
            from .api_settings import APISettings
            cfg=APISettings(self.store.path.parent).profile('translation',self.provider)
            if self.provider=='deepl':
                result=self.transport(c['url']+'/translate',{'text':[text],'source_lang':source_lang.upper(),'target_lang':'ZH' if target_lang.startswith('zh') else target_lang.upper(),'show_billed_characters':True},headers={'Authorization':'DeepL-Auth-Key '+cfg['api_key']})
                items=result.get('translations') or []
                translation=items[0].get('text') if len(items)==1 else None
                if items:trace['provider_billed_characters']=items[0].get('billed_characters')
            else:
                result=self.transport(c['url']+'/translate?api-version=3.0&'+urlencode({'from':source_lang,'to':'zh-Hans' if target_lang.startswith('zh') else target_lang}),[{'Text':text}],headers={'Ocp-Apim-Subscription-Key':cfg['api_key'],'Ocp-Apim-Subscription-Region':cfg['region']})
                items=result[0].get('translations',[]) if isinstance(result,list) and len(result)==1 else []
                translation=items[0].get('text') if len(items)==1 else None
        elif self.provider in ('libretranslate','argos'):
            payload={'q':text,'source':source_lang,'target':'zh' if target_lang.startswith('zh') else target_lang,'format':'text'}
            key=os.environ.get('RC_LIBRETRANSLATE_API_KEY')
            if key:payload['api_key']=key
            result=self.transport(c['url']+'/translate',payload);translation=result.get('translatedText')
        else:
            result=self.transport(c['url']+'/get?'+urlencode({'q':text,'langpair':source_lang+'|'+target_lang,'mt':'1'}))
            status=str(result.get('responseStatus',''))
            if result.get('quotaFinished') or status in ('403','429'):raise CopilotError('provider_quota','MyMemory额度耗尽；没有自动切换服务。','translation')
            if status!='200':raise CopilotError('provider_response','MyMemory未返回成功译文；没有自动重试。','translation')
            translation=(result.get('responseData') or {}).get('translatedText')
        if not isinstance(translation,str) or not 1<=len(translation.strip())<=10000:raise CopilotError('provider_response','服务商没有返回有效文本译文。','translation')
        trace['status']='returned'
        return {'translation':translation,'input_text':text,'input_normalizer':'pdf-prose-v1','terminology_notes':['基础NMT未使用上下文或强制术语表，请核查术语、否定、限定词及公式。'],
            'provider':self.provider,'engine':self.public_info()['engine'],'source_language':source_lang,'target_language':target_lang,
            'usage':{'input_characters':len(text),'unit':'characters','provider_reported_usage':None},'cost_kind':c['cost_kind'],'cost_amount':None}

    def translate_baidu(self,text,source_lang,target_lang):
        from .prose import request_segments
        global _baidu_last
        c=self.config_data;cfg=baidu_config(self.store.path.parent);pieces=request_segments(text,c['request_character_limit'],6000);outputs=[]
        for piece in pieces:
            reservation=self.reserve(len(piece));trace={'provider':'baidu','input_characters':len(piece),'usage_unit':'characters','status':'attempted','admission_id':reservation['id'],'source_consent':'explicit_current_paper_provider','file_sha256':self.doc['file_sha256']};self.calls.append(trace)
            salt=str(uuid.uuid4().int%2147483647 or 1);sign=baidu_signature(cfg['RC_BAIDU_APP_ID'],piece,salt,cfg['RC_BAIDU_SECRET_KEY'])
            body=urlencode({'q':piece,'from':source_lang,'to':'zh' if target_lang.startswith('zh') else target_lang,'appid':cfg['RC_BAIDU_APP_ID'],'salt':salt,'sign':sign}).encode()
            with _baidu_lock:
                delay=(1.05 if c['plan']=='standard' else 0.12)-(time.monotonic()-_baidu_last)
                if delay>0:time.sleep(delay)
                _baidu_last=time.monotonic()
                result=self.transport(c['url'],body,headers={'Content-Type':'application/x-www-form-urlencoded'})
            code=str(result.get('error_code',''))
            if code and code!='52000':
                kind='provider_auth' if code in ('52003','54001','58000','58002') else 'provider_quota' if code in ('54003','54004') else 'provider_length' if code=='54005' else 'provider_response'
                raise CopilotError(kind,'百度翻译返回错误 '+code+'；请检查凭证、套餐、额度或请求限制。未自动重试。','translation')
            items=result.get('trans_result') or []
            if not items or any(not isinstance(x.get('dst'),str) or not x['dst'].strip() for x in items):raise CopilotError('provider_response','百度未返回有效译文，未当作翻译成功。','translation')
            outputs.append('\n\n'.join(x['dst'] for x in items));trace['status']='returned'
        return {'translation':''.join(outputs),'input_text':text,'input_normalizer':'pdf-prose-v1','terminology_notes':['百度通用机器译文需核查术语、否定、限定条件及公式；不等于学术质量验收。'],
            'provider':'baidu','engine':'DeepL Text Translation' if self.provider=='deepl' else 'Azure Translator v3' if self.provider=='azure' else 'Baidu General Translation API','source_language':source_lang,'target_language':target_lang,
            'usage':{'input_characters':len(text),'unit':'characters','provider_reported_usage':None},'cost_kind':c['cost_kind'],'cost_amount':None}
