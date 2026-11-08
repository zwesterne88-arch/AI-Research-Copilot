"""Structured reading: offline contract/process tests, never extraction accuracy claims."""
from copy import deepcopy
import json

import pytest

from research_copilot import CopilotService
from research_copilot.errors import CopilotError
from research_copilot.reading import StructuredReading
from research_copilot.reading_schema import MODULE_FIELDS, validate_module, empty_content
from scripts.reading_mock import ReadingDouble, SYNTHETIC_TEXT


@pytest.fixture
def reading_setup(service, pdf_bytes):
    doc = service.import_pdf(pdf_bytes([SYNTHETIC_TEXT], title="Synthetic botanical fixture"), "synthetic.pdf")["data"]
    return doc, StructuredReading(service, {doc["file_sha256"]})


def generate(setup, client=None, **kwargs):
    doc, reading = setup
    return reading.generate(doc["paper_id"], document_version_id=doc["document_version_id"], allow_remote=True, client=client or ReadingDouble(), **kwargs)


def test_full_reading_schema_evidence_and_restore(reading_setup, service):
    client = ReadingDouble()
    card = generate(reading_setup, client)
    assert card["card_status"] == "draft", card
    assert card["test_double_calls"] == 11 and card["model_calls"] == 0
    assert set(card["structured_content"]["fields"]) == {f for fs in MODULE_FIELDS.values() for f in fs}
    pages = {p["pdf_page"]:p["raw_text"] for p in service.get_pages(card["document_version_id"])}
    for ref in card["evidence_references"].values():
        assert pages[ref["pdf_page"]][ref["quote_char_start"]:ref["quote_char_end"]] == ref["quote"]
        assert ref["file_sha256"] == card["file_sha256"] and ref["review_status"] == "unreviewed"
    reopened = StructuredReading(CopilotService(service.root), set())
    assert reopened.history.get(card["card_id"]) == card
    assert all("sources" not in p["user"] for p in client.payloads if "review:" in p["stage"])


def test_background_and_motivation_are_distinct(reading_setup):
    c = generate(reading_setup)["structured_content"]
    background = c["facts"][c["fields"]["background"]["fact_ids"][0]]["text"]
    motivation = c["facts"][c["fields"]["motivation"]["fact_ids"][0]]["text"]
    assert background != motivation and "背景" in background and "不足" in motivation


@pytest.mark.parametrize("kind", ["experimental", "theoretical", "case_study"])
def test_validation_types_do_not_require_dataset_metrics(service, pdf_bytes, kind):
    text = SYNTHETIC_TEXT
    if kind != "experimental":
        text = text.replace("A controlled greenhouse experiment tests one species.", "A mathematical proof establishes a bound under stated assumptions." if kind=="theoretical" else "A qualitative case study examines one archive.")
    d = service.import_pdf(pdf_bytes([text]), f"{kind}.pdf")["data"]
    card = generate((d, StructuredReading(service, {d["file_sha256"]})), ReadingDouble(validation_type=kind))
    assert card["card_status"] == "draft", card
    assert card["structured_content"]["validation_units"]["validation:u1"]["type"] == kind
    assert "dataset" not in card["structured_content"]["fields"]


def test_unknown_field_is_scoped_not_full_paper_absence(reading_setup):
    c = generate(reading_setup)
    f = c["structured_content"]["fields"]["reading_suggestions"]
    assert f["status"] == "not_found_in_checked_scope" and not f["fact_ids"]
    assert f["checked_source_ids"] and not f["coverage_complete"]
    assert c["extraction_scope"]["exhaustive_full_text_review"] is False


def test_cache_makes_no_calls_and_regenerate_preserves_old_version(reading_setup):
    first = generate(reading_setup)
    old = deepcopy(first)
    double = ReadingDouble()
    cached = generate(reading_setup, double)
    assert cached["cache_hit"] and cached["model_calls_this_invocation"] == 0 and double.calls == []
    second = generate(reading_setup, regenerate=True)
    assert second["card_version"] == 2 and second["card_id"] != first["card_id"]
    assert reading_setup[1].history.get(first["card_id"]) == old


@pytest.mark.parametrize("stage", ["reading_planning", "reading_extract:framing", "reading_review:approach"])
def test_timeouts_checkpoint_and_manual_resume_new_version(reading_setup, stage):
    failed = generate(reading_setup, ReadingDouble(error_stage=stage))
    assert failed["card_status"] == "failed" and failed["error"]["code"] == "model_timeout"
    original = deepcopy(failed)
    retry_client = ReadingDouble()
    resumed = generate(reading_setup, retry_client, resume_card_id=failed["card_id"])
    assert resumed["card_status"] == "draft", resumed
    assert resumed["resumed_from_card_id"] == failed["card_id"] and resumed["card_version"] == 2
    if failed.get("plan_validated"): assert retry_client.calls[0]["stage"] != "reading_planning"
    assert reading_setup[1].history.get(failed["card_id"]) == original


@pytest.mark.parametrize("failure", ["unknown_field", "bad_source", "independent_novelty", "bad_unit", "raw_number", "raw_unit", "malformed", "uncited_missing_note"])
def test_invalid_module_never_publishes_candidates(reading_setup, failure):
    def modify(stage, obj, user):
        if stage != "reading_extract:approach": return obj
        if failure == "unknown_field": obj["facts"][0]["field"] = "domain_only_field"
        elif failure == "bad_source": obj["facts"][0]["evidence"][0]["source_id"] = "other:E1"
        elif failure == "independent_novelty": obj["facts"][0]["kind"] = "analysis"
        elif failure == "bad_unit": obj["facts"][0]["validation_unit_id"] = "other:u99"
        elif failure == "raw_number": obj["facts"][0]["attribute"] = {"name_raw":None, "value_raw":"987654.321", "unit_raw":None}
        elif failure == "raw_unit": obj["facts"][0]["attribute"] = {"name_raw":None, "value_raw":None, "unit_raw":"UNREPORTED_UNIT"}
        elif failure == "malformed": return {"facts":"not a list"}
        else: obj["field_states"]["method"]["reason_text"] = "An uncited scientific assertion"
        return obj
    card = generate(reading_setup, ReadingDouble(modifier=modify))
    if failure in ("raw_number","raw_unit"):
        assert card["card_status"] == "draft",card
        field=card["structured_content"]["fields"]["claimed_contributions"]
        assert field["status"]=="uncertain" and field["fact_ids"]
        assert card["structured_content"]["facts"][field["fact_ids"][0]]["attribute"] is None
        assert card["raw_outputs"]["approach:extract"]["facts"][0]["attribute"] is not None
        return
    assert card["card_status"] == "failed" and card["error"]["code"] in ("invalid_reading_output", "invalid_evidence_output"), card
    assert card["structured_content"]["fields"]["claimed_contributions"]["fact_ids"] == []
    assert "approach:extract" in card["raw_outputs"]


def test_not_applicable_requires_cited_rationale(reading_setup):
    def modify(stage, obj, user):
        if stage == "reading_extract:findings":
            obj["field_states"]["reading_suggestions"] = {"status":"not_applicable", "reason_code":"not_applicable", "rationale_fact_id":None}
        return obj
    card = generate(reading_setup, ReadingDouble(modifier=modify))
    assert card["card_status"] == "failed" and card["error"]["code"] == "invalid_reading_output"


def test_cited_applicability_rationale_and_rejected_rationale(reading_setup):
    def modify(stage, obj, user):
        if stage == "reading_extract:findings":
            f = deepcopy(obj["facts"][0]); f.update(fact_id="rationale", field="reading_suggestions", purpose="applicability", kind="analysis")
            obj["facts"].append(f)
            obj["field_states"]["reading_suggestions"] = {"status":"not_applicable", "reason_code":"not_applicable", "rationale_fact_id":"rationale"}
        return obj
    card = generate(reading_setup, ReadingDouble(modifier=modify))
    assert card["card_status"] == "draft", card
    assert card["structured_content"]["fields"]["reading_suggestions"]["rationale_fact_id"] == "findings:rationale"
    # This is a scripted acceptance contract, not a claim that the synthetic rationale is semantically valid.


def test_withheld_facts_are_uncertain_and_not_published(reading_setup):
    def modify(stage, obj, user):
        if stage == "reading_review:approach":
            obj["claim_reviews"][0]["verdict"] = "unsupported"
        return obj
    card = generate(reading_setup, ReadingDouble(modifier=modify))
    assert card["card_status"] == "draft", card
    f = card["structured_content"]["fields"]["claimed_contributions"]
    assert f["status"] == "uncertain" and not f["fact_ids"]
    assert not any(k.startswith("approach:f1:") for k in card["evidence_references"])


def test_result_carries_own_validation_and_literal_units(reading_setup):
    card = generate(reading_setup)
    c = card["structured_content"]; result = c["facts"][c["fields"]["findings"]["fact_ids"][0]]
    assert result["validation_unit_id"] == "validation:u1" and result["attribute"]["unit_raw"] == "mm"
    assert all(card["evidence_references"][eid]["module"] == "findings" for eid in result["evidence_ids"])


def test_unlinked_result_is_uncertain_not_assigned_by_position(reading_setup):
    def modify(stage, obj, user):
        if stage == "reading_extract:findings": obj["facts"][0]["validation_unit_id"] = None
        return obj
    card = generate(reading_setup, ReadingDouble(modifier=modify))
    assert card["card_status"] == "draft"
    assert card["structured_content"]["fields"]["findings"]["status"] == "uncertain"


def test_relations_require_legal_endpoints_and_their_own_evidence(reading_setup):
    def modify(stage, obj, user):
        if stage == "reading_extract:framing":
            obj["relations"] = [{"relation_id":"r1", "from_fact_id":"f2", "to_fact_id":"f3", "type":"motivates", "text":"合成作者因恒定光照研究不足而研究变化遮阴。",
                                "kind":"author_statement", "evidence":obj["facts"][2]["evidence"]}]
        return obj
    card = generate(reading_setup, ReadingDouble(modifier=modify))
    assert card["card_status"] == "draft", card
    edge = card["structured_content"]["relations"][0]
    assert edge["from_fact_id"] == "framing:f2" and edge["to_fact_id"] == "framing:f3" and edge["evidence_ids"]


def test_no_forced_causal_chain_and_invalid_endpoint_is_quarantined(reading_setup):
    first = generate(reading_setup)
    assert first["structured_content"]["relations"] == []
    def modify(stage, obj, user):
        if stage == "reading_extract:framing": obj["relations"] = [{"relation_id":"r1", "from_fact_id":"other:f9", "to_fact_id":"f3", "type":"motivates", "text":"Unsupported link", "kind":"analysis", "evidence":obj["facts"][2]["evidence"]}]
        return obj
    card = generate(reading_setup, ReadingDouble(modifier=modify), regenerate=True)
    assert card["card_status"] == "draft",card
    assert not card["structured_content"]["relations"]
    assert card["module_validation_notes"]["framing"][0]["reason_code"] == "missing_relation_endpoint"
    assert card["raw_outputs"]["framing:extract"]["relations"][0]["from_fact_id"] == "other:f9"


def test_no_consent_and_paper_scope_are_blocked_before_model(reading_setup):
    doc, reading = reading_setup; double = ReadingDouble()
    card = reading.generate(doc["paper_id"], document_version_id=doc["document_version_id"], client=double)
    assert card["error"]["code"] == "source_consent_required" and not double.calls
    with pytest.raises(CopilotError): reading.generate("other-paper", document_version_id=doc["document_version_id"], client=double)


def test_saved_cards_isolated_across_papers_and_file_versions(reading_setup, service, pdf_bytes):
    doc, reading = reading_setup
    card = generate(reading_setup)
    second = service.import_pdf(pdf_bytes([SYNTHETIC_TEXT+" Revised."],title="Same title"), "revision.pdf", paper_id=doc["paper_id"])["data"]
    other = service.import_pdf(pdf_bytes([SYNTHETIC_TEXT+" Other."],title="Same title"), "other.pdf")["data"]
    assert not reading.history.list(doc["paper_id"], second["document_version_id"])
    assert not reading.history.list(other["paper_id"], card["document_version_id"])


def test_completed_card_is_immutable(reading_setup):
    c = generate(reading_setup); c["raw_outputs"]["planner"] = {"bad":"replacement"}
    with pytest.raises(CopilotError) as exc: reading_setup[1].history.save(c)
    assert exc.value.code == "card_immutable"


def test_corrupt_source_blocks_cache_and_regeneration(reading_setup, service):
    c = generate(reading_setup); service.store.path(c["file_sha256"]).unlink()
    with pytest.raises(CopilotError) as exc: generate(reading_setup)
    assert exc.value.code == "source_missing"
