import json
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from urllib.request import build_opener,HTTPCookieProcessor,Request
from urllib.error import HTTPError

import pytest
from research_copilot.accounts import Accounts
from research_copilot.api_settings import APISettings
from research_copilot.errors import CopilotError
from research_copilot.provider_client import ProviderClient
from research_copilot.workspace import Workspace
from research_copilot.web import make_server,Workbench
from research_copilot.usage_report import report


def test_password_sessions_revocation_and_workspace_ownership(tmp_path):
    accounts=Accounts(tmp_path)
    owner,token=accounts.register('Owner','a-good-test-password')
    second,other=accounts.register('Second','a-good-test-password')
    assert accounts.workspace(owner)==tmp_path
    assert accounts.workspace(second)!=tmp_path
    assert (accounts.workspace(second)/'.isolated-account').exists()
    raw=accounts.path.read_bytes()
    assert b'a-good-test-password' not in raw and token.encode() not in raw
    with pytest.raises(CopilotError):accounts.register('owner','a-good-test-password')
    with pytest.raises(CopilotError):accounts.login('Owner','incorrect')
    user,new_token=accounts.login('OWNER','a-good-test-password')
    session=accounts.resolve(Accounts.cookie(new_token));assert user['id']==owner['id']==session['id']
    accounts.logout(session);assert accounts.resolve(Accounts.cookie(new_token)) is None
    assert accounts.resolve(Accounts.cookie(other))['id']==second['id']


def test_credentials_encrypted_masked_and_reloaded(service):
    settings=APISettings(service.root)
    result=settings.save({'kind':'llm','provider':'deepseek','values':{'api_key':'synthetic-secret-key','model':'deepseek-flash'}})
    assert 'synthetic-secret-key' not in json.dumps(result)
    assert b'synthetic-secret-key' not in settings.path.read_bytes()
    assert APISettings(service.root).llm()['api_key']=='synthetic-secret-key'
    settings.save({'kind':'llm','provider':'deepseek','values':{'api_key':'','model':'another-model'}})
    assert settings.llm()['model']=='another-model' and settings.llm()['api_key']=='synthetic-secret-key'
    settings.save({'kind':'translation','provider':'aliyun','values':{'access_key_id':'fake-id','access_key_secret':'fake-secret'}})
    from research_copilot.aliyun_translation import configuration
    assert configuration(service.root).access_key_secret=='fake-secret'
    assert 'fake-secret' not in json.dumps(settings.public())
    for url in ('http://example.test','https://user:password@example.test','https://example.test/?key=secret'):
        with pytest.raises(CopilotError):settings.save({'kind':'llm','provider':'deepseek','values':{'base_url':url}})


def test_new_account_never_inherits_server_credentials(tmp_path,monkeypatch):
    accounts=Accounts(tmp_path);accounts.register('owner','test-password');user,_=accounts.register('reader','test-password')
    root=accounts.workspace(user)
    monkeypatch.setenv('ALIBABA_CLOUD_ACCESS_KEY_ID','GLOBAL-KEY')
    monkeypatch.setenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET','GLOBAL-SECRET')
    monkeypatch.setenv('RC_GOOGLE_TRANSLATE_API_KEY','GLOBAL-GOOGLE')
    from research_copilot.aliyun_translation import configuration
    from research_copilot.translation_providers import google_config
    assert not configuration(root).configured
    assert not google_config(root)['RC_GOOGLE_TRANSLATE_API_KEY']
    with pytest.raises(CopilotError):APISettings(root).llm()


def test_http_accounts_scope_csrf_logout_and_saved_settings(service,pdf_bytes):
    doc=service.import_pdf(pdf_bytes(),'OWNER.pdf')['data']
    server=make_server(service.root,0,require_login=True);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def browser():return build_opener(HTTPCookieProcessor(CookieJar()))
    def get(client,path):return json.load(client.open(base+path))
    def post(client,path,data,token):return json.load(client.open(Request(base+path,json.dumps(data).encode(),{'Content-Type':'application/json','X-Workspace-Token':token})))
    first=browser();second=browser()
    try:
        anonymous=get(first,'/api/bootstrap');assert anonymous['login_required'] and anonymous['user'] is None
        with pytest.raises(HTTPError) as exc:get(first,'/api/library')
        assert exc.value.code==401
        post(first,'/api/auth/register',{'username':'owner','password':'test-password'},anonymous['token'])
        boot=get(first,'/api/bootstrap');assert boot['user']['owner']
        assert len(get(first,'/api/library')['documents'])==1
        with pytest.raises(HTTPError):post(first,'/api/settings',{},anonymous['token'])
        post(first,'/api/settings',{'kind':'llm','provider':'deepseek','values':{'api_key':'fake-secret','model':'deepseek-flash'}},boot['token'])
        assert 'fake-secret' not in json.dumps(get(first,'/api/settings'))
        post(second,'/api/auth/register',{'username':'second','password':'test-password'},get(second,'/api/bootstrap')['token'])
        assert get(second,'/api/library')['documents']==[]
        with pytest.raises(HTTPError):get(second,'/api/paper?version='+doc['document_version_id'])
        assert get(second,'/api/usage')['totals']['requests']==0
        assert not any(p['configured'] for p in get(second,'/api/settings')['llm'])
        post(first,'/api/auth/logout',{},boot['token'])
        with pytest.raises(HTTPError):get(first,'/api/library')
        assert get(second,'/api/library')['documents']==[]
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('protocol',['chat_completions','responses','anthropic'])
def test_real_http_protocol_shapes_and_exact_token_accounting(service,pdf_bytes,protocol):
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));observed.append((self.path,dict(self.headers),body))
            content=json.dumps({'answer':'synthetic'})
            if protocol=='chat_completions':obj={'choices':[{'message':{'content':content},'finish_reason':'stop'}],'usage':{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18}}
            elif protocol=='responses':obj={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':content}]}],'usage':{'input_tokens':11,'output_tokens':7}}
            else:obj={'stop_reason':'end_turn','content':[{'type':'text','text':content}],'usage':{'input_tokens':6,'cache_read_input_tokens':5,'output_tokens':7}}
            raw=json.dumps(obj).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    doc=service.import_pdf(pdf_bytes(),'SYNTHETIC.pdf')['data'];store=Workspace(service.root)
    try:
        profile={'provider':'test','model':'synthetic-model','api_key':'synthetic-key','protocol':protocol,'base_url':'http://127.0.0.1:'+str(server.server_port)+'/v1'}
        client=ProviderClient(profile,store,doc);assert client.complete_json('answering','Return JSON',{'q':'test'})=={'answer':'synthetic'}
        path,headers,body=observed[0]
        assert path.endswith({'chat_completions':'/chat/completions','responses':'/responses','anthropic':'/messages'}[protocol])
        assert ('X-Api-Key' in headers) if protocol=='anthropic' else headers['Authorization']=='Bearer synthetic-key'
        assert body['model']=='synthetic-model'
        total=report(service,store,{'period':['all']})['totals']
        assert total['total_tokens']==18 and total['input_tokens']==11 and total['output_tokens']==7 and total['requests']==1
        store.add('translation',doc['document_version_id'],{'request_traces':client.calls,'model_info':{'provider':'test'}})
        assert report(service,store,{'period':['all']})['totals']['total_tokens']==18
    finally:server.shutdown();server.server_close();thread.join()


@pytest.mark.parametrize('provider',['google','baidu','deepl','azure'])
def test_saved_translation_credentials_drive_adapters_without_monthly_confirmation(service,pdf_bytes,provider):
    from research_copilot.translation_providers import NativeProvider
    doc=service.import_pdf(pdf_bytes(),'SYNTHETIC.pdf')['data'];settings=APISettings(service.root);store=Workspace(service.root)
    values={'google':{'api_key':'fake'},'baidu':{'app_id':'fake-app','secret_key':'fake-secret'},'deepl':{'api_key':'fake'},'azure':{'api_key':'fake','region':'eastasia'}}[provider]
    settings.save({'kind':'translation','provider':provider,'values':values});calls=[]
    def transport(url,payload,headers):
        calls.append((url,payload,headers))
        return {'google':{'data':{'translations':[{'translatedText':'测试译文'}]}},'baidu':{'trans_result':[{'dst':'测试译文'}]},'deepl':{'translations':[{'text':'测试译文','billed_characters':12}]},'azure':[{'translations':[{'text':'测试译文'}]}]}[provider]
    client=NativeProvider(provider,store,doc,True,provider,transport=transport)
    result=client.translate('Test passage','en','zh-CN');assert result['translation']=='测试译文' and len(calls)==1
    assert len(store.records('translation_admission'))==1


def test_workbench_question_uses_selected_model_without_legacy_session(service,pdf_bytes,monkeypatch):
    from types import SimpleNamespace
    import research_copilot.web as web
    doc=service.import_pdf(pdf_bytes(),'SYNTHETIC.pdf')['data'];app=Workbench(service.root)
    client=SimpleNamespace(config=SimpleNamespace(provider='anthropic'))
    monkeypatch.setattr(app,'client',lambda data,doc:client)
    def answer(self,paper_id,question,**kwargs):
        assert self.allowed_provider=='anthropic' and kwargs['allow_remote'] and kwargs['client'] is client
        return {'status':'answered'}
    monkeypatch.setattr(web.PaperQA,'answer_question',answer)
    assert app.post('/api/qa',{'version':doc['document_version_id'],'question':'What is the method?'})['status']=='answered'
