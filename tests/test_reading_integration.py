"""Offline safeguards and UI integration, with temporary libraries only."""
from copy import deepcopy

import pytest

from research_copilot import CopilotService
from research_copilot.errors import CopilotError
from research_copilot.llm import LLMConfig, create_client
from research_copilot.llm_budget import DeepSeekValidationBudget
from research_copilot.reading import StructuredReading
from scripts.reading_mock import ReadingDouble, SYNTHETIC_TEXT


def setup(service, pdf_bytes):
    doc = service.import_pdf(pdf_bytes([SYNTHETIC_TEXT]), "synthetic.pdf")["data"]
    return doc, StructuredReading(service, {doc["file_sha256"]})


def run(doc, engine, client=None, **kwargs):
    return engine.generate(doc["paper_id"], document_version_id=doc["document_version_id"], allow_remote=True, client=client or ReadingDouble(), **kwargs)


def test_30_call_budget_blocks_real_adapter_without_network_or_reset(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    # This synthetic fixture must not inherit a developer's private source grant.
    engine.allowed_provider = 'deepseek'
    budget = DeepSeekValidationBudget(service.db)
    for _ in range(30): budget.reserve(1000, 2000)
    before = budget.summary()
    client = create_client(LLMConfig("https://api.deepseek.com", "deepseek-flash", "synthetic-not-real", provider="deepseek"), service.db)
    c = run(doc, engine, client)
    assert c["card_status"] == "failed" and c["error"]["code"] == "validation_budget_exhausted", c
    assert c["model_calls"] == 0 and budget.summary() == before
    assert client.calls[0]["status"] == "not_sent"


def test_wrong_version_from_retrieval_never_reaches_extraction(service, pdf_bytes, monkeypatch):
    import research_copilot.reading as reading
    doc, engine = setup(service, pdf_bytes)
    original = reading.retrieve_context
    def bad(*args, **kwargs):
        sources = original(*args, **kwargs)
        sources[0]["document_version_id"] = "other-version"
        return sources
    monkeypatch.setattr(reading, "retrieve_context", bad)
    client = ReadingDouble(); c = run(doc, engine, client)
    assert c["card_status"] == "failed" and c["error"]["code"] == "invalid_source_locator"
    assert len(client.calls) == 1


def test_provider_authorization_precedes_reading_calls(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    class OtherProvider:
        calls = []
        config = LLMConfig("https://synthetic.invalid", "synthetic", "synthetic-not-real", provider="unauthorized")
        def complete_json(self, *args): pytest.fail("Unauthorized provider must not be contacted")
    card = run(doc, engine, OtherProvider())
    assert card["error"]["code"] == "source_provider_not_authorized" and card["model_calls"] == 0


def test_support_output_requires_each_reading_citation(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    def modify(stage, obj, user):
        if stage == "reading_review:metadata": obj["claim_reviews"][0]["citation_support"] = []
        return obj
    c = run(doc, engine, ReadingDouble(modifier=modify))
    assert c["card_status"] == "failed" and c["error"]["stage"] == "support_verification"
    assert not c["structured_content"]["fields"]["title"]["fact_ids"]


def test_rejected_unit_anchor_cannot_leave_orphan_unit(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    def modify(stage, obj, user):
        if stage == "reading_review:validation": obj["claim_reviews"][0]["verdict"] = "unsupported"
        if stage == "reading_extract:findings": obj["facts"][0]["validation_unit_id"] = None
        return obj
    c = run(doc, engine, ReadingDouble(modifier=modify))
    assert c["card_status"] == "draft", c
    assert not c["structured_content"]["validation_units"]
    assert c["structured_content"]["fields"]["validation"]["status"] == "uncertain"


def test_resume_rejects_different_pipeline_and_caps_manual_chain(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    failed = run(doc, engine, ReadingDouble(error_stage="reading_extract:metadata"))
    second = run(doc, engine, ReadingDouble(error_stage="reading_extract:metadata"), resume_card_id=failed["card_id"])
    third = run(doc, engine, ReadingDouble(error_stage="reading_extract:metadata"), resume_card_id=second["card_id"])
    client = ReadingDouble()
    fourth = run(doc, engine, client, resume_card_id=third["card_id"])
    assert fourth["card_status"] == "failed" and not client.calls
    # Version changes cannot resume old source outputs.
    revision = service.import_pdf(pdf_bytes([SYNTHETIC_TEXT+" Revised."]), "revision.pdf", paper_id=doc["paper_id"])["data"]
    engine.allowed_hashes.add(revision["file_sha256"])
    other = run(revision, engine, resume_card_id=failed["card_id"])
    assert other["card_status"] == "failed" and other["error"]["code"] == "invalid_reading_output"


def test_interrupted_card_recovery_does_not_call_model(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    completed = run(doc, engine)
    # Synthetic crash fixture: retain an incomplete processing row using the existing record shape.
    pending = deepcopy(completed)
    pending.update(card_status="processing", stage="framing:extracting")
    engine.history.create(pending)
    recovered = StructuredReading(CopilotService(service.root), set()).history.get(pending["card_id"])
    assert recovered["card_status"] == "failed" and recovered["error"]["code"] == "reading_interrupted"
    assert recovered["raw_outputs"] == pending["raw_outputs"]


def test_streamlit_reading_saved_card_evidence_and_budget_gate(service, pdf_bytes, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import research_copilot.reading as reading
    doc, engine = setup(service, pdf_bytes); run(doc, engine)
    budget = DeepSeekValidationBudget(service.db)
    for _ in range(30): budget.reserve(1000, 2000)
    monkeypatch.setattr(reading, "authorized_hashes", lambda: {doc["file_sha256"]})
    monkeypatch.setattr(LLMConfig, "from_environment", lambda *a, **kw: LLMConfig("https://synthetic.invalid", "synthetic", "synthetic-not-real", provider="deepseek"))
    monkeypatch.setenv("READING_UI_LIBRARY", str(service.root))
    app = AppTest.from_string("import os\nfrom research_copilot import CopilotService\nfrom research_copilot.reading_ui import render_reading\ns=CopilotService(os.environ['READING_UI_LIBRARY'])\nrender_reading(s,s.list_documents()[0])").run(timeout=20)
    next(c for c in app.checkbox if c.label.startswith("我确认当前论文")).check()
    app.run(timeout=20)
    assert not app.exception
    assert next(b for b in app.button if b.label.startswith("生成精读")).disabled
    assert any("离线Mock" in w.value for w in app.warning)
    assert any("研究动机" == e.label for e in app.expander)
    assert any("PDF第1页" in e.label for e in app.expander)
    assert any("当前没有确认可引用的关系" in c.value for c in app.caption)
    assert budget.summary()["attempted_calls"] == 30


def test_rejected_applicability_becomes_uncertain(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    def modify(stage, obj, user):
        if stage == "reading_extract:findings":
            rationale = deepcopy(obj["facts"][0]); rationale.update(fact_id="na", field="reading_suggestions", purpose="applicability", kind="analysis")
            obj["facts"].append(rationale)
            obj["field_states"]["reading_suggestions"] = {"status":"not_applicable", "reason_code":"not_applicable", "rationale_fact_id":"na"}
        if stage == "reading_review:findings": obj["claim_reviews"][-1]["verdict"] = "unsupported"
        return obj
    c = run(doc, engine, ReadingDouble(modifier=modify))
    f = c["structured_content"]["fields"]["reading_suggestions"]
    assert c["card_status"] == "draft" and f["status"] == "uncertain" and f["rationale_fact_id"] is None


def test_dynamic_metric_definition_and_category_keep_raw_source(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    def modify(stage, obj, user):
        if stage == "reading_extract:findings":
            obj["facts"][0]["attribute"].update(role="metric", definition_raw="Growth reached 12 mm in the shaded group.")
        return obj
    c = run(doc, engine, ReadingDouble(modifier=modify)); assert c["card_status"] == "draft", c
    a = c["structured_content"]["facts"]["findings:f1"]["attribute"]
    assert a["role"] == "metric" and a["definition_raw"].endswith("shaded group.")


@pytest.mark.parametrize("bad_type", ["status", "reason_code", "source_id"])
def test_malformed_typed_values_are_reading_validation_errors(service, pdf_bytes, bad_type):
    doc, engine = setup(service, pdf_bytes)
    def modify(stage, obj, user):
        if stage == "reading_extract:metadata":
            if bad_type == "source_id": obj["facts"][0]["evidence"][0]["source_id"] = ["E1"]
            else: obj["field_states"]["title"][bad_type] = ["invalid"]
        return obj
    c = run(doc, engine, ReadingDouble(modifier=modify))
    assert c["card_status"] == "failed" and c["error"]["code"] == "invalid_reading_output"


def test_full_app_keeps_upload_search_qa_and_reading_tabs(service, pdf_bytes, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    setup(service, pdf_bytes)
    monkeypatch.setenv("RESEARCH_COPILOT_DATA_DIR", str(service.root))
    monkeypatch.setattr(LLMConfig, "from_environment", lambda *a, **kw: (_ for _ in ()).throw(CopilotError("llm_not_configured", "Synthetic missing config", "configuration")))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/"app.py")).run(timeout=20)
    assert not app.exception
    assert {t.label for t in app.tabs} == {"智能精读", "正文与页码", "文本检索", "论文问答", "文件与处理记录"}
    assert any(b.label == "导入并建立索引" for b in app.button)
    assert any(b.label == "检索当前论文" for b in app.button)


def test_reading_prompts_are_generic_and_do_not_assume_experiments():
    from research_copilot import reading_prompts
    text = reading_prompts.PLANNER+reading_prompts.EXTRACT+reading_prompts.REVIEW
    assert all(t not in text for t in ("CANival", "IoV", "Dirichlet", "FedAvg", "DGIDS", "P07"))
    assert "theoretical" in text and "untrusted" in text and "not_applicable" in text


def test_cross_module_relation_review_includes_endpoint_meaning_not_extra_quotes(service, pdf_bytes):
    doc, engine = setup(service, pdf_bytes)
    def modify(stage, obj, user):
        if stage == "reading_extract:approach":
            obj["relations"] = [{"relation_id":"r1", "from_fact_id":"framing:f4", "to_fact_id":"f1", "type":"addressed_by",
                "text":"合成作者用其声称的光照处理方案回应遮阴影响生长的问题。", "kind":"author_statement", "evidence":obj["facts"][0]["evidence"]}]
        return obj
    client = ReadingDouble(modifier=modify); card = run(doc, engine, client)
    assert card["card_status"] == "draft", card
    payload = next(p["user"] for p in client.payloads if p["stage"] == "reading_review:approach")
    endpoints = payload["claims"][-1]["reading_context"]["endpoints"]
    assert endpoints[0]["fact_id"] == "framing:f4" and "遮阴" in endpoints[0]["text"]
    assert all("evidence" not in e and "citations" not in e for e in endpoints)
    finding_review = next(p["user"] for p in client.payloads if p["stage"] == "reading_review:findings")
    anchor = finding_review["claims"][0]["reading_context"]["validation_anchor"]
    assert anchor["fact_id"] == "validation:f1" and "温室" in anchor["text"]
