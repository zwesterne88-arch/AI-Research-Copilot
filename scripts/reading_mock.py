"""OFFLINE TEST DOUBLE ONLY. Never imported by the application or an actual LLM path."""
from copy import deepcopy

from research_copilot.errors import CopilotError
from research_copilot.reading_schema import MODULE_FIELDS

SYNTHETIC_TEXT = ("Synthetic study fixture. Title: Shade and plant growth. Authors: Alex Example. Year: 2024. "
    "This empirical study examines shade in botany. Botanical research concerns plant growth under changing light. "
    "Prior studies examine constant light only. Because prior studies examine constant light only, the authors aim to test variable shade. "
    "The research question is how shade affects growth. The authors claim a variable-light treatment as their contribution. "
    "The method alternates light and shade to change plant exposure. A controlled greenhouse experiment tests one species. "
    "Growth reached 12 mm in the shaded group. The observed result supports a conclusion restricted to one species. "
    "The study does not establish transfer to other species. Future work will test more species. ") * 5

NEEDLES = {
    "title":("Title: Shade and plant growth.", "合成材料的标题为Shade and plant growth。"),
    "authors":("Authors: Alex Example.", "合成材料作者为Alex Example。"),
    "publication_year":("Year: 2024.", "合成材料标记年份为2024。"),
    "research_topics":("This empirical study examines shade in botany.", "合成材料主题为植物遮阴。"),
    "paper_type":("This empirical study examines shade in botany.", "合成材料自述采用实证研究。"),
    "background":("Botanical research concerns plant growth under changing light.", "合成背景是光照变化下的植物生长。"),
    "prior_work_gaps":("Prior studies examine constant light only.", "合成材料指出既有研究仅考察恒定光照。"),
    "motivation":("Because prior studies examine constant light only, the authors aim to test variable shade.", "合成作者因恒定光照研究的不足而研究变化遮阴。"),
    "research_questions":("The research question is how shade affects growth.", "合成问题是遮阴如何影响生长。"),
    "claimed_contributions":("The authors claim a variable-light treatment as their contribution.", "合成作者声称贡献是变化光照处理方案。"),
    "method":("The method alternates light and shade to change plant exposure.", "合成方法交替光照和遮阴以改变植物暴露条件。"),
    "validation":("A controlled greenhouse experiment tests one species.", "合成验证采用一种植物的温室对照实验。"),
    "findings":("Growth reached 12 mm in the shaded group.", "合成材料报告遮阴组生长12 mm。"),
    "conclusions":("The observed result supports a conclusion restricted to one species.", "合成结论限于所测试植物。"),
    "author_limitations":("The study does not establish transfer to other species.", "合成材料尚未建立对其他植物的推广性。"),
    "future_work":("Future work will test more species.", "合成作者计划测试更多植物。"),
}


class ReadingDouble:
    is_test_double = True
    def __init__(self, modifier=None, error_stage=None, validation_type="experimental", literal_replay=False):
        self.calls = []
        self.payloads = []
        self.modifier = modifier
        self.error_stage = error_stage
        self.validation_type = validation_type
        self.literal_replay = literal_replay

    def complete_json(self, stage, system, user):
        self.calls.append({"stage":stage, "status":"test_double"})
        self.payloads.append({"stage":stage, "system":system, "user":deepcopy(user)})
        if stage == self.error_stage:
            raise CopilotError("model_timeout", "Injected offline timeout, not a supplier response.", stage, True)
        if stage == "reading_planning":
            result = {"modules":{m:["study research background motivation method validation findings limitations"] for m in MODULE_FIELDS}}
        elif stage.startswith("reading_review:"):
            result = {"claim_reviews":[{"claim_id":c["claim_id"], "verdict":"supported",
                "citation_support":[{"source_id":e["source_id"], "supports":True, "support_level":"supported"} for e in c["evidence"]],
                "reason":"Offline scripted verdict; not real semantic evaluation."} for c in user["claims"]]}
        else:
            module = stage.split(":")[1]
            fields = MODULE_FIELDS[module]
            result = {"field_states":{f:{"status":"not_found_in_checked_scope", "reason_code":"not_found", "rationale_fact_id":None} for f in fields},
                      "facts":[], "validation_units":[], "relations":[]}
            for f in fields:
                if self.literal_replay:
                    if f != "background": continue
                    source = user["sources"][0]
                    raw = source["chunk_text"][:300]
                    cut = raw.rfind(" ")
                    raw = raw[:cut] if cut > 0 else raw
                    chinese = "【离线原文摘录，非LLM提取】"+raw
                else:
                    if f not in NEEDLES: continue
                    needle, chinese = NEEDLES[f]
                    if f == "validation" and self.validation_type != "experimental":
                        needle, chinese = ("A mathematical proof establishes a bound under stated assumptions.", "合成验证为给定假设下的数学证明。") if self.validation_type == "theoretical" else ("A qualitative case study examines one archive.", "合成验证采用档案案例研究。")
                    source = next(s for s in user["sources"] if needle in s["chunk_text"])
                fact = {"fact_id":f"f{len(result['facts'])+1}", "field":f, "text":chinese, "kind":"author_statement", "purpose":"value",
                        "evidence":[{"source_id":source["source_id"]}], "attribute":None, "validation_unit_id":None}
                if f == "validation":
                    fact["validation_unit_id"] = "u1"
                    result["validation_units"] = [{"unit_id":"u1", "type":self.validation_type, "anchor_fact_id":fact["fact_id"]}]
                if f == "findings":
                    fact["validation_unit_id"] = "validation:u1"
                    fact["attribute"] = {"name_raw":"Growth", "value_raw":"12", "unit_raw":"mm"}
                result["facts"].append(fact)
                result["field_states"][f].update(status="reported", reason_code="supported_in_scope")
        return self.modifier(stage, deepcopy(result), user) if self.modifier else result
