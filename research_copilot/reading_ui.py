"""Thin Streamlit reading page. Viewing stored cards makes no model requests."""
import streamlit as st

from .errors import CopilotError
from .llm import LLMConfig
from .llm_budget import DeepSeekValidationBudget
from .reading import StructuredReading
from .reading_schema import LABELS, MODULE_FIELDS, REASONS, validate_card
from .reading_prompts import PROMPT_VERSION

STATUS_LABELS = {"reported":"有原文支持（待人工核查）", "uncertain":"待核查／无法确认", "not_applicable":"不适用（有适用性依据）",
                 "not_found_in_checked_scope":"已检查范围未确认到"}
DISPLAY_ORDER = ("research_questions", "background", "prior_work_gaps", "motivation", "claimed_contributions", "method", "validation",
                 "findings", "conclusions", "author_limitations", "future_work", "reading_suggestions")
MODULE_LABELS = {"metadata":"论文基本信息", "framing":"研究问题与动机", "approach":"作者贡献与方法", "validation":"验证设计", "findings":"结果与局限"}


def _progress_label(stage):
    if stage.startswith("reading_plan"): return "正在确定阅读范围与检索表达……"
    module, _, step = stage.partition(":")
    action = {"retrieving":"查找原文", "extracting":"整理内容", "validating":"核对结构与来源", "reviewing":"核查证据支持", "review_validation":"核对支持结果", "completed":"已保存"}.get(step,"处理")
    return MODULE_LABELS.get(module,"精读")+"："+action


def _evidence(card, ids):
    for eid in ids:
        ref = card["evidence_references"][eid]
        with st.expander(f"原文依据 · PDF第{ref['pdf_page']}页 · {eid}"):
            st.text(ref["quote"])
            st.caption(f"{card['source_file']} · 页内字符[{ref['quote_char_start']}, {ref['quote_char_end']})")
            st.json({k:ref[k] for k in ("paper_id", "document_version_id", "file_sha256", "chunk_id", "quote_found", "locator_valid", "semantic_support", "review_status")})


def _field(card, key):
    content = card["structured_content"]
    field = content["fields"][key]
    st.caption(STATUS_LABELS[field["status"]]+" · "+REASONS[field["reason_code"]])
    ids = field["fact_ids"] + ([field["rationale_fact_id"]] if field["rationale_fact_id"] else [])
    for fid in ids:
        fact = content["facts"][fid]
        st.text(fact["text"])
        for module,data in card.get("modules",{}).items():
            notes=data.get("attribute_validation_notes",[])
            if any(n.get("reason_code")=="unlocatable_optional_attribute_withheld" and module+":"+str(n.get("fact_id"))==fid for n in notes):
                st.caption("此项专业属性无法按原文定位，已置为空；文字仍为待人工核查草稿，不作为结构化数值比较。")
        st.caption({"author_statement":"作者表述", "evidence_summary":"原文归纳", "analysis":"阅读分析，非作者结论"}[fact["kind"]])
        if fact["attribute"]: st.json(fact["attribute"])
        if fact["validation_unit_id"]:
            unit = content["validation_units"][fact["validation_unit_id"]]
            st.caption("关联验证单元："+unit["unit_id"]+" · "+unit["type"]+"（模型关联，待核查）")
        elif key == "findings":
            st.caption("尚未确认对应的验证单元，不用于数值横向比较。")
        _evidence(card, fact["evidence_ids"])


def render_reading(service, doc):
    reading = StructuredReading(service)
    st.subheader("单篇智能精读")
    st.caption("按研究逻辑组织的证据草稿；不是已人工确认的论文结论或创新性评价。")
    st.text("当前文件："+doc["source_file"])
    st.caption(f"PDF物理页：{doc['page_count']} · 文件版本：{doc['file_sha256'][:12]}")
    configured = False
    try:
        info = LLMConfig.from_environment().public_info()
        configured = True
        st.caption(f"配置模型：{info['provider']} / {info['model']} · {info['endpoint_host']}")
    except CopilotError as exc:
        st.info(exc.message)
    table = service.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='qa_api_budget'")
    budget = service.db.one("SELECT * FROM qa_api_budget WHERE session_id=?", (DeepSeekValidationBudget.SESSION,)) if table else None
    exhausted = bool(budget and budget["attempted_calls"] >= DeepSeekValidationBudget.MAX_CALLS)
    from .validation_session import ui_state
    independent = ui_state(doc)
    from .retest_session import ui_state as retest_ui_state
    independent = retest_ui_state(doc) or independent
    if independent:
        exhausted = independent["exhausted"]
        st.info(independent["notice"])
    authorized = doc["file_sha256"] in reading.allowed_hashes
    if exhausted and not independent:
        st.warning("原30次问答额度已耗尽，常规生成入口保持禁用；4.3独立验证额度仅用于获准的验证脚本。查看草稿不会调用模型。")
    elif exhausted:
        st.warning("当前独立额度已用完或已结束；原卡及引用仍可本地查看。")
    live=service.db.one("SELECT * FROM qa_api_budget WHERE session_id='stage4_3_p07_deepseek_flash_11calls_4cny'") if table else None
    if live:
        st.caption(f"P07独立验证：已用{live['attempted_calls']}/11次，预算预占{live['reserved_cny']}元（非实际扣费）。")
    if not authorized: st.info("此文件未获准发送；仍可查看本机已有阅读卡和原文。")
    consent = st.checkbox("我确认当前论文允许第三方处理，同意仅发送精读所需片段", key="reading_consent_"+doc["document_version_id"], disabled=not authorized)
    regenerate = st.checkbox("重新生成新版本（保留旧卡，可能产生模型费用）", key="reading_regenerate_"+doc["document_version_id"])
    allowed = configured and authorized and consent and not exhausted and doc["index_status"] == "ready"
    st.caption("五模块顺序提取，每篇最多11次请求；无自动重试。默认优先读取兼容的已保存草稿。")

    def run(resume=None):
        with st.status("正在整理当前论文……", expanded=True) as status:
            try:
                card = reading.generate(doc["paper_id"], document_version_id=doc["document_version_id"], allow_remote=consent,
                    regenerate=regenerate, resume_card_id=resume, progress=lambda s:status.update(label=_progress_label(s)))
                st.session_state["reading_card_"+doc["document_version_id"]] = card["card_id"]
                status.update(label="草稿已保存" if card["card_status"]=="draft" else "任务未完成，记录已保存", state="complete" if card["card_status"]=="draft" else "error")
            except CopilotError as exc:
                st.error(exc.message)
        st.rerun()

    if st.button("生成精读草稿／读取已保存卡片", key="reading_generate_"+doc["document_version_id"], disabled=not allowed): run()
    cards = reading.history.list(doc["paper_id"], doc["document_version_id"])
    if not cards:
        st.info("当前版本尚无阅读卡；生成需要有效的资料授权及剩余调用额度。")
        return
    by_id = {c["card_id"]:c for c in cards}
    selected = st.selectbox("已保存阅读卡版本", list(by_id), format_func=lambda c:f"v{by_id[c]['card_version']} · {by_id[c]['card_status']} · {by_id[c]['created_at']}", key="reading_card_"+doc["document_version_id"])
    card = by_id[selected]
    try:
        validate_card(card)
    except (CopilotError, KeyError, TypeError):
        st.error("已保存卡片的结构或证据引用不完整，请保留诊断记录并检查，未展示其内容。")
        return
    st.caption(f"卡片v{card['card_version']} · {card['schema_version']} · {card['model_id']} · {card['prompt_version']}")
    if card["prompt_version"] != PROMPT_VERSION:
        st.caption("这是旧版提取Prompt的真实／历史草稿，原始结果保留；新版Prompt尚需另行验证。")
    if any(card["pipeline"].get(k) != doc.get(k) for k in ("parser_version","chunker_version","index_version")):
        st.warning("这是旧解析管线的历史草稿；引文偏移对应当时版本，不自动视为当前解析文本。")
    if card["execution_mode"] == "test_double": st.warning("离线Mock／回放卡片，未进行真实LLM精读；内容不能作为提取效果。")
    if card["card_status"] == "failed":
        st.error(card["error"]["message"]+" · 阶段："+card["stage"])
        if card.get("failure_kind"):
            st.caption("失败模块："+str(card.get("failure_module") or "规划／配置")+" · 类型："+card["failure_kind"])
        st.caption("下方仅显示已完成模块的草稿，不展示校验失败的模型候选。")
        limit = independent["max_resume_depth"] if independent else 2
        if st.button("续跑失败任务为新版本", key="reading_retry_"+card["card_id"], disabled=not allowed or card.get("retry_depth",0)>=limit): run(card["card_id"])
    st.info("字符串定位、自动支持检查、人工核查是不同环节。本卡尚未经人工核查；未找到不表示全文未提及。")
    for module,data in card.get("modules",{}).items():
        if data.get("withheld_review_notes"):
            st.caption(MODULE_LABELS[module]+"：有候选被扣留；已保存的独立事实仍待人工核查。")
            with st.expander(MODULE_LABELS[module]+" · 支持状态处理记录"):
                st.json(data.get("support_decisions", []))
        rel=len(data.get("relation_validation_notes",[])); num=len(data.get("numeric_validation_notes",[]))
        if rel or num:
            st.caption(f"{MODULE_LABELS[module]}：{rel}条候选关系、{num}条数字证据不足的候选事实未发布；没有自动补齐。")
    with st.expander("论文基本信息", expanded=False):
        for key in MODULE_FIELDS["metadata"]:
            st.write(LABELS[key])
            _field(card, key)
    for key in DISPLAY_ORDER:
        with st.expander(LABELS[key], expanded=key in ("research_questions", "background")):
            _field(card, key)
    with st.expander("研究逻辑关系（仅展示有独立证据的联系）"):
        st.caption("以下为模型关联、待独立人工核查；有引文不等于已证明的因果关系。")
        content = card["structured_content"]
        if not content["relations"]: st.caption("当前没有确认可引用的关系；保留独立陈述，不自动拼出完整因果链。")
        for edge in content["relations"]:
            st.text(LABELS[content["facts"][edge["from_fact_id"]]["field"]]+" → "+LABELS[content["facts"][edge["to_fact_id"]]["field"]])
            st.text(edge["text"])
            _evidence(card, edge["evidence_ids"])
    with st.expander("提取范围与运行记录"):
        st.json({k:card.get(k) for k in ("card_id", "paper_id", "document_version_id", "file_sha256", "card_status", "stage", "model_info", "extraction_scope", "resumed_from_card_id", "model_calls", "test_double_calls", "request_traces", "human_review")})
