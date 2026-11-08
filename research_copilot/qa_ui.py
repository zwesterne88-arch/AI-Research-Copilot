"""Streamlit presentation only; core QA remains independently callable."""
import streamlit as st

from .errors import CopilotError
from .llm import LLMConfig
from .rag import PaperQA
from .llm_budget import DeepSeekValidationBudget
from .qa_prompts import PROMPT_VERSION


def render_qa(service, doc):
    qa = PaperQA(service)
    configured = False
    try:
        info = LLMConfig.from_environment().public_info()
        configured = True
        st.caption(f"当前模型：{info['provider']} / {info['model']} · 发送至 {info['endpoint_host']}")
    except CopilotError as exc:
        st.info(exc.message)
    authorized = doc["file_sha256"] in qa.allowed_hashes
    has_budget_table=service.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='qa_api_budget'")
    budget=service.db.one("SELECT * FROM qa_api_budget WHERE session_id=?",(DeepSeekValidationBudget.SESSION,)) if has_budget_table else None
    exhausted=bool(budget and budget["attempted_calls"]>=DeepSeekValidationBudget.MAX_CALLS)
    from .validation_session import ui_state
    independent = ui_state(doc)
    from .retest_session import ui_state as retest_ui_state
    independent = retest_ui_state(doc) or independent
    if independent:
        exhausted = independent["exhausted"]
        st.info(independent["notice"])
    if exhausted:
        st.warning("当前独立额度已用完或已结束，新增收费调用已停止；历史答案和来源仍可查看。" if independent else "本轮30次模型验证额度已用完，新增收费调用已停止。历史答案、证据和本地检索仍可查看；新一轮额度需另行设定，不会因重启自动重置。")
    if not authorized:
        st.warning("当前文件尚未列入本轮获准发送清单；可继续本地浏览和检索，问答不会发送原文。")
    consent = st.checkbox("我确认本篇允许第三方处理，并同意本次问答仅发送必要原文片段",key="qa_consent_"+doc["document_version_id"],disabled=not authorized)
    st.write("围绕当前论文自由提问：研究背景、研究不足、动机、问题、贡献主张、方法、验证、发现或局限。")
    with st.form("qa_"+doc["document_version_id"]):
        question = st.text_area("向当前论文提问",max_chars=2000,placeholder="输入你需要核查的科研问题；不局限于实验参数。")
        send = st.form_submit_button("依据原文回答",type="primary",disabled=not(configured and authorized and consent and doc["index_status"]=="ready" and not exhausted))
    if doc["index_status"] != "ready":
        st.caption("当前版本索引未就绪，需先处理或重试。")
    st.caption("每次问答最多3次模型请求；4.5问答最多2题、与精读共用17次／5元独立额度。没有自动重试；支持检查仍需人工核查。" if independent else "每次问答最多3次模型请求；原轮累计最多30次。没有自动重试；支持检查仍需人工核查。")

    def run(text):
        labels={"query_planning":"正在理解问题并生成检索表达……","retrieving":"正在当前论文中查找来源……",
                "answering":"正在根据原文组织回答……","evidence_validation":"正在核对原句与页码……",
                "support_verification":"正在检查陈述是否得到来源支持……"}
        with st.status("正在处理当前论文问题……",expanded=True) as status:
            try:
                result=qa.answer_question(doc["paper_id"],text,document_version_id=doc["document_version_id"],allow_remote=consent,
                                         progress=lambda stage:status.update(label=labels.get(stage,"正在处理……")))
            except CopilotError as exc:
                status.update(label="本次未提交",state="error")
                st.error(exc.message)
                return
            status.update(label="问答处理结束",state="error" if result["status"]=="failed" else "complete")
        st.rerun()

    if send:
        if not question.strip():st.warning("请先输入问题。")
        else:run(question.strip())
    records=qa.history.list(doc["paper_id"],doc["document_version_id"],limit=5)
    seen=set()
    for index,record in enumerate(records):
        st.divider()
        if record.get("execution_mode")=="test_double":st.warning("离线Mock问答记录，不是真实LLM回答；仅用于程序验证。")
        st.caption("历史尝试" if record["question"] in seen else "当前问题的最新记录")
        seen.add(record["question"])
        st.text("问题："+record["question"])
        if record.get("prompt_version") != PROMPT_VERSION:
            st.caption("旧版历史记录：保留当时结果；没有自动应用本轮范围复核或引用隔离策略。")
        if record["status"] == "failed":
            st.error(record["error"]["message"])
            if record["error"].get("retryable") and configured and authorized and consent and not exhausted:
                if st.button("手动重试此问题",key="retry_"+record["run_id"]):run(record["question"])
        elif record["status"] == "processing":
            st.info("此请求正在处理或曾被中断，尚未发布答案；不会自动重新收费调用。")
        elif record["status"] == "insufficient_evidence":
            st.warning("当前检索到的原文不足以确认该问题。")
        else:
            st.caption("离线Mock回放 · 不代表真实模型效果" if record.get("execution_mode")=="test_double" else "基于当前论文的模型回答 · 自动支持检查完成，尚未经人工核查")
            st.caption("引用存在、原文可定位、内容支持陈述是三个不同检查；自动检查不能替代事实复核。")
            for claim in record["claims"]:
                kind={"author_statement":"作者表述","evidence_summary":"原文归纳","analysis":"基于证据的分析"}[claim["kind"]]
                st.write(kind)
                st.text(claim["text"])
                for ref in claim["citations"]:
                    with st.expander(f"原文依据 · PDF第{ref['pdf_page']}页 · {ref['source_id']}"):
                        st.text(ref["quote"])
                        a,b=ref.get("quote_char_start",ref["char_start"]),ref.get("quote_char_end",ref["char_end"])
                        st.caption(f"{record['source_file']} · 页内字符范围[{a}, {b})")
                        st.json({k:ref[k] for k in ("chunk_id","file_sha256","document_version_id","quote_found","locator_valid","semantic_support","review_status")})
        if record.get("unanswered"):
            st.warning("以下模型未确认说明没有逐条绑定引用；其中的事实性表述也不能视为已核实原文，仍需检查来源。")
        for text in record.get("unanswered",[]):st.info("待核查的模型范围说明："+text)
        for note in record.get("scope_reviews",[]):
            with st.expander("范围复核（非新增事实） · "+note["scope_id"]):
                st.text(note["original_note"])
                st.caption(note["assessment"]+" · 模型判断，待独立人工核查")
                st.text(note["reason"])
                for ref in note["evidence"]:
                    st.caption(f"{record['source_file']} · PDF第{ref['pdf_page']}页 · {ref['source_id']}")
                    st.text(ref["quote"])
                    st.caption(ref["document_version_id"]+f" · 字符[{ref['quote_char_start']}, {ref['quote_char_end']})")
        with st.expander("本次来源范围与处理记录"):
            st.json({k:record.get(k) for k in ("run_id","status","stage","prompt_version","retrieval_version","evidence_assessment","checked_scope","retrieval_diagnostics","model_info","model_calls","request_traces","error")})
