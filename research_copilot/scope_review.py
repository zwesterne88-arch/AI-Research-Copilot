"""Audit unresolved notes separately from claim evidence. Never creates facts."""
from copy import deepcopy

from .rag import model_sources

SCOPE_VERSION = "unanswered-audit-v1"
VERDICTS = {"unconfirmed", "evidence_available", "response_limit", "unsupported_assertion"}


def add_scope_context(payload, notes, sources):
    result = deepcopy(payload)
    if notes:
        result["unanswered_review_context"] = {
            "items": [{"scope_id":f"U{i}", "text":text} for i, text in enumerate(notes, 1)],
            "sources": model_sources(sources),
            "restriction": "ONLY audit unanswered notes. NEVER borrow these sources to grade claims."}
    return result


def interpret_scope_reviews(output, notes, sources):
    """Valid IDs locate passages; they never prove the review's semantic verdict."""
    if not notes:
        return []
    reviews = output.get("scope_reviews")
    def unreviewed(reason):
        return [{"scope_id":f"U{i}", "original_note":t, "assessment":"not_reviewed", "reason":reason,
                 "evidence":[], "review_status":"unreviewed"} for i,t in enumerate(notes,1)]
    if reviews is None:
        return unreviewed("模型未返回范围复核；保留未确认状态，不自动补写答案。")
    expected = {f"U{i}" for i in range(1, len(notes)+1)}
    if not isinstance(reviews, list) or not all(isinstance(r,dict) and isinstance(r.get("scope_id"),str) for r in reviews):
        return unreviewed("范围复核格式无效。")
    ids = [r["scope_id"] for r in reviews]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        return unreviewed("范围复核未完整对应原说明。")
    by_id, indexed = {s["source_id"]:s for s in sources}, {r["scope_id"]:r for r in reviews}
    result = []
    for i,text in enumerate(notes,1):
        r, refs = indexed[f"U{i}"], indexed[f"U{i}"].get("source_ids")
        valid = isinstance(r.get("verdict"),str) and r["verdict"] in VERDICTS and isinstance(r.get("reason"),str) and 1 <= len(r["reason"]) <= 1000
        valid = valid and isinstance(refs,list) and all(isinstance(s,str) and s in by_id for s in refs) and len(refs) <= 3 and len(refs) == len(set(refs))
        valid = valid and (r["verdict"] != "evidence_available" or bool(refs))
        if not valid:
            return unreviewed("范围复核状态或来源无效，没有采用其证据。")
        evidence = []
        for sid in refs:
            s = by_id[sid]
            evidence.append({**{k:v for k,v in s.items() if k != "chunk_text"}, "quote":s["chunk_text"],
                             "quote_char_start":s["char_start"], "quote_char_end":s["char_end"],
                             "quote_found":True, "locator_valid":True, "review_status":"unreviewed"})
        result.append({"scope_id":f"U{i}", "original_note":text, "assessment":"model_"+r["verdict"],
                       "reason":r["reason"], "evidence":evidence, "review_status":"unreviewed"})
    return result
