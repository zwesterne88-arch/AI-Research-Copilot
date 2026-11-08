"""Structured reading orchestration over the EXISTING index, evidence checks and LLM adapter."""
from copy import deepcopy
import json
import re
import hashlib

from .errors import CopilotError
from .evidence import support_payload, validate_source_locations
from .files import writer_lock
from .model_access import prepare_client
from .qa_retrieval import RETRIEVAL_VERSION, retrieve_context
from .rag import ROOT, authorized_hashes, apply_support_check, model_sources
from .reading_history import ReadingHistory
from .reading_schema import SCHEMA_VERSION, MODULE_FIELDS, LABELS, RELATIONS, VALIDATION_TYPES, ATTRIBUTE_ROLES, STATES, REASONS, check, empty_content, validate_module, validate_card, isolate_invalid_relations, prepare_optional_attributes, isolate_unsupported_numeric_facts
from . import reading_prompts as prompts
from .support_policy import adjudicate_support, POLICY_VERSION
from .research_structure import reading_sources,VERSION as STRUCTURE_VERSION

READING_RETRIEVAL_VERSION=RETRIEVAL_VERSION+'+'+STRUCTURE_VERSION


def context_fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def contract(module):
    return {"module":module, "fields":{f:LABELS[f] for f in MODULE_FIELDS[module]}, "states":STATES,
            "reason_codes":REASONS, "validation_types":VALIDATION_TYPES, "relation_types":RELATIONS,
            "response_shape":{"field_states":{"<each required field>":{"status":"reported|uncertain|not_applicable|not_found_in_checked_scope",
                "reason_code":"enumerated code", "rationale_fact_id":None}},
                "facts":[{"fact_id":"f1", "field":"one required field", "text":"one atomic Chinese fact", "kind":"author_statement|evidence_summary|analysis",
                          "purpose":"value|applicability", "evidence":[{"source_id":"E1"}], "attribute":None, "validation_unit_id":None}],
                "validation_units":[{"unit_id":"u1", "type":"theoretical", "anchor_fact_id":"f1"}],
                "relations":[{"relation_id":"r1", "from_fact_id":"earlier_module:f1 or local f1", "to_fact_id":"f2",
                              "type":"motivates", "text":"explicitly supported link", "kind":"author_statement|analysis", "evidence":[{"source_id":"E1"}]}]},
            "attribute_shape":{"name_raw":"one verbatim original name or null", "value_raw":"one verbatim value or null", "unit_raw":"verbatim original unit or null", "definition_raw":"verbatim definition or null", "role":"other"},
            "attribute_roles":list(ATTRIBUTE_ROLES),
            "limits":{"facts_and_relations_total":8, "validation_units":3, "non_validation_module_units":"must be []"}}


def normalize_reading_plan(plan):
    """Map only known schema field queries to their existing owning module.

    A planner may repeat a field (e.g. conclusions) as an extra module key. Keep
    that search intent without inventing a sixth extraction module or dropping
    queries. Unknown keys or an overlong merged query list still fail closed.
    The original model response is never modified.
    """
    check(isinstance(plan, dict) and set(plan) == {"modules"} and isinstance(plan["modules"], dict))
    modules = plan["modules"]
    check(set(MODULE_FIELDS) <= set(modules))
    owners = {f:m for m, fields in MODULE_FIELDS.items() for f in fields}
    check(set(modules) <= set(MODULE_FIELDS) | set(owners))
    check(all(isinstance(xs, list) and 1 <= len(xs) <= 4 and all(isinstance(q, str) and 1 <= len(q) <= 160 and re.search(r"[A-Za-z]", q) for q in xs) for xs in modules.values()))
    normalized = {m:list(modules[m]) for m in MODULE_FIELDS}
    notes = []
    for field in modules.keys() - MODULE_FIELDS.keys():
        target = owners[field]
        normalized[target] = list(dict.fromkeys(normalized[target] + modules[field]))
        notes.append({"extra_field":field, "target_module":target, "action":"preserve_queries_in_owning_module_not_a_new_module"})
    check(all(1 <= len(xs) <= 4 for xs in normalized.values()))
    return {"modules":normalized}, notes


def withhold_empty_reported_fields(obj):
    """Keep unsupported status declarations unknown; never invent a value fact."""
    if not isinstance(obj, dict) or not isinstance(obj.get("field_states"), dict) or not isinstance(obj.get("facts"), list):
        return obj, []
    if not all(isinstance(f, dict) for f in obj["facts"]):
        return obj, []
    result, notes = deepcopy(obj), []
    for field, state in result["field_states"].items():
        if isinstance(state, dict) and state.get("status") == "reported" and not any(f.get("field") == field and f.get("purpose", "value") == "value" for f in result["facts"]):
            result["field_states"][field] = {"status":"uncertain", "reason_code":"support_withheld", "rationale_fact_id":None}
            notes.append({"field":field, "reason_code":"reported_without_any_value_fact_withheld"})
    return result, notes


def merge_module(content, module, obj, claims, bindings, sources):
    """Only reviewed facts/edges enter the draft; raw candidates stay private in run checkpoints."""
    content = deepcopy(content)
    evidence = {}
    mapping = {f"C{i}":b for i,b in enumerate(bindings,1)}
    kept = {mapping[c["claim_id"]]["fact_id"] for c in claims if "fact_id" in mapping[c["claim_id"]]}
    units = {u["unit_id"]:u for u in obj["validation_units"] if u["anchor_fact_id"] in kept}
    scoped = lambda identifier: identifier if ":" in identifier else module+":"+identifier
    for unit_id, unit in units.items():
        content["validation_units"][scoped(unit_id)] = {"unit_id":scoped(unit_id), "type":unit["type"], "anchor_fact_id":scoped(unit["anchor_fact_id"]), "review_status":"unreviewed"}
    deferred = []
    for claim in claims:
        binding = mapping[claim["claim_id"]]
        if "relation_id" in binding:
            deferred.append((claim, binding))
            continue
        unit = binding.get("validation_unit_id")
        if unit and scoped(unit) not in content["validation_units"]:
            kept.discard(binding["fact_id"])
            continue
        identifier = scoped(binding["fact_id"])
        attribute = binding.get("attribute")
        if attribute is not None:
            attribute = {"role":"other", "definition_raw":None, **attribute}
        refs = []
        for citation in claim["citations"]:
            eid = identifier+":"+citation["source_id"]
            evidence[eid] = {**citation, "evidence_id":eid, "module":module}
            refs.append(eid)
        content["facts"][identifier] = {"fact_id":identifier, "field":binding["field"], "text":claim["text"], "kind":claim["kind"],
            "purpose":binding.get("purpose", "value"), "attribute":attribute, "validation_unit_id":scoped(unit) if unit else None,
            "association_status":"linked_by_model_not_human_verified" if unit else "not_established", "evidence_ids":refs, "review_status":"unreviewed"}
    for claim, binding in deferred:
        a, b = scoped(binding["from_fact_id"]), scoped(binding["to_fact_id"])
        if a not in content["facts"] or b not in content["facts"]:
            continue
        identifier = module+":relation:"+binding["relation_id"]
        refs = []
        for citation in claim["citations"]:
            eid = identifier+":"+citation["source_id"]
            evidence[eid] = {**citation, "evidence_id":eid, "module":module}
            refs.append(eid)
        content["relations"].append({"relation_id":identifier, "from_fact_id":a, "to_fact_id":b, "type":binding["type"],
                                     "text":claim["text"], "kind":claim["kind"], "evidence_ids":refs, "review_status":"unreviewed"})
    for field, state in obj["field_states"].items():
        originals = [f for f in obj["facts"] if f["field"] == field]
        accepted = [f for f in originals if f["fact_id"] in kept]
        withheld = len(accepted) != len(originals)
        new_state = deepcopy(state)
        if withheld:
            new_state.update(status="uncertain", reason_code="support_withheld", rationale_fact_id=None)
        elif new_state["rationale_fact_id"]:
            new_state["rationale_fact_id"] = scoped(new_state["rationale_fact_id"])
        values = [scoped(f["fact_id"]) for f in accepted if f.get("purpose", "value") == "value"]
        # Untied result facts are retained as attributed statements, not silently assigned to a validation.
        if field == "findings" and any(content["facts"][i]["validation_unit_id"] is None for i in values):
            new_state.update(status="uncertain", reason_code="ambiguous")
        content["fields"][field] = {**new_state, "fact_ids":values, "checked_source_ids":[module+":"+s["source_id"] for s in sources], "coverage_complete":False}
    return content, evidence


class StructuredReading:
    def __init__(self, service, allowed_hashes=None):
        self.service = service
        self.history = ReadingHistory(service.db)
        self.history.recover_interrupted(service.root)
        self.allowed_hashes = authorized_hashes() if allowed_hashes is None else set(allowed_hashes)
        path = ROOT/"config/authorized_sources.json"
        self.allowed_provider = json.loads(path.read_text(encoding="utf-8")).get("provider") if path.is_file() else None

    def generate(self, paper_id, *, document_version_id, allow_remote=False, client=None, regenerate=False, resume_card_id=None, progress=None):
        doc = self.service.get_document(document_version_id)
        if doc["paper_id"] != paper_id:
            raise CopilotError("scope_not_found", "文件版本不属于当前论文。", "reading_selection")
        self.service.search(paper_id, "research", document_version_id=document_version_id, top_k=1)
        pipeline = {k:doc[k] for k in ("file_sha256", "parser_version", "chunker_version", "index_version")}
        mode = "test_double" if getattr(client, "is_test_double", False) else "real_api"
        if not regenerate and not resume_card_id:
            for saved in self.history.list(paper_id, document_version_id):
                if saved["card_status"] == "draft" and saved.get("support_policy_version") == POLICY_VERSION and saved["schema_version"] == SCHEMA_VERSION and saved["prompt_version"] == prompts.PROMPT_VERSION and saved["pipeline"] == pipeline and saved["retrieval_version"] == READING_RETRIEVAL_VERSION and saved["execution_mode"] == mode:
                    validate_card(saved)
                    for checkpoint in saved["modules"].values():
                        validate_source_locations(self.service, doc, checkpoint["sources"])
                    return {**saved, "cache_hit":True, "model_calls_this_invocation":0}
        with writer_lock(self.service.root):
            return self._generate(doc, pipeline, mode, allow_remote, client, resume_card_id, progress)

    def _generate(self, doc, pipeline, mode, allow_remote, client, resume_card_id, progress):
        record = self.history.create({"paper_id":doc["paper_id"], "document_version_id":doc["document_version_id"], "file_sha256":doc["file_sha256"],
            "source_file":doc["source_file"], "file_metadata_title_unverified":doc["title"], "schema_version":SCHEMA_VERSION,
            "page_count":doc["page_count"],
            "prompt_version":prompts.PROMPT_VERSION, "planner_version":prompts.PLAN_VERSION, "retrieval_version":READING_RETRIEVAL_VERSION,
            "pipeline":pipeline, "execution_mode":mode, "model_id":getattr(getattr(client,"config",None),"model", "test-double" if mode=="test_double" else "pending-configuration"),
            "model_info":getattr(client,"config",None).public_info() if getattr(client,"config",None) else None,
            "card_status":"processing", "stage":"configuration", "structured_content":empty_content(), "evidence_references":{},
            "modules":{}, "raw_outputs":{}, "extraction_scope":{"exhaustive_full_text_review":False, "parse_status":doc["parse_status"]},
            "error":None, "human_review":"not_performed", "cache_hit":False, "request_traces":[], "model_calls":0, "test_double_calls":0,
            "support_policy_version":POLICY_VERSION, "module_attempts":{}})
        # The configured model is recorded immutably before the first request. A pending
        # configuration is never allowed to reach a paid request or a published card.
        start_calls = len(client.calls) if client else 0
        def checkpoint(stage):
            record["stage"] = stage
            record["request_traces"] = deepcopy(client.calls[start_calls:]) if client else []
            self.history.save(record)
            if progress: progress(stage)
        try:
            client = prepare_client(self.service, doc, allow_remote=allow_remote, allowed_hashes=self.allowed_hashes,
                                    allowed_provider=self.allowed_provider, client=client)
            info = client.config.public_info() if getattr(client,"config",None) else None
            # A single initialization-only assignment is handled by the history layer.
            self.history.configure(record, info, "test-double" if mode=="test_double" else client.config.model)
            prior = self.history.get(resume_card_id) if resume_card_id else None
            if prior:
                validate_card(prior)
                check(prior["card_status"] == "failed" and all(prior[k] == record[k] for k in
                    ("paper_id", "document_version_id", "pipeline", "schema_version", "prompt_version", "planner_version", "retrieval_version", "execution_mode", "model_info", "model_id")),
                    "旧任务的文件、模型或管线不匹配，不能续跑。")
                record["resumed_from_card_id"] = prior["card_id"]
                record["retry_depth"] = prior.get("retry_depth",0)+1
                max_depth = min(getattr(client, "max_resume_depth", 2), 3)
                check(record["retry_depth"] <= max_depth, "同一失败任务已达到有限手动续跑次数；请先排查错误。")
                for module, data in prior["modules"].items():
                    if data.get("status") == "completed":
                        validate_source_locations(self.service, doc, data["sources"])
                        record["modules"][module] = deepcopy(data)
                        if module in prior["extraction_scope"]:
                            record["extraction_scope"][module] = deepcopy(prior["extraction_scope"][module])
                # Rebuild from completed checkpoints, not arbitrary failed draft state.
                for module in MODULE_FIELDS:
                    if module in record["modules"]:
                        data = record["modules"][module]
                        content, refs = merge_module(record["structured_content"], module, data["candidate"], data["accepted_claims"], data["bindings"], data["sources"])
                        record["structured_content"] = content
                        record["evidence_references"].update(refs)
                        for step in ("extract", "review"):
                            key = module+":"+step
                            if key in prior["raw_outputs"]: record["raw_outputs"][key] = deepcopy(prior["raw_outputs"][key])
                if prior.get("plan_validated") and "planner" in prior["raw_outputs"]:
                    record["raw_outputs"]["planner"] = deepcopy(prior["raw_outputs"]["planner"])
            checkpoint("reading_planning")
            plan = record["raw_outputs"].get("planner")
            if plan is None:
                pages = self.service.get_pages(doc["document_version_id"])
                usable = [p for p in pages if p["status"] == "text_present"]
                # Small distributed previews, not only the abstract; never send an entire PDF.
                sample = list(dict.fromkeys([0, len(usable)//2, max(0,len(usable)-2), max(0,len(usable)-1)]))
                preview = [{"pdf_page":usable[i]["pdf_page"], "text":usable[i]["raw_text"][:1000]} for i in sample if i < len(usable)]
                plan = client.complete_json("reading_planning", prompts.PLANNER, {"modules":{m:{f:LABELS[f] for f in fs} for m,fs in MODULE_FIELDS.items()}, "preview":preview})
                record["raw_outputs"]["planner"] = plan
                checkpoint("reading_plan_validation")
            plan, plan_notes = normalize_reading_plan(plan)
            record["planner_validation_notes"] = plan_notes
            record["plan_validation_version"] = "bounded-four-expressions-known-field-mapping-v2"
            record["plan_validated"] = True
            for module in MODULE_FIELDS:
                if module in record["modules"]: continue
                record["active_module"] = module
                checkpoint(module+":retrieving")
                diagnostics = {}
                question = "整理科研论文的" + "、".join(LABELS[f] for f in MODULE_FIELDS[module])
                sources = reading_sources(self.service, doc, module, plan["modules"][module], question, diagnostics, retriever=retrieve_context)
                validate_source_locations(self.service, doc, sources)
                record["extraction_scope"][module] = {"diagnostics":diagnostics, "pages":sorted({s["pdf_page"] for s in sources}),
                    "source_ids":[module+":"+s["source_id"] for s in sources], "exhaustive_full_text_review":False}
                if not sources:
                    raise CopilotError("reading_no_context", "当前模块没有可用来源，未生成该部分。", module+":retrieving", True)
                previous = record["structured_content"]
                extract_input = {"contract":contract(module), "sources":[{**x,"section":s.get("section_hint"),"retrieval_reason":s.get("retrieval_reason")} for x,s in zip(model_sources(sources),sources)],
                    "previous_facts":[{k:f[k] for k in ("fact_id", "field", "text", "validation_unit_id")} for f in previous["facts"].values()],
                    "previous_validation_units":list(previous["validation_units"].values())}
                signature = context_fingerprint({"input":extract_input, "sources":sources, "prompt":prompts.EXTRACT})
                attempt = {"sources":deepcopy(sources), "extract_input_fingerprint":signature, "extraction_reused":False, "review_reused":False}
                record["module_attempts"][module] = attempt
                old_attempt = prior.get("module_attempts",{}).get(module,{}) if prior else {}
                compatible = old_attempt.get("extract_input_fingerprint") == signature and old_attempt.get("sources") == sources
                checkpoint(module+":extracting")
                if compatible and module+":extract" in prior["raw_outputs"]:
                    generated = deepcopy(prior["raw_outputs"][module+":extract"])
                    attempt["extraction_reused"] = True
                else:
                    generated = client.complete_json("reading_extract:"+module, prompts.EXTRACT, extract_input)
                record["raw_outputs"][module+":extract"] = generated
                checkpoint(module+":validating")
                generated, field_notes = withhold_empty_reported_fields(generated)
                generated, attribute_notes = prepare_optional_attributes(generated,sources)
                generated, numeric_notes = isolate_unsupported_numeric_facts(generated,sources)
                generated, relation_notes = isolate_invalid_relations(generated,previous)
                record.setdefault("module_validation_notes",{})[module] = field_notes+relation_notes+attribute_notes+numeric_notes
                claims, bindings = validate_module(generated, module, sources, previous)
                if claims:
                    payload = support_payload(question, claims)
                    for claim, binding in zip(payload["claims"], bindings):
                        claim["reading_context"] = {k:binding.get(k) for k in ("field", "purpose", "attribute", "validation_unit_id", "from_fact_id", "to_fact_id", "type")}
                        unit_id = binding.get("validation_unit_id")
                        unit = next((u for u in generated["validation_units"] if u["unit_id"] == unit_id), None) or previous["validation_units"].get(unit_id)
                        local_facts = {f["fact_id"]:f for f in generated["facts"]}
                        if unit:
                            claim["reading_context"]["validation_type"] = unit["type"]
                            anchor = local_facts.get(unit["anchor_fact_id"]) or previous["facts"].get(unit["anchor_fact_id"])
                            if anchor:
                                claim["reading_context"]["validation_anchor"] = {k:anchor[k] for k in ("fact_id","field","text")}
                        if "relation_id" in binding:
                            claim["reading_context"]["endpoints"] = []
                            for identifier in (binding["from_fact_id"], binding["to_fact_id"]):
                                endpoint = local_facts.get(identifier) or previous["facts"][identifier]
                                claim["reading_context"]["endpoints"].append({k:endpoint[k] for k in ("fact_id","field","text")})
                    review_signature = context_fingerprint({"input":payload, "prompt":prompts.REVIEW})
                    attempt["review_input_fingerprint"] = review_signature
                    checkpoint(module+":reviewing")
                    if compatible and old_attempt.get("review_output_usable") and old_attempt.get("review_input_fingerprint") == review_signature and module+":review" in prior["raw_outputs"]:
                        reviewed = deepcopy(prior["raw_outputs"][module+":review"])
                        attempt["review_reused"] = True
                    else:
                        reviewed = client.complete_json("reading_review:"+module, prompts.REVIEW, payload)
                    record["raw_outputs"][module+":review"] = reviewed
                    checkpoint(module+":review_validation")
                    accepted, withheld, support_decisions = adjudicate_support(reviewed, claims, require_levels=True)
                    attempt["review_output_usable"] = True
                else:
                    accepted, withheld, support_decisions = [], [], []
                content, refs = merge_module(previous, module, generated, accepted, bindings, sources)
                record["structured_content"] = content
                record["evidence_references"].update(refs)
                record["modules"][module] = {"status":"completed", "sources":sources, "candidate":generated, "bindings":bindings,
                                            "accepted_claims":accepted, "withheld_review_notes":withheld, "relation_validation_notes":relation_notes,
                                            "attribute_validation_notes":attribute_notes,"numeric_validation_notes":numeric_notes,"field_validation_notes":field_notes,
                                            "support_decisions":support_decisions, "support_outcome":"withheld_candidates" if withheld else "model_checked"}
                checkpoint(module+":completed")
            self.service.search(doc["paper_id"], "research", document_version_id=doc["document_version_id"], top_k=1)
            validate_card(record)
            record.update(card_status="draft", stage="completed")
        except CopilotError as exc:
            kind = "support_protocol" if exc.stage == "support_verification" else "provenance" if exc.code == "invalid_source_locator" else "schema" if exc.code == "invalid_reading_output" else "retrieval" if exc.code == "reading_no_context" else "technical"
            record.update(card_status="failed", stage=exc.stage, error=exc.as_dict(), failure_kind=kind, failure_module=record.get("active_module"))
        except Exception as exc:
            record.update(card_status="failed", error={"code":"reading_internal_error", "stage":record["stage"], "message":f"精读未完成（{type(exc).__name__}），可查看失败阶段。", "retryable":True})
        finally:
            traces = client.calls[start_calls:] if client else []
            record["request_traces"] = deepcopy(traces)
            record["model_calls"] = 0 if mode=="test_double" else sum(t.get("status")!="not_sent" for t in traces)
            record["test_double_calls"] = len(traces) if mode=="test_double" else 0
            self.history.save(record)
        return record
