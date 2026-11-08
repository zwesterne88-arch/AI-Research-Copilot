"""Translation adapter for the shared resumable reading worker."""
from .translation_providers import NativeProvider
from .prose import reading_prose,request_segments
from .aliyun_translation import sha
from .errors import CopilotError


class ReadingNativeProvider(NativeProvider):
    def preview(self,text,glossary=None):
        text=reading_prose(text)
        chunks=[{'text':part,'characters':len(part),'cached':None,'cache_key':sha([self.public_info(),part])} for part in request_segments(text,3000,100000)]
        return {'original_characters':len(text),'characters':len(text),'new_characters':len(text),'cache_saved_characters':0,'chunks':chunks}

    def translate(self,text,source_lang,target_lang,context=None,glossary=None):
        prepared=self.preview(text,glossary);results=[]
        for chunk in prepared['chunks']:
            if getattr(self,'cancel_event',None) and self.cancel_event.is_set():raise CopilotError('translation_stopped','翻译已暂停。','translation')
            results.append(super().translate(chunk['text'],source_lang,target_lang,context,glossary))
        if not results:raise CopilotError('translation_empty','没有可翻译的正文。','translation')
        return {**results[-1],'translation':'\n\n'.join(r['translation'] for r in results),'input_text':reading_prose(text),'usage':{'input_characters':prepared['characters'],'unit':'characters'}}


def configured_provider(settings,store,doc,consent):
    provider=settings.read()['translation_provider']
    if provider=='aliyun':
        from .aliyun_translation import AliyunProvider
        return AliyunProvider(store,doc,consent,'aliyun')
    return ReadingNativeProvider(provider,store,doc,consent,provider)
