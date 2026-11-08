import {createSourceReading,readingProse} from './source-reading.mjs';
import {FIELD_NAMES,GROUPS,coverage,supportLabel,clampAssistant,isRealCard,sameSelection,matchesPaper,menuPosition,revealThenNavigate,LAYOUT_HINTS} from './ux.mjs';

/** Presentation layer over existing services; no model generation or support upgrade. */
export function createUX(c) {
  const {state,$,el,button,api,row,notice,select,title,viewer,openPaper,setView,locate,toast,renderPanel,findField,goToPage}=c;
  state.activeField='research_questions';
  state.layer=localStorage.getItem('compare-layer')||'research_logic';
  state.layout=localStorage.getItem('reading-layout')||'assisted';
  try{state.annotationDrafts=JSON.parse(sessionStorage.getItem('annotation-drafts')||'{}');}catch{state.annotationDrafts={};}
  state.activeAnnotation=sessionStorage.getItem('active-annotation');
  let evidenceRequest=0,returnPosition=null;
  let resizing=false;
  function updateProgress(){
    const page=viewer.currentPageNumber||1,total=viewer.pagesCount||0;
    $('#progress-text').textContent=total?page+' / '+total+' 页 · '+Math.round(page/total*100)+'%':'—';
    $('#prev').disabled=!total||page<=1;$('#next').disabled=!total||page>=total;
    $('.progress-track').setAttribute('role','progressbar');$('.progress-track').setAttribute('aria-label','论文阅读进度');
    $('.progress-track').setAttribute('aria-valuenow',total?Math.round(page/total*100):0);
    $('#progress-fill').style.width=total?(page/total*100)+'%':'0%';
    const zoom=$('#zoom'),value=String(viewer.currentScaleValue);zoom.querySelector('[data-custom]')?.remove();
    if(value&&value!=='null'&&![...zoom.options].some(o=>o.value===value)){
      const o=el('option','',Math.round(viewer.currentScale*100)+'%');o.value=value;o.dataset.custom='true';zoom.append(o);
    }zoom.value=value;
  }
  function preserveSelection(){
    if(state.selection)sessionStorage.setItem('selected-passage',JSON.stringify(state.selection));
  }
  function selectionGhost(){
    document.querySelectorAll('.selection-ghost').forEach(n=>n.remove());
    const a=state.selection;
    if(!sameSelection(a,state.paper?.document.document_version_id))return;
    const page=document.querySelector('.page[data-page-number="'+a.page+'"]');
    if(!page)return;
    const b=page.getBoundingClientRect();
    for(const [x,y,w,h] of a.rects||[]){
      const n=el('div','selection-ghost');
      Object.assign(n.style,{left:x*b.width+'px',top:y*b.height+'px',width:w*b.width+'px',height:h*b.height+'px'});
      page.append(n);
    }
    $('#selection-status').textContent='选段已保留 · '+a.text.length+'字符 · 操作';$('#selection-status').tabIndex=0;$('#selection-status').setAttribute('role','button');
  }
  function applyLayout(layout,remember=true) {
    if(!['standard','assisted','focus'].includes(layout))layout='assisted';
    preserveSelection();$('#selection-menu').hidden=true;state.layout=layout;
    document.body.classList.remove('mode-standard','mode-assisted','mode-focus');
    document.body.classList.add('mode-'+layout);
    document.body.classList.toggle('collapsed',layout==='assisted');
    document.body.classList.toggle('assistant-hidden',layout!=='assisted');
    document.querySelectorAll('[data-layout]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.layout===layout)));
    if(remember)localStorage.setItem('reading-layout',layout);
    let panels;try{panels=JSON.parse(localStorage.getItem('reader-panels-'+layout)||'null');}catch{}
    if(panels&&layout!=='focus'){
      document.body.classList.toggle('collapsed',panels.collapsed);
      document.body.classList.toggle('assistant-hidden',panels.hidden);
    }
    setWidth(localStorage.getItem('assistant-width')||420);
    $('#collapse').setAttribute('aria-label',document.body.classList.contains('collapsed')?'展开导航':'收起导航');
    $('#collapse').textContent=document.body.classList.contains('collapsed')?'›':'‹';
    requestAnimationFrame(selectionGhost);
    syncControls();
  }
  function syncControls(){
    const hidden=document.body.classList.contains('assistant-hidden'),collapsed=document.body.classList.contains('collapsed');
    $('#assistant-toggle').setAttribute('aria-expanded',String(!hidden));$('#nav-toggle').setAttribute('aria-expanded',String(!collapsed&&state.layout!=='focus'));
    document.querySelectorAll('[data-layout]').forEach(b=>b.title=LAYOUT_HINTS[b.dataset.layout]);
    $('#layout-hint').textContent=LAYOUT_HINTS[state.layout];
    if(state.layout!=='focus')localStorage.setItem('reader-panels-'+state.layout,JSON.stringify({collapsed,hidden}));
  }
  function setWidth(width){
    const nav=document.querySelector('#sidebar').getBoundingClientRect().width;
    const result=clampAssistant(width,innerWidth,nav);
    document.documentElement.style.setProperty('--right',result+'px');
    $('#resize').setAttribute('aria-valuenow',result);
  }
  function ensureAssistant(){
    if(state.layout!=='assisted')applyLayout('assisted');
    document.body.classList.remove('assistant-hidden');
    syncControls();
  }
  function viewChanged(view){
    closeSource(false);
    document.body.classList.toggle('compare-full',view==='compare');
    if(view!=='compare')ensureAssistant();
    localStorage.setItem('workbench-view',view);
  }
  function cardIndicator(){
    const card=state.card;$('#card-indicator').textContent=state.view==='reading'?(state.sourceBook?.source_reading_version?'原文阅读 '+state.sourceBook.source_reading_version:'原文阅读'):card?'精读 v'+card.card_version:'待精读';
    $('#card-indicator').className='badge'+(card?' purple':'');
  }
  function sourceBadge(ref,label,claim='',field=null) {
    return button(label||'原文 · p.'+ref.pdf_page,()=>openEvidence(ref,{claim,field}),'evidence-btn');
  }
  async function openEvidence(ref,context={}){
    const request=++evidenceRequest;
    if(!document.body.classList.contains('source-open'))returnPosition=c.capturePosition();
    if(context.field&&FIELD_NAMES[context.field])state.activeField=context.field;
    // Server verifies paper/version/SHA/page/quote offsets before navigation.
    const currentRef=state.card?.evidence_references?.[ref.evidence_id];
    const cardId=ref.card_id||ref.evidence_key?.split('::')[0]||(
      currentRef&&currentRef.document_version_id===ref.document_version_id&&currentRef.quote===ref.quote&&currentRef.pdf_page===ref.pdf_page&&state.card.card_id);
    const result=await api('/api/inspect-source',{ref,card_id:cardId||undefined});
    if(request!==evidenceRequest)return;
    
    const source={ref:{...ref,...result.anchor,quote:ref.quote||result.anchor.text},...context,location:result.location_status,auxiliary:result.auxiliary_source_review};state.sourceContext=source;
    if(state.view!=='compare')ensureAssistant();
    await revealThenNavigate({
      load:async()=>{if(state.paper.document.document_version_id!==ref.document_version_id)await openPaper(ref.document_version_id);},
      show:()=>{if(request!==evidenceRequest)return;state.sourceContext=source;state.evidence=source.ref;document.body.classList.add('source-open');renderSource();$('#source-drawer').hidden=false;},
      settle:()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))),
      navigate:()=>request===evidenceRequest&&locate(source.ref)
    });
    $('#source-drawer').focus({preventScroll:true});
    return result;
  }
  function renderSource(){
    const p=$('#source-drawer');p.replaceChildren();p.tabIndex=-1;
    const s=state.sourceContext;if(!s)return;
    p.append(row(el('span','badge green','原文证据'),button(state.view==='compare'?'返回比较表':'返回当前任务',()=>closeSource())));
    if(returnPosition&&state.view!=='compare')p.append(button('↶ 返回刚才的阅读位置',async()=>{
      const position=returnPosition;
      if(position.version!==state.paper.document.document_version_id)await openPaper(position.version);
      closeSource();c.restorePosition(position);
    },'text-btn'));
    p.append(el('h2','',s.fullOriginalText?'英文原文':'核对这条陈述'));
    p.append(el('p','source-meta',title(state.paper.document)+' · PDF物理第'+s.ref.pdf_page+'页'));
    if(s.ref.section_hint)p.append(el('p','source-meta','章节：'+s.ref.section_hint));
    if(s.ref.boundary_may_cut_sentence)p.append(notice('片段按现有索引边界拆分，请结合相邻原文核查完整句意。'));
    if(s.claim)p.append(el('div','source-context',s.claim));
    else p.append(el('p','muted','当前是来源片段，尚未关联一条已确认结论。'));
    const states=el('div','source-states');
    for(const [name,value] of [['来源定位',s.location==='parser_text_matched'?'原文字符区间已核对':'仅视觉锚点 · 偏移未确认'],['语义支持',s.independentReview?'请本人独立判断':supportLabel(s.ref)],['用户独立核查','待本人独立核查']]){
      const r=el('div');r.append(el('span','',name),el('strong','',value));states.append(r);
    }
    if(s.auxiliary&&!s.independentReview){const line=el('div');line.append(el('span','','开发助手原文辅助核查'),el('strong','',supportLabel({source_support_status:s.auxiliary.status})+' · 非用户审核'));states.append(line);}
    p.append(states,el('blockquote','evidence-quote',readingProse(s.fullOriginalText||s.ref.quote||s.ref.text||s.ref.chunk_text||'')),
      button('再次定位原文',()=>locate(s.ref),'evidence-btn'),
      el('p','muted','定位成功不代表原文支持整条陈述。请检查限定条件、实验归属与上下文。'));
    if(s.sourceRanges?.length>1){const ranges=row(...s.sourceRanges.map((ref,index)=>button('原文范围 '+(index+1)+' · p.'+ref.pdf_page,()=>openEvidence(ref,{field:s.field,claim:s.claim,sourceRanges:s.sourceRanges,fullOriginalText:s.fullOriginalText}),'evidence-btn')));p.append(ranges);}
    const details=el('details','diagnostics');details.append(el('summary','','来源与版本详情'),
      el('p','source-meta',s.ref.document_version_id),
      el('p','source-meta',s.ref.file_sha256||''),
      el('p','source-meta','字符区间：'+(s.ref.char_start??'未知')+'—'+(s.ref.char_end??'未知')));
    p.append(details);
  }
  function closeSource(focus=true){
    document.body.classList.remove('source-open');$('#source-drawer').hidden=true;
    if(focus)document.querySelector(state.view==='compare'?'#comparison-panel':'#panel')?.focus({preventScroll:true});
  }
  const sourceReading=createSourceReading({...c,openEvidence,refreshIndicator:cardIndicator});
  function reading(p){return sourceReading.reading(p);}
  function evidencePanel(p){
    p.append(el('h2','','原文证据'),el('p','muted','先定位来源，再判断它能否支持陈述。'));
    if(state.evidence)p.append(button('打开当前证据抽屉',()=>openEvidence(state.evidence,{claim:state.sourceContext?.claim||''}),'primary'));
    else p.append(el('div','pending-card','点击精读、问答或比较中的陈述与引用，查看原文、语义支持及人工核查三个独立状态。'));
    p.append(button('保存当前PDF选段为候选',saveSelectionPanel,'evidence-btn'));
  }
  async function saveSelectionPanel(){
    if(!state.selection)throw Error('请先在PDF中选择原文。');
    $('#selection-menu').hidden=true;ensureAssistant();setView('selectionEvidence');
  }
  function selectionEvidence(p){
    const a=state.selection;
    p.append(el('span','badge green','补充证据候选'),el('h2','','把这段原文留给研究字段'));
    if(!a){p.append(notice('请先在PDF中选择原文。'));return;}
    p.append(el('p','muted','PDF物理第'+a.page+'页 · 当前文件版本'),el('blockquote','evidence-quote',a.text));
    const field=select(Object.entries(FIELD_NAMES).map(([v,t])=>[v,t]),v=>state.activeField=v,'补充证据所属字段');field.value=state.activeField;
    p.append(el('label','block','关联字段'),field,el('p','muted','保存为待核查候选，保留操作来源与原卡版本，不会覆盖原陈述。'));
    const save=button('保存到精读补充候选',async()=>{
      const card=state.card;
      if(!card||card.card_status!=='draft')throw Error('当前文件尚无可关联的精读草稿；可先保存为阅读笔记。');
      await api('/api/selection-evidence',{...a,field:field.value,card_id:card.card_id});
      state.paper.supplements=(await api('/api/paper?version='+a.version)).supplements;
      toast('候选已保存 · 原精读草稿保持不变');setView('reading');
    },'primary');
    p.append(save,button('作为阅读笔记保存',()=>setView('notes'),'text-btn'));
  }
  function terms(p){
    const a=state.selection;
    p.append(el('span','badge green','术语理解 · 与翻译独立'),el('h2','','读懂这个术语'));
    if(!a){p.append(el('div','pending-card','请先选择一个术语，再点击浮动工具条“解释术语”。'));return;}
    p.append(el('div','original',a.text));
    const term=Object.entries(state.glossary).find(([k])=>k.toLowerCase()===a.text.toLowerCase());
    if(term)p.append(el('p','', '本篇术语表译法：'+term[1]));
    p.append(el('p','muted','本轮没有启用AI通俗解释。可查阅本篇已保存的用户说明，或记录自己的理解；不会调用翻译服务冒充解释。'));
    const saved=state.paper.notes.filter(n=>n.note_type==='term_explanation'&&n.anchor?.text===a.text);
    saved.forEach(n=>p.append(el('div','candidate','用户说明：'+n.text)));
    const text=el('textarea');text.placeholder='记录术语含义、适用上下文与仍有疑问的地方';text.setAttribute('aria-label','我的术语说明');
    p.append(text,button('保存我的术语说明',async()=>{
      const n=await api('/api/note',{version:a.version,text:text.value,page:a.page,selection:a,note_type:'term_explanation'});
      state.paper.notes.unshift(n);toast('已保存用户说明，不是AI生成内容');await renderPanel();
    },'primary'));
  }
  function notes(p){
    p.append(el('span','badge green','我的阅读记录'),el('h2','','让问题与思考留在原文旁'));
    p.append(el('p','muted','笔记不会自动成为论文事实，也不会发送给模型。'));
    const text=el('textarea');text.placeholder='记录这段原文引发的疑问、思考或待核查内容…';text.setAttribute('aria-label','阅读笔记内容');
    text.value=sessionStorage.getItem('note-draft-'+state.paper.document.document_version_id)||'';
    text.oninput=()=>sessionStorage.setItem('note-draft-'+state.paper.document.document_version_id,text.value);
    const a=state.selection;if(a)p.append(el('blockquote','evidence-quote',a.text.slice(0,350)));
    p.append(text,button(a?'保存笔记并关联选段':'保存当前页笔记',async()=>{
      const n=await api('/api/note',{version:state.paper.document.document_version_id,text:text.value,page:viewer.currentPageNumber,selection:a||undefined});
      state.paper.notes.unshift(n);sessionStorage.removeItem('note-draft-'+state.paper.document.document_version_id);toast('笔记已保存');await renderPanel();
    },'primary'));
    if(!state.paper.notes.length)p.append(el('div','pending-card','还没有阅读笔记。选择一段原文，写下第一个问题。'));
    state.paper.notes.forEach(n=>{
      const box=el('div','candidate');box.append(el('span','badge',n.note_type==='term_explanation'?'用户术语说明':'阅读笔记'),el('p','',n.text),el('small','','p.'+n.page+' · '+n.created_at.slice(0,10)));
      if(n.anchor)box.append(sourceBadge(n.anchor,'回到笔记原文',n.text));p.append(box);
    });
  }
  function origin(col){return col.source_kind==='manual_fixture'?'人工Fixture · 开发样例':col.execution_mode==='real_api'?'真实API草稿 · 未独立审核':'Mock／测试资料';}
  async function compare(p){
    const host=$('#comparison-panel');host.replaceChildren();
    const head=el('div','comparison-header');const intro=el('div');intro.append(el('span','badge purple','双层结构化比较'),el('h2','','把不同研究，放在同一张桌面'),
      el('p','','先比较研究逻辑，再核查验证条件。并列展示不等于自动语义异同分析。'));
    head.append(intro,button('返回论文阅读',()=>setView('reading')));host.append(head);
    const picks=el('div','comparison-selection');const eligible=state.docs.filter(d=>d.reading_cards.some(isRealCard));
    const selection=new Set(state.compareSelections||[]);
    eligible.forEach(d=>{
      const label=el('label','paper-pick');const input=el('input');input.type='checkbox';input.value=d.document_version_id;input.checked=selection.has(input.value);
      input.onchange=()=>{state.compareSelections=[...picks.querySelectorAll('input:checked')].map(x=>x.value);};
      const info=el('div');const card=d.reading_cards.find(isRealCard);info.append(el('strong','',title(d)),el('small','','真实API草稿 v'+card.card_version+' · 待独立核查'));label.append(input,info);picks.append(label);
    });
    host.append(picks);
    const output=el('div');const gen=button('生成比较快照',async()=>{
      const checked=[...picks.querySelectorAll('input:checked')];
      if(checked.length<2||checked.length>3)throw Error('请选择2—3篇有真实精读草稿的论文。');
      const selections=checked.map(x=>{const d=state.docs.find(d=>d.document_version_id===x.value),card=d.reading_cards.find(isRealCard);return {source_kind:'saved_card',paper_id:d.paper_id,document_version_id:d.document_version_id,card_id:card.card_id,card_version:card.card_version,validation_unit_id:null,context_id:null};});
      state.comparison=await api('/api/compare',{selections});localStorage.setItem('workbench-comparison',state.comparison.comparison_id+'@'+state.comparison.comparison_version);renderComparison(output,state.comparison);toast('比较快照已保存 · 未调用模型');await compare(p);
    },'primary');
    const controls=row(gen,el('span','muted','2—3篇 · 不调用模型 · 不生成数值排名'));
    host.append(controls,output);
    const footer=el('div','comparison-footer');footer.append(el('span','','恢复历史比较'));
    const history=(await api('/api/comparisons')).snapshots;
    const s=select([['','选择已保存比较'],...history.map(x=>[x.comparison_id+'@'+x.comparison_version,x.created_at.slice(0,10)+' · '+x.columns.length+'篇 · '+x.comparison_id.slice(-7)])],async id=>{
      if(!id)return;const [a,b]=id.split('@');state.comparison=await api('/api/comparison?id='+a+'&version='+b);localStorage.setItem('workbench-comparison',id);renderComparison(output,state.comparison);
    },'已保存比较');
    footer.append(s);host.append(footer);
    const config=el('details','comparison-config');config.open=true;
    config.append(el('summary','','论文选择与历史快照 · 点击调整'),picks,controls,footer);
    host.insertBefore(config,output);
    const saved=localStorage.getItem('workbench-comparison');
    if(saved){const [a,b]=saved.split('@');try{state.comparison=await api('/api/comparison?id='+a+'&version='+b);s.value=saved;}catch(e){output.append(notice(e.message,true));}}
    if(state.comparison){renderComparison(output,state.comparison);config.open=false;}
  }
  function renderComparison(out,r){
    out.replaceChildren();
    if(r.error){out.append(notice(r.error.message,true));return;}
    out.append(el('p','muted','固定版本：'+r.columns.map(x=>'v'+x.selection.card_version).join(' / ')+' · 所有事实仍保留原审核状态'));
    if(r.stale)out.append(notice('历史来源或卡片已变化，此快照已过期。保留旧内容供核查，请勿用于确定性比较。',true));
    if(r.columns.some(x=>x.execution_mode!=='real_api'))out.append(notice('历史快照包含人工Fixture或Mock资料，不是多张真实模型卡的验收结果。',true));
    out.append(notice('数值条件：'+r.numeric_condition_checks.map(x=>x.status).join(' / ')+'。未知不视为相同，不生成性能排名。'));
    const switcher=el('div','layer-switch');switcher.setAttribute('role','group');switcher.setAttribute('aria-label','比较层次');
    const tableHost=el('div');
    const layers=[['research_logic','研究逻辑与方法'],['validation_results','验证方式与结果']];
    const draw=()=>{
      tableHost.replaceChildren();switcher.querySelectorAll('button').forEach(b=>{const active=b.dataset.layer===state.layer;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
      const scroll=el('div','comparison-scroll'),table=el('table','comparison-table');table.setAttribute('aria-label',state.layer==='research_logic'?'研究逻辑比较表':'验证结果比较表');
      const thead=el('thead'),tr=el('tr');tr.append(el('th','','比较维度'));
      r.columns.forEach(col=>{const h=el('th');h.scope='col';h.append(el('div','',title(col)),el('span','badge','v'+col.selection.card_version),el('p','muted',origin(col)));tr.append(h);});thead.append(tr);table.append(thead);
      const body=el('tbody');
      r.layers[state.layer].forEach(dim=>{
        const tr=el('tr'),label=el('th','',dim.label);label.scope='row';tr.append(label);
        dim.cells.forEach(cell=>{
          const td=el('td');td.append(el('span','badge'+(cell.status==='reported'?'':' warn'),cell.status==='reported'?'有来源草稿':cell.status==='not_applicable'?'不适用':'待核查 / 未确认'));
          if(!cell.facts.length)td.append(el('p','muted','当前卡片没有已保留内容，不补值。'));
          cell.facts.forEach((f,i)=>{
            const wrap=i>0?el('details'):el('div');
            if(i>0)wrap.append(el('summary','','展开其他'+(cell.facts.length-1)+'条中的第'+(i+1)+'条'));
            const text=f.text.length>200?el('details','long-claim'):null;
            const claim=button(f.text,()=>f.citations[0]&&openEvidence(f.citations[0],{claim:f.text,field:dim.dimension}),'fact-button');
            if(text){text.append(el('summary','',f.text.slice(0,130)+'… 展开全文'),claim);wrap.append(text);}else wrap.append(claim);
            f.citations.forEach(ref=>wrap.append(sourceBadge(ref,'p.'+ref.pdf_page+' 原文',f.text,dim.dimension)));
            if(!f.source_checked_candidate)wrap.append(el('p','muted','支持性待核查，不作确定性比较'));td.append(wrap);
          });tr.append(td);
        });body.append(tr);
      });table.append(body);scroll.append(table);tableHost.append(scroll);
    };
    layers.forEach(([id,name])=>{const b=button(name,()=>{state.layer=id;localStorage.setItem('compare-layer',id);draw();});b.dataset.layer=id;switcher.append(b);});
    out.append(switcher,tableHost);draw();
  }
  function renderPapers(){
    const host=$('#papers');host.replaceChildren();const list=state.docs.filter(d=>matchesPaper(d,$('#paper-search').value,$('#paper-filter').value));
    list.forEach(d=>{const b=button('',()=>openPaper(d.document_version_id),'paper-item');b.classList.toggle('active',d.document_version_id===state.paper?.document.document_version_id);
      b.append(el('strong','',title(d)));const meta=el('small');meta.append(el('span','',d.reading_cards.some(isRealCard)?'◦ 有精读草稿':'◦ 待精读'),el('span','',d.page_count+'页'));b.append(meta);host.append(b);});
    if(!list.length)host.append(el('p','muted','没有匹配论文，试试其他关键词。'));$('#library-count').textContent=list.length+'/'+state.docs.length;
  }
  function libraryCards(){
    const host=$('#library-grid');host.replaceChildren();
    const list=state.docs.filter(d=>matchesPaper(d,$('#library-search').value,$('#library-filter').value));
    if(!list.length)host.append(el('div','pending-card',state.docs.length?'没有匹配论文，调整搜索或状态筛选。':'导入第一篇文本型PDF，开始阅读与核查。'));
    list.forEach(d=>{
      const card=d.reading_cards.find(isRealCard),box=el('div','library-card');
      box.append(el('span','badge green',d.index_status==='ready'?'已解析 · 可检索':'处理状态：'+d.index_status),
        el('h3','',title(d)),el('p','',d.page_count+'页 · '+d.created_at.slice(0,10)+'导入'),
        el('span','badge'+(card?' purple':''),card?'真实精读 v'+card.card_version+' · 待核查':'待精读'),
        el('p','muted','第三方处理：每个会话单独授权'),el('small','',d.source_file),
        button('继续阅读 ↗',async()=>{$('#library-dialog').close();await openPaper(d.document_version_id);setView('reading');},'primary'));
      host.append(box);
    });
  }
  async function showLibrary(){await c.refreshLibrary();libraryCards();$('#library-dialog').showModal();$('#library-search').focus();}
  async function annotations(p){
    p.append(el('span','badge','独立人工核查'),el('h2','','对照原文，留下你的判断'),el('p','muted','引用定位、语义支持和内容完整性应分别判断。此处不由AI代填。'));
    const data=await api('/api/tasks');state.annotationTasks=data.tasks;
    const batchMap=new Map(data.batch.items.map(x=>[x.task_id,x]));
    const mode=select([['first10','首批10项 · 真实质量核查'],['all','全部22项历史任务']],v=>{sessionStorage.setItem('review-task-list',v);state.activeAnnotation=null;sessionStorage.removeItem('active-annotation');renderPanel();},'标注任务批次');
    mode.value=sessionStorage.getItem('review-task-list')||'first10';
    const visible=mode.value==='all'?data.tasks:data.batch.items.map(x=>data.tasks.find(t=>t.task_id===x.task_id));
    p.append(mode,el('p','muted','正式标签仅由本人独立核查后提交。保存草稿不计入质量指标。'));
    visible.forEach(t=>t.batch_info=batchMap.get(t.task_id));
    const editor=el('div','annotation-editor');const taskSelect=select([['','选择待核查任务'],...visible.map(t=>[t.task_id,t.task_id+' · '+(t.batch_info?.title||taskLabels[t.annotation_type]||t.annotation_type)+' · '+(t.labels.length?'已有标签':'待核查')])],async id=>{
      state.activeAnnotation=id;sessionStorage.setItem('active-annotation',id);if(!id){editor.replaceChildren();return;}
      const t=data.tasks.find(t=>t.task_id===id);
      if(t.record.document_version_id&&t.record.document_version_id!==state.paper.document.document_version_id){await openPaper(t.record.document_version_id);return;}
      renderTask(t,editor);
    },'待核查任务');
    p.append(el('p','muted','待核查 '+data.tasks.filter(t=>!t.labels.length).length+' / '+data.tasks.length+' 项'),taskSelect,editor);
    if(state.activeAnnotation&&visible.some(t=>t.task_id===state.activeAnnotation)){taskSelect.value=state.activeAnnotation;const t=data.tasks.find(x=>x.task_id===state.activeAnnotation);if(t&&(!t.record.document_version_id||t.record.document_version_id===state.paper.document.document_version_id))renderTask(t,editor);}
  }
  const taskLabels={fact_support:'引用支持性',field_completeness:'字段完整性',qa_completeness:'回答完整性',refusal_reasonableness:'拒答合理性',attribution:'实验归属',comparison_juxtaposition:'比较并列准确性',numeric_accuracy:'数值准确性',retrieval:'证据召回'};
  const judgmentLabels={fully_supported:'充分支持',partially_supported:'部分支持',unrelated:'不相关',contradicted:'与原文冲突',insufficient_evidence:'证据不足',unable_to_judge:'无法判断',complete:'完整',partial:'部分缺失',missing:'缺失',not_applicable:'不适用',uncertain:'待确认',appropriate:'拒答合理',inappropriate:'拒答不合理',correct:'正确',incorrect:'错误',relevant_targets_confirmed:'已确认相关原文目标',no_answer_in_reviewed_scope:'检查范围内未找到答案'};
  function renderTask(t,editor){
    editor.replaceChildren();const r=t.record;
    editor.append(el('p','muted',(taskLabels[t.annotation_type]||t.annotation_type)+' · '+t.task_id+' · '+(r.card_version?'精读 v'+r.card_version:'历史输出')));
    if(t.batch_info)editor.append(el('p','',t.batch_info.instruction));
    if(t.labels.length){
      const history=el('details','saved-reviews');history.open=true;history.append(el('summary','','已保存独立核查 · '+t.labels.length+'条'));
      t.labels.forEach(l=>{const record=el('div','candidate');record.append(el('strong','',judgmentLabels[l.label]||l.label),el('p','',l.rationale),el('small','',(l.reviewer?.reviewer_id||'')+' · '+l.reviewed_at));history.append(record);});editor.append(history);
    }
    const material=t.review_material;
    if(r.structured_content){
      if(material.raw_candidate)editor.append(notice('原始模型提取候选 · 未发布为事实；请核查数据集、指标与验证单元的归属。',true));
      if(material.field_state)editor.append(el('p','muted','原字段状态：'+material.field_state.status+'；原因：'+material.field_state.reason_code));
      if(!material.statements.length)editor.append(el('div','source-context','该字段当前未保存模型陈述。请阅读原PDF核查是否存在遗漏，不将空字段当作论文没有此内容。'));
      material.statements.forEach(f=>{if(f.kind==='analysis')editor.append(el('span','badge warn','模型分析关系 · 待本人独立核查'));editor.append(el('div','source-context',f.text+(f.validation_unit_id?'（原验证单元 '+f.validation_unit_id+'）':'')));});
      if(material.context_statements.length){const context=el('details');context.open=true;context.append(el('summary','','关系两端的贡献主张与方法（上下文，不另计标签）'));material.context_statements.forEach(f=>context.append(el('p','',f.text)));editor.append(context);}
      const refs=el('details');refs.append(el('summary','','原引用与原检索范围 · '+material.sources.length+'段（不预认定相关性）'));
      material.sources.forEach(ref=>{const line=el('div','candidate');line.append(el('small','',ref.review_context_only?'原检查范围片段 · 不是人工目标':'原模型引用'),el('blockquote','',(ref.quote||ref.chunk_text||'').slice(0,260)),button('核查原文 · p.'+ref.pdf_page,()=>openEvidence(ref,{claim:material.statements[0]?.text||'待核查的缺失字段',independentReview:true}),'evidence-btn'));refs.append(line);});editor.append(refs);
    }else if(r.claims){editor.append(el('h3','',r.question));r.claims.forEach(f=>{editor.append(el('p','',f.text));f.citations.forEach(ref=>editor.append(button('核查原文 · p.'+ref.pdf_page,()=>openEvidence(ref,{claim:f.text,independentReview:true}),'evidence-btn')));});editor.append(el('p','muted',(t.canonical_response?.unanswered||r.unanswered||[]).join('\n')));}
    else if(r.columns)renderComparison(editor,r);
    const options={fact_support:['fully_supported','partially_supported','unrelated','contradicted','insufficient_evidence','unable_to_judge'],field_completeness:['complete','partial','missing','not_applicable','uncertain'],qa_completeness:['complete','partial','missing','not_applicable','uncertain'],refusal_reasonableness:['appropriate','inappropriate','not_applicable','uncertain'],retrieval:['relevant_targets_confirmed','no_answer_in_reviewed_scope','unable_to_judge']};
    const draft=state.annotationDrafts[t.task_id]||((!t.labels.length||t.draft?.created_at>t.labels[0]?.reviewed_at)&&t.draft)||{};
    let targets=[...(draft.targets||[])];
    const judgment=select([['','请选择你的判断'],...(options[t.annotation_type]||['correct','incorrect','not_applicable','uncertain']).map(x=>[x,judgmentLabels[x]||x])],()=>saveDraft(),'核查判断');judgment.value=draft.label||'';
    const reason=el('textarea');reason.placeholder='写下支持、缺失或冲突的理由，附页码与原文范围';reason.setAttribute('aria-label','核查理由');reason.value=draft.rationale||'';
    const reviewer=el('input');reviewer.placeholder='你的标注者ID';reviewer.setAttribute('aria-label','标注者ID');reviewer.value=draft.reviewer||draft.reviewer_id||'';
    const scope=el('input');scope.placeholder='如：全文，或第1—3页引言与方法';scope.setAttribute('aria-label','核查检查范围');scope.value=draft.review_scope||'';
    const targetList=el('div');
    function showTargets(){targetList.replaceChildren(el('p','muted','本人选定原文依据：'+targets.length+'段'));targets.forEach((x,i)=>targetList.append(row(el('span','muted','p.'+x.pdf_page+' · '+x.quote.slice(0,60)),button('移除第'+(i+1)+'段',()=>{targets.splice(i,1);showTargets();saveDraft();}))));}
    showTargets();
    const attach=button('将当前PDF选段加入核查依据',async()=>{
      if(!state.selection||state.selection.version!==r.document_version_id)throw Error('请在此任务对应的PDF中选择原文。');
      const a=await api('/api/anchor',state.selection);if(a.mapping_status!=='parser_text_matched')throw Error('此选区无法唯一对应原文字符区间，请选择更完整的一句或一段。');
      const item={document_version_id:a.document_version_id,pdf_page:a.pdf_page,char_start:a.char_start,char_end:a.char_end,quote:a.exact_quote};
      if(!targets.some(t=>t.pdf_page===item.pdf_page&&t.char_start===item.char_start&&t.char_end===item.char_end))targets.push(item);showTargets();saveDraft();
    },'evidence-btn');attach.disabled=!r.document_version_id;
    const confirm=el('input');confirm.type='checkbox';const label=el('label','small-check');label.append(confirm,document.createTextNode(' 我本人独立阅读原文后作此判断，不是AI代填'));
    function saveDraft(){state.annotationDrafts[t.task_id]={unit_fingerprint:t.unit_fingerprint,label:judgment.value,rationale:reason.value,reviewer:reviewer.value,review_scope:scope.value,targets};sessionStorage.setItem('annotation-drafts',JSON.stringify(state.annotationDrafts));}
    reason.oninput=saveDraft;reviewer.oninput=saveDraft;scope.oninput=saveDraft;
    const draftStatus=el('p','muted',t.draft?'已从本地数据库恢复草稿 · 不计入质量指标':'未提交 · 输入暂存于本次浏览器会话');draftStatus.setAttribute('role','status');
    const saveLocal=button('保存草稿（不提交）',async()=>{saveDraft();const saved=await api('/api/annotation-draft',{task_id:t.task_id,...state.annotationDrafts[t.task_id],reviewer_id:reviewer.value});draftStatus.textContent='草稿已持久化保存 · '+saved.created_at+' · 不计入质量指标';toast('草稿已保存，未提交人工标签');});
    const save=button('保存独立人工标签',async()=>{
      if(!judgment.value||!reason.value.trim()||!reviewer.value.trim()||!confirm.checked)throw Error('请本人填写判断、理由、标注者ID并确认独立阅读。');
      if(t.annotation_type==='retrieval'&&judgment.value==='relevant_targets_confirmed'&&!targets.length)throw Error('请先将原文选段加入核查依据。');
      await api('/api/annotation',{task_id:t.task_id,label:judgment.value,rationale:reason.value,reviewer_id:reviewer.value,independent_confirm:true,unit_fingerprint:t.unit_fingerprint,review_scope:scope.value,targets});
      delete state.annotationDrafts[t.task_id];sessionStorage.setItem('annotation-drafts',JSON.stringify(state.annotationDrafts));toast('本人独立标签已保存；原模型草稿未改');await renderPanel();
    },'primary');
    editor.append(el('label','block','独立判断'),judgment,reason,reviewer,scope,attach,targetList,saveLocal,draftStatus,label,save);
    const advanced=el('details','diagnostics');advanced.append(el('summary','','开发走查（不计入人工标签）'),el('p','muted',t.case_id+' · '+t.unit),button('保存界面测试记录',async()=>{
      await api('/api/annotation',{task_id:t.task_id,label:judgment.value,rationale:reason.value,test_record:true});toast('仅保存界面测试记录，不计人工指标');
    }));editor.append(advanced);
  }
  function init(){
    const hint=el('span','layout-hint');hint.id='layout-hint';$('.workspace-heading').append(hint);
    applyLayout(state.layout,false);$('#resize').setAttribute('aria-valuemin','340');$('#resize').setAttribute('aria-valuemax','560');
    $('#nav-toggle').onclick=$('#collapse').onclick=()=>{
      preserveSelection();if(state.layout==='focus')applyLayout('standard');else{document.body.classList.toggle('collapsed');setWidth(localStorage.getItem('assistant-width')||420);$('#collapse').textContent=document.body.classList.contains('collapsed')?'›':'‹';$('#collapse').setAttribute('aria-label',document.body.classList.contains('collapsed')?'展开导航':'收起导航');}syncControls();
    };
    document.querySelectorAll('[data-layout]').forEach(b=>b.onclick=()=>applyLayout(b.dataset.layout));
    $('#assistant-toggle').onclick=()=>{preserveSelection();if(state.layout==='focus')applyLayout('assisted');else{document.body.classList.toggle('assistant-hidden');if(!document.body.classList.contains('assistant-hidden'))setWidth(localStorage.getItem('assistant-width')||420);}syncControls();};
    $('#hide-assistant').onclick=()=>{preserveSelection();document.body.classList.add('assistant-hidden');syncControls();};
    $('#expand').onclick=()=>{preserveSelection();const current=parseInt(getComputedStyle(document.documentElement).getPropertyValue('--right'));setWidth(current>460?420:540);};
    $('#mode-pill').onclick=()=>setView('settings');
    $('#paper-filter').onchange=renderPapers;$('#library-search').oninput=libraryCards;$('#library-filter').onchange=libraryCards;
    $('#library-open').onclick=showLibrary;
    const importButton=button('＋ 导入PDF',()=>{$('#library-dialog').close();$('#file').click();},'primary');importButton.id='library-import';
    document.querySelector('.dialog-head').insertBefore(importButton,$('#close-library'));
    $('#tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;const tabs=[...$('#tabs').querySelectorAll('button')],i=tabs.indexOf(document.activeElement);if(i<0)return;e.preventDefault();const target=tabs[(i+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length];target.click();document.querySelector('#tabs button[data-view="'+target.dataset.view+'"]').focus();});
    $('#selection-status').onclick=()=>{const a=state.selection;if(!a)return;goToPage(a.page);requestAnimationFrame(()=>{selectionGhost();const ghost=document.querySelector('.page[data-page-number="'+a.page+'"] .selection-ghost');if(!ghost)return;const menu=$('#selection-menu');menu.hidden=false;const pos=menuPosition(ghost.getBoundingClientRect(),menu.getBoundingClientRect(),{width:innerWidth,height:innerHeight});menu.style.left=pos.left+'px';menu.style.top=pos.top+'px';});};
    $('#selection-status').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('#selection-status').click();}};
    $('#save-selection').onclick=()=>saveSelectionPanel().catch(e=>toast(e.message));
    $('#explain-selection').onclick=()=>{if(!state.selection)return;$('#selection-menu').hidden=true;setView('terms');};
    $('#dismiss-selection').onclick=()=>$('#selection-menu').hidden=true;
    $('#resize').onkeydown=e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();setWidth(parseInt(getComputedStyle(document.documentElement).getPropertyValue('--right'))+(e.key==='ArrowLeft'?20:-20));};
    $('#resize').onpointerdown=e=>{preserveSelection();resizing=true;$('#resize').setPointerCapture(e.pointerId);};
    $('#resize').onpointermove=e=>{if(resizing){setWidth(innerWidth-e.clientX);localStorage.setItem('assistant-width',parseInt(getComputedStyle(document.documentElement).getPropertyValue('--right')));}};
    $('#resize').onpointerup=()=>{resizing=false;selectionGhost();};
    document.addEventListener('keydown',e=>{
      if(e.target.closest('input,textarea,select,[contenteditable]'))return;
      if(e.key==='['){e.preventDefault();$('#nav-toggle').click();}
      if(e.key===']'){e.preventDefault();$('#assistant-toggle').click();}
      if(e.key==='Escape'){closeSource(false);$('#selection-menu').hidden=true;}
    });
    $('#selection-menu').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;const buttons=[...$('#selection-menu').querySelectorAll('button')];const index=buttons.indexOf(document.activeElement);buttons[(index+(e.key==='ArrowRight'?1:buttons.length-1))%buttons.length].focus();e.preventDefault();});
  }
  return {init,reading,compare,renderComparison,renderPapers,showLibrary,evidencePanel,annotations,notes,terms,selectionEvidence,
    viewChanged,cardIndicator,openEvidence,closeSource,preserveSelection,selectionGhost,updateProgress,applyLayout,setWidth};
}
