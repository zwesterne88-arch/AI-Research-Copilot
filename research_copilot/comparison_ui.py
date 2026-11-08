"""Thin, local comparison UI. Opening and comparing never calls a model."""
import json
from pathlib import Path

import streamlit as st

from .comparison import ComparisonService
from .comparison_materials import ComparisonCatalog, fingerprint
from .errors import CopilotError
from .evidence import SUPPORT_LEVELS
from .reading_schema import LABELS

ROOT=Path(__file__).resolve().parents[1]
FIXTURE_ROOT=ROOT/"verification/stage4_4/private"
ORIGINS={"saved_card":"已保存模型草稿", "manual_fixture":"真实论文人工整理Fixture（非模型输出、未独立审核）"}
FIELD_STATES={"reported":"有来源的原始草稿，仍待核查", "uncertain":"待核查／无法确认",
              "not_applicable":"不适用（保留原适用性依据）", "not_found_in_checked_scope":"已检查范围未确认到"}


def make_catalog(service, include_fixtures=False):
    fixtures=[];fixture_service=None
    path=FIXTURE_ROOT/"manual_materials.json"
    if include_fixtures and path.is_file():
        from .service import CopilotService
        fixture_service=CopilotService(FIXTURE_ROOT/"library")
        fixtures=json.loads(path.read_text(encoding="utf-8"))["materials"]
    return ComparisonCatalog(service,fixture_service=fixture_service,fixtures=fixtures,
                             audit_paths=[ROOT/"verification/stage4_3/source_audit.json"])


def _facts(cell, column):
    st.caption(FIELD_STATES[cell["status"]])
    if not cell["facts"]:
        st.write("当前所选范围无已保存事实；不补写或填0。")
    for fact in cell["facts"]:
        st.text(fact["text"])
        if fact["purpose"]=="applicability":st.caption("这是不适用的来源依据，不是该字段的取值。")
        st.caption({"author_statement":"作者表述／贡献主张", "evidence_summary":"来源归纳", "analysis":"阅读分析，非作者结论"}[fact["kind"]])
        st.caption("来源支持："+SUPPORT_LEVELS[fact["source_support_status"]]+" · 用户审核：待核查")
        if not fact["source_checked_candidate"]:st.warning("此项支持不充分或仅有模型检查，不能作确定性比较依据。")
        if fact["audit_note"]:
            with st.expander("核查备注 · "+fact["fact_key"]):st.text(fact["audit_note"])
        if fact["attribute"]:st.json(fact["attribute"])
        if fact["validation_unit_id"]:st.caption("原卡关联单元："+fact["validation_unit_id"]+"（关联仍待核查）")
        elif fact["field"]=="findings":st.caption("此结果归属未确认；没有挂到当前选择的实验。")
        for ref in fact["citations"]:
            with st.expander(f"原文 · PDF第{ref['pdf_page']}页 · {ref['evidence_key']}"):
                if not column.get("current_source_valid",False):st.warning("历史缓存引文；当前来源验证未通过，不能当作新核查证据。")
                st.caption(column["source_file"]+" · "+ref["document_version_id"])
                st.text(ref["quote"])
                st.caption("本条引文支持："+SUPPORT_LEVELS[ref["source_support_status"]]+"；原文可定位不等于语义正确。")
                st.caption(f"页内字符[{ref['quote_char_start']}, {ref['quote_char_end']}) · 待独立人工审核")


def _snapshot(snapshot, catalog):
    st.caption(f"比较快照 {snapshot.get('comparison_id','未保存')} · v{snapshot.get('comparison_version',0)}")
    if snapshot["stale"]:
        st.warning("该比较已过期，以下为固定旧版本快照，不自动换成新卡。请核对原因后重新生成。")
        st.json(snapshot["stale_reasons"])
    for warning in snapshot["warnings"]:st.caption(warning)
    headers=st.columns(len(snapshot["columns"]))
    for i,(container,col) in enumerate(zip(headers,snapshot["columns"])):
        with container:
            s=col["selection"];st.write(col.get("display_title",col["source_file"]));st.caption(col["source_file"])
            st.caption(ORIGINS[col["source_kind"]]+f" · 卡片v{s['card_version']}")
            if col["execution_mode"]=="test_double":st.warning("离线Mock测试卡，不是真实模型精读。")
            st.caption(s["card_id"]+" · "+col["file_sha256"][:12])
            st.caption("所选验证："+(s.get("validation_unit_id") or "全部，分别保留原关联"))
            try:
                material=catalog.get(s)
                path=material["service"].store.check(col["file_sha256"])
                st.download_button("下载该列对应原PDF",path.read_bytes(),file_name=col["source_file"],mime="application/pdf",
                    key=f"comparison_pdf_{snapshot.get('comparison_id')}_{snapshot.get('comparison_version')}_{i}")
            except CopilotError:st.caption("当前原PDF不可用；不跳转到其他文件。")
    for layer,label in (("research_logic","第一层：研究逻辑与方法"),("validation_results","第二层：验证方式与结果")):
        st.subheader(label)
        for row in snapshot["layers"][layer]:
            with st.expander(row["label"],expanded=row["dimension"] in ("research_questions","motivation","claimed_contributions","method")):
                containers=st.columns(len(row["cells"]))
                for container,cell,col in zip(containers,row["cells"],snapshot["columns"]):
                    with container:_facts(cell,col)
    with st.expander("研究逻辑关系（原卡候选，非确定性因果）"):
        for col in snapshot["columns"]:
            st.text(col["source_file"])
            for edge in col["relations"]:
                st.text(edge["text"]);st.caption(SUPPORT_LEVELS[edge["source_support_status"]]+" · 待核查；不推断跨论文因果。")
                for ref in edge["citations"]:
                    st.caption(f"PDF第{ref['pdf_page']}页 · {ref['evidence_key']} · "+SUPPORT_LEVELS[ref["source_support_status"]])
                    with st.expander("关系原文 · "+ref["evidence_key"]):st.text(ref["quote"])
    st.subheader("数值结果条件检查")
    st.caption("可以并列阅读不等于可以直接比较数值。没有自动排名、综合分或跨论文提升百分比。")
    for pair in snapshot["numeric_condition_checks"]:
        st.write(f"第{pair['left_column']+1}列 ↔ 第{pair['right_column']+1}列：{pair['status']}")
        if snapshot["stale"]:st.caption("下表保留历史条件标注，历史状态为"+pair["snapshot_status"]+"；当前不可据此直接比较数值。")
        st.dataframe([{"条件":r["label"],"左侧":r["left"].get("value"),"右侧":r["right"].get("value"),"状态":r["status"],"原因":r["reason"]} for r in pair["checks"]],hide_index=True)
        with st.expander("该组条件检查的来源 · "+str(pair["left_column"])+"/"+str(pair["right_column"])):
            shown=set()
            for row in pair["checks"]:
                for side,index in (("left",pair["left_column"]),("right",pair["right_column"])):
                    col=snapshot["columns"][index]
                    for eid in row[side].get("evidence_ids",[]):
                        key=col["selection"]["card_id"]+"::"+eid
                        if key in shown:continue
                        ref=col["evidence"].get(key)
                        if ref:
                            shown.add(key)
                            st.caption(f"第{index+1}列 · {col['source_file']} · PDF第{ref['pdf_page']}页 · {key}")
                            st.text(ref["quote"])
                            st.caption(SUPPORT_LEVELS[ref["source_support_status"]]+" · 用户待核查")
            if not shown:st.caption("没有提供可核查的数值条件来源，保持未知。")
        st.caption(pair["notice"])


def render_comparison(service):
    st.subheader("多论文比较")
    st.caption("2—3篇 · 先研究逻辑，再验证条件 · 本地结构化证据并列，不调用LLM")
    include=st.checkbox("启用真实论文人工整理开发样例（非DeepSeek精读、未独立审核）",key="comparison_use_fixtures")
    try:catalog=make_catalog(service,include)
    except (CopilotError,ValueError,OSError) as exc:
        st.error("比较资料无法加载："+str(exc));return
    engine=ComparisonService(catalog)
    docs={d["paper_id"]:d for d in catalog.list_documents()}
    materials=catalog.list_materials()
    def paper_label(pid):
        available=any(m["paper_id"]==pid for m in materials)
        return docs[pid].get("title",docs[pid]["source_file"])+( "" if available else "（暂无可用阅读卡）")
    selected=st.multiselect("选择2—3篇论文",list(docs),format_func=paper_label,key="comparison_papers")
    selections=[];ready=2<=len(selected)<=3
    for pid in selected:
        options=[m for m in materials if m["paper_id"]==pid]
        if not options:
            st.warning(paper_label(pid)+"：需要已保存草稿；不会自动调用模型或补入样例。")
            ready=False;continue
        by_id={m["card_id"]:m for m in options}
        cid=st.selectbox("资料版本 · "+paper_label(pid),list(by_id),key="comparison_card_"+pid,
            format_func=lambda c,by_id=by_id:ORIGINS[by_id[c]["source_kind"]]+f" · v{by_id[c]['card_version']} · "+by_id[c]["source_file"])
        selection={k:by_id[cid][k] for k in ("source_kind","paper_id","document_version_id","card_id","card_version")}
        try:m=catalog.get(selection)
        except CopilotError as exc:st.error(exc.message);ready=False;continue
        units=m["card"]["structured_content"]["validation_units"]
        unit=st.selectbox("验证范围 · "+m["card"]["source_file"],[None,*units],key="comparison_unit_"+cid,
            format_func=lambda u,units=units:"全部单元（保留各自归属）" if u is None else u+" · "+units[u]["type"])
        contexts={c["context_id"]:c for c in m.get("contexts",[]) if unit is None or c.get("unit_id")==unit}
        context=st.selectbox("数值条件检查语境 · "+m["card"]["source_file"],[None,*contexts],key="comparison_context_"+cid+str(unit),
            format_func=lambda x:"未选择可靠数值语境（显示未知）" if x is None else x)
        selections.append({**selection,"validation_unit_id":unit,"context_id":context})
        with st.expander("预览已保存资料 · "+m["card"]["source_file"]):
            st.caption(ORIGINS[m["source_kind"]]+"；此预览不应用任何修订建议。")
            for field in ("research_questions","motivation","claimed_contributions","method"):
                data=m["card"]["structured_content"];st.write(LABELS[field])
                facts=[data["facts"][i]["text"] for i in data["fields"][field]["fact_ids"]]
                st.text("\n".join(facts) if facts else "当前资料未确认，保留未知。")
    signature=fingerprint({"include":include,"selections":selections,"papers":selected})
    if st.button("生成双层比较并保存快照",disabled=not ready,key="comparison_generate"):
        result=engine.compare_papers(selections)
        if result["status"]=="failed":st.error(result["error"]["message"])
        else:st.session_state["comparison_active"]={"id":result["comparison_id"],"version":result["comparison_version"],"signature":signature}
    active=st.session_state.get("comparison_active")
    if active and active["signature"]!=signature:
        st.warning("当前选择已变化，旧结果不填入新论文／卡片／验证列；请重新生成。旧快照可在历史中读取。")
    elif active:
        try:_snapshot(engine.load(active["id"],active["version"]),catalog)
        except CopilotError as exc:st.error(exc.message)
    history=engine.history.list()
    if history:
        with st.expander("已保存比较（可重启恢复）"):
            lookup={h["comparison_id"]+"@"+str(h["comparison_version"]):h for h in history}
            hid=st.selectbox("比较快照",[None,*lookup],key="comparison_history")
            if hid is not None:
                h=lookup[hid]
                # Distinct widgets when the same snapshot is open in the active view.
                if active and active["signature"]==signature and (h["comparison_id"],h["comparison_version"])==(active["id"],active["version"]):
                    st.caption("此快照已在上方展示。")
                else:
                    try:_snapshot(engine.load(h["comparison_id"],h["comparison_version"]),catalog)
                    except CopilotError as exc:st.error(exc.message)
