// Pure presentation contracts. They never change model support or source status.
export const FIELD_NAMES = {
  research_questions:'核心研究问题',background:'研究背景',prior_work_gaps:'现有研究不足',
  motivation:'研究动机',claimed_contributions:'作者声称的贡献',method:'技术方法与作用机制',
  validation:'研究验证方式',findings:'主要结果',conclusions:'研究结论',
  author_limitations:'作者明确的局限',future_work:'未来工作'
};
export const GROUPS = [
  ['核心研究问题',['research_questions']],['研究背景',['background']],['现有研究不足',['prior_work_gaps']],
  ['研究动机',['motivation']],['作者明确列出的贡献',['claimed_contributions']],['核心技术方法',['method']],
  ['研究验证、结论与局限',['validation','findings','conclusions','author_limitations','future_work']]
];
export function coverage(card) {
  const fields=card?.structured_content?.fields||{};
  const rows=Object.keys(FIELD_NAMES).map(field=>({field,status:fields[field]?.status||'not_found_in_checked_scope',
    populated:!!fields[field]?.fact_ids?.length}));
  return {rows,populated:rows.filter(r=>r.populated).length,total:rows.length,
    notApplicable:rows.filter(r=>r.status==='not_applicable').length,
    pending:rows.filter(r=>r.status!=='reported').length};
}
export function supportLabel(ref={}) {
  const raw=ref.source_support_status||ref.support_level||ref.semantic_support;
  const map={supported:'辅助核查支持',fully_supported:'模型标注充分支持',
    partial:'部分支持',partially_supported:'部分支持',background_related:'仅背景相关',not_supported:'不支持',background_only:'仅背景相关',
    contradicted:'存在冲突',unsupported:'不支持',unverifiable:'支持性待核查',
    model_supported_not_human_verified:'模型标注支持 · 未独立核查'};
  return map[raw]||'支持性待核查';
}
export function clampAssistant(width,viewport,nav) {
  return Math.max(340,Math.min(560,Number(width)||420,Math.max(340,viewport-nav-600)));
}
export function isRealCard(card) {return card?.card_status==='draft'&&card.execution_mode==='real_api';}
export function sameSelection(a,version) {return !!a&&a.version===version&&Number.isInteger(a.page)&&a.page>0;}
export function menuPosition(rect,menu,viewport) {
  const left=Math.max(12,Math.min(rect.left,viewport.width-menu.width-12));
  const top=rect.bottom+menu.height+16<=viewport.height?rect.bottom+10:Math.max(8,rect.top-menu.height-10);
  return {left,top};
}
export function matchesPaper(doc,query,filter='all') {
  const hay=[doc.title,doc.source_file].filter(Boolean).join(' ').toLowerCase();
  if(!hay.includes(query.toLowerCase().trim()))return false;
  const has=doc.reading_cards?.some(isRealCard);
  return filter==='all'||(filter==='reading'&&has)||(filter==='unread'&&!has)||(filter==='ready'&&doc.index_status==='ready');
}

// A hidden PDF viewer cannot establish its final page geometry. Reveal first;
// navigate only after the consumer confirms the visible layout has settled.
export async function revealThenNavigate({load,show,settle,navigate}) {
  await load();
  show();
  await settle();
  return navigate();
}

export function scheduleReaderRefit({schedule,currentPage,refit,navigate}) {
  schedule(()=>{
    // Read at execution time: a citation may have changed the page since the
    // resize event was observed, before this animation frame is delivered.
    const page=currentPage();
    refit();
    navigate(page);
  });
}

// Positions remain scoped to the file version; fractions survive width refits.
export function readerPosition(container,page,version){
  if(!page||!container.clientWidth)return null;
  const bounds=container.getBoundingClientRect(),rect=page.getBoundingClientRect();
  return {version,page:Number(page.dataset.pageNumber),fraction:Math.max(0,Math.min(1,(bounds.top-rect.top)/rect.height))};
}
export function validPosition(position,version,total){
  return !!position&&position.version===version&&Number.isInteger(position.page)&&position.page>0&&position.page<=total&&Number.isFinite(position.fraction)&&position.fraction>=0&&position.fraction<=1;
}
export const LAYOUT_HINTS={standard:'论文导航 + 宽幅原文',assisted:'窄导航 + 原文 + 研究助手',focus:'隐藏两栏，专注原文'};

// PDF.js 6 releases a document through its loading task, not the proxy.
export async function releasePdfDocument(document){await document.loadingTask.destroy();}
