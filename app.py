"""Thin local library and evidence QA page. Run: python -m streamlit run app.py"""
import os
from pathlib import Path

import streamlit as st

from research_copilot import CopilotService
from research_copilot.errors import CopilotError
from research_copilot.files import MAX_BYTES
from research_copilot.service import DEFAULT_DATA_DIR
from research_copilot.qa_ui import render_qa
from research_copilot.reading_ui import render_reading
from research_copilot.comparison_ui import render_comparison

st.set_page_config(page_title="AI Research Copilot · 本地论文库", page_icon="📄", layout="wide")


@st.cache_resource
def get_service(root):
    return CopilotService(root)


service = get_service(str(Path(os.environ.get("RESEARCH_COPILOT_DATA_DIR", DEFAULT_DATA_DIR)).resolve()))
st.title("AI Research Copilot")
if os.environ.get("RESEARCH_COPILOT_VALIDATION_LABEL"):
    st.warning(os.environ["RESEARCH_COPILOT_VALIDATION_LABEL"])
st.caption("通用科研论文助手 · 本地论文库、来源检索与证据问答")
st.info("文件、索引和草稿保存在本机，模型内容仍待独立人工审核。下方多论文比较仅并列已存资料、核对条件，不调用模型；新增模型请求须符合独立授权与剩余额度。")
documents = service.list_documents()

with st.expander("导入一篇PDF", expanded=True):
    with st.form("import_pdf", clear_on_submit=False):
        upload = st.file_uploader("选择文本型PDF", type=["pdf"], max_upload_size=25,
                                  help="首版限制25 MiB、40页；不支持OCR和加密文件。上传仅发送至本机程序。")
        paper_options = {d["paper_id"]: d["title"] for d in documents}
        association = st.selectbox("文件归属", [None, *paper_options],
                                   format_func=lambda p: "新论文（同标题也不自动合并）" if p is None else "已有论文的新版本：" + paper_options[p],
                                   help="仅在你确认属于同一篇学术论文时选择已有记录。旧版本与证据不会被覆盖。")
        submit = st.form_submit_button("导入并建立索引", type="primary")
    if submit:
        if upload is None:
            st.warning("请先选择一个PDF。")
        else:
            message = st.empty()
            bar = st.progress(0, text="正在校验文件")

            def show_progress(stage, current, total):
                if stage == "parsing":
                    bar.progress(current / total, text=f"正在逐页解析：{current}/{total}页")
                elif stage == "indexing":
                    message.info("正文已保存，正在建立本地词法索引……")
                else:
                    message.info("正在校验PDF类型、完整性、大小和文件版本……")

            try:
                result = service.import_pdf(upload.getvalue()[:MAX_BYTES + 1], upload.name, paper_id=association, progress=show_progress)
                st.session_state["last_import"] = result
                if result.get("document_version_id"):
                    st.session_state["selected_version"] = result["document_version_id"]
                st.rerun()
            except CopilotError as exc:
                st.error(exc.message)

last = st.session_state.get("last_import")
if last:
    if last["status"] == "duplicate":
        st.info("此文件已导入，已打开原记录；没有重复创建文件版本或索引。未就绪时请使用下方重试。")
    elif last.get("error"):
        st.error(f"{last['error']['message']} 失败阶段：{last['error']['stage']}；错误码：{last['error']['code']}")
    else:
        st.success("文件已处理并保存索引。" if last["status"] == "ready" else "部分页面可用，已仅对可提取文字页建立索引。")

st.subheader("论文与文件版本")
if not documents:
    st.write("论文库为空，请先导入一篇文本型PDF。")
else:
    st.dataframe([{"文件":d["source_file"], "页数":d["page_count"], "正文状态":d["parse_status"],
                   "索引状态":d["index_status"], "片段数":d["chunk_count"], "可索引页":d["indexed_page_count"]}
                  for d in documents], hide_index=True, width="stretch")
    by_version = {d["document_version_id"]:d for d in documents}
    if st.session_state.get("selected_version") not in by_version:
        st.session_state["selected_version"] = documents[0]["document_version_id"]
    selected = st.selectbox("当前文件版本", list(by_version), key="selected_version",
                            format_func=lambda v: by_version[v]["source_file"] + " · " + by_version[v]["file_sha256"][:10])
    doc = by_version[selected]
    st.write("当前来源：", doc["source_file"])
    st.caption("题名来自未核验PDF元数据／文件名，不参与自动去重。")
    if doc["error_message"]:
        st.error(f"{doc['error_message']} 阶段：{doc['error_stage']}；错误码：{doc['error_code']}")
    if doc["index_status"] != "ready":
        if st.button("重试当前文件处理", help="复用受控原文件；解析已保存时仅重建失败或过期的索引。"):
            with st.status("正在重试当前版本……", expanded=True):
                try:
                    st.session_state["last_import"] = service.retry_document(selected)
                    st.rerun()
                except CopilotError as exc:
                    st.error(exc.message)
    if doc["parse_status"] == "partial":
        st.warning("部分页面文字不足或提取失败。仅text_present页进入索引；没有命中不能证明全文未提及。")

    reading_tab, text_tab, search_tab, qa_tab, detail_tab = st.tabs(["智能精读", "正文与页码", "文本检索", "论文问答", "文件与处理记录"])
    with reading_tab:
        render_reading(service,doc)
    with text_tab:
        pages = service.get_pages(selected)
        if not pages:
            st.write("该文件尚无已保存的解析页面。")
        else:
            number = st.selectbox("PDF物理页码", [p["pdf_page"] for p in pages], key="page_" + selected)
            page = next(p for p in pages if p["pdf_page"] == number)
            st.caption(f"PDF第{number}页 · {page['status']} · {len(page['raw_text'])}字符")
            if page["error_message"]:
                st.warning(page["error_message"])
            st.text_area("原始提取文本", page["raw_text"], height=330, disabled=True, key=f"raw_{selected}_{number}")
            st.caption("文本读取不等于研究语义理解。双栏顺序、表格行列和公式均待原页核查；精读草稿可在智能精读页查看。")
            from research_copilot.aliyun_ui import render_aliyun
            render_aliyun(service,doc,number)
    with search_tab:
        st.write("仅检索上方当前论文、当前文件版本。使用英文术语或数字；尚不支持中文语义检索。")
        with st.form("search_" + selected):
            query = st.text_input("英文／数字检索词", placeholder="例如：motivation method findings", max_chars=2000)
            top_k = st.selectbox("Top-K", [3,5,10])
            search_clicked = st.form_submit_button("检索当前论文", disabled=doc["index_status"] != "ready")
        if doc["index_status"] != "ready":
            st.caption("当前索引未就绪，检索禁用；请先处理或重试。")
        if search_clicked:
            try:
                st.session_state["search_result_" + selected] = service.search(doc["paper_id"], query, document_version_id=selected, top_k=top_k)
            except CopilotError as exc:
                st.session_state.pop("search_result_" + selected, None)
                st.warning(exc.message)
        result = st.session_state.get("search_result_" + selected)
        if result:
            st.caption("本次检索：" + result["query"] + " · " + result["warning"])
            if not result["hits"]:
                st.info("没有词法匹配片段。请调整英文关键词；这不是全文无答案结论。")
            for i, hit in enumerate(result["hits"], 1):
                with st.container(border=True):
                    st.write(f"来源片段 {i} · PDF第{hit['pdf_page']}页")
                    st.caption(f"{result['source_file']} · 字符偏移[{hit['char_start']}, {hit['char_end']}) · 词法分数{hit['score']}")
                    st.text(hit["chunk_text"])
                    with st.expander("来源与版本"):
                        st.json({k:hit[k] for k in ("paper_id","document_version_id","file_sha256","chunk_id","pdf_page","parser_version","chunker_version","index_version")})
    with qa_tab:
        render_qa(service,doc)
    with detail_tab:
        st.json({k:doc[k] for k in ("paper_id","document_version_id","file_sha256","source_file","stored_path","page_count","parse_status","index_status","parser_version","chunker_version","index_version","created_at","updated_at")})
        try:
            source = service.store.check(doc["file_sha256"])
            st.download_button("下载当前版本原PDF", source.read_bytes(), file_name=doc["source_file"], mime="application/pdf")
        except CopilotError as exc:
            st.warning(exc.message)
        st.caption("本机数据目录：" + str(service.root))
with st.expander("最近处理记录（包括被拒绝的文件）"):
    st.dataframe(service.jobs(), hide_index=True, width="stretch")
st.divider()
render_comparison(service)
