"""Per-claim isolation over the strict protocol, never semantic inference."""
from copy import deepcopy

from .errors import CopilotError
from .evidence import SUPPORT_LEVELS

POLICY_VERSION = "claim-isolation-v1"


def adjudicate_support(obj, claims, *, require_levels=False):
    from .rag import apply_support_check, require, numeric_values
    from .date_evidence import numeric_check_text
    reviews = obj.get("claim_reviews")
    require(isinstance(reviews, list) and all(isinstance(r, dict) and isinstance(r.get("claim_id"), str) for r in reviews), stage="support_verification")
    ids = [r["claim_id"] for r in reviews]
    require(len(ids) == len(set(ids)) and set(ids) == {c["claim_id"] for c in claims}, "支持检查未完整对应当前陈述，未发布模块。", "support_verification")
    by_id = {r["claim_id"]: r for r in reviews}
    # Invalid identity/coverage/enum is a malformed envelope, not an invitation
    # to guess what the model meant. Do not publish any siblings in that case.
    for claim in claims:
        r = by_id[claim["claim_id"]]
        refs = r.get("citation_support")
        require(r.get("verdict") in ("supported", "unsupported", "non_atomic") and isinstance(r.get("reason"), str) and 1 <= len(r["reason"]) <= 1000, stage="support_verification")
        require(isinstance(refs, list) and all(isinstance(e, dict) and isinstance(e.get("source_id"), str) and isinstance(e.get("supports"), bool) for e in refs), stage="support_verification")
        eids = [e["source_id"] for e in refs]
        require(len(eids) == len(set(eids)) and set(eids) == {e["source_id"] for e in claim["citations"]}, "支持检查引用集合无效，未发布模块。", "support_verification")
        for e in refs:
            if require_levels or "support_level" in e:
                require(isinstance(e.get("support_level"), str) and e["support_level"] in SUPPORT_LEVELS, "逐引用支持等级无效，未发布模块。", "support_verification")
    accepted, reasons, decisions = [], [], []
    for original in claims:
        claim, review = deepcopy(original), deepcopy(by_id[original["claim_id"]])
        refs = review["citation_support"]
        conflict = [e["source_id"] for e in refs if "support_level" in e and e["supports"] != (e["support_level"] == "supported")]
        decision = {"claim_id": claim["claim_id"], "policy_version": POLICY_VERSION, "original_review": deepcopy(review),
                    "removed_citations": [], "review_status": "unreviewed"}
        if conflict:
            decision.update(outcome="withheld_protocol_conflict", conflicting_source_ids=conflict)
            reasons.append(f"{claim['claim_id']} 未发布：引用支持等级与布尔值冲突；没有自动纠正或升级。")
        else:
            # Remove ONLY explicit background extras, with a separate individually
            # full-supported quote and a supported atomic verdict. Never prune
            # partial/conflicting/unknown evidence to make a claim pass.
            if review["verdict"] == "supported" and any(e.get("support_level") == "supported" for e in refs):
                drop = {e["source_id"] for e in refs if e.get("support_level") == "background_only" and not e["supports"]}
                decision["removed_citations"] = [{**deepcopy(e), "reason": "model_graded_background_extra_other_full_quote_kept"} for e in claim["citations"] if e["source_id"] in drop]
                claim["citations"] = [e for e in claim["citations"] if e["source_id"] not in drop]
                review["citation_support"] = [e for e in refs if e["source_id"] not in drop]
            kept, withheld = apply_support_check({"claim_reviews": [review]}, [claim], require_levels=require_levels)
            if kept:
                quotes = [r["quote"] for r in claim["citations"]]
                if not numeric_values(numeric_check_text(claim["text"], quotes)) <= numeric_values(" ".join(quotes)):
                    kept = []
                    withheld = [f"{claim['claim_id']} 未发布：保留引用不能覆盖陈述数值；不借用已移除引用。"]
            if kept:
                accepted.extend(kept)
                decision.update(outcome="accepted_model_supported_not_human_verified", retained_source_ids=[e["source_id"] for e in kept[0]["citations"]])
            else:
                reasons.extend(withheld)
                decision.update(outcome="withheld_semantic_or_non_atomic")
        decisions.append(decision)
    if decisions and all(d["outcome"] == "withheld_protocol_conflict" for d in decisions):
        raise CopilotError("invalid_evidence_output", "所有陈述的支持协议均冲突，未发布模块。", "support_verification", True)
    return accepted, reasons, decisions
