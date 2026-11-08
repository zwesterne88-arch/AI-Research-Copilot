import {FIELD_NAMES,GROUPS} from './ux.mjs';
import {providerReason} from './translation-ui.mjs';
import {createChineseReading} from './chinese-reading.mjs';

export function readingProse(text=''){
  const compounds=new Set(['single','multi','time','signal','state','end','well','semi','non','pre','post','data','in','on','off','long','short','high','low','cross','real','self','case','one','two','three','vehicle','privacy']);
  return text.replace(/\u00ad/g,'').replace(/[ﬁﬂﬀﬃﬄ]/g,c=>({'ﬁ':'fi','ﬂ':'fl','ﬀ':'ff','ﬃ':'ffi','ﬄ':'ffl'}[c]))
    .replace(/([A-Za-z]+)-[ \t]*\r?\n[ \t]*([A-Za-z]+)/g,(_,a,b)=>a+(compounds.has(a.toLowerCase())||a.length===1||a!==a.toLowerCase()||b[0]!==b[0].toLowerCase()?'-':'')+b)
    .replace(/^W[ \t]+e\b/gm,'We').split(/\n[ \t]*\n/).filter(p=>p.trim()).map(p=>p.replace(/\s+/g,' ').trim()).join('\n\n');
}

export function readingStatus(value={}){
  if(value.conflict)return '历史陈述的引用支持存在冲突，请对照原文核查。';
  if(!value.units?.length)return '当前已检查范围未找到相关原文。';
  if(value.coverage==='partial_related_scope')return '仅定位到部分相关原文，请结合上下文核查。';
  return ''; // Human review is independent; it never hides located content.
}
export function unitPresentation(unit){
  const tr=unit.translation;
  if(tr?.status==='complete'&&tr.text&&!tr.is_placeholder){
    let note=tr.mode==='mock'?'Mock译文演示 · 非真实翻译':tr.human_review==='reviewed'?'用户已独立校对':tr.provider==='aliyun'?'阿里云通用版译文 · 尚未人工校对':tr.provider==='google'?'Google译文 · 尚未人工校对':tr.provider==='baidu'?'百度译文 · 尚未人工校对':(['argos','libretranslate','mymemory'].includes(tr.provider)?'基础机器译文 · 术语和限定条件尚待人工校对':'AI译文 · 尚未人工校对');
    if(tr.numeric_preservation_warnings?.length)note+=' · 数字需核查：'+tr.numeric_preservation_warnings.join('、');
    if(tr.math_layout_risk)note+=' · 含公式，请对照PDF核查';
    return {text:tr.text,language:'zh',note};
  }
  return {text:unit.original_text,language:'en',note:tr?.error?.message||''};
}
export function primaryUnits(field,units){
  if(['research_questions','background','prior_work_gaps','motivation','claimed_contributions'].includes(field))return [units,[]];
  const representatives=new Map(),main=[],more=[];
  for(const u of units){
    const old=representatives.get(u.section),prose=/[.!?。][\]"”)]*\s*$/.test(u.original_text||'')&&!/^(?:Fig(?:ure)?|Table|Algorithm)\s*\d/i.test(u.original_text||'');
    if(!old||(!old.prose&&prose))representatives.set(u.section,{unit:u,prose});
  }
  for(const u of units){if(u.key_passage||representatives.get(u.section).unit===u)main.push(u);else more.push(u);}
  return [main,more];
}

export function createSourceReading(c){
  const {state,el,button,api,row,notice,select,toast,renderPanel,openEvidence}=c;
  let request=0;
  const jobs=new Map(),attempted=new Set();let queue=Promise.resolve();
  const scope=()=>({version:state.paper.document.document_version_id,card_id:state.card?.card_id,snapshot_id:state.sourceBook?.id});
  const key=()=>state.paper.document.document_version_id+':'+(state.card?.card_id||'none');
  function remember(book){if(book?.id)localStorage.setItem('source-reading-'+key(),book.id);if(!state.sourceHistoryPreview)localStorage.removeItem('source-preview-'+key());}
  const chinese=createChineseReading(c,{scope,key,remember});
  function localProvider(){return state.translationProviders?.providers.find(p=>p.id==='argos'&&p.configured&&p.auto_translate);}
  function googlePreferred(){return ['aliyun','google','baidu'].includes(state.translationProviders?.default);}
  function localChinese(book,force=false){
    const target=key(),identity=target+':'+(book.id||'initial');
    if(!localProvider()||jobs.has(target)||(!force&&attempted.has(identity)))return;
    if(!force&&Object.values(book.fields).every(f=>f.units.every(u=>u.translation?.status==='complete'&&!u.translation.is_placeholder&&u.translation.mode!=='mock')))return;
    attempted.add(identity);state.sourceLocalError=null;
    const captured={...scope(),snapshot_id:book.id};
    const job=queue.then(async()=>{
      if(target!==key()){jobs.delete(target);attempted.delete(identity);return;}
      try{
        const result=await api('/api/source-reading/local-translate',{...captured,glossary:state.glossary});
        attempted.add(target+':'+result.book.id);
        if(target===key()&&state.sourceBook?.id===book.id&&!state.sourceHistoryPreview){
          state.sourceBook=result.book;remember(result.book);state.sourceLocalError=result.translation_error?.message||null;
          jobs.delete(target);await renderPanel();
        }
      }catch(e){if(target===key()){state.sourceLocalError=e.message;jobs.delete(target);await renderPanel();}}
      finally{jobs.delete(target);}
    });jobs.set(target,job);queue=job.catch(()=>{});
  }
  async function load(force=false,snapshot){
    if(!force&&state.sourceBookKey===key()&&state.sourceBook)return state.sourceBook;
    const target=key(),version=state.paper.document.document_version_id;
    state.sourceHistoryPreview=localStorage.getItem('source-preview-'+target)==='true';
    let url='/api/source-reading?version='+encodeURIComponent(version);
    if(state.card)url+='&card_id='+encodeURIComponent(state.card.card_id);
    const selected=snapshot||localStorage.getItem('source-reading-'+target);
    if(selected)url+='&snapshot_id='+encodeURIComponent(selected);
    const result=await api(url);
    if(target!==key())return null;
    state.sourceBook=result.book;state.sourceBookKey=target;state.sourceVersions=result.versions;remember(result.book);
    await chinese.restore();return state.sourceBook;
  }
  async function retrieve(field){
    const target=key(),result=await api('/api/source-reading/retrieve',{...scope(),field});
    if(target!==key())return;
    state.sourceHistoryPreview=!!state.chineseReadingJob;
    if(state.sourceHistoryPreview)localStorage.setItem('source-preview-'+target,'true');
    state.sourceBook=result.book;state.sourceBookKey=target;remember(result.book);toast('原文阅读新版本已保存；新增范围可开始新的精读任务，历史卡未修改');await renderPanel();
  }
  function original(unit,field){
    return openEvidence(unit.sources[0],{field,sourceUnitId:unit.unit_id,claim:FIELD_NAMES[field]+' · 作者原文',sourceRanges:unit.sources,fullOriginalText:unit.original_text});
  }
  function passage(parent,unit,field){
    const n=el('article','source-passage');n.dataset.unitId=unit.unit_id;
    if(unit.ordinal)n.append(el('h4','','贡献 '+(unit.author_number||unit.ordinal)+(unit.author_marker&&!unit.author_number?' · '+unit.author_marker:'')));
    else if(field==='claimed_contributions')n.append(el('p','source-reading-note','其他位置的作者贡献陈述 · 未检测到显式贡献列表'));
    const content=unitPresentation(unit),ready=content.language==='zh'&&unit.translation?.mode!=='mock'&&unit.translation?.provider!=='mock'&&(!googlePreferred()||unit.translation?.provider!=='argos'||state.showLocalDrafts);
    if(ready&&content.note&&(unit.translation?.provider!=='aliyun'||unit.translation?.human_review==='reviewed'||unit.translation?.math_layout_risk))n.append(el('p','source-reading-note',content.note));
    n.append(el('div',ready?'source-chinese':'source-english',ready?content.text:readingProse(unit.original_text)));
    if(!ready){const error=state.chineseReadingJob?.failed?.[unit.unit_id];if(error)n.append(notice(error.message,true));else if(unit.source_only_reason)n.append(el('p','source-reading-note',unit.source_only_reason));}
    if(ready&&unit.translation?.numeric_preservation_warnings?.length)n.append(el('p','source-reading-note','数字待核查：'+unit.translation.numeric_preservation_warnings.join('、')));
    const pages=[...new Set(unit.sources.map(r=>r.pdf_page))].join('、');
    n.append(row(el('span','source-citation',unit.section+' · PDF物理第'+pages+'页'),
      button('查看英文原文',()=>original(unit,field),'source-return')));
    parent.append(n);
  }
  async function translate(unit,field){
    const target=key(),captured=scope(),provider=state.translationProvider||state.translationProviders?.default||'aliyun';
    const prepared=await api('/api/source-reading/plan',{...captured,unit_id:unit.unit_id,provider,glossary:state.glossary});
    if(target!==key())return;
    const result=await api('/api/source-reading/translate',{...captured,unit_id:unit.unit_id,provider,plan_id:prepared.plan_id,confirm_plan:true,glossary:state.glossary,consent:true,consent_provider:provider,request_id:crypto.randomUUID()});
    if(target===key()){state.sourceBook=result.book;state.sourceBookKey=target;remember(result.book);await renderPanel();}
    if(result.error)throw Error(result.error.message);
    toast('译文已保存');
  }
  async function googleField(book){
    const target=key(),captured=scope(),provider=state.translationProviders.default||'aliyun',field=state.activeField||'research_questions';
    const prepared=await api('/api/source-reading/field-plan',{...captured,provider,field,glossary:state.glossary});
    if(target!==key())return;
    const result=await api('/api/source-reading/cloud-field',{...captured,provider,field,glossary:state.glossary,plan_id:prepared.plan_id,confirm_plan:true,consent:true,consent_provider:provider});
    if(target===key()){state.sourceHistoryPreview=false;state.sourceBook=result.book;remember(result.book);await renderPanel();}
    if(result.translation_error)throw Error(result.translation_error.message);
    toast('本模块译文已保存');
  }
  function advanced(p,book){
    const d=el('details','source-advanced');d.append(el('summary','','版本、历史分析与来源详情'));
    const body=el('div','section-body');body.append(el('p','muted',(book.source_reading_version?'原文阅读版本 '+book.source_reading_version:'当前原文定位 · 尚未保存版本')+'；历史分析卡 '+(book.base_card_version?'v'+book.base_card_version:'无')+' 保留不变。重新定位或翻译会另存原文阅读新版本，不会自动补写旧卡。'));
    if(googlePreferred())body.append(button('单独补译某个模块…',()=>googleField(book),'text-btn'));
    body.append(button('重新定位并保存新版本',()=>retrieve(),'text-btn'));
    const localDrafts=el('input');localDrafts.type='checkbox';localDrafts.checked=!!state.showLocalDrafts;localDrafts.onchange=()=>{state.showLocalDrafts=localDrafts.checked;renderPanel();};const draftLabel=el('label','small-check');draftLabel.append(localDrafts,document.createTextNode(' 显示已有本地基础译文（已知可能存在术语误译，非当前服务结果）'));body.append(draftLabel);
    const available=Object.entries(book.fields).flatMap(([field,value])=>value.units.map(unit=>({field,unit})));
    if(available.length){
      const choose=select(available.map((x,i)=>[String(i),FIELD_NAMES[x.field]+' · '+x.unit.section+' · p.'+x.unit.sources[0].pdf_page]),()=>{},'需要重新翻译的段落');
      body.append(el('p','muted','重新翻译或改用其他引擎时，先查看范围与请求计划。'),choose,button('查看翻译计划',()=>{const x=available[Number(choose.value)];return translate(x.unit,x.field);},'text-btn'));
    }
    const field=select(Object.entries(FIELD_NAMES).map(([id,name])=>[id,name]),v=>state.activeField=v,'选段对应字段');field.value=state.activeField;
    body.append(el('p','muted','需要调整证据范围时，在PDF选择完整段落，再选择对应字段。采用范围会另存版本，不自动设为人工审核通过。'),field,
      button('采用当前PDF选段作为原文范围',async()=>{
        const a=state.selection;if(!a||a.version!==state.paper.document.document_version_id)throw Error('请先在当前PDF选择完整段落。');
        const target=key(),result=await api('/api/source-reading/range',{...a,...scope(),field:field.value});
        if(target!==key())return;
        state.sourceBook=result.book;state.sourceBookKey=target;remember(result.book);await renderPanel();toast('原文范围已另存新版本');
      },'text-btn'));
    const versions=(state.sourceVersions||[]).filter(v=>v.base_card_id===book.base_card_id);
    if(book.id&&!versions.some(v=>v.id===book.id))versions.unshift({id:book.id,version:book.source_reading_version,base_card_id:book.base_card_id});
    if(versions.length){const choose=select(versions.map(v=>[v.id,'原文阅读版本 '+v.version]),async id=>{state.sourceHistoryPreview=true;localStorage.setItem('source-preview-'+key(),'true');await load(true,id);await renderPanel();},'原文阅读版本');choose.value=book.id||'';body.append(choose);}
    if(state.paper.cards.length){
      const history=select(state.paper.cards.map(card=>[card.card_id,'历史分析卡 v'+card.card_version+(card.execution_mode==='test_double'?' · Mock':'')]),async id=>{
        state.card=state.paper.cards.find(card=>card.card_id===id);localStorage.setItem('card-'+state.paper.document.document_version_id,id);state.sourceBook=null;await renderPanel();
      },'历史分析卡版本');history.value=state.card?.card_id;body.append(history);
    }
    for(const [field,value] of Object.entries(book.fields)){
      const detail=el('details');detail.append(el('summary','',FIELD_NAMES[field]+' · 定位、提取与审核记录'));
      const diagnostics=value.diagnostics||{};
      detail.append(el('p','muted','原文定位：'+value.location+'；历史模型提取：'+diagnostics.extraction+'；人工核查：未由位置或模型结果推断。'));
      const content=state.card?.structured_content;
      for(const id of content?.fields[field]?.fact_ids||[])detail.append(el('p','historical-summary',content.facts[id].text));
      const raw=el('details');raw.append(el('summary','','查看结构化诊断'),el('pre','',JSON.stringify(diagnostics,null,2)));detail.append(raw);body.append(detail);
    }
    const legacy=el('details');legacy.append(el('summary','','历史格式的模型分析服务'));
    const generate=button('另建历史格式分析卡',()=>c.paidAction('/api/reading',{}));legacy.append(generate);body.append(legacy);
    d.append(body);p.append(d);
  }
  async function reading(p){
    const id=++request,target=key();
    p.append(el('h2','','智能精读'),el('p','muted','阅读中文译文，点击段落下方回到对应英文原文。'));
    const controls=el('div','chinese-reading-controls');p.append(controls);chinese.render(controls);
    const loading=el('div','notice','正在定位论文原文…');p.append(loading);
    let book;
    try{book=await load();}catch(e){if(p.isConnected)loading.replaceChildren(notice(e.message,true),button('重新定位原文',()=>renderPanel()));return;}
    if(!book||id!==request||!p.isConnected||target!==key())return;loading.remove();c.refreshIndicator?.();
    controls.replaceChildren();chinese.render(controls);
    p.append(el('p','source-availability','先定位作者原文，再自动生成中文；基础模式使用章节和证据映射，不调用DeepSeek推断。'));
    const job=state.chineseReadingKey===key()?state.chineseReadingJob:null;
    if(!job&&!Object.values(book.fields).some(f=>f.units.some(u=>u.translation?.provider==='aliyun'&&u.translation.status==='complete'))){
      p.append(notice('点击“开始智能精读”，自动定位原文、生成各模块中文并保存。'));
      const preview=el('details');preview.append(el('summary','','预览已定位的英文依据'));
      for(const [field,value] of Object.entries(book.fields)){preview.append(el('h3','',FIELD_NAMES[field]));if(!value.units.length)preview.append(el('p','source-reading-note','当前检查范围未找到相关原文。'));value.units.forEach(u=>passage(preview,u,field));}
      p.append(preview);advanced(p,book);return;
    }
    if(state.sourceLocalError)p.append(notice(state.sourceLocalError,true),button('重试本地中文翻译',()=>{localChinese(book,true);renderPanel();},'text-btn'));
    if(state.sourceHistoryPreview&&localProvider()&&Object.values(book.fields).some(f=>f.units.some(u=>unitPresentation(u).language!=='zh'||u.translation?.mode==='mock')))p.append(button('为此版本生成中文（本地免费）',()=>{state.sourceHistoryPreview=false;localStorage.removeItem('source-preview-'+key());localChinese(book,true);renderPanel();},'text-btn'));
    const nav=el('div','logic-nav');const sections=[];
    GROUPS.forEach(([name,fields],index)=>{
      const visible=fields.filter(f=>!book.selected_fields||book.selected_fields.includes(f));if(!visible.length)return;
      const section=el('details','section-card source-section');section.id='logic-module-'+index;section.open=true;
      section.append(el('summary','',name));const body=el('div','section-body');
      for(const field of visible){
        const value=book.fields[field]||{units:[]};const wrap=el('section','source-field');wrap.dataset.field=field;
        if(fields.length>1)wrap.append(el('h3','',FIELD_NAMES[field]));
        const status=readingStatus(value);if(status)wrap.append(el('p','source-reading-note',status));
        if(!value.units.length)wrap.append(button('查找原文',()=>retrieve(field),'text-btn'));
        const [main,more]=primaryUnits(field,value.units);main.forEach(u=>passage(wrap,u,field));
        if(more.length){const extra=el('details','source-context');extra.append(el('summary','','更多'+FIELD_NAMES[field]+'内容'));more.forEach(u=>passage(extra,u,field));wrap.append(extra);}
        body.append(wrap);
      }
      section.append(body);sections.push(section);
      nav.append(button(name,()=>{section.open=true;section.scrollIntoView({block:'start',behavior:'smooth'});state.activeField=fields[0];},''));
    });
    nav.setAttribute('aria-label','研究逻辑导航');p.append(nav,...sections);advanced(p,book);
  }
  return {reading,translate};
}
