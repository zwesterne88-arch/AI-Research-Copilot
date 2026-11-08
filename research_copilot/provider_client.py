"""Configured LLM protocols for intentional workbench actions, with actual token accounting."""
import json
import uuid
from .llm import ChatCompletionsClient, LLMConfig
from .errors import CopilotError


class RequestAccounting:
    def __init__(self, store, version):
        self.store, self.version = store, version

    def reserve(self, input_bytes, output_limit):
        if input_bytes > 500_000:
            raise CopilotError('model_input_size','本次模型输入过长，请缩小问题范围。','input')
        row = self.store.add('model_admission',self.version,{'input_bytes':input_bytes,'output_limit':output_limit})
        return {'admission_id':row['id']}

    def estimate(self, usage):
        return None


class ProviderClient(ChatCompletionsClient):
    is_test_double = False

    def __init__(self, profile, store, doc):
        self.protocol = profile.get('protocol','chat_completions')
        self.store, self.doc = store, doc
        config = LLMConfig(profile['base_url'],profile['model'],profile['api_key'],60,
                           profile.get('max_output_tokens',4000),True,'max_tokens',profile['provider'],self.protocol)
        super().__init__(config,RequestAccounting(store,doc['document_version_id']))

    def wire_request(self, system, user):
        cfg = self.config
        content = json.dumps(user,ensure_ascii=False)
        system += '\nReturn only a valid JSON object, without Markdown fences.'
        if self.protocol=='responses':
            payload = {'model':cfg.model,'instructions':system,'input':content,'max_output_tokens':cfg.max_output_tokens,'store':False}
            return cfg.base_url+'/responses',payload,{'Authorization':'Bearer '+cfg.api_key}
        if self.protocol=='anthropic':
            payload = {'model':cfg.model,'system':system,'messages':[{'role':'user','content':content}],'max_tokens':cfg.max_output_tokens}
            return cfg.base_url+'/messages',payload,{'x-api-key':cfg.api_key,'anthropic-version':'2023-06-01'}
        return super().wire_request(system,user)

    def extra_payload(self):
        if self.config.provider=='deepseek':return {'thinking':{'type':'disabled'}}
        if self.config.provider=='qwen':return {'enable_thinking':False}
        return {}

    def normalize_response(self, obj):
        if self.protocol=='chat_completions':return obj
        usage = obj.get('usage') or {}
        if self.protocol=='responses':
            text = ''.join(c.get('text','') for item in obj.get('output',[]) if item.get('type')=='message'
                           for c in item.get('content',[]) if c.get('type')=='output_text')
            complete = obj.get('status')=='completed'
            input_tokens = usage.get('input_tokens',0)
        else:
            text = ''.join(c.get('text','') for c in obj.get('content',[]) if c.get('type')=='text')
            complete = obj.get('stop_reason') in ('end_turn','stop_sequence')
            input_tokens = sum(usage.get(k,0) for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens'))
        output_tokens = usage.get('output_tokens',0)
        return {'model':obj.get('model',self.config.model),'usage':{**usage,'prompt_tokens':input_tokens,'completion_tokens':output_tokens,'total_tokens':input_tokens+output_tokens},
                'choices':[{'finish_reason':'stop' if complete else 'length','message':{'content':text}}]}

    def complete_json(self, stage, system, user):
        start = len(self.calls)
        try:
            return super().complete_json(stage,system,user)
        finally:
            for trace in self.calls[start:]:
                trace['provider'] = self.config.provider
                trace['protocol'] = self.protocol
                trace['usage_record_id'] = uuid.uuid4().hex
                self.store.add('model_usage',self.doc['document_version_id'],dict(trace))
