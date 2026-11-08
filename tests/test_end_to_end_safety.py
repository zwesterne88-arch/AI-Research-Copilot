"""Bounded 4.5 Mock regressions, no real model or user-library writes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from research_copilot.errors import CopilotError
from research_copilot.rag import PaperQA

QUOTE="This synthetic study evaluates shade in plants."
TEXT=(QUOTE+" The authors compare variable light with constant light. The experiment is a greenhouse study. ")*10


class ControlledQAClient:
    is_test_double=True
    def __init__(self,entered=None,release=None,error_stage=None):
        self.calls=[];self.entered=entered;self.release=release;self.error_stage=error_stage
    def complete_json(self,stage,system,user):
        self.calls.append({'stage':stage,'status':'test_double'})
        if stage==self.error_stage:raise CopilotError('model_timeout','Synthetic timeout, no API request','answering',True)
        if stage=='query_planning':
            if self.entered:self.entered.set();assert self.release.wait(10)
            return {'queries_en':['synthetic shade plants greenhouse']}
        if stage=='answering':
            src=next(s for s in user['sources'] if QUOTE in s['chunk_text'])
            return {'status':'answered','claims':[{'text':'合成材料研究植物遮阴。','kind':'author_statement','evidence':[{'source_id':src['source_id'],'quote':QUOTE}]}],'unanswered':[]}
        return {'claim_reviews':[{'claim_id':c['claim_id'],'verdict':'supported','reason':'Synthetic assumption only',
            'citation_support':[{'source_id':r['source_id'],'supports':True} for r in c['evidence']]} for c in user['claims']]}


def test_concurrent_qa_submission_never_starts_a_second_model_flow(service,pdf_bytes):
    d=service.import_pdf(pdf_bytes([TEXT]),'SYNTHETIC.pdf')['data']
    first=PaperQA(service,{d['file_sha256']});second=PaperQA(service,{d['file_sha256']})
    entered,release=Event(),Event();a=ControlledQAClient(entered,release);b=ControlledQAClient()
    def ask(qa,client):return qa.answer_question(d['paper_id'],'研究问题是什么？',document_version_id=d['document_version_id'],allow_remote=True,client=client)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(ask,first,a)
        try:
            assert entered.wait(5)
            try:
                duplicate=ask(second,b);code=duplicate.get('error',{}).get('code') if duplicate.get('error') else None
            except CopilotError as exc:code=exc.code
            assert not b.calls and code=='qa_busy'
        finally:release.set()
        assert pending.result(timeout=10)['status']=='answered'


def test_failed_model_flow_releases_request_lock_for_manual_retry(service,pdf_bytes):
    d=service.import_pdf(pdf_bytes([TEXT]),'SYNTHETIC.pdf')['data'];qa=PaperQA(service,{d['file_sha256']})
    failed=qa.answer_question(d['paper_id'],'研究问题？',document_version_id=d['document_version_id'],allow_remote=True,client=ControlledQAClient(error_stage='answering'))
    assert failed['status']=='failed' and failed['error']['code']=='model_timeout'
    retried=qa.answer_question(d['paper_id'],'研究问题？',document_version_id=d['document_version_id'],allow_remote=True,client=ControlledQAClient())
    assert retried['status']=='answered' and retried['model_calls']==0
    assert qa.history.list(d['paper_id'],d['document_version_id'])[1]['run_id']==failed['run_id']


def test_mock_qa_history_is_visibly_distinct_and_never_enables_real_calls(service,pdf_bytes,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from research_copilot.llm import LLMConfig
    d=service.import_pdf(pdf_bytes([TEXT]),'SYNTHETIC.pdf')['data'];qa=PaperQA(service,{d['file_sha256']})
    qa.answer_question(d['paper_id'],'研究问题？',document_version_id=d['document_version_id'],allow_remote=True,client=ControlledQAClient())
    monkeypatch.setattr(LLMConfig,'from_environment',lambda *a,**kw: (_ for _ in ()).throw(CopilotError('llm_not_configured','Synthetic unavailable configuration','configuration')))
    code=f"from research_copilot import CopilotService\nfrom research_copilot.qa_ui import render_qa\ns=CopilotService({str(service.root)!r})\nrender_qa(s,s.get_document({d['document_version_id']!r}))"
    app=AppTest.from_string(code).run(timeout=20)
    assert not app.exception
    assert any('离线Mock问答记录' in w.value for w in app.warning)
    assert next(b for b in app.button if b.label=='依据原文回答').disabled


def test_uncited_scope_statements_are_visibly_unverified_and_history_is_unchanged(service,pdf_bytes,monkeypatch):
    from streamlit.testing.v1 import AppTest
    from research_copilot.llm import LLMConfig
    class ScopeDouble(ControlledQAClient):
        def complete_json(self,stage,system,user):
            result=super().complete_json(stage,system,user)
            if stage=='answering':result['unanswered']=['SYNTHETIC uncited model scope statement.']
            return result
    d=service.import_pdf(pdf_bytes([TEXT]),'SYNTHETIC.pdf')['data'];qa=PaperQA(service,{d['file_sha256']})
    record=qa.answer_question(d['paper_id'],'研究问题？',document_version_id=d['document_version_id'],allow_remote=True,client=ScopeDouble())
    before=qa.history.get(record['run_id'])
    monkeypatch.setattr(LLMConfig,'from_environment',lambda *a,**kw: (_ for _ in ()).throw(CopilotError('llm_not_configured','Synthetic unavailable configuration','configuration')))
    code=f"from research_copilot import CopilotService\nfrom research_copilot.qa_ui import render_qa\ns=CopilotService({str(service.root)!r})\nrender_qa(s,s.get_document({d['document_version_id']!r}))"
    app=AppTest.from_string(code).run(timeout=20)
    assert not app.exception and any('没有逐条绑定引用' in w.value for w in app.warning)
    assert any('SYNTHETIC uncited' in i.value for i in app.info)
    assert qa.history.get(record['run_id'])==before
