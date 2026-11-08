"""Streamlit adapter over the exact same parser/cache/jobs used by the web UI."""
import streamlit as st
from .workspace import Workspace
from .aliyun_reading import AliyunReading
from .aliyun_translation import status,usage
from .errors import CopilotError

@st.cache_resource
def reader(root,_service):return AliyunReading(_service,Workspace(_service.root))

def render_aliyun(service,doc,physical_page):
    reading=reader(str(service.root),service);version=doc['document_version_id'];prefix='ali_'+version
    st.subheader('阿里云机器翻译 · 双语阅读')
    st.caption('仅TranslateGeneral通用版；译文是阅读辅助，不覆盖原文、索引或精读事实。')
    config=status(reading.store)
    if not config['configured']:st.info(config['reason'])
    recorded=usage(reading.store)
    st.caption(f"本应用本月记录用量（{recorded['period']}，北京时间）：已提交/预留{recorded['submitted_characters']}；成功{recorded['successful_characters']}；本地上限{recorded['monthly_limit']}；本地剩余{recorded['remaining_characters']}。")
    st.caption('阿里云账号实际剩余额度请以云控制台为准。失败或不明确结果也保守计入本地预留。')
    consent=st.checkbox('允许当前论文必要文本发送给阿里云通用版',key=prefix+'_consent')
    mode=st.radio('阅读方式',['中英对照','仅看英文','仅看中文'],horizontal=True,key=prefix+'_mode')
    try:data=reading.page(version,physical_page)
    except CopilotError as e:st.warning(e.message);return
    blocks=data['blocks'];translatable=[b for b in blocks if not b['source_only']]
    if translatable:
        chosen=st.selectbox('选择当前页段落',[b['block_id'] for b in translatable],format_func=lambda k:next(b['original_text'].replace('\n',' ')[:90] for b in translatable if b['block_id']==k),key=prefix+f'_block_{physical_page}')
        if st.button('翻译所选段落',key=prefix+'_translate_block'):
            try:
                plan=reading.plan(version,'block',physical_page,chosen)
                job=reading.start(version,'block',physical_page,chosen,plan['plan_id'],True,consent)
                st.session_state[prefix+'_job']=job['job_id']
            except CopilotError as e:st.warning(e.message)
    a,b=st.columns(2)
    with a:page_plan=st.button('预览并翻译当前页',key=prefix+'_page_plan')
    with b:full_plan=st.button('预览全文翻译',key=prefix+'_document_plan')
    if page_plan or full_plan:
        try:st.session_state[prefix+'_plan']=reading.plan(version,'document' if full_plan else 'page',physical_page)
        except CopilotError as e:st.warning(e.message)
    planned=st.session_state.get(prefix+'_plan')
    if planned:
        with st.container(border=True):
            st.write('全文翻译计划' if planned['scope']=='document' else f"第{planned['page']}页翻译计划")
            st.write(f"原文{planned['original_characters']}字符；缓存{planned['cached_blocks']}段，节省{planned['cache_saved_characters']}提交字符；预计新增{planned['estimated_new_characters']}提交字符。")
            st.caption(f"首次最多{planned['requests_upper']}次请求，暂时性错误最多重试1次，含重试保守预留上界{planned['retry_reserved_upper']}字符；仅确认后启动。")
            confirm=st.checkbox('确认该范围、缓存与字符预算',key=prefix+'_plan_confirm')
            if st.button('确认开始翻译',disabled=not confirm,key=prefix+'_start'):
                try:
                    job=reading.start(version,planned['scope'],planned['page'],planned['block_id'],planned['plan_id'],confirm,consent)
                    st.session_state[prefix+'_job']=job['job_id'];st.session_state.pop(prefix+'_plan',None)
                except CopilotError as e:st.warning(e.message)
    history=reading.history(version)
    if history:
        selected=st.selectbox('已保存的翻译任务',['',*[j['job_id'] for j in history]],format_func=lambda v:'选择历史任务' if not v else next(j['scope']+' · '+j['status'] for j in history if j['job_id']==v),key=prefix+'_history')
        if selected:st.session_state[prefix+'_job']=selected
    @st.fragment(run_every='1s')
    def progress():
        jid=st.session_state.get(prefix+'_job')
        if not jid:return
        try:job=reading.get_job(jid)
        except CopilotError as e:st.warning(e.message);return
        st.progress((len(job['completed'])+len(job['retained']))/max(1,job['total']))
        st.caption(f"任务{job['status']}；成功{len(job['completed'])}段，失败{len(job['failed'])}段，保留原文{len(job['retained'])}块。")
        if job['status']=='running':
            if st.button('暂停翻译',key=prefix+'_pause'):reading.pause(jid);st.info('当前已提交段落结束后暂停。')
        elif job['status']!='completed':
            if st.button('继续未完成段落',key=prefix+'_resume'):
                try:reading.resume(jid,consent)
                except CopilotError as e:st.warning(e.message)
        for error in job['failed'].values():st.warning(error['message'])
        if st.button('刷新已完成译文',key=prefix+'_refresh'):st.rerun()
    progress()
    if not blocks:st.info('本页暂无可靠解析文本，原PDF保留，未将图片提交给文本翻译API。')
    for block in blocks:
        with st.container(border=True):
            kind={'paragraph':'正文','heading':'章节标题','caption':'图表说明','formula':'公式','table':'表格','reference':'参考文献','page_furniture':'页眉页脚'}[block['block_kind']]
            label={'cached':'已保存译文，尚待校对','not_translated':'尚未翻译','original_retained':'保留原文'}[block['translation_status']]
            st.caption(f"PDF物理第{physical_page}页 · {kind} · {label}")
            if mode!='仅看中文':st.write(block['original_text'].replace('\n',' '))
            if mode!='仅看英文':
                if block['translation']:st.write(block['translation'])
                else:st.info(block['source_only_reason'] or '尚未翻译。打开、刷新或切换页码不会自动请求。')
                if block['source_only'] and mode=='仅看中文':st.text(block['original_text'])
