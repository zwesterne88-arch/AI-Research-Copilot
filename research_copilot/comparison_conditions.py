"""Condition checks over explicit, source-checked annotations, not semantic matching."""
CONDITION_LABELS = {
    "research_task":"研究任务", "validation_object":"数据／验证对象", "object_version":"对象版本",
    "subset_split":"子集／划分", "metric_name":"指标名称", "metric_definition":"指标定义",
    "metric_unit":"指标单位", "aggregation":"统计／聚合口径", "protocol":"实验／验证条件",
    "model_role":"模型角色", "baseline_context":"基线语境", "result_assignment":"结果所属验证",
}


def check_conditions(left, right):
    """Partial matches never imply missing conditions match; values are not ranked.

    Input dimensions must already be bound to this result's facts and citations by
    comparison_materials. Equal raw labels are a condition check, not proof of
    semantic equivalence, fairness or statistical significance.
    """
    checks=[]
    for key,label in CONDITION_LABELS.items():
        a=(left or {}).get("dimensions",{}).get(key,{})
        b=(right or {}).get("dimensions",{}).get(key,{})
        known=lambda d:d.get("checked") is True and d.get("status") in ("reported","not_applicable")
        if not known(a) or not known(b):
            status="unknown";reason="至少一篇缺少已核对的本结果条件；不默认相同。"
        elif a["status"]==b["status"]=="not_applicable":
            status="aligned";reason="两侧均有该条件不适用的来源依据。"
        elif a["status"]!=b["status"]:
            status="not_aligned";reason="所标注的适用性不同。"
        elif key=="result_assignment":
            status="aligned";reason="两侧结果分别核对了各自验证归属；不要求论文内部表号或实验ID相同。"
        elif a.get("value")==b.get("value") or (key=="metric_name" and isinstance(a.get("value"),str) and isinstance(b.get("value"),str) and a["value"].casefold()==b["value"].casefold()):
            status="aligned";reason="所提供且已核对的条件标注一致；未作同义词或语义推断。"
        else:
            status="not_aligned";reason="所提供且已核对的条件标注不同；未自动合并别名。"
        checks.append({"dimension":key,"label":label,"status":status,"reason":reason,
            "left":a,"right":b})
    states=[r["status"] for r in checks]
    if not left or not right:
        status="unknown"
    elif "not_aligned" in states:
        status="not_aligned"
    elif all(s=="aligned" for s in states):
        status="aligned"
    elif "aligned" in states:
        status="partially_aligned"
    else:
        status="unknown"
    return {"status":status,"checks":checks,"direct_numeric_comparison_conditions_met":status=="aligned",
        "notice":"aligned仅指所检查条件标注满足对照要求，不证明方法优劣、统计显著性或独立人工审核；不生成排名。"}
