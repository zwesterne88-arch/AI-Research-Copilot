"""Per-workspace provider registry and encrypted credentials. Public views never contain keys."""
import base64
import ctypes
import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from .errors import CopilotError

LOCK = threading.RLock()
LLM_PROVIDERS = [
    {'id':'deepseek', 'label':'DeepSeek', 'protocol':'chat_completions', 'base_url':'https://api.deepseek.com', 'model':'deepseek-flash'},
    {'id':'openai', 'label':'OpenAI / Codex', 'protocol':'responses', 'base_url':'https://api.openai.com/v1', 'model':''},
    {'id':'anthropic', 'label':'Claude · Anthropic', 'protocol':'anthropic', 'base_url':'https://api.anthropic.com/v1', 'model':''},
    {'id':'qwen', 'label':'Qwen · 阿里云百炼', 'protocol':'chat_completions', 'base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1', 'model':'qwen-plus'},
    {'id':'xai', 'label':'Grok · xAI', 'protocol':'chat_completions', 'base_url':'https://api.x.ai/v1', 'model':''},
    {'id':'openai_compatible', 'label':'自定义兼容接口', 'protocol':'chat_completions', 'base_url':'', 'model':''},
]
TRANSLATION_PROVIDERS = [
    {'id':'aliyun','label':'阿里云机器翻译','fields':[('access_key_id','AccessKey ID',True),('access_key_secret','AccessKey Secret',True)]},
    {'id':'baidu','label':'百度翻译','fields':[('app_id','APP ID',False),('secret_key','密钥',True)]},
    {'id':'google','label':'Google Cloud Translation','fields':[('api_key','API Key',True)]},
    {'id':'deepl','label':'DeepL','fields':[('api_key','API Key',True),('base_url','API 地址',False)],'base_url':'https://api-free.deepl.com/v2'},
    {'id':'azure','label':'Microsoft Azure Translator','fields':[('api_key','API Key',True),('region','资源区域',False)],'base_url':'https://api.cognitive.microsofttranslator.com'},
]
SECRETS = {'api_key','access_key_id','access_key_secret','secret_key'}


def isolated(root):
    return (Path(root) / '.isolated-account').exists()


def _dpapi(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_char))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise CopilotError('credentials_locked','无法读取本机保存的密钥，请使用保存密钥时的系统账号。','configuration')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def _atomic(path, data):
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with open(temp, 'xb') as stream:
            os.chmod(temp, 0o600)
            stream.write(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class APISettings:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'api_settings.enc'

    def _cipher(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / '.api_settings.key'
        with LOCK:
            if not path.exists():
                key = Fernet.generate_key()
                _atomic(path, b'win:'+base64.b64encode(_dpapi(key)) if os.name=='nt' else b'raw:'+key)
            stored = path.read_bytes()
        key = _dpapi(base64.b64decode(stored[4:]), True) if stored.startswith(b'win:') else stored[4:]
        return Fernet(key)

    def read(self):
        if not self.path.exists():
            return {'llm':{}, 'translation':{}, 'llm_provider':'deepseek', 'translation_provider':'aliyun'}
        try:
            return json.loads(self._cipher().decrypt(self.path.read_bytes()))
        except CopilotError:
            raise
        except Exception:
            raise CopilotError('credentials_locked','接口配置无法解密，请检查运行账号和本机密钥文件。','configuration') from None

    def profile(self, kind, provider):
        return self.read().get(kind, {}).get(provider)

    def legacy_llm(self):
        if isolated(self.root):
            return None
        from .llm import LLMConfig
        try:
            config = LLMConfig.from_environment(self.root.parent)
            return {'provider':config.provider, 'base_url':config.base_url, 'model':config.model,
                    'api_key':config.api_key, 'protocol':'chat_completions', 'max_output_tokens':config.max_output_tokens}
        except CopilotError:
            return None

    def llm(self):
        data = self.read(); provider = data['llm_provider']
        profile = data['llm'].get(provider)
        if profile is None:
            legacy = self.legacy_llm()
            if legacy and legacy['provider']==provider:
                profile = legacy
        if not profile or not profile.get('api_key') or not profile.get('model'):
            raise CopilotError('llm_not_configured','请在接口设置中填写大模型的 API Key 和模型名称。','configuration')
        return {**profile, 'provider':provider}

    def save(self, data):
        kind, provider = data.get('kind'), data.get('provider')
        catalog = LLM_PROVIDERS if kind=='llm' else TRANSLATION_PROVIDERS if kind=='translation' else []
        preset = next((p for p in catalog if p['id']==provider), None)
        if not preset:
            raise CopilotError('provider_unknown','请选择列表中的接口类型。','configuration')
        values = data.get('values')
        if not isinstance(values, dict):
            raise CopilotError('settings_invalid','请填写接口配置。','configuration')
        with LOCK:
            saved = self.read()
            previous = saved[kind].get(provider, {})
            if kind=='llm' and not previous:
                legacy = self.legacy_llm()
                previous = legacy if legacy and legacy['provider']==provider else {}
            if kind=='translation' and not previous:
                previous = self.legacy_translation(provider)
            fields = ['api_key','model','base_url','protocol'] if kind=='llm' else [f[0] for f in preset['fields']]
            profile = dict(previous)
            for key in fields:
                value = values.get(key)
                if value is None or key in SECRETS and value=='':
                    continue
                if not isinstance(value,str) or len(value)>4000 or '\n' in value or '\r' in value:
                    raise CopilotError('settings_invalid','接口配置格式不正确。','configuration')
                profile[key] = value.strip()
            if kind=='llm':
                profile.setdefault('base_url',preset['base_url'])
                profile.setdefault('protocol',preset['protocol'])
                profile.setdefault('model',preset['model'])
                if profile['protocol'] not in ('responses','chat_completions','anthropic'):
                    raise CopilotError('settings_invalid','不支持的接口协议。','configuration')
                if not profile.get('model') or not profile.get('api_key'):
                    raise CopilotError('settings_invalid','请填写模型名称和 API Key。','configuration')
                profile['max_output_tokens'] = 4000
            else:
                if any(not profile.get(f[0]) for f in preset['fields'] if f[0]!='base_url'):
                    raise CopilotError('settings_invalid','请填全所选翻译服务的凭证。','configuration')
                if provider=='deepl':profile.setdefault('base_url',preset['base_url'])
            if profile.get('base_url'):
                parsed = urlsplit(profile['base_url'])
                if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise CopilotError('settings_endpoint','API 地址需为完整 HTTPS 根地址。','configuration')
                profile['base_url'] = profile['base_url'].rstrip('/')
            saved[kind][provider] = profile
            saved[kind+'_provider'] = provider
            _atomic(self.path, self._cipher().encrypt(json.dumps(saved,ensure_ascii=False).encode()))
        return self.public()

    def legacy_translation(self, provider):
        if isolated(self.root):return {}
        if provider=='aliyun':
            from .aliyun_translation import configuration
            cfg=configuration(self.root, use_saved=False)
            return {'access_key_id':cfg.access_key_id,'access_key_secret':cfg.access_key_secret}
        if provider in ('baidu','google'):
            from .translation_providers import file_config
            names = {'baidu':{'app_id':'RC_BAIDU_APP_ID','secret_key':'RC_BAIDU_SECRET_KEY'},'google':{'api_key':'RC_GOOGLE_TRANSLATE_API_KEY'}}[provider]
            cfg=file_config(provider,tuple(names.values()),self.root,use_saved=False)
            return {key:cfg[name] for key,name in names.items()}
        return {}

    def public(self):
        data = self.read()
        result = {'llm_provider':data['llm_provider'],'translation_provider':data['translation_provider'],'llm':[],'translation':[]}
        legacy = self.legacy_llm()
        for kind,catalog in (('llm',LLM_PROVIDERS),('translation',TRANSLATION_PROVIDERS)):
            for preset in catalog:
                provider = preset['id']
                values = data[kind].get(provider)
                if values is None:
                    values = (legacy if legacy and legacy['provider']==provider else {}) if kind=='llm' else self.legacy_translation(provider)
                public = {k:v for k,v in values.items() if k not in SECRETS and k!='provider'}
                keys = ['api_key'] if kind=='llm' else [f[0] for f in preset['fields'] if f[2]]
                credentials = {k:bool(values.get(k)) for k in keys}
                configured = all(credentials.values()) and bool(values.get('model')) if kind=='llm' else all(bool(values.get(f[0])) for f in preset['fields'] if f[0]!='base_url')
                result[kind].append({**preset,'values':public,'credentials':credentials,'configured':configured})
        return result
