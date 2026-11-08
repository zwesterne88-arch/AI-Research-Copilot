import {FIELD_NAMES} from './ux.mjs';

export function readingProgress(job){
  if(!job)return {label:'等待开始',done:0,total:0};
  const labels={running:'正在翻译为中文',paused:'任务已暂停',interrupted:'任务中断，可继续',finished_with_errors:'部分模块需要重试',completed:'中文精读处理完成'};
  return {label:job.stage==='saving'?'正在保存中文阅读卡':labels[job.status]||'正在处理',done:job.completed.length+job.retained.length,total:job.total};
}
export function createChineseReading(c,{scope,key,remember}){
  const {state,api,el,button,notice,renderPanel,toast}=c;
  let timer,restoring,starting;
  async function restore(){
    const target=key();if(state.chineseReadingKey===target)return;
    if(restoring?.key===target)return restoring.promise;
    const promise=(async()=>{
      const captured=scope(),result=await api('/api/chinese-reading?version='+encodeURIComponent(captured.version)+(captured.card_id?'&card_id='+encodeURIComponent(captured.card_id):''));
      if(target!==key())return;
      state.chineseReadingKey=target;state.chineseReadingJob=result.job;
      if(result.book&&!state.sourceHistoryPreview){state.sourceBook=result.book;remember(result.book);}
    })();restoring={key:target,promise};try{await promise;}finally{if(restoring?.key===target)restoring=null;}
  }
  function accept(result,target){
    if(target!==key())return false;
    state.chineseReadingKey=target;state.chineseReadingJob=result.job;
    if(result.book&&!state.sourceHistoryPreview){state.sourceBook=result.book;state.sourceBookKey=target;remember(result.book);}
    return true;
  }
  function poll(){
    clearTimeout(timer);const job=state.chineseReadingJob,target=key();if(!job||job.status!=='running')return;
    timer=setTimeout(async()=>{
      if(target!==key()||state.view!=='reading')return;
      try{
        const result=await api('/api/chinese-reading?version='+encodeURIComponent(scope().version)+'&job_id='+encodeURIComponent(job.job_id));
        if(accept(result,target)){await renderPanel();poll();}
      }catch(e){if(target===key()){state.chineseReadingError=e.message;await renderPanel();}}
    },1000);
  }
  async function resume(fields){
    const target=key(),job=state.chineseReadingJob;
    const result=await api('/api/chinese-reading/resume',{version:scope().version,job_id:job.job_id,fields});
    state.sourceHistoryPreview=false;if(accept(result,target)){await renderPanel();poll();}
  }
  async function begin(){
    const target=key();if(starting===target)return;
    const captured=scope();starting=target;state.chineseReadingError=null;
    await renderPanel();
    try{
      const result=await api('/api/chinese-reading/begin',{version:captured.version,card_id:captured.card_id,
        ...(state.sourceHistoryPreview?{snapshot_id:captured.snapshot_id}:{})});
      if(target!==key())return;
      state.sourceHistoryPreview=false;
      if(accept(result,target)){await renderPanel();poll();}
    }catch(e){if(target===key()){state.chineseReadingError=e.message;await renderPanel();}}
    finally{if(starting===target)starting=null;if(target===key())await renderPanel();}
  }
  function render(parent){
    let job=state.chineseReadingKey===key()?state.chineseReadingJob:null;
    if(state.sourceHistoryPreview&&job?.status!=='running')job=null;
    const progress=readingProgress(job);
    parent.append(button(starting===key()?'正在开始…':job?.status==='completed'?'中文精读已完成':'开始智能精读',begin,'primary wide'));
    parent.lastChild.disabled=starting===key()||job?.status==='running'||job?.status==='completed';
    if(state.card)parent.append(el('p','muted','基于历史精读 v'+state.card.card_version+'的原文依据生成中文报告。'));
    if(state.sourceHistoryPreview)parent.append(el('p','source-reading-note','当前为另存的原文范围；点击开始即可生成中文。'));
    if(state.chineseReadingError)parent.append(notice(state.chineseReadingError,true));
    if(!job)return;
    const box=el('section','chinese-reading-progress');box.setAttribute('aria-label','中文精读任务进度');box.setAttribute('aria-live','polite');
    box.append(el('strong','',progress.label),el('p','muted','本地PDF已解析 → 研究结构已识别 → 原文已定位 → 阿里云翻译 → 中文卡保存'));
    const bar=el('progress');bar.max=Math.max(1,progress.total);bar.value=progress.done;bar.setAttribute('aria-label','已处理原文段落');box.append(bar,
      el('p','',progress.done+'/'+progress.total+'段'+(Object.keys(job.failed).length?' · '+Object.keys(job.failed).length+'段待重试':'')));
    if(job.status==='running')box.append(button('暂停任务',async()=>{await api('/api/chinese-reading/pause',{version:scope().version,job_id:job.job_id});toast('已请求暂停；当前请求完成后不会继续提交新片段。');},'text-btn'));
    else if(job.status!=='completed')box.append(button('继续智能精读',()=>resume(),'primary'));
    if(Object.keys(job.failed).length||job.status==='interrupted')box.append(button('检查阿里云连接（不翻译）',async()=>{state.aliNetworkDiagnostic=await api('/api/aliyun/network');await renderPanel();},'text-btn'));
    if(state.aliNetworkDiagnostic)box.append(notice(state.aliNetworkDiagnostic.message,!state.aliNetworkDiagnostic.reachable));
    for(const [field,value] of Object.entries(job.field_status||{}))if(Object.keys(value.errors).length)box.append(button('重试'+FIELD_NAMES[field],()=>resume([field]),'text-btn'));
    if(job.message&&['paused','interrupted'].includes(job.status))box.append(notice(job.message,true));
    box.append(el('p','source-reading-note','阿里云机器译文 · 点击段落下方可查看英文原文。'));parent.append(box);poll();
  }
  return {restore,render};
}
