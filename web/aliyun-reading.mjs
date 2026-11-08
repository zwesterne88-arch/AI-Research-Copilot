/** On-demand bilingual reading. Rendering and polling never start a translation. */
export function createAliyunReading(c){
  const {state,el,button,row,api,select,notice,renderPanel,toast}=c;
  let loading=null,request=0,timer=null;
  const version=()=>state.paper.document.document_version_id;
  const activeKey=()=>version()+':'+(state.aliPage||1);
  const statuses={running:'翻译中',paused:'已暂停',completed:'已完成',interrupted:'已中断，可继续',finished_with_errors:'部分完成，需重试'};
  const kinds={paragraph:'正文',heading:'章节标题',caption:'图表说明',formula:'公式',table:'表格',reference:'参考文献',page_furniture:'页眉页脚'};
  async function load(force=false){
    const key=activeKey(),target=version();
    if(!force&&state.aliPageKey===key&&state.aliPageData)return;
    if(loading===key)return;loading=key;const id=++request;
    try{
      const [data,history]=await Promise.all([api('/api/aliyun/page?version='+encodeURIComponent(target)+'&page='+state.aliPage),api('/api/aliyun/jobs?version='+encodeURIComponent(target))]);
      if(target!==version()||key!==activeKey()||id!==request)return;
      state.aliPageData=data;state.aliPageKey=key;state.aliJobs=history.jobs;state.aliError=null;await renderPanel();
    }catch(e){if(target===version()){state.aliError=e.message;state.aliPageKey=key;await renderPanel();}}
    finally{if(loading===key)loading=null;}
  }
  function usage(data){
    const box=el('div','aliyun-usage');box.append(el('strong','',data.label+' · '+data.period+'（北京时间）'),
      el('p','muted','已提交/预留 '+data.submitted_characters+' · 成功 '+data.successful_characters+' · 本地上限 '+data.monthly_limit+' · 本地剩余 '+data.remaining_characters),
      el('p','muted',data.notice));return box;
  }
  async function preview(scope,block){
    const target=version(),captured={version:target,scope,page:state.aliPage,block_id:block?.block_id};
    const plan=await api('/api/aliyun/plan',captured);
    if(target!==version())return;
    const job=await api('/api/aliyun/start',{...captured,plan_id:plan.plan_id,confirm_plan:true,consent:true});
    if(target===version()){state.aliJob=job;await renderPanel();poll(job.job_id,target);}
  }
  function poll(jobId,target){
    clearTimeout(timer);
    timer=setTimeout(async()=>{
      if(!state.paper||version()!==target||state.view!=='translation')return;
      try{
        const job=await api('/api/aliyun/job?id='+encodeURIComponent(jobId));state.aliJob=job;
        if(job.status!=='running'){await load(true);await renderPanel();return;}
        await load(true);await renderPanel();poll(jobId,target);
      }catch(e){state.aliError=e.message;await renderPanel();}
    },900);
  }
  function showBlock(parent,block){
    const article=el('article','aliyun-block');article.dataset.blockId=block.block_id;
    const mode=state.aliMode||'bilingual';const heading=block.heading_level;
    article.append(el(heading?'h'+Math.min(4,heading+1):'p',heading?'':'source-citation',(kinds[block.block_kind]||'原文')+' · PDF物理第'+block.pdf_page+'页'));
    if(mode!=='chinese')article.append(el('div','source-english',block.original_text.replace(/\s+/g,' ')));
    if(mode!=='english'){
      if(block.translation)article.append(el('div','source-chinese',block.translation));
      else article.append(el('p','source-pending',block.source_only_reason||'尚未翻译；原文保留，不会因打开页面自动调用。'));
      if(block.source_only&&mode==='chinese')article.append(el('div','source-english',block.original_text.replace(/\s+/g,' ')));
    }
    const actions=row(button('↗ 定位原文',()=>c.locate(block.sources[0]),'source-return'));
    if(!block.source_only)actions.append(button(block.translation?'使用/查看缓存':'翻译本段',()=>preview('block',block),'text-btn'));
    article.append(actions);parent.append(article);
  }
  function render(p){
    if(state.aliVersion!==version()){
      state.aliVersion=version();state.aliPage=Math.max(1,Math.min(state.paper.document.page_count,c.viewer?.currentPageNumber||1));state.aliPageData=null;state.aliPageKey=null;state.aliJob=null;state.aliError=null;
    }
    p.append(el('h3','','阿里云双语阅读'),el('p','muted','仅使用TranslateGeneral通用版。译文是阅读辅助，不进入RAG原文索引。'));
    const page=select(Array.from({length:state.paper.document.page_count},(_,i)=>[String(i+1),'PDF物理第'+(i+1)+'页']),async value=>{state.aliPage=Number(value);state.aliPageData=null;await load(true);},'双语阅读页码');page.value=String(state.aliPage);
    const mode=select([['english','仅看英文'],['chinese','仅看中文'],['bilingual','中英对照']],value=>{state.aliMode=value;renderPanel();},'双语展示方式');mode.value=state.aliMode||'bilingual';
    p.append(row(page,mode),row(button('跟随PDF当前页',async()=>{state.aliPage=c.viewer?.currentPageNumber||1;await load(true);},'text-btn'),button('翻译当前页',()=>preview('page'),'primary'),button('翻译全文',()=>preview('document'),'text-btn')));
    if(state.aliError)p.append(notice(state.aliError,true));
    const data=state.aliPageData;
    if(!data||state.aliPageKey!==activeKey()){p.append(notice('正在读取已解析段落与持久化缓存…'));load();return;}

    const job=state.aliJob;
    if(job&&job.version===version()){
      const total=job.total||1,done=job.completed.length+job.retained.length;
      const box=el('div','notice');box.append(el('p','','任务 '+(statuses[job.status]||job.status)+' · '+done+' / '+total+' 块；成功 '+job.completed.length+'，失败 '+Object.keys(job.failed).length+'，保留原文 '+job.retained.length));
      const progress=el('progress');progress.max=total;progress.value=done;progress.setAttribute('aria-label','全文翻译进度');box.append(progress);
      if(job.status==='running')box.append(button('暂停翻译',async()=>{await api('/api/aliyun/pause',{job_id:job.job_id});toast('已请求暂停；当前提交中的段落结束后停止。');},'text-btn'));
      else if(job.status!=='completed')box.append(button('继续未完成段落',async()=>{state.aliJob=await api('/api/aliyun/resume',{job_id:job.job_id,consent:true});await renderPanel();poll(job.job_id,version());},'text-btn'));
      for(const failed of Object.values(job.failed))box.append(el('p','source-reading-note',failed.message));p.append(box);
      if(job.status==='running')poll(job.job_id,version());
    }
    if(state.aliJobs?.length){const history=select([['','查看历史任务'],...state.aliJobs.map(j=>[j.job_id,({block:'段落',page:'当前页',document:'全文'}[j.scope]||j.scope)+' · '+(statuses[j.status]||j.status)+' · '+j.completed.length+'/'+j.total])],async id=>{if(id){state.aliJob=await api('/api/aliyun/job?id='+encodeURIComponent(id));await renderPanel();}},'翻译任务历史');p.append(history);}
    if(!data.blocks.length)p.append(notice('本页没有可靠的解析文本；请查看原PDF，未提交图片给文本API。'));
    data.blocks.forEach(block=>showBlock(p,block));
  }
  return {render};
}
