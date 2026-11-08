"""Bounded, discipline-independent reading contract. Evidence checks are reused from QA."""
import re
from copy import deepcopy

from .errors import CopilotError, NumericEvidenceError
from .rag import normalized, validate_answer

SCHEMA_VERSION = "research-reading-v1"
MODULE_FIELDS = {
    "metadata": ("title", "authors", "publication_year", "research_topics", "paper_type"),
    "framing": ("background", "prior_work_gaps", "motivation", "research_questions"),
    "approach": ("claimed_contributions", "method"),
    "validation": ("validation",),
    "findings": ("findings", "conclusions", "author_limitations", "future_work", "reading_suggestions"),
}
LABELS = {"title":"标题", "authors":"作者", "publication_year":"发表年份", "research_topics":"研究主题", "paper_type":"论文类型",
          "background":"研究背景", "prior_work_gaps":"现有研究不足", "motivation":"研究动机", "research_questions":"核心研究问题",
          "claimed_contributions":"作者声称的贡献", "method":"技术方法与作用机制", "validation":"研究验证方式",
          "findings":"主要结果", "conclusions":"研究结论", "author_limitations":"作者明确的局限", "future_work":"作者未来工作",
          "reading_suggestions":"阅读分析建议（非作者结论）"}
STATES = ("reported", "uncertain", "not_applicable", "not_found_in_checked_scope")
REASONS = {"supported_in_scope":"当前证据有支持；不代表穷尽全文", "ambiguous":"来源或对应关系存在歧义", "not_found":"已检查范围未确认到",
           "not_applicable":"适用性判断有引用依据", "not_extracted":"该模块尚未完成", "support_withheld":"部分候选未通过支持检查"}
VALIDATION_TYPES = ("experimental", "theoretical", "simulation", "case_study", "qualitative", "mixed", "other", "uncertain")
ATTRIBUTE_ROLES = ("dataset", "configuration", "baseline", "metric", "assumption", "proof_step", "case_material", "other")
RELATIONS = {
    "motivates": (("prior_work_gaps", "motivation"), ("author_limitations", "future_work")),
    "frames": (("motivation", "research_questions"),),
    "addressed_by": (("research_questions", "claimed_contributions"),),
    "implemented_by": (("claimed_contributions", "method"),),
    "validated_by": (("method", "validation"),),
    "yields": (("validation", "findings"),),
    "supports": (("findings", "conclusions"),),
}


def check(condition, message="精读输出不符合Schema，未发布该模块。"):
    if not condition:
        raise CopilotError("invalid_reading_output", message, "reading_validation", True)


def empty_content():
    return {"fields": {key: {"status":"uncertain", "reason_code":"not_extracted", "fact_ids":[], "rationale_fact_id":None,
                             "checked_source_ids":[], "coverage_complete":False} for fields in MODULE_FIELDS.values() for key in fields},
            "facts":{}, "validation_units":{}, "relations":[]}


def _id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9_]{0,23}", value))


def isolate_invalid_relations(obj, previous):
    """Optional bad graph edges must not turn independently valid facts into a total failure.

    Do not repair, reverse or invent an edge. The original response remains untouched.
    Fact/schema/evidence checks still run on the retained candidate, fail closed.
    """
    if not isinstance(obj,dict) or not isinstance(obj.get("facts"),list) or not isinstance(obj.get("relations"),list):
        return obj,[]
    if len(obj["facts"]) > 8:
        return obj,[]  # Do not arbitrarily drop scientific facts to fit the limit.
    known = dict(previous["facts"])
    for fact in obj["facts"]:
        if isinstance(fact,dict) and _id(fact.get("fact_id")):
            known[fact["fact_id"]] = fact
    candidate, notes, accepted = deepcopy(obj), [], []
    seen = set()
    for edge in obj["relations"]:
        reason = None
        if not isinstance(edge,dict) or not _id(edge.get("relation_id")):
            reason = "invalid_relation_shape"
        elif not isinstance(edge.get("type"),str) or edge["type"] not in RELATIONS:
            reason = "unknown_relation_type"
        elif not isinstance(edge.get("from_fact_id"),str) or not isinstance(edge.get("to_fact_id"),str):
            reason = "invalid_relation_endpoint"
        else:
            a,b = known.get(edge["from_fact_id"]),known.get(edge["to_fact_id"])
            if not a or not b:
                reason = "missing_relation_endpoint"
            elif (a.get("field"),b.get("field")) not in RELATIONS[edge["type"]]:
                reason = "illegal_relation_field_pair_or_direction"
            elif edge["relation_id"] in seen:
                reason = "duplicate_relation_id"
            elif len(obj["facts"])+len(accepted) >= 8:
                reason = "optional_relation_exceeds_claim_limit"
        if reason:
            notes.append({"relation_id":edge.get("relation_id") if isinstance(edge,dict) else None, "reason_code":reason,
                          "action":"quarantined_without_rewriting; inspect original raw_outputs"})
        else:
            accepted.append(deepcopy(edge))
            seen.add(edge["relation_id"])
    candidate["relations"] = accepted
    return candidate,notes


def prepare_optional_attributes(obj, sources):
    """Lossless scalar-role adaptation; unverifiable optional raw metadata becomes unknown.

    Never rewrite claim text, numbers, units, evidence IDs or validation associations.
    The unchanged textual claim still passes the full evidence/numeric/support checks.
    """
    if not isinstance(obj,dict) or not isinstance(obj.get("facts"),list): return obj,[]
    candidate,notes = deepcopy(obj),[]
    by_id = {s["source_id"]:s for s in sources}
    for fact in candidate["facts"]:
        if not isinstance(fact,dict) or not isinstance(fact.get("attribute"),dict): continue
        attr = fact["attribute"]
        role = attr.get("role","other")
        if isinstance(role,list) and len(role)==1 and isinstance(role[0],str) and role[0] in ATTRIBUTE_ROLES:
            attr["role"] = role[0]
            notes.append({"fact_id":fact.get("fact_id"),"reason_code":"singleton_attribute_role_normalized","action":"JSON array to same scalar enum; raw output retained"})
        refs = fact.get("evidence")
        if not isinstance(refs,list) or not all(isinstance(e,dict) and isinstance(e.get("source_id"),str) and e["source_id"] in by_id for e in refs):
            continue  # Unknown sources remain fatal in normal validation; never remap them.
        quotes = []
        for e in refs:
            quote = e.get("quote",by_id[e["source_id"]]["chunk_text"])
            if isinstance(quote,str): quotes.append(quote)
        joined = normalized(" ".join(quotes))
        invalid = []
        for key in ("name_raw","value_raw","unit_raw","definition_raw"):
            value = attr.get(key)
            if value is not None and (not isinstance(value,str) or not normalized(value) or len(value)>(300 if key=="definition_raw" else 120) or normalized(value) not in joined):
                invalid.append(key)
        if invalid:
            fact["attribute"] = None
            notes.append({"fact_id":fact.get("fact_id"),"reason_code":"unlocatable_optional_attribute_withheld","invalid_keys":invalid,
                          "action":"attribute=null; original value stays in raw_outputs, not a comparable numeric field"})
            field = candidate.get("field_states",{}).get(fact.get("field"))
            if isinstance(field,dict) and field.get("status") in ("reported","uncertain"):
                field.update(status="uncertain",reason_code="ambiguous",rationale_fact_id=None)
    return candidate,notes


def isolate_unsupported_numeric_facts(obj, sources):
    """Reading may publish a partial draft; never repair or silently round bad numbers.

    Other malformed fields, invalid source IDs and invented quotes remain fatal.
    QA's all-or-nothing contract is unchanged. Table labels still count as numbers:
    conservative false withholding is logged rather than weakening numeric checks.
    """
    if not isinstance(obj,dict) or not isinstance(obj.get("facts"),list): return obj,[]
    candidate, notes, removed = deepcopy(obj), [], set()
    for fact in obj["facts"]:
        if not isinstance(fact,dict) or not {"text","kind","evidence","fact_id","field"} <= fact.keys(): continue
        if not _id(fact["fact_id"]) or not isinstance(fact["field"],str) or not isinstance(fact["evidence"],list): continue
        if not all(isinstance(e,dict) and isinstance(e.get("source_id"),str) for e in fact["evidence"]): continue
        try:
            validate_answer({"status":"answered","claims":[{k:fact[k] for k in ("text","kind","evidence")}],"unanswered":[]},sources)
        except NumericEvidenceError:
            removed.add(fact["fact_id"])
            notes.append({"fact_id":fact["fact_id"],"reason_code":"unsupported_numeric_fact_quarantined",
                          "action":"claim omitted; no value, unit, reference label or baseline rewritten"})
    if not removed: return candidate,notes
    units = candidate.get("validation_units",[])
    if isinstance(units,list):
        removed_units = {u["unit_id"] for u in units if isinstance(u,dict) and u.get("anchor_fact_id") in removed}
        candidate["validation_units"] = [u for u in units if not isinstance(u,dict) or u.get("unit_id") not in removed_units]
        for fact in candidate["facts"]:
            if isinstance(fact,dict) and fact.get("validation_unit_id") in removed_units:
                removed.add(fact["fact_id"])
    for fact in obj["facts"]:
        if isinstance(fact,dict) and fact.get("fact_id") in removed:
            field = candidate.get("field_states",{}).get(fact.get("field"))
            if isinstance(field,dict): field.update(status="uncertain",reason_code="support_withheld",rationale_fact_id=None)
    candidate["facts"] = [f for f in candidate["facts"] if not isinstance(f,dict) or f.get("fact_id") not in removed]
    return candidate,notes


def validate_module(obj, module, sources, previous):
    """Return validated claim list + bindings. Semantic decisions happen in the shared reviewer."""
    check(isinstance(obj, dict) and set(obj) == {"field_states", "facts", "validation_units", "relations"})
    fields, facts, units, edges = (obj[k] for k in ("field_states", "facts", "validation_units", "relations"))
    check(isinstance(fields, dict) and set(fields) == set(MODULE_FIELDS[module]))
    check(isinstance(facts, list) and isinstance(edges, list) and len(facts) + len(edges) <= 8)
    check(isinstance(units, list) and len(units) <= 3 and (module == "validation" or not units))
    local, unit_map = {}, {}
    for unit in units:
        check(isinstance(unit, dict) and set(unit) == {"unit_id", "type", "anchor_fact_id"})
        check(_id(unit["unit_id"]) and unit["unit_id"] not in unit_map and unit["type"] in VALIDATION_TYPES)
        unit_map[unit["unit_id"]] = unit
    for fact in facts:
        check(isinstance(fact, dict) and {"fact_id", "field", "text", "kind", "evidence"} <= fact.keys())
        check(fact.keys() <= {"fact_id", "field", "text", "kind", "evidence", "purpose", "attribute", "validation_unit_id"})
        check(_id(fact["fact_id"]) and fact["fact_id"] not in local and isinstance(fact["field"],str) and fact["field"] in fields)
        check(fact.get("purpose", "value") in ("value", "applicability"))
        check(isinstance(fact["evidence"],list) and 1<=len(fact["evidence"])<=3 and all(isinstance(e,dict) and isinstance(e.get("source_id"),str) for e in fact["evidence"]))
        if fact["field"] == "claimed_contributions": check(fact["kind"] == "author_statement", "作者贡献须明确为作者主张。")
        if fact["field"] == "reading_suggestions": check(fact["kind"] == "analysis", "阅读建议不能冒充作者结论。")
        local[fact["fact_id"]] = fact
        unit_id = fact.get("validation_unit_id")
        if unit_id is not None:
            check(isinstance(unit_id, str) and (unit_id in unit_map or unit_id in previous["validation_units"]), "结果引用了未知验证单元。")
    for unit in units:
        anchor = local.get(unit["anchor_fact_id"])
        check(anchor and anchor["field"] == "validation" and anchor.get("validation_unit_id") == unit["unit_id"]
              and anchor.get("purpose", "value") == "value", "验证单元缺少对应原文事实。")
    for field, state in fields.items():
        check(isinstance(state, dict) and set(state) == {"status", "reason_code", "rationale_fact_id"})
        check(isinstance(state["status"],str) and state["status"] in STATES and isinstance(state["reason_code"],str) and state["reason_code"] in REASONS)
        values = [f for f in facts if f["field"] == field and f.get("purpose", "value") == "value"]
        rationale = local.get(state["rationale_fact_id"]) if isinstance(state["rationale_fact_id"], str) else None
        if state["status"] == "reported":
            check(bool(values) and state["reason_code"] == "supported_in_scope" and state["rationale_fact_id"] is None)
        elif state["status"] == "not_applicable":
            check(not values and rationale and rationale["field"] == field and rationale.get("purpose") == "applicability"
                  and state["reason_code"] == "not_applicable", "不适用必须有可核查的适用性依据。")
        elif state["status"] == "not_found_in_checked_scope":
            check(not values and state["rationale_fact_id"] is None and state["reason_code"] == "not_found")
        else:
            check(state["rationale_fact_id"] is None and state["reason_code"] in ("ambiguous", "not_extracted", "support_withheld", "not_found"))
            if state["reason_code"] == "not_found":
                check(not values, "未找到原因不能与已报告数值事实并存。")
        check(all(f.get("purpose", "value") != "applicability" or f["fact_id"] == state["rationale_fact_id"] for f in facts if f["field"] == field))
    bindings = list(facts)
    for edge in edges:
        check(isinstance(edge, dict) and set(edge) == {"relation_id", "from_fact_id", "to_fact_id", "type", "text", "kind", "evidence"})
        check(_id(edge["relation_id"]) and isinstance(edge["type"],str) and edge["type"] in RELATIONS and edge["kind"] in ("author_statement", "analysis"))
        check(isinstance(edge["from_fact_id"],str) and isinstance(edge["to_fact_id"],str))
        a = local.get(edge["from_fact_id"]) or previous["facts"].get(edge["from_fact_id"])
        b = local.get(edge["to_fact_id"]) or previous["facts"].get(edge["to_fact_id"])
        check(a and b and a.get("purpose", "value") == "value" and b.get("purpose", "value") == "value"
              and (a["field"], b["field"]) in RELATIONS[edge["type"]], "关系端点或研究逻辑方向不合法。")
        check(edge["relation_id"] not in {e.get("relation_id") for e in bindings})
        bindings.append(edge)
    if not bindings:
        return [], bindings
    claims, _ = validate_answer({"status":"answered", "claims":[{k:f[k] for k in ("text", "kind", "evidence")} for f in bindings], "unanswered":[]}, sources)
    for fact, claim in zip(bindings, claims):
        attribute = fact.get("attribute")
        if attribute is not None:
            check(isinstance(attribute, dict) and {"name_raw", "value_raw", "unit_raw"} <= set(attribute)
                  and set(attribute) <= {"name_raw", "value_raw", "unit_raw", "role", "definition_raw"})
            check(attribute.get("role","other") in ATTRIBUTE_ROLES)
            quotes = normalized(" ".join(e["quote"] for e in claim["citations"]))
            for key in ("name_raw", "value_raw", "unit_raw", "definition_raw"):
                value = attribute.get(key)
                check(value is None or isinstance(value, str) and 0 < len(value) <= (300 if key=="definition_raw" else 120) and normalized(value) in quotes,
                      "专业字段原始名称、值或单位不在本事实引用中。")
    return claims, bindings


def validate_card(card):
    """Check saved graph integrity and provenance metadata, not scientific truth."""
    check(card["schema_version"] == SCHEMA_VERSION and isinstance(card["card_version"],int) and card["card_version"] > 0)
    content, refs = card["structured_content"], card["evidence_references"]
    check(set(content) == {"fields", "facts", "validation_units", "relations"})
    check(set(content["fields"]) == {f for fields in MODULE_FIELDS.values() for f in fields})
    for key, field in content["fields"].items():
        check(field["status"] in STATES and field["reason_code"] in REASONS and field["coverage_complete"] is False)
        ids = field["fact_ids"] + ([field["rationale_fact_id"]] if field["rationale_fact_id"] else [])
        check(all(i in content["facts"] and content["facts"][i]["field"] == key for i in ids))
        check(field["status"] != "reported" or bool(field["fact_ids"]))
        check(field["status"] != "not_applicable" or field["rationale_fact_id"] is not None)
    for fid, fact in content["facts"].items():
        check(fid == fact["fact_id"] and 1 <= len(fact["evidence_ids"]) <= 3 and fact["review_status"] == "unreviewed")
        check(fact["validation_unit_id"] is None or fact["validation_unit_id"] in content["validation_units"])
        check(all(e in refs for e in fact["evidence_ids"]))
    for unit_id, unit in content["validation_units"].items():
        check(unit_id == unit["unit_id"] and unit["type"] in VALIDATION_TYPES and unit["anchor_fact_id"] in content["facts"])
        check(content["facts"][unit["anchor_fact_id"]]["validation_unit_id"] == unit_id)
    for edge in content["relations"]:
        check(edge["from_fact_id"] in content["facts"] and edge["to_fact_id"] in content["facts"] and edge["evidence_ids"])
        a, b = content["facts"][edge["from_fact_id"]], content["facts"][edge["to_fact_id"]]
        check(edge["type"] in RELATIONS and (a["field"], b["field"]) in RELATIONS[edge["type"]],
              "已保存关系的字段类型或方向不合法。")
        check(edge["review_status"] == "unreviewed")
        check(all(e in refs for e in edge["evidence_ids"]))
    for eid, ref in refs.items():
        check(ref["evidence_id"] == eid and all(ref[k] == card[k] for k in ("paper_id", "document_version_id", "file_sha256")))
        check(all(ref[k] == card["pipeline"][k] for k in ("parser_version", "chunker_version", "index_version")))
        check(isinstance(ref["pdf_page"],int) and 1 <= ref["pdf_page"] <= card["page_count"])
        check(ref["quote_found"] and ref["locator_valid"] and ref["review_status"] == "unreviewed")
        if card.get("support_policy_version") == "claim-isolation-v1":
            check(ref.get("model_support_level") == "supported" and ref.get("semantic_support") == "model_supported_not_human_verified",
                  "新策略草稿只能引用通过模型完整支持检查的证据；不等于用户审核。")
        check(len(ref["quote"]) == ref["quote_char_end"]-ref["quote_char_start"])
    return True


def card_json_schema():
    """Portable saved-card shape; Python additionally enforces graph and evidence invariants."""
    field = {"type":"object", "required":["status","reason_code","fact_ids","rationale_fact_id","checked_source_ids","coverage_complete"],
        "properties":{"status":{"enum":list(STATES)}, "reason_code":{"enum":list(REASONS)},
            "fact_ids":{"type":"array","items":{"type":"string"},"uniqueItems":True},
            "rationale_fact_id":{"type":["string","null"]}, "checked_source_ids":{"type":"array","items":{"type":"string"}},
            "coverage_complete":{"const":False}}, "additionalProperties":False}
    attribute = {"type":["object","null"], "properties":{**{k:{"type":["string","null"]} for k in ("name_raw","value_raw","unit_raw","definition_raw")},"role":{"enum":list(ATTRIBUTE_ROLES)}},
                 "required":["name_raw","value_raw","unit_raw","role","definition_raw"], "additionalProperties":False}
    fact = {"type":"object", "required":["fact_id","field","text","kind","purpose","attribute","validation_unit_id","association_status","evidence_ids","review_status"],
        "properties":{"fact_id":{"type":"string"}, "field":{"enum":list(LABELS)}, "text":{"type":"string","minLength":1,"maxLength":400},
            "kind":{"enum":["author_statement","evidence_summary","analysis"]}, "purpose":{"enum":["value","applicability"]},
            "attribute":attribute, "validation_unit_id":{"type":["string","null"]},
            "association_status":{"enum":["linked_by_model_not_human_verified","not_established"]},
            "evidence_ids":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":3}, "review_status":{"const":"unreviewed"}}, "additionalProperties":False}
    unit = {"type":"object", "required":["unit_id","type","anchor_fact_id","review_status"],
        "properties":{"unit_id":{"type":"string"}, "type":{"enum":list(VALIDATION_TYPES)}, "anchor_fact_id":{"type":"string"}, "review_status":{"const":"unreviewed"}}, "additionalProperties":False}
    relation = {"type":"object", "required":["relation_id","from_fact_id","to_fact_id","type","text","kind","evidence_ids","review_status"],
        "properties":{"relation_id":{"type":"string"}, "from_fact_id":{"type":"string"}, "to_fact_id":{"type":"string"}, "type":{"enum":list(RELATIONS)},
            "text":{"type":"string","maxLength":400}, "kind":{"enum":["author_statement","analysis"]}, "evidence_ids":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":3}, "review_status":{"const":"unreviewed"}}, "additionalProperties":False}
    return {"$schema":"https://json-schema.org/draft/2020-12/schema", "$id":"urn:ai-research-copilot:reading:v1", "title":"Saved structured research reading draft",
        "description":"Shape only. Cross-references, immutable provenance, numeric checks and semantic support require the Python pipeline and human review.",
        "type":"object", "required":["card_id","paper_id","document_version_id","file_sha256","page_count","card_version","schema_version","model_id","prompt_version","extraction_scope","card_status","structured_content","evidence_references","created_at","updated_at","execution_mode","human_review"],
        "properties":{"schema_version":{"const":SCHEMA_VERSION}, "card_version":{"type":"integer","minimum":1}, "page_count":{"type":"integer","minimum":1},
            "file_sha256":{"type":"string","pattern":"^[0-9a-f]{64}$"}, "card_status":{"enum":["processing","draft","failed"]},
            "human_review":{"const":"not_performed"}, "execution_mode":{"enum":["test_double","real_api"]},
            "structured_content":{"type":"object", "required":["fields","facts","validation_units","relations"], "additionalProperties":False,
                "properties":{"fields":{"type":"object","properties":{f:field for f in LABELS},"required":list(LABELS),"additionalProperties":False},
                    "facts":{"type":"object","additionalProperties":fact}, "validation_units":{"type":"object","additionalProperties":unit},
                    "relations":{"type":"array","items":relation}}},
            "evidence_references":{"type":"object","additionalProperties":{"type":"object","required":["evidence_id","paper_id","document_version_id","file_sha256","pdf_page","chunk_id","quote","quote_char_start","quote_char_end","quote_found","locator_valid","semantic_support","review_status"]}}}}
