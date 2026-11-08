"""Mock HTTP transport tests; never send credentials or source text to a service."""
from io import BytesIO
import json
from urllib.error import HTTPError, URLError

import pytest

from research_copilot.errors import CopilotError
from research_copilot import llm
from research_copilot.llm_budget import DeepSeekValidationBudget


class Response:
    headers={"x-request-id":"synthetic-request-id"}
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,*args):return self.data


def client(service):
    config=llm.LLMConfig("https://api.deepseek.com","deepseek-flash","synthetic-secret-not-real",json_mode=True,provider="deepseek")
    return llm.create_client(config,service.db)


def test_deepseek_payload_and_usage(service,monkeypatch):
    cli=client(service)
    class Opener:
        def open(self,request,timeout):
            payload=json.loads(request.data)
            assert payload["thinking"] == {"type":"disabled"}
            assert payload["model"] == "deepseek-flash" and payload["stream"] is False
            assert payload["response_format"] == {"type":"json_object"}
            assert request.headers["Authorization"] == "Bearer synthetic-secret-not-real"
            return Response(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":'{"ok":true}'}}],"usage":{"prompt_tokens":100,"completion_tokens":20}}).encode())
    monkeypatch.setattr(llm,"build_opener",lambda *a:Opener())
    assert cli.complete_json("test","Return JSON",{"question":"synthetic"}) == {"ok":True}
    assert cli.calls[0]["usage"]["prompt_tokens"] == 100
    assert cli.calls[0]["estimated_peak_cost_cny"] is not None
    assert "synthetic-secret-not-real" not in json.dumps(cli.calls)


@pytest.mark.parametrize("status,code",[(401,"model_auth_failed"),(403,"model_auth_failed"),(429,"model_rate_limited"),(500,"model_service_unavailable"),(400,"model_request_rejected"),(302,"model_request_rejected")])
def test_http_errors_are_safe_no_retry(service,monkeypatch,status,code):
    cli=client(service)
    class Opener:
        def open(self,request,timeout):
            raise HTTPError(request.full_url,status,"secret might appear in vendor text",{},BytesIO(b"synthetic-secret-not-real"))
    monkeypatch.setattr(llm,"build_opener",lambda *a:Opener())
    with pytest.raises(CopilotError) as exc:cli.complete_json("answering","JSON",{})
    assert exc.value.code == code and "synthetic-secret-not-real" not in str(exc.value)
    assert len(cli.calls) == 1 and cli.budget.summary()["attempted_calls"] == 1


@pytest.mark.parametrize("error,code",[(TimeoutError(),"model_timeout"),(URLError("test network failure"),"model_network_error")],ids=["timeout","network"])
def test_network_errors(service,monkeypatch,error,code):
    cli=client(service)
    class Opener:
        def open(self,*a,**k):raise error
    monkeypatch.setattr(llm,"build_opener",lambda *a:Opener())
    with pytest.raises(CopilotError) as exc:cli.complete_json("query_planning","JSON",{})
    assert exc.value.code == code and len(cli.calls) == 1


@pytest.mark.parametrize("content,finish,code",[("not json","stop","invalid_model_output"),("{}","length","model_output_incomplete"),(None,"stop","invalid_model_output")])
def test_invalid_or_truncated_output(service,monkeypatch,content,finish,code):
    cli=client(service)
    body=json.dumps({"choices":[{"finish_reason":finish,"message":{"content":content}}],"usage":{"prompt_tokens":30,"completion_tokens":10}}).encode()
    class Opener:
        def open(self,*a,**k):return Response(body)
    monkeypatch.setattr(llm,"build_opener",lambda *a:Opener())
    with pytest.raises(CopilotError) as exc:cli.complete_json("answering","JSON",{})
    assert exc.value.code == code
    assert cli.calls[0]["usage"]["prompt_tokens"] == 30


def test_no_credentials_redirected():
    assert llm.NoRedirect().redirect_request(None,None,302,"redirect",{},"https://other.invalid") is None


def test_budget_rejection_never_opens_network(service,monkeypatch):
    cli=client(service)
    for _ in range(30):cli.budget.reserve(1000,2000)
    monkeypatch.setattr(llm,"build_opener",lambda *a:pytest.fail("Network must not open"))
    with pytest.raises(CopilotError):cli.complete_json("answering","JSON",{})
    assert cli.calls[0]["status"] == "not_sent"
