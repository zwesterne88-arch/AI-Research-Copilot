import {createTranslationUI} from '/translation-ui.mjs';
import {createUX} from '/workbench.mjs';
import {menuPosition,readerPosition,validPosition,releasePdfDocument} from '/ux.mjs';
import * as pdfjs from '/vendor/pdf.mjs';
const {PDFViewer,PDFLinkService,PDFFindController,EventBus}=await import('/vendor/pdf_viewer.mjs');
pdfjs.GlobalWorkerOptions.workerSrc='/vendor/pdf.worker.mjs';
const $=s=>document.querySelector(s), el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const state={docs:[],paper:null,card:null,view:localStorage.getItem('workbench-view')||'reading',token:'',paid:false,selection:null,glossary:{},session:localStorage.getItem('research-session'),busy:false,translation:null};
const names={reading:'智能精读',qa:'论文问答',translation:'学术翻译',evidence:'原文证据',notes:'阅读笔记',compare:'多论文比较',annotations:'人工质量标注',settings:'接口设置',usage:'使用记录',terms:'术语理解',selectionEvidence:'补充证据'};
const fieldNames={research_questions:'核心研究问题',background:'研究背景',prior_work_gaps:'现有研究不足',motivation:'研究动机',claimed_contributions:'作者声称的贡献',method:'技术方法与作用机制',validation:'研究验证方式',findings:'主要结果',conclusions:'研究结论',author_limitations:'作者明确的局限',future_work:'未来工作'};
const groups=[['研究问题',['research_questions']],['研究背景与动机',['background','prior_work_gaps','motivation']],['作者贡献与创新思路',['claimed_contributions']],['技术方法',['method']],['研究验证',['validation','findings']],['结论与局限',['conclusions','author_limitations','future_work']]];
const statusLabel={reported:'有来源草稿',uncertain:'待核查',not_found_in_checked_scope:'当前范围未确认',not_applicable:'不适用'};
const paths={library:'M3 4h14v16H3z M6 8h8M6 12h8M6 16h5',read:'M3 4h8v16H3zM11 4h8v16h-8',qa:'M3 4h16v12H9l-5 4v-4H3z',reading:'M4 3h12l3 3v15H4zM8 9h7M8 13h7M8 17h4',compare:'M3 4h7v16H3zM14 4h7v16h-7z',annotations:'M4 3h15v18H4zM7 8l2 2 5-4M7 15h8',settings:'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6'};
function icon(type){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.classList.add('nav-icon');const p=document.createElementNS(svg.namespaceURI,'path');p.setAttribute('d',paths[type]||paths.reading);svg.append(p);return svg;}
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toast.t);toast.t=setTimeout(()=>$('#toast').hidden=true,4200);}
async function api(path,data){const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Workspace-Token':state.token},body:JSON.stringify(data)};const response=await fetch(path,options);const value=await response.json();if(response.status===401){location.href='/login';throw Error('请先登录');}if(!response.ok||value.error)throw Error(value.error?.message||value.error?.code||'操作未完成');return value;}
function button(text,fn,cls=''){
 const b=el('button',cls,text);b.type='button';
 b.onclick=async()=>{try{const result=fn();if(result&&typeof result.then==='function'){b.disabled=true;b.setAttribute('aria-busy','true');await result;}}catch(e){toast(e.message);}finally{b.disabled=false;b.removeAttribute('aria-busy');}};return b;
}
function notice(text,warning=false){return el('div','notice'+(warning?' warning':''),text);}
function row(...nodes){const n=el('div','row');n.append(...nodes);return n;}
function select(options,fn,label){const s=el('select','wide');if(label)s.setAttribute('aria-label',label);options.forEach(([value,text])=>{const o=el('option','',text);o.value=value;s.append(o);});s.onchange=()=>Promise.resolve(fn(s.value)).catch(e=>toast(e.message));return s;}
function title(d){return (d.title||d.display_title||d.source_file||'未命名论文').replace(/^论文标题为[“"]?/,'').replace(/[”。"]+$/,'');}
function evidence(ref,label,claim=''){return button(label||'原文 · p.'+ref.pdf_page,()=>ux.openEvidence(ref,{claim}),'evidence-btn');}
const container=$('#viewer-container'),eventBus=new EventBus(),link=new PDFLinkService({eventBus}),find=new PDFFindController({eventBus,linkService:link});
const viewer=new PDFViewer({container,viewer:$('#pdf-viewer'),eventBus,linkService:link,findController:find,textLayerMode:1,annotationEditorMode:-1,enableAutoLinking:false});link.setViewer(viewer);
eventBus.on('pagesinit',()=>{
 $('#pdf-loading').style.display='none';
 const saved=Number(localStorage.getItem('page-'+state.paper.document.document_version_id)||1);
 // A comparison intentionally hides the reader. Do not ask PDF.js to scroll
 // through a detached layout; reveal and navigate through goToPage instead.
 if(container.clientWidth){viewer.currentScaleValue=localStorage.getItem('pdf-scale')||'page-width';goToPage(Math.min(saved,viewer.pagesCount));restorePosition(readSavedPosition());}
});
eventBus.on('pagechanging',e=>{if(pendingEvidence&&e.pageNumber!==pendingEvidence.pdf_page){pendingEvidenceScroll=false;$('#location-status').textContent='PDF物理第'+e.pageNumber+'页 · 原文可选择、复制';}$('#page').value=e.pageNumber;if(state.paper)localStorage.setItem('page-'+state.paper.document.document_version_id,e.pageNumber);});
let lastReaderWidth=0,lastPosition=null,positionFrame=0;
function readSavedPosition(){try{return JSON.parse(localStorage.getItem('position-'+state.paper.document.document_version_id));}catch{return null;}}
function capturePosition(){return readerPosition(container,document.querySelector('.page[data-page-number="'+viewer.currentPageNumber+'"]'),state.paper?.document.document_version_id);}
function restorePosition(position){
 if(!validPosition(position,state.paper?.document.document_version_id,viewer.pagesCount)||!container.clientWidth)return;
 const page=document.querySelector('.page[data-page-number="'+position.page+'"]');if(!page)return;
 goToPage(position.page);const rect=page.getBoundingClientRect();container.scrollTop+=rect.top-container.getBoundingClientRect().top+rect.height*position.fraction;viewer.update();lastPosition=capturePosition();
}
container.addEventListener('scroll',()=>{cancelAnimationFrame(positionFrame);positionFrame=requestAnimationFrame(()=>{const pos=capturePosition();if(pos){lastPosition=pos;localStorage.setItem('position-'+pos.version,JSON.stringify(pos));}});},{passive:true});
new ResizeObserver(()=>{
 const width=container.clientWidth;
 if(!width||width===lastReaderWidth)return;lastReaderWidth=width;
 if(viewer.pdfDocument&&(viewer.currentScaleValue==='page-width'||!viewer.currentScaleValue)){
  const position=lastPosition;
  requestAnimationFrame(()=>{
   // A newer citation navigation wins over a previously queued resize.
   const page=viewer.currentPageNumber,valid=position?.page===page;
   viewer.currentScaleValue=viewer.currentScaleValue||localStorage.getItem('pdf-scale')||'page-width';
   if(valid)restorePosition(position);else goToPage(page);ux.selectionGhost();paintEvidence();
  });
 }
}).observe(container);
let loadId=0,currentDocumentLoad=Promise.resolve();
function openPaper(version){currentDocumentLoad=loadPaper(version);return currentDocumentLoad;}
async function loadPaper(version){const id=++loadId;$('#pdf-loading').style.display='flex';const skeleton=el('div','loading-book');skeleton.append(el('span'),el('span'),el('span'));$('#pdf-loading').replaceChildren(skeleton,el('p','','正在打开原始PDF…'));$('#selection-status').textContent='本地PDF.js';$('#location-status').textContent='原文可选择、复制 · 物理页码';ux.preserveSelection();ux.closeSource(false);state.selection=null;pendingEvidence=null;state.translationConsent=false;$('#selection-menu').hidden=true;state.translation=null;state.evidence=null;state.sourceBook=null;state.sourceBookKey=null;state.sourceHistoryPreview=false;state.sourceLocalError=null;
 const paper=await api('/api/paper?version='+encodeURIComponent(version));if(id!==loadId)return;state.paper=paper;state.card=paper.cards.find(c=>c.card_id===localStorage.getItem('card-'+version))||paper.cards.find(c=>c.card_status==='draft')||paper.cards[0];state.glossary=paper.glossaries[0]?.terms||{};try{const saved=JSON.parse(sessionStorage.getItem('selected-passage')||'null');if(saved?.version===version&&saved.page<=paper.document.page_count)state.selection=saved;}catch{}localStorage.setItem('selected-paper',version);
 $('#paper-title').textContent=title(paper.document);$('#paper-meta').textContent=paper.document.page_count+'页 · 索引'+(paper.document.index_status==='ready'?'已就绪':'待处理')+' · 本地原文';$('#paper-title').title=title(paper.document);$('#paper-meta').title=paper.document.source_file+' · '+version;$('#pages').textContent='/ '+paper.document.page_count;$('#download').href='/api/pdf?version='+encodeURIComponent(version);$('#download').setAttribute('download',paper.document.source_file);renderPapers();if(state.view!=='compare'||!$('#comparison-panel').childNodes.length)await renderPanel();else ux.cardIndicator();
 const pdf=await pdfjs.getDocument({url:'/api/pdf?version='+encodeURIComponent(version),cMapUrl:'/vendor/cmaps/',cMapPacked:true,standardFontDataUrl:'/vendor/standard_fonts/',wasmUrl:'/vendor/wasm/',isEvalSupported:false}).promise;if(id!==loadId){await releasePdfDocument(pdf);return;}viewer.setDocument(pdf);link.setDocument(pdf);find.setDocument(pdf);
 // setDocument returns before page views exist. Cross-paper citations must wait
 // for initialization; setting currentPageNumber early silently leaves page 1.
 await viewer.pagesPromise;ux.selectionGhost();}
let pendingEvidence=null,pendingEvidenceScroll=false;
function paintEvidence(){const ref=pendingEvidence;if(!ref||ref.pdf_page!==viewer.currentPageNumber)return;const page=document.querySelector('.page[data-page-number="'+ref.pdf_page+'"]');if(!page)return;page.querySelectorAll('.evidence-hit').forEach(n=>n.remove());if(ref.mapping_status==='visual_anchor_only_not_verified'){const bounds=page.getBoundingClientRect();let first;for(const [x,y,w,h] of ref.rects||[]){const hit=el('div','evidence-hit');Object.assign(hit.style,{left:(x*bounds.width)+'px',top:(y*bounds.height)+'px',width:(w*bounds.width)+'px',height:(h*bounds.height)+'px'});page.append(hit);first ||=hit;}if(first&&pendingEvidenceScroll){pendingEvidenceScroll=false;first.scrollIntoView({block:'center',behavior:'smooth'});}$('#location-status').textContent='物理第'+ref.pdf_page+'页 · 仅视觉选区锚点，后台字符偏移未确认';return;}const spans=[...page.querySelectorAll('.textLayer span')];const chars=[],mapping=[];for(const span of spans){const node=span.firstChild;if(!node||node.nodeType!==3)continue;for(let i=0;i<node.textContent.length;i++){for(const c of node.textContent[i].normalize('NFKC'))if(!/\s/.test(c)){chars.push(c);mapping.push([node,i]);}}}const needle=(ref.quote||ref.chunk_text||ref.text||'').normalize('NFKC').replace(/\s/g,'');const at=chars.join('').indexOf(needle);if(!needle||at<0||chars.join('').indexOf(needle,at+1)>=0){$('#location-status').textContent='已定位物理第'+ref.pdf_page+'页；文本层高亮尚未匹配，请核查原文';return;}const range=document.createRange();range.setStart(...mapping[at]);const end=mapping[at+needle.length-1];range.setEnd(end[0],end[1]+1);const bounds=page.getBoundingClientRect();let first;for(const r of range.getClientRects()){if(!r.width)continue;const hit=el('div','evidence-hit');Object.assign(hit.style,{left:(r.left-bounds.left)+'px',top:(r.top-bounds.top)+'px',width:r.width+'px',height:r.height+'px'});page.append(hit);first ||=hit;}if(first&&pendingEvidenceScroll){pendingEvidenceScroll=false;first.scrollIntoView({block:'center',behavior:'smooth'});}$('#location-status').textContent='物理第'+ref.pdf_page+'页 · 已高亮匹配的原文文本层';}
eventBus.on('textlayerrendered',()=>{paintEvidence();ux.selectionGhost();});
function goToPage(number){
 if(!container.clientWidth||!viewer.pdfDocument)return false;
 const page=document.querySelector('.page[data-page-number="'+number+'"]');
 if(!page)return false;
 viewer.currentPageNumber=number;
 viewer.update();
 // The PDF.js helper may use its previous hidden viewport location. Position
 // the browser scroll container from the now-visible page geometry last.
 const target=page.getBoundingClientRect(),bounds=container.getBoundingClientRect();
 container.scrollTop+=target.top-bounds.top-16;

 lastPosition=capturePosition();return true;
}
async function locate(ref){
 if(ref.document_version_id&&ref.document_version_id!==state.paper?.document.document_version_id)await openPaper(ref.document_version_id);else await currentDocumentLoad;
 pendingEvidence=ref;pendingEvidenceScroll=true;
 if(!goToPage(ref.pdf_page)){$('#location-status').textContent='请展开阅读器后前往物理第'+ref.pdf_page+'页';return;}
 $('#location-status').textContent='正在核对物理第'+ref.pdf_page+'页的原文位置';
 setTimeout(paintEvidence,150);
}

$('#prev').onclick=()=>goToPage(Math.max(1,viewer.currentPageNumber-1));$('#next').onclick=()=>goToPage(Math.min(viewer.pagesCount,viewer.currentPageNumber+1));$('#page').onchange=e=>goToPage(Math.max(1,Math.min(viewer.pagesCount,Number(e.target.value)||1)));
$('#page').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();goToPage(Math.max(1,Math.min(viewer.pagesCount,Number(e.target.value)||1)));}};
function scale(v){const page=viewer.currentPageNumber;viewer.currentScaleValue=v;goToPage(page);localStorage.setItem('pdf-scale',v);}$('#zoom').onchange=e=>scale(e.target.value);$('#zoom-in').onclick=()=>scale(String(Math.min(2.5,viewer.currentScale+.15)));$('#zoom-out').onclick=()=>scale(String(Math.max(.5,viewer.currentScale-.15)));
function setView(view){if(view==='settings'||view==='usage'){location.href='/'+view;return;}
 state.view=view;ux.viewChanged(view);
 $('#workspace-title').textContent=names[view]||'论文阅读';
 renderNav();renderTabs();renderPanel().catch(e=>toast(e.message));
}
function renderNav(){const nav=$('#nav');nav.replaceChildren();[['library','论文库'],['read','PDF 阅读'],['qa','论文问答'],['reading','AI 智能精读'],['compare','多论文比较'],['annotations','人工质量标注'],['settings','接口设置'],['usage','使用记录']].forEach(([id,text])=>{const b=button('',()=>id==='library'?showLibrary():id==='read'?setView('reading'):setView(id));b.append(icon(id),el('span','',text));b.title=text;b.dataset.view=id;b.setAttribute('aria-label',text);b.classList.toggle('active',state.view===id||(id==='read'&&state.view==='translation'));nav.append(b);});}
function renderTabs(){$('#tabs').replaceChildren();['reading','qa','translation','evidence','notes'].forEach(id=>{const b=button(names[id],()=>setView(id));b.dataset.view=id;b.setAttribute('role','tab');b.setAttribute('aria-selected',String(state.view===id));b.classList.toggle('active',state.view===id);$('#tabs').append(b);});}
function renderPapers(){return ux.renderPapers();}
async function refreshLibrary(){state.docs=(await api('/api/library')).documents;renderPapers();}
async function showLibrary(){return ux.showLibrary();}
$('#library-open').onclick=showLibrary;$('#close-library').onclick=()=>$('#library-dialog').close();$('#paper-search').oninput=renderPapers;$('#import').onclick=()=>$('#file').click();
$('#file').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{toast('正在本地解析并建立索引…');const r=await fetch('/api/upload?name='+encodeURIComponent(f.name),{method:'POST',headers:{'X-Workspace-Token':state.token},body:f});const value=await r.json();if(!r.ok||value.error)throw Error(value.error?.message||'导入失败');await refreshLibrary();const version=value.document_version_id||value.data?.document_version_id;if(version){setView('reading');await openPaper(version);}toast('导入完成，可以开始智能精读；原PDF已保存在本机');}catch(err){toast(err.message);}finally{e.target.value='';}};
$('#collapse').onclick=()=>{const closed=document.body.classList.toggle('collapsed');$('#collapse').textContent=closed?'›':'‹';$('#collapse').setAttribute('aria-label',closed?'展开导航':'收起导航');localStorage.setItem('nav-collapsed',closed);};
$('#expand').onclick=()=>{const wide=document.body.classList.toggle('assistant-wide');document.documentElement.style.setProperty('--right',wide?'620px':'430px');};
$('#resize').onpointerdown=e=>{$('#resize').setPointerCapture(e.pointerId);$('#resize').onpointermove=ev=>{const width=Math.max(340,Math.min(620,window.innerWidth-ev.clientX));document.documentElement.style.setProperty('--right',width+'px');localStorage.setItem('assistant-width',width);};$('#resize').onpointerup=()=>$('#resize').onpointermove=null;};
function heading(panel,text,sub){const top=el('div','panel-head');top.append(el('h2','',text));panel.append(top);if(sub)panel.append(el('p','muted',sub));}
async function renderPanel(){
 const p=$('#panel');p.replaceChildren();p.tabIndex=-1;
 if(!state.paper){p.append(el('div','onboard','导入一篇文本型PDF，开始阅读、划词和来源核查。'));return;}
 const view=state.view;
 if(view==='compare')await compare(p);
 else if(view==='annotations')await annotations(p);
 else if(view==='settings')await settings(p);
 else if(view==='qa')qa(p);
 else if(view==='translation')translation(p);
 else if(view==='evidence')evidencePanel(p);
 else if(view==='notes')notes(p);
 else if(view==='terms')ux.terms(p);
 else if(view==='selectionEvidence')ux.selectionEvidence(p);
 else reading(p);
 ux.cardIndicator();
}
function reading(p){return ux.reading(p);}
async function findField(field,body,section=null){
 const version=state.paper.document.document_version_id,card=state.card;
 body.querySelector('.candidates')?.remove();
 const list=el('div','candidates');list.append(notice('正在本地检索原文候选…'));body.append(list);
 try{
  const r=await api('/api/find-field',{version,field,section});
  if(state.paper.document.document_version_id!==version||!body.isConnected)return;
  list.replaceChildren(el('h3','',fieldNames[field]+' · '+r.candidates.length+'条候选'),el('p','muted','仅表示可能相关。逐条核查后可另存候选，不会覆盖原草稿。'));
  if(!r.candidates.length)list.append(notice('当前检索范围未找到候选，可从PDF选择原文补充。',true));
  r.candidates.forEach((source,index)=>{
   const box=el('div','candidate');box.append(el('span','badge warn','候选 '+(index+1)+' · 物理第'+source.pdf_page+'页 · 待核查'));
   box.append(el('p','candidate-preview',source.chunk_text.slice(0,160)+'…'));
   const full=el('details');full.append(el('summary','','展开完整上下文'),el('blockquote','',source.chunk_text));box.append(full);
   const save=button('选为补充候选',async()=>{
    if(save.dataset.saved)return;
    if(state.paper.document.document_version_id!==version||state.card?.card_id!==card?.card_id)throw Error('当前论文或卡片已变化，请重新查找候选。');
    const saved=await api('/api/supplement',{version,field,chunk_id:source.chunk_id,char_start:source.char_start,section,card_id:card?.card_id});
    state.paper.supplements.unshift(saved);save.dataset.saved='true';save.textContent='已保存 · 待核查';box.classList.add('candidate-saved');toast('已追加候选并保留原卡版本，原草稿未修改');
   },'evidence-btn');
   box.append(row(button('核对原文与定位',()=>ux.openEvidence(source,{field,claim:'检索补证候选 · '+fieldNames[field]+'，尚未确认语义支持'}),'evidence-btn'),save));list.append(box);
  });
 }catch(e){list.replaceChildren(notice(e.message,true),button('重试本地检索',()=>findField(field,body,section)));}
}
function evidencePanel(p){return ux.evidencePanel(p);}
async function paidAction(path,extra){if(state.busy)return;state.busy=true;const version=state.paper.document.document_version_id;try{const r=await api(path,{version,request_id:crypto.randomUUID(),...extra});if(r.status==='failed'||r.card_status==='failed')toast(r.error?.message||'模型任务未完成，已保留记录');if(state.paper.document.document_version_id===version)await openPaper(version);}finally{state.busy=false;}}
function qa(p){heading(p,'与当前论文对话','围绕问题、方法与结论提问，回答附有原文依据。');const input=el('textarea');input.placeholder='这篇论文希望解决什么问题？核心方法如何回应这一问题？';input.setAttribute('aria-label','向当前论文提问');p.append(input);const send=button('发送问题 ↗',async()=>{if(!input.value.trim()){input.focus();return;}send.textContent='正在阅读论文并组织回答…';try{await paidAction('/api/qa',{question:input.value});}finally{send.textContent='发送问题 ↗';}},'primary wide');p.append(send);

 state.paper.qa.slice(0,6).forEach(r=>{const d=el('details','section-card');d.open=r===state.paper.qa[0];d.append(el('summary','',r.question));const b=el('div','section-body');b.append(el('span','badge'+(r.status!=='answered'?' warn':''),r.status==='partial'?'部分回答 · 有待核查':r.status));if(r.error)b.append(notice(r.error.message,true));r.claims.forEach(c=>{b.append(el('div','fact',c.text));c.citations.forEach(ref=>b.append(evidence(ref,undefined,c.text)));});if(r.unanswered?.length){const u=el('details');u.append(el('summary','muted','未确认范围 · 尚未逐项人工核查'));r.unanswered.forEach(t=>u.append(el('p','muted',t)));b.append(u);}d.append(b);p.append(d);});}
function glossaryFrom(text){const terms={};for(const line of text.split('\n')){const i=line.search(/[:：]/);if(i>0)terms[line.slice(0,i).trim()]=line.slice(i+1).trim();}return terms;}
function translation(p){return translationUI.render(p);}
let selectionTimer;
async function captureSelection(){const s=window.getSelection();if(!s||s.isCollapsed||!s.rangeCount)return;const range=s.getRangeAt(0);let n=range.startContainer.parentElement;const page=n.closest('.page');if(!page)return;if(!range.endContainer.parentElement?.closest('.page')?.isSameNode(page)){toast('请选择单个物理页内的文本；跨页选区请分次翻译');return;}const text=s.toString().trim();if(!text||!/[A-Za-z]/.test(text))return;if(text.length>2500){toast('选段超过2500字符，请缩小范围');return;}const bounds=page.getBoundingClientRect();const rects=[...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(r=>[(r.left-bounds.left)/bounds.width,(r.top-bounds.top)/bounds.height,r.width/bounds.width,r.height/bounds.height]);state.translation=null;state.translationError=null;state.selection={version:state.paper.document.document_version_id,page:Number(page.dataset.pageNumber),pdf_page:Number(page.dataset.pageNumber),text,rects};ux.preserveSelection();const rect=range.getBoundingClientRect(),menu=$('#selection-menu');menu.hidden=false;
 const pos=menuPosition(rect,menu.getBoundingClientRect(),{width:innerWidth,height:innerHeight});menu.style.left=pos.left+'px';menu.style.top=pos.top+'px';$('#selection-count').textContent=text.length+'字符';ux.selectionGhost();}
container.addEventListener('mouseup',()=>{clearTimeout(selectionTimer);selectionTimer=setTimeout(()=>captureSelection().catch(e=>toast(e.message)),350);});container.addEventListener('keyup',()=>{clearTimeout(selectionTimer);selectionTimer=setTimeout(()=>captureSelection().catch(e=>toast(e.message)),400);});
$('#selection-menu').addEventListener('mousedown',e=>e.preventDefault());$('#translate-selection').onclick=()=>translateSelection().catch(e=>toast(e.message));$('#copy-selection').onclick=async()=>{await navigator.clipboard.writeText(state.selection.text);toast('已复制选中原文');};
async function translateSelection(force=false){if(state.busy||!state.selection)return;if(state.selection.version!==state.paper.document.document_version_id)throw Error('选段不属于当前文件，请重新选择。');state.busy=true;$('#selection-menu').hidden=true;setView('translation');const p=$('#panel');p.append(notice('正在处理当前选段…'));try{state.translationError=null;state.translation=await api('/api/translate',{...state.selection,provider:state.translationProvider||'mock',consent_provider:state.translationProvider||'mock',glossary:state.glossary,force,consent:true,request_id:crypto.randomUUID()});await renderPanel();}catch(e){state.translationError=e.message;toast(e.message);await renderPanel();}finally{state.busy=false;}}
function notes(p){return ux.notes(p);}
async function compare(p){return ux.compare(p);}
function comparisonOrigin(c){return c.source_kind==='manual_fixture'?'人工Fixture · 开发样例':c.execution_mode==='real_api'?'真实API草稿 · 未独立审核':'Mock／测试资料 · 非真实模型';}
function renderComparison(out,r){return ux.renderComparison(out,r);}
async function annotations(p){return ux.annotations(p);}
function settings(){location.href='/settings';}

const translationUI=createTranslationUI({state,el,button,row,notice,select,api,toast,locate,renderPanel,translateSelection,setView});
const ux=createUX({state,$,el,button,api,row,notice,select,title,viewer,openPaper,setView,locate,toast,renderPanel,findField,refreshLibrary,paidAction,goToPage,capturePosition,restorePosition});
ux.init();
eventBus.on('pagechanging',()=>ux.updateProgress());
eventBus.on('pagesinit',()=>ux.updateProgress());
eventBus.on('scalechanging',()=>ux.updateProgress());
try{
 const boot=await api('/api/bootstrap');state.token=boot.token;state.paid=boot.paid_enabled;if(boot.login_required&&!boot.user){location.replace('/login');throw Error('请先登录');}if(state.view==='settings'||state.view==='usage'){const target=state.view;localStorage.setItem('workbench-view','reading');location.replace('/'+target);}
 if(boot.validation_label)document.body.append(el('div','validation-banner',boot.validation_label));
 $('#mode-pill').textContent=boot.user?.username||'接口设置';$('#safe-label').textContent='论文 · 笔记 · 阅读进度';
 state.translationProviders=await api('/api/translation-providers');const savedProvider=localStorage.getItem('translation-provider');state.translationProvider=state.translationProviders.default||savedProvider||'mock';
 renderNav();renderTabs();await refreshLibrary();
 const saved=localStorage.getItem('selected-paper'),doc=state.docs.find(d=>d.document_version_id===saved)||state.docs[0];
 document.body.classList.toggle('compare-full',state.view==='compare');$('#workspace-title').textContent=names[state.view]||'论文阅读';
 if(doc)await openPaper(doc.document_version_id);else{$('#pdf-loading').textContent='导入第一篇文本型PDF，开始阅读';await renderPanel();}
}catch(e){$('#pdf-loading').textContent='无法打开阅读器：'+e.message;toast(e.message);console.error(e);}
