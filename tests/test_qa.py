"""Offline contract/failure checks. Model doubles never count as real API validation."""
import json
from pathlib import Path

import pytest

from research_copilot import CopilotService
from research_copilot.errors import CopilotError
from research_copilot.llm import LLMConfig, create_client
from research_copilot.llm_budget import DeepSeekValidationBudget
from research_copilot.rag import PaperQA
from research_copilot import qa_prompts

TEXT = ("Synthetic test fixture: This botanical study investigates how shade affects plant growth. "
        "Prior work examined only constant light. The authors propose a variable-light treatment and "
        "validate it using a controlled greenhouse experiment. The findings support a context-limited "
        "conclusion, not universal effectiveness. Future work should examine more species. ") * 8
QUOTE = "This botanical study investigates how shade affects plant growth."


class ScriptedClient:
    is_test_double = True
    def __init__(self, modifier=None, error_stage=None):
        self.calls = []
        self.modifier = modifier
        self.error_stage = error_stage

    def complete_json(self,stage,system,user):
        self.calls.append({"stage":stage,"status":"test_double"})
        if stage == self.error_stage:
            raise CopilotError("model_timeout","Injected timeout; synthetic test only",stage,True)
        if stage == "query_planning":
            return {"queries_en":["shade plant growth research question"]}
        if stage == "answering":
            source = next(s for s in user["sources"] if QUOTE in s["chunk_text"])
            result = {"status":"answered","claims":[{"text":"这份合成测试材料研究遮阴如何影响植物生长。","kind":"author_statement",
                      "evidence":[{"source_id":source["source_id"],"quote":QUOTE}]}],"unanswered":[]}
            return self.modifier(result) if self.modifier else result
        return {"claim_reviews":[{"claim_id":c["claim_id"],"verdict":"supported",
                 "citation_support":[{"source_id":e["source_id"],"supports":True} for e in c["evidence"]],
                 "reason":"Synthetic scripted check; not a semantic evaluation."} for c in user["claims"]]}


def setup_qa(service,pdf_bytes):
    doc = service.import_pdf(pdf_bytes([TEXT],title="Synthetic botanical test"),"synthetic.pdf")
    return doc,PaperQA(service,allowed_hashes={doc["data"]["file_sha256"]})


def test_general_chinese_question_citations_persistence(service,pdf_bytes):
    doc,qa = setup_qa(service,pdf_bytes)
    client = ScriptedClient()
    result = qa.answer_question(doc["paper_id"],"作者为什么研究这个问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=client)
    assert result["status"] == "answered",result
    assert result["model_calls"] == 0 and result["test_double_calls"] == 3
    ref = result["claims"][0]["citations"][0]
    assert ref["paper_id"] == doc["paper_id"] and ref["document_version_id"] == doc["document_version_id"]
    assert ref["pdf_page"] == 1 and ref["review_status"] == "unreviewed"
    reopened = PaperQA(CopilotService(service.root),allowed_hashes=set())
    assert reopened.history.list(doc["paper_id"],doc["document_version_id"])[0]["run_id"] == result["run_id"]
    assert reopened.history.list("other-paper",doc["document_version_id"]) == []


def test_source_ids_bind_server_owned_exact_passage(service,pdf_bytes):
    doc,qa=setup_qa(service,pdf_bytes)
    def no_quote(r):
        del r["claims"][0]["evidence"][0]["quote"]
        return r
    result=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=ScriptedClient(no_quote))
    assert result["status"] == "answered",result
    pages={p["pdf_page"]:p["raw_text"] for p in service.get_pages(doc["document_version_id"])}
    for c in result["claims"]:
        for e in c["citations"]:
            assert pages[e["pdf_page"]][e["char_start"]:e["char_end"]] == e["quote"]


def test_cached_real_query_plan_is_bound_to_question_version_and_model(service,pdf_bytes):
    doc,qa=setup_qa(service,pdf_bytes)
    result=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=ScriptedClient())
    # Synthetic history fixture explicitly emulates metadata; not a real API result.
    result["execution_mode"]="real_api"
    result["request_traces"]=[{"stage":"query_planning","status":"completed","model":"synthetic-model"}]
    qa.history.save(result)
    from research_copilot.qa_prompts import PLANNER_VERSION
    current=service.get_document(doc["document_version_id"])
    assert qa.history.cached_plan(current,"研究问题？","synthetic-model",PLANNER_VERSION)
    assert qa.history.cached_plan(current,"另一个问题？","synthetic-model",PLANNER_VERSION) is None
    assert qa.history.cached_plan(current,"研究问题？","other-model",PLANNER_VERSION) is None


def test_omitted_empty_unanswered_is_safe(service,pdf_bytes):
    doc,qa=setup_qa(service,pdf_bytes)
    def omit(r):del r["unanswered"];return r
    result=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=ScriptedClient(omit))
    assert result["status"] == "answered" and result["unanswered"] == []


def test_explicit_candidate_resume_revalidates_and_does_not_regenerate(service,pdf_bytes):
    doc,qa=setup_qa(service,pdf_bytes)
    def bad_type(r):r["unanswered"]=None;return r
    failed=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=ScriptedClient(bad_type))
    assert failed["status"]=="failed"
    # Correct a synthetic fixture only; real model candidates are retained unchanged.
    failed["candidate_output"]["unanswered"]=[]
    qa.history.save(failed)
    class NoRegeneration(ScriptedClient):
        def complete_json(self,stage,system,user):
            assert stage != "answering"
            return super().complete_json(stage,system,user)
    result=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=NoRegeneration(),resume_candidate_run_id=failed["run_id"])
    assert result["status"]=="answered" and result["resumed_candidate_run_id"]==failed["run_id"]
    wrong=qa.answer_question(doc["paper_id"],"不同问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=NoRegeneration(),resume_candidate_run_id=failed["run_id"])
    assert wrong["error"]["stage"]=="candidate_resume" and wrong["test_double_calls"]==0


def test_consent_and_allowlist_precede_model(service,pdf_bytes):
    doc,qa = setup_qa(service,pdf_bytes)
    client=ScriptedClient()
    r=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],client=client)
    assert r["error"]["code"] == "source_consent_required" and client.calls == []
    qa.allowed_hashes = set()
    r=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=client)
    assert r["error"]["code"] == "source_not_authorized" and client.calls == []


def test_wrong_paper_version_rejected(service,pdf_bytes):
    doc,qa = setup_qa(service,pdf_bytes)
    with pytest.raises(CopilotError):
        qa.answer_question("different-paper","研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=ScriptedClient())


def test_source_authorization_is_provider_scoped(service,pdf_bytes):
    doc,qa = setup_qa(service,pdf_bytes)
    class OtherProvider:
        calls=[]
        config=LLMConfig("https://different.invalid","other","synthetic",provider="not-authorized")
        def complete_json(self,*args):pytest.fail("No request to an unauthorized provider")
    result=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=OtherProvider())
    assert result["error"]["code"] == "source_provider_not_authorized"


@pytest.mark.parametrize("kind",["wrong_source","invented_quote","invented_number"])
def test_invalid_evidence_never_published(service,pdf_bytes,kind):
    doc,qa = setup_qa(service,pdf_bytes)
    def change(r):
        if kind == "wrong_source":r["claims"][0]["evidence"][0]["source_id"]="other-paper:E1"
        elif kind == "invented_quote":r["claims"][0]["evidence"][0]["quote"]="An invented sentence not present in the source paper."
        else:r["claims"][0]["text"]="材料报告了99.9%的提升。"
        return r
    r=qa.answer_question(doc["paper_id"],"主要发现？",document_version_id=doc["document_version_id"],allow_remote=True,client=ScriptedClient(change))
    assert r["status"] == "failed" and r["claims"] == []
    assert r["error"]["code"] == "invalid_evidence_output"


def test_model_refusal_is_not_transport_error(service,pdf_bytes):
    doc,qa = setup_qa(service,pdf_bytes)
    client=ScriptedClient(lambda r:{"status":"insufficient_evidence","claims":[],"unanswered":["当前材料不足以确认。"]})
    r=qa.answer_question(doc["paper_id"],"不能确认的内容？",document_version_id=doc["document_version_id"],allow_remote=True,client=client)
    assert r["status"] == "insufficient_evidence" and r["error"] is None
    assert len(client.calls) == 2


def test_support_check_can_reject_author_overclaim(service,pdf_bytes):
    doc,qa = setup_qa(service,pdf_bytes)
    class Reject(ScriptedClient):
        def complete_json(self,stage,system,user):
            if stage == "support_verification":
                self.calls.append({"stage":stage,"status":"test_double"})
                return {"claim_reviews":[{"claim_id":"C1","verdict":"unsupported",
                    "citation_support":[{"source_id":e["source_id"],"supports":False} for e in user["claims"][0]["evidence"]],
                    "reason":"证据不足以支持该概括。"}]}
            return super().complete_json(stage,system,user)
    r=qa.answer_question(doc["paper_id"],"结论是否成立？",document_version_id=doc["document_version_id"],allow_remote=True,client=Reject())
    assert r["status"] == "insufficient_evidence" and not r["claims"]


@pytest.mark.parametrize("stage",["query_planning","answering","support_verification"])
def test_stage_timeout_preserves_question_and_history(service,pdf_bytes,stage):
    doc,qa=setup_qa(service,pdf_bytes)
    r=qa.answer_question(doc["paper_id"],"动机和方法？",document_version_id=doc["document_version_id"],allow_remote=True,client=ScriptedClient(error_stage=stage))
    assert r["status"] == "failed" and r["error"]["code"] == "model_timeout"
    assert qa.history.list(doc["paper_id"],doc["document_version_id"])[0]["question"] == "动机和方法？"


def test_missing_source_midflight_invalidates_answer(service,pdf_bytes):
    doc,qa = setup_qa(service,pdf_bytes)
    class DeleteSource(ScriptedClient):
        def complete_json(self,stage,system,user):
            result=super().complete_json(stage,system,user)
            if stage == "support_verification":service.store.path(doc["data"]["file_sha256"]).unlink()
            return result
    r=qa.answer_question(doc["paper_id"],"研究问题？",document_version_id=doc["document_version_id"],allow_remote=True,client=DeleteSource())
    assert r["status"] == "failed" and r["error"]["code"] == "source_missing" and r["claims"] == []


def test_budget_is_persistent_and_bounds_calls(service):
    budget = DeepSeekValidationBudget(service.db)
    for _ in range(30):budget.reserve(1000,2000)
    reopened=DeepSeekValidationBudget(service.db)
    with pytest.raises(CopilotError) as exc:reopened.reserve(1000,2000)
    assert exc.value.code == "validation_budget_exhausted"
    assert reopened.summary()["attempted_calls"] == 30


def test_budget_prevents_large_requests(service):
    budget=DeepSeekValidationBudget(service.db)
    with pytest.raises(CopilotError):budget.reserve(60001,2000)
    with pytest.raises(CopilotError):budget.reserve(1000,4000)
    assert budget.summary()["attempted_calls"] == 0


def test_configuration_alias_and_secret_not_in_repr(tmp_path,monkeypatch):
    for name in list(__import__('os').environ):
        if name.startswith("RESEARCH_COPILOT_LLM_") or name=="DEEPSEEK_API_KEY":monkeypatch.delenv(name,raising=False)
    (tmp_path/".env").write_text("RESEARCH_COPILOT_LLM_BASE_URL=https://api.deepseek.com\nRESEARCH_COPILOT_LLM_MODEL=deepseek-flash\nRESEARCH_COPILOT_LLM_API_KEY_ENV=DEEPSEEK_API_KEY\nDEEPSEEK_API_KEY=synthetic-secret-not-real\n",encoding="utf-8")
    config=LLMConfig.from_environment(tmp_path)
    assert config.api_key == "synthetic-secret-not-real"
    assert config.api_key not in repr(config)+json.dumps(config.public_info())


def test_missing_config_is_explicit(tmp_path,monkeypatch):
    for name in list(__import__('os').environ):
        if name.startswith("RESEARCH_COPILOT_LLM_"):monkeypatch.delenv(name,raising=False)
    with pytest.raises(CopilotError) as exc:LLMConfig.from_environment(tmp_path)
    assert exc.value.code == "llm_not_configured"


def test_prompts_have_no_corpus_specific_terms():
    combined=qa_prompts.PLANNER+qa_prompts.ANSWER+qa_prompts.VERIFY
    assert all(term not in combined for term in ["CANival","IoV","Dirichlet","FedAvg","P03","P07","Non-IID"])
    assert "untrusted" in combined and "author-claimed" in combined
