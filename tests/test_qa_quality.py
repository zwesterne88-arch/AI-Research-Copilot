"""4.2.1 offline regressions. Synthetic facts and scripted verdicts are not live-model scores."""
import copy
import json

import pytest

from research_copilot.evidence import locate_quote, support_payload, validate_source_locations
from research_copilot.errors import CopilotError
from research_copilot.qa_retrieval import closing_windows, retrieve_context, MAX_CONTEXT_CHARS
from research_copilot.rag import apply_support_check, validate_answer, PaperQA


def sources_for(service, pdf_bytes, texts=None):
    texts = texts or [("Synthetic evidence: The study examines how shade affects plant growth. "
                        "The sample covers one species. Future work should test other species. ") * 18]
    imported = service.import_pdf(pdf_bytes(texts), "synthetic.pdf")
    doc = service.get_document(imported["document_version_id"])
    return doc, retrieve_context(service, doc, ["shade plant study"], question="研究问题是什么？")


def claim(source, text="这份合成材料研究遮阴对植物生长的影响。"):
    return {"status": "answered", "claims": [{"text": text, "kind": "author_statement",
            "evidence": [{"source_id": source["source_id"]}]}], "unanswered": []}


def reviews(claims, verdict="supported"):
    return {"claim_reviews": [{"claim_id": c["claim_id"], "verdict": verdict,
            "citation_support": [{"source_id": e["source_id"], "supports": verdict == "supported"} for e in c["citations"]],
            "reason": "合成复核结果，仅验证程序控制流程。"} for c in claims]}


@pytest.mark.parametrize("heading", ["Discussion", "Limitations", "Conclusion", "Benefits and risks of the method", "Future work"])
def test_late_research_sections_recalled_without_answer_keywords(service, pdf_bytes, heading):
    background = ("Prior work has limitations in the research problem and method. " * 55)
    # Deliberately avoid the search terms in the actual caveat; no domain-specific metric.
    own = ("Synthetic study overview. " * 35 + f"\n6. {heading}\n"
           "The observations cover one institution, so transfer to other settings remains untested. " * 1
           + "Additional observations will be needed before broader use. " * 18)
    doc, _ = sources_for(service, pdf_bytes, [background, background, own])
    diagnostic = {}
    sources = retrieve_context(service, doc, ["research method limitations"], question="这项研究有哪些局限？", diagnostics=diagnostic)
    assert any("transfer to other settings remains untested" in s["chunk_text"] and s["pdf_page"] == 3 for s in sources)
    assert diagnostic["original_question"] == "这项研究有哪些局限？"
    assert diagnostic["navigation_windows"] and not diagnostic["exhaustive_full_text_review"]


def test_heading_ligatures_use_raw_offsets():
    text = "Paper\n6.5. Beneﬁts and risks of the framework\n" + "A cautious finding. " * 150 + "\n7. Conclusion\nEnd."
    windows = closing_windows([{"pdf_page": 10, "raw_text": text, "status": "text_present"}])
    assert "Benefits and risks" in windows[0][-1]
    assert "6.5." in text[windows[0][2]:windows[0][3]]
    assert text.index("7. Conclusion") >= windows[0][3]


def test_original_english_terms_and_translated_chinese_plan_both_preserved(service, pdf_bytes):
    doc, _ = sources_for(service, pdf_bytes)
    diagnostic = {}
    retrieve_context(service, doc, ["shade plant growth"], question="How does shade affect growth?", diagnostics=diagnostic)
    assert diagnostic["search_expressions"] == ["How does shade affect growth?", "shade plant growth"]
    chinese = {}
    retrieve_context(service, doc, ["shade plant growth"], question="研究动机是什么？", diagnostics=chinese)
    assert chinese["original_question"] == "研究动机是什么？"
    assert chinese["search_expressions"] == ["shade plant growth"]  # No fabricated offline Chinese semantic search.


def test_context_is_deduplicated_bounded_and_exactly_locatable(service, pdf_bytes):
    doc, sources = sources_for(service, pdf_bytes)
    validate_source_locations(service, doc, sources)
    assert sum(len(s["chunk_text"]) for s in sources) <= MAX_CONTEXT_CHARS
    assert len({s["chunk_text"] for s in sources}) == len(sources)
    for i, a in enumerate(sources):
        for b in sources[i + 1:]:
            if a["pdf_page"] == b["pdf_page"]:
                assert max(a["char_start"], b["char_start"]) >= min(a["char_end"], b["char_end"])


def test_source_scope_isolates_papers_and_versions(service, pdf_bytes):
    a, _ = sources_for(service, pdf_bytes)
    service.import_pdf(pdf_bytes(["Synthetic unrelated astronomy material. galaxy stars " * 25]), "other.pdf")
    revised = service.import_pdf(pdf_bytes(["Synthetic revised edition. shade plant growth data " * 30]), "revision.pdf", paper_id=a["paper_id"])
    sources = retrieve_context(service, a, ["shade plant"])
    assert sources and all(s["document_version_id"] == a["document_version_id"] and s["file_sha256"] == a["file_sha256"] for s in sources)
    assert revised["document_version_id"] != a["document_version_id"]


@pytest.mark.parametrize("field,value", [("pdf_page", 99), ("paper_id", "other"), ("document_version_id", "other"),
                                         ("file_sha256", "other"), ("char_start", 99999), ("chunk_text", "unrelated quote")])
def test_locator_rejects_wrong_scope_page_offset_or_text(service, pdf_bytes, field, value):
    doc, sources = sources_for(service, pdf_bytes)
    bad = copy.deepcopy(sources)
    bad[0][field] = value
    with pytest.raises(CopilotError, match="来源版本"):
        validate_source_locations(service, doc, bad)


def test_subquote_offsets_bind_exact_raw_ligature_and_whitespace():
    text = "Prefix. The ﬁnal\n  observation remains uncertain. Suffix."
    a, b = locate_quote(text, "The final observation remains uncertain.")
    assert text[a:b] == "The ﬁnal\n  observation remains uncertain."
    assert locate_quote(text, "Invented statement") is None


def test_verifier_has_no_uncited_global_evidence(service, pdf_bytes):
    _, sources = sources_for(service, pdf_bytes)
    claims, _ = validate_answer(claim(sources[0]), sources)
    payload = support_payload("研究问题？", claims)
    assert "sources" not in payload
    assert len(payload["claims"][0]["evidence"]) == 1
    assert payload["claims"][0]["evidence"][0]["source_id"] == sources[0]["source_id"]
    assert {e["source_id"] for c in payload["claims"] for e in c["evidence"]} == {sources[0]["source_id"]}


def test_composite_statement_withheld_and_supported_atomic_fact_retained(service, pdf_bytes):
    _, sources = sources_for(service, pdf_bytes)
    obj = claim(sources[0])
    obj["claims"].append({"text": "材料覆盖一种植物，而且已证明对所有植物有效。", "kind": "evidence_summary", "evidence": [{"source_id": sources[0]["source_id"]}]})
    claims, _ = validate_answer(obj, sources)
    review = reviews(claims)
    review["claim_reviews"][1]["verdict"] = "non_atomic"
    kept, withheld = apply_support_check(review, claims)
    assert [c["claim_id"] for c in kept] == ["C1"] and len(withheld) == 1
    assert kept[0]["citations"][0]["semantic_support"] == "model_supported_not_human_verified"


def test_existing_locatable_numeric_text_does_not_establish_support(service, pdf_bytes):
    _, sources = sources_for(service, pdf_bytes)
    claims, _ = validate_answer(claim(sources[0], "作者证明了对所有研究对象都有效。"), sources)
    assert claims[0]["citations"][0]["quote_found"]
    kept, reasons = apply_support_check(reviews(claims, "unsupported"), claims)
    assert kept == [] and reasons  # Presence is not treated as factual accuracy.


@pytest.mark.parametrize("failure", ["missing_claim", "missing_citation", "invented_source", "old_schema"])
def test_support_review_requires_complete_current_claim_and_reference_coverage(service, pdf_bytes, failure):
    _, sources = sources_for(service, pdf_bytes)
    claims, _ = validate_answer(claim(sources[0]), sources)
    r = reviews(claims)
    if failure == "missing_claim": r["claim_reviews"] = []
    elif failure == "missing_citation": r["claim_reviews"][0]["citation_support"] = []
    elif failure == "invented_source": r["claim_reviews"][0]["citation_support"][0]["source_id"] = "other-paper:E1"
    else: r = {"supported_claim_ids": ["C1"], "unsupported": []}
    with pytest.raises(CopilotError):
        apply_support_check(r, claims)


def test_a_supported_verdict_with_an_irrelevant_citation_is_withheld(service, pdf_bytes):
    _, sources = sources_for(service, pdf_bytes)
    claims, _ = validate_answer(claim(sources[0]), sources)
    r = reviews(claims)
    r["claim_reviews"][0]["citation_support"][0]["supports"] = False
    assert apply_support_check(r, claims)[0] == []


def test_refusal_is_retrieved_insufficiency_not_proven_full_text_absence(service, pdf_bytes):
    doc, _ = sources_for(service, pdf_bytes)
    class Refuse:
        is_test_double = True
        calls = []
        def complete_json(self, stage, system, user):
            self.calls.append({"stage": stage, "status": "test_double"})
            if stage == "query_planning": return {"queries_en": ["shade plant growth"]}
            return {"status": "insufficient_evidence", "claims": [], "unanswered": ["当前片段不足以确认发布日期。"]}
    r = PaperQA(service, {doc["file_sha256"]}).answer_question(doc["paper_id"], "何时发布？", document_version_id=doc["document_version_id"], allow_remote=True, client=Refuse())
    assert r["status"] == "insufficient_evidence" and r["evidence_assessment"] == "retrieved_evidence_insufficient"
    assert r["error"] is None and r["model_calls"] == 0
    assert r["checked_scope"]["exhaustive_full_text_review"] is False


def test_runtime_retrieval_contains_no_corpus_specific_tuning():
    from pathlib import Path
    import research_copilot.qa_retrieval as module
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert all(word not in text for word in ("CANival", "false positives", "Dirichlet", "IoV", "P07", "DGIDS"))


@pytest.mark.parametrize("mode", ["empty", "failure"])
def test_empty_retrieval_and_retrieval_error_are_distinct(service, pdf_bytes, monkeypatch, mode):
    import research_copilot.rag as rag
    doc, _ = sources_for(service, pdf_bytes)
    def retrieval(*args, **kwargs):
        if mode == "failure": raise CopilotError("offline_index_error", "合成检索故障。", "retrieval")
        return []
    monkeypatch.setattr(rag, "retrieve_context", retrieval)
    class PlanOnly:
        is_test_double = True
        calls = []
        def complete_json(self, stage, system, user):
            assert stage == "query_planning"
            return {"queries_en": ["shade plant"]}
    r = PaperQA(service, {doc["file_sha256"]}).answer_question(doc["paper_id"], "研究问题？", document_version_id=doc["document_version_id"], allow_remote=True, client=PlanOnly())
    if mode == "failure":
        assert r["status"] == "failed" and r["error"]["stage"] == "retrieval"
    else:
        assert r["status"] == "insufficient_evidence" and r["evidence_assessment"] == "retrieval_empty" and r["error"] is None


def test_streamlit_old_history_and_budget_gate_render_offline(service, pdf_bytes, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from research_copilot.llm import LLMConfig
    from research_copilot.llm_budget import DeepSeekValidationBudget
    from research_copilot.qa_history import QAHistory
    from research_copilot.repository import now
    import research_copilot.rag as rag
    doc, sources = sources_for(service, pdf_bytes)
    claims, _ = validate_answer(claim(sources[0]), sources)
    record = {"run_id": "synthetic-legacy-run", "paper_id": doc["paper_id"], "document_version_id": doc["document_version_id"],
              "question": "合成历史问题", "status": "answered", "claims": claims, "unanswered": [], "source_file": "synthetic.pdf",
              "created_at": now(), "prompt_version": "research-logic-evidence-qa-v2-sourceids"}
    QAHistory(service.db).save(record)
    budget = DeepSeekValidationBudget(service.db)
    for _ in range(30): budget.reserve(1000, 2000)
    monkeypatch.setattr(rag, "authorized_hashes", lambda: {doc["file_sha256"]})
    monkeypatch.setattr(LLMConfig, "from_environment", lambda *args, **kwargs: LLMConfig("https://synthetic.invalid", "synthetic-model", "synthetic-not-real", provider="deepseek"))
    monkeypatch.setenv("QUALITY_TEST_LIBRARY", str(service.root))
    app = AppTest.from_string("import os\nfrom research_copilot import CopilotService\nfrom research_copilot.qa_ui import render_qa\ns = CopilotService(os.environ['QUALITY_TEST_LIBRARY'])\nrender_qa(s, s.list_documents()[0])").run(timeout=20)
    assert not app.exception
    assert any("旧版历史记录" in c.value for c in app.caption)
    assert any("三个不同检查" in c.value for c in app.caption)
    assert any("30次" in w.value for w in app.warning)
    assert next(b for b in app.button if b.label == "依据原文回答").disabled
    assert budget.summary()["attempted_calls"] == 30


def test_frozen_real_evidence_regression_if_local_audit_available():
    from pathlib import Path
    from research_copilot.rag import normalized
    path = Path(__file__).resolve().parents[1] / "verification/stage4_2_quality/private/latest_runs.json"
    if not path.exists(): pytest.skip("Local genuine traces unavailable; synthetic regressions remain portable")
    from scripts.review_stage4_2_quality import ReadOnlyService
    service = ReadOnlyService()
    runs = {e["case"]["case_id"]: e["result"] for e in json.loads(path.read_text(encoding="utf-8"))}
    for case_id, expected_pages in (("G05", {7}), ("G06", {7, 9, 10}), ("G07", {10})):
        r = runs[case_id]
        doc = service.db.one("SELECT * FROM documents WHERE document_version_id=?", (r["document_version_id"],))
        sources = retrieve_context(service, doc, r["retrieval_queries"], question=r["question"])
        validate_source_locations(service, doc, sources)
        assert expected_pages <= {s["pdf_page"] for s in sources}
        if case_id == "G07":
            needle = "using the max function for final decision-making increases false positives"
            assert not any(needle in normalized(s["chunk_text"]) for s in r["sources"])
            assert any(needle in normalized(s["chunk_text"]) for s in sources)
