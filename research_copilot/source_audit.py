"""Validate an assistant's read-only audit sidecar; never approve or edit a card.

The sidecar contains source judgments and proposed corrections, not new LLM output.
It is bound to the full immutable card and exact citation locations. Consumers must
explicitly load it; the extraction cache and database are not modified.
"""
import hashlib
import json

from .errors import CopilotError
from .evidence import SUPPORT_LEVELS


def card_fingerprint(card):
    encoded = json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_source_audit(audit, card, pages):
    """Check binding/completeness/location, NOT whether the judgments are true."""
    def check(value):
        if not value:
            raise CopilotError("invalid_source_audit", "来源辅助核查与卡片或原文不匹配，不能采用该核查记录。", "source_audit")

    check(audit.get("schema_version") == "source-audit-v1")
    check(all(audit.get(k) == card[k] for k in ("card_id", "card_version", "paper_id", "document_version_id", "file_sha256")))
    check(audit.get("card_fingerprint") == card_fingerprint(card))
    check(audit.get("reviewer_type") == "development_assistant" and audit.get("human_review") == "not_performed")
    content = card["structured_content"]
    originals = {**content["facts"], **{e["relation_id"]:e for e in content["relations"]}}
    items, citations = audit.get("items"), audit.get("citations")
    check(isinstance(items,list) and all(isinstance(i,dict) for i in items))
    check(isinstance(citations,list) and all(isinstance(i,dict) for i in citations))
    check(all(isinstance(i.get("item_id"),str) for i in items))
    check(all(isinstance(e.get("evidence_id"),str) for e in citations))
    check(len(items) == len(originals) and {i.get("item_id") for i in items} == set(originals))
    refs = card["evidence_references"]
    check(len(citations) == len(refs) and {e.get("evidence_id") for e in citations} == set(refs))
    for item in items:
        original = originals[item["item_id"]]
        check(item.get("original_statement") == original["text"] and item.get("evidence_ids") == original["evidence_ids"])
        fields = original.get("field")
        if fields is None:
            fields = {"from":originals[original["from_fact_id"]]["field"], "to":originals[original["to_fact_id"]]["field"]}
        check(item.get("current_field") == fields and item.get("research_module") == item["item_id"].split(":")[0])
        check(isinstance(item.get("support_status"),str) and item["support_status"] in SUPPORT_LEVELS and item.get("user_review_status") == "pending")
        for key in ("layer1", "layer2"):
            decision = item.get("comparison_candidate",{}).get(key)
            check(isinstance(decision,str) and decision in ("candidate", "pending", "exclude"))
            # An adverse source judgment can never be quietly upgraded to a candidate.
            if item["support_status"] != "supported": check(decision != "candidate")
    owners = {eid:i["item_id"] for i in items for eid in i["evidence_ids"]}
    for citation in citations:
        ref = refs[citation["evidence_id"]]
        check(citation.get("item_id") == owners.get(citation["evidence_id"]))
        check(isinstance(citation.get("support_status"),str) and citation["support_status"] in SUPPORT_LEVELS and citation.get("user_review_status") == "pending")
        check(all(citation.get(k) == ref[k] for k in ("pdf_page", "quote", "quote_char_start", "quote_char_end",
                  "paper_id", "document_version_id", "file_sha256", "chunk_id")))
        start, end = ref["quote_char_start"], ref["quote_char_end"]
        text = pages.get(ref["pdf_page"])
        check(isinstance(text,str) and isinstance(start,int) and isinstance(end,int) and 0 <= start < end <= len(text))
        check(text[start:end] == ref["quote"] and citation.get("locatable") is True)
    return True
