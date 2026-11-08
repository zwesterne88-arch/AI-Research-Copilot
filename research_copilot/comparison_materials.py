"""Version-bound reading sources. Manual fixtures never enter reading_cards."""
from copy import deepcopy
import hashlib
import json
import re

from .errors import CopilotError
from .reading_schema import validate_card
from .source_audit import card_fingerprint, validate_source_audit
from .rag import normalized
from .comparison_conditions import CONDITION_LABELS


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def require(value, code, message):
    if not value:raise CopilotError(code,message,"comparison_selection")


class ComparisonCatalog:
    def __init__(self, service, *, fixture_service=None, fixtures=None, audit_paths=(), allow_test_doubles=False):
        self.service=service
        self.fixture_service=fixture_service
        self.fixtures=deepcopy(fixtures or [])
        self.audit_paths=list(audit_paths)
        self.allow_test_doubles=allow_test_doubles

    def _cards(self):
        table=self.service.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='reading_cards'")
        rows=self.service.db.rows("SELECT record_json FROM reading_cards ORDER BY card_version DESC") if table else []
        for row in rows:
            c=json.loads(row["record_json"])
            if c["card_status"]=="draft":
                yield {"card":c,"source_kind":"saved_card","service":self.service,"contexts":c.get("comparison_contexts",[])}
        for entry in self.fixtures:
            yield {**entry,"source_kind":"manual_fixture","service":self.fixture_service}

    def list_materials(self):
        return [{**{k:e["card"][k] for k in ("paper_id","document_version_id","card_id","card_version","source_file")},
                 "source_kind":e["source_kind"],"execution_mode":e["card"]["execution_mode"]} for e in self._cards()]

    def list_documents(self):
        docs={d["paper_id"]:d for d in self.service.list_documents()}
        for e in self.fixtures:
            c=e["card"]
            ids=c["structured_content"]["fields"]["title"]["fact_ids"]
            title=c["structured_content"]["facts"][ids[0]]["text"] if ids else c["source_file"]
            docs.setdefault(c["paper_id"],{"paper_id":c["paper_id"],"title":title,"source_file":c["source_file"]})
        return list(docs.values())

    def get(self, selection):
        require(isinstance(selection,dict),"comparison_invalid_selection","论文选择格式无效。")
        required=("paper_id","document_version_id","card_id","card_version","source_kind")
        require(all(k in selection for k in required),"comparison_missing_card","需要明确论文、文件及卡片版本，不能自动补选阅读卡。")
        entry=next((e for e in self._cards() if e["card"]["card_id"]==selection["card_id"] and e["source_kind"]==selection["source_kind"]),None)
        require(entry is not None,"comparison_missing_card","指定阅读卡不存在或不是可用草稿。")
        c,service=entry["card"],entry["service"]
        require((entry["source_kind"]=="manual_fixture" and c["execution_mode"]=="manual_fixture") or
                (entry["source_kind"]=="saved_card" and c["execution_mode"] in ("real_api","test_double")),
                "comparison_origin_mismatch","资料来源类型与模型／人工标识冲突。")
        require(all(c[k]==selection[k] for k in required[:-1]),"comparison_version_mismatch","所选论文、文件版本或卡片版本不匹配。")
        require(c["execution_mode"]!="test_double" or self.allow_test_doubles,"comparison_test_double","Mock卡只允许在明确隔离的离线测试中使用。")
        require(service is not None,"comparison_source_unavailable","人工样例来源库不可用。")
        try:validate_card(c)
        except (CopilotError,KeyError,TypeError,ValueError) as exc:
            raise CopilotError("comparison_invalid_card","阅读卡结构或来源绑定无效，未生成比较。","comparison_sources") from exc
        doc=service.get_document(c["document_version_id"])
        require(doc["index_status"]=="ready","comparison_source_not_ready","当前来源索引未就绪，不能作为新的已核对比较来源。")
        require(all(doc[k]==c[k] for k in ("paper_id","file_sha256","page_count")),"comparison_source_mismatch","阅读卡不属于该论文或原文件。")
        require(all(doc[k]==c["pipeline"][k] for k in ("parser_version","chunker_version","index_version")),"comparison_pipeline_stale","卡片的解析／索引版本已过期，请核查来源。")
        service.store.check(c["file_sha256"])
        pages={p["pdf_page"]:p["raw_text"] for p in service.get_pages(c["document_version_id"])}
        chunks={r["chunk_id"]:r for r in service.db.rows("SELECT * FROM chunks WHERE document_version_id=? AND paper_id=? AND file_sha256=?",(c["document_version_id"],c["paper_id"],c["file_sha256"]))}
        for ref in c["evidence_references"].values():
            ch=chunks.get(ref["chunk_id"]);start,end=ref["quote_char_start"],ref["quote_char_end"]
            valid=ch and ch["pdf_page"]==ref["pdf_page"] and ch["char_start"]<=start<end<=ch["char_end"]
            valid=valid and pages.get(ref["pdf_page"],"")[start:end]==ref["quote"]
            valid=valid and all(ch[k]==c["pipeline"][k] for k in ("parser_version","chunker_version","index_version"))
            require(valid,"comparison_invalid_evidence","原文引文、物理页或片段偏移已失效，未生成确定性比较。")
        audit=deepcopy(entry.get("audit"))
        if audit is None:
            for path in self.audit_paths:
                if path.is_file():
                    try:a=json.loads(path.read_text(encoding="utf-8"))
                    except (ValueError,OSError) as exc:raise CopilotError("comparison_invalid_audit","来源核查文件无法读取。","comparison_sources") from exc
                    if a.get("card_id")==c["card_id"]:audit=a;break
        if audit is not None:validate_source_audit(audit,c,pages)
        return {**entry,"card":deepcopy(c),"doc":doc,"audit":audit,
                "card_fingerprint":card_fingerprint(c),"audit_fingerprint":fingerprint(audit),
                "contexts_fingerprint":fingerprint(entry.get("contexts",[]))}

    def latest(self, material):
        c=material["card"]
        versions=[e["card"]["card_version"] for e in self._cards() if e["source_kind"]==material["source_kind"] and e["card"]["paper_id"]==c["paper_id"] and e["card"]["document_version_id"]==c["document_version_id"]]
        doc=material["service"].db.one("SELECT document_version_id FROM documents WHERE paper_id=? ORDER BY created_at DESC,document_version_id DESC LIMIT 1",(c["paper_id"],))
        return {"card_version":max(versions),"document_version_id":doc["document_version_id"] if doc else None}


def projected_facts(material):
    c,a=material["card"],material["audit"]
    items={i["item_id"]:i for i in a["items"]} if a else {}
    refs={r["evidence_id"]:r for r in a["citations"]} if a else {}
    facts={}
    for fid,f in c["structured_content"]["facts"].items():
        citations=[]
        for eid in f["evidence_ids"]:
            ref=c["evidence_references"][eid]
            citations.append({**deepcopy(ref),"evidence_key":c["card_id"]+"::"+eid,
                "source_support_status":refs.get(eid,{}).get("support_status","unverifiable"),
                "support_origin":"development_assistant" if a else "model_only_not_independently_checked",
                "user_review_status":"pending"})
        level=items.get(fid,{}).get("support_status","unverifiable")
        fully_checked=level=="supported" and all(r["source_support_status"]=="supported" for r in citations)
        facts[fid]={**deepcopy(f),"fact_key":c["card_id"]+"::"+fid,"citations":citations,
            "source_support_status":level,"source_checked_candidate":fully_checked,"user_review_status":"pending",
            "quality_status":"reported" if fully_checked else "uncertain",
            "audit_note":items.get(fid,{}).get("assessment_and_issues","仅有模型复核或未提供来源辅助核查；不是人工确认。"),
            "comparison_readiness":items.get(fid,{}).get("comparison_candidate",{"layer1":"pending","layer2":"pending"})}
    return facts


def validated_context(material, context_id, unit_id):
    if context_id is None:return None
    raw=next((x for x in material.get("contexts",[]) if x.get("context_id")==context_id),None)
    require(raw is not None,"comparison_context_not_found","数值语境不属于当前卡片。")
    if unit_id is not None:require(raw.get("unit_id")==unit_id,"comparison_context_mismatch","数值结果不属于当前所选验证单元。")
    c=material["card"];facts=projected_facts(material);result=facts.get(raw.get("result_fact_id"))
    require(result is not None and result["field"]=="findings" and result["kind"]!="analysis","comparison_context_mismatch","数值语境没有属于当前卡片的作者结果事实。")
    require(result.get("validation_unit_id")==raw.get("unit_id"),"comparison_context_mismatch","数值结果的验证归属不匹配，不能静默挂接。")
    attr=result.get("attribute")
    require(isinstance(attr,dict) and isinstance(attr.get("value_raw"),str) and bool(re.search(r"\d",attr["value_raw"])),
            "comparison_context_not_numeric","没有明确的原始数值属性，不能建立数值条件检查。")
    quoted=" ".join(r["quote"] for r in result["citations"])
    require(normalized(attr["value_raw"]) in normalized(quoted),"comparison_context_not_numeric","原始数值无法在该结果自己的引用中定位。")
    dims={}
    for key in CONDITION_LABELS:
        d=deepcopy(raw.get("dimensions",{}).get(key,{"value":None,"status":"uncertain","fact_ids":[],"evidence_ids":[]}))
        bound=[facts[f] for f in d.get("fact_ids",[]) if f in facts]
        allowed={e for f in bound for e in f["evidence_ids"]}
        ids=d.get("evidence_ids",[])
        require(not ids or set(ids)<=allowed,"comparison_context_evidence_mismatch","条件引用没有绑定本卡片的对应事实。")
        same_unit=all(f.get("validation_unit_id") in (None,raw.get("unit_id")) for f in bound)
        checked=bool(bound and ids and len(bound)==len(d.get("fact_ids",[])) and same_unit)
        checked=checked and all(f["source_checked_candidate"] for f in bound)
        text=" ".join(c["evidence_references"][e]["quote"] for e in ids)
        if d.get("status")=="reported":checked=checked and isinstance(d.get("value"),str) and bool(d["value"]) and normalized(d["value"]) in normalized(text)
        elif d.get("status")=="not_applicable":checked=checked and all(f.get("purpose")=="applicability" for f in bound)
        else:checked=False
        if key=="result_assignment":
            unit=c["structured_content"]["validation_units"].get(raw.get("unit_id"),{})
            anchor=facts.get(unit.get("anchor_fact_id"),{})
            checked=checked and raw.get("attribution_status")=="source_checked" and raw.get("unit_id") is not None and raw["result_fact_id"] in d.get("fact_ids",[]) and result["source_checked_candidate"] and anchor.get("source_checked_candidate",False)
        d["checked"]=bool(checked);dims[key]=d
    return {"context_id":context_id,"unit_id":raw.get("unit_id"),"result_fact_key":result["fact_key"],
        "attribution_status":raw.get("attribution_status","uncertain"),"dimensions":dims,
        "notice":"仅依据显式来源标注检查条件，不推断未知条件；数值仍未经独立人工审核。"}
