"""Deterministic two-layer evidence juxtaposition. No LLM or cross-paper ranking."""
from copy import deepcopy
from itertools import combinations

from .errors import CopilotError
from .comparison_conditions import check_conditions
from .comparison_history import ComparisonHistory
from .comparison_materials import require, projected_facts, validated_context
from .reading_schema import LABELS

SCHEMA_VERSION="research-comparison-v1"
LOGIC_FIELDS=("research_questions","background","prior_work_gaps","motivation","claimed_contributions","method","author_limitations","future_work")
VALIDATION_FIELDS=("validation","findings","conclusions","author_limitations","reading_suggestions")


class ComparisonService:
    def __init__(self, catalog, *, snapshot_root=None):
        self.catalog=catalog
        self.history=ComparisonHistory(snapshot_root or catalog.service.root)

    def compare_papers(self, selections, *, save=True, comparison_id=None):
        try:
            require(isinstance(selections,list) and 2<=len(selections)<=3,"comparison_count","请选择2—3篇不同论文。")
            require(all(isinstance(s,dict) for s in selections),"comparison_invalid_selection","论文选择格式无效。")
            ids=[s.get("paper_id") for s in selections]
            require(all(isinstance(i,str) for i in ids) and len(ids)==len(set(ids)),"comparison_duplicate_paper","同一论文不能重复计入比较。")
            materials=[self.catalog.get(s) for s in selections]
            # Same bytes under two providers/aliases are still one paper version.
            hashes=[m["card"]["file_sha256"] for m in materials]
            require(len(hashes)==len(set(hashes)),"comparison_duplicate_file","相同文件不能以不同样例身份重复参加比较。")
            columns=[]
            for selection,material in zip(selections,materials):
                c=material["card"];content=c["structured_content"]
                unit=selection.get("validation_unit_id")
                require(unit is None or unit in content["validation_units"],"comparison_unit_not_found","验证单元不属于所选阅读卡。")
                projected=projected_facts(material)
                fields={}
                for field in dict.fromkeys((*LOGIC_FIELDS,*VALIDATION_FIELDS)):
                    original=deepcopy(content["fields"][field])
                    ids=original["fact_ids"]+([original["rationale_fact_id"]] if original["rationale_fact_id"] else [])
                    facts=[projected[f] for f in ids]
                    if field in ("validation","findings") and unit is not None:
                        # Unknown assignment stays visibly unassigned; never attach it to this unit.
                        facts=[f for f in facts if f["validation_unit_id"] in (unit,None)]
                    fields[field]={**original,"facts":facts,"selected_unit":unit,
                        "empty_in_selected_scope":not facts,"notice":"原字段状态不等于事实正确或完整；缺失不补0。"}
                evidence={r["evidence_key"]:r for f in projected.values() for r in f["citations"]}
                relations=[]
                audit_items={i["item_id"]:i for i in material["audit"]["items"]} if material["audit"] else {}
                audit_refs={r["evidence_id"]:r for r in material["audit"]["citations"]} if material["audit"] else {}
                for edge in content["relations"]:
                    status=audit_items.get(edge["relation_id"],{}).get("support_status","unverifiable")
                    citations=[]
                    for eid in edge["evidence_ids"]:
                        ref={**deepcopy(c["evidence_references"][eid]),"evidence_key":c["card_id"]+"::"+eid,
                             "source_support_status":audit_refs.get(eid,{}).get("support_status","unverifiable"),"user_review_status":"pending"}
                        evidence[ref["evidence_key"]]=ref;citations.append(ref)
                    relations.append({**deepcopy(edge),"relation_key":c["card_id"]+"::"+edge["relation_id"],"citations":citations,
                        "source_support_status":status,"user_review_status":"pending",
                        "assertable_causal_relation":False,"notice":"作者／模型论证联系，非独立证明的因果关系。"})
                context=validated_context(material,selection.get("context_id"),unit)
                titles=content["fields"]["title"]["fact_ids"]
                columns.append({"selection":deepcopy(selection),"source_kind":material["source_kind"],"execution_mode":c["execution_mode"],
                    "display_title":content["facts"][titles[0]]["text"] if titles else c["source_file"],
                    **{k:c[k] for k in ("source_file","file_sha256","page_count","model_id","prompt_version")},
                    "card_fingerprint":material["card_fingerprint"],"audit_fingerprint":material["audit_fingerprint"],
                    "contexts_fingerprint":material["contexts_fingerprint"],"latest_at_creation":self.catalog.latest(material),
                    "fields":fields,"relations":relations,"validation_units":deepcopy(content["validation_units"]),
                    "context":context,"evidence":evidence,"human_review":"not_performed",
                    "audit_available":material["audit"] is not None,
                    "proposed_changes_applied":False})
            numeric=[]
            for i,j in combinations(range(len(columns)),2):
                numeric.append({"left_column":i,"right_column":j,**check_conditions(columns[i]["context"],columns[j]["context"])})
            layers={"research_logic":[],"validation_results":[]}
            for name,fields in (("research_logic",LOGIC_FIELDS),("validation_results",VALIDATION_FIELDS)):
                for field in fields:
                    layers[name].append({"dimension":field,"label":LABELS[field],"cells":[col["fields"][field] for col in columns]})
            record={"schema_version":SCHEMA_VERSION,"status":"ready_with_warnings","error":None,"selections":deepcopy(selections),
                "columns":columns,"layers":layers,"numeric_condition_checks":numeric,
                "qualitative_comparison":{"available":True,"mode":"evidence_juxtaposition","semantic_similarity_inferred":False,
                    "notice":"可对照研究逻辑和方法原陈述；没有自动判断机制相同、创新领先或生成跨论文总结。"},
                "warnings":["全部资料仍待独立人工审核；来源辅助核查不等于用户通过。",
                    "部分支持／仅背景／无法确认的引文不作为充分证明，原候选仅带警告展示。",
                    "未知条件不默认一致，aligned不证明方法优劣，不生成排行榜或跨论文提升百分比。"],
                "stale":False,"stale_reasons":[],"ranking":None,"llm_requests":0}
            result=self.history.save(record,comparison_id=comparison_id) if save else record
            return self.refresh_stale(result)
        except CopilotError as exc:
            return {"schema_version":SCHEMA_VERSION,"status":"failed","error":exc.as_dict(),"llm_requests":0}
        except (KeyError,TypeError,ValueError):
            return {"schema_version":SCHEMA_VERSION,"status":"failed","error":{
                "code":"comparison_invalid_material","stage":"comparison_sources","message":"比较资料结构损坏或参数类型无效，未保存新快照。","retryable":False},"llm_requests":0}

    def refresh_stale(self, snapshot):
        result=deepcopy(snapshot);reasons=[]
        require(result.get("schema_version")==SCHEMA_VERSION,"comparison_schema_mismatch","比较快照格式不兼容。")
        for i,col in enumerate(result["columns"]):
            col["current_source_valid"]=True
            try:
                m=self.catalog.get(col["selection"])
                for key in ("card_fingerprint","audit_fingerprint","contexts_fingerprint"):
                    if m[key]!=col[key]:
                        col["current_source_valid"]=False
                        reasons.append({"column":i,"code":key+"_changed","message":"卡片、核查或条件资料已变化。"})
                latest=self.catalog.latest(m)
                if latest["card_version"]!=col["selection"]["card_version"]:
                    reasons.append({"column":i,"code":"newer_card","message":"同一文件已有新草稿版本；旧结果仍绑定旧卡。"})
                if latest["document_version_id"]!=col["selection"]["document_version_id"]:
                    reasons.append({"column":i,"code":"newer_document","message":"该论文已有新文件版本。"})
            except (CopilotError,KeyError,ValueError,TypeError) as exc:
                col["current_source_valid"]=False
                reasons.append({"column":i,"code":getattr(exc,"code","comparison_source_unavailable"),"message":"原卡片、核查资料或原文来源已不可验证。"})
        result.update(stale=bool(reasons),stale_reasons=reasons,status="stale" if reasons else "ready_with_warnings")
        for pair in result["numeric_condition_checks"]:
            historic=pair.get("snapshot_status",pair["status"])
            pair["snapshot_status"]=historic
            pair["status"]="unknown" if reasons else historic
            pair["direct_numeric_comparison_conditions_met"]=not reasons and historic=="aligned"
            pair["current_source_validation_required"]=bool(reasons)
        return result

    def load(self, comparison_id, version):
        return self.refresh_stale(self.history.get(comparison_id,version))


def compare_papers(catalog, selections, **kwargs):
    """Script-callable entry; the service class also exposes snapshot recovery."""
    return ComparisonService(catalog).compare_papers(selections,**kwargs)
