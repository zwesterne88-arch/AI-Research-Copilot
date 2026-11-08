import test from 'node:test';
import assert from 'node:assert/strict';
import {coverage,supportLabel,clampAssistant,isRealCard,sameSelection,menuPosition,matchesPaper,revealThenNavigate,scheduleReaderRefit,validPosition,readerPosition,releasePdfDocument} from '../web/ux.mjs';

test('coverage counts saved fields, never manufactures accuracy',()=>{
 const result=coverage({structured_content:{fields:{
   motivation:{status:'uncertain',fact_ids:['f1']},research_questions:{status:'not_found_in_checked_scope',fact_ids:[]},
   validation:{status:'not_applicable',fact_ids:[]}}}});
 assert.equal(result.total,11);assert.equal(result.populated,1);assert.equal(result.notApplicable,1);
 assert.equal(result.rows.find(x=>x.field==='motivation').status,'uncertain');
 assert.equal(result.accuracy,undefined);
});
test('partial support and unknown support never become fully supported',()=>{
 assert.equal(supportLabel({source_support_status:'partially_supported'}),'部分支持');
 assert.equal(supportLabel({source_support_status:'unverifiable'}),'支持性待核查');
 assert.match(supportLabel({semantic_support:'model_supported_not_human_verified'}),/未独立核查/);
});
test('desktop layout reserves PDF width rather than expanding assistant without bound',()=>{
 for(const viewport of [1366,1440]){
   const width=clampAssistant(620,viewport,220);
   assert.ok(viewport-220-width>=600);
   assert.ok(width>=340&&width<=560);
 }
});
test('toolbar sits below selection, or above it at the bottom edge',()=>{
 const v={width:1440,height:900},m={width:410,height:48};
 const bottom=menuPosition({left:1200,top:780,bottom:820},m,v);
 assert.ok(bottom.left+m.width<=v.width);assert.ok(bottom.top>=820);
 const top=menuPosition({left:450,top:820,bottom:880},m,v);
 assert.ok(top.top+m.height<820);
});
test('selection provenance cannot follow a different document version',()=>{
 assert.equal(sameSelection({version:'A',page:3},'A'),true);
 assert.equal(sameSelection({version:'A',page:3},'B'),false);
});
test('paper filters distinguish genuine cards from fixtures and failed drafts',()=>{
 const doc={title:'A generic botanical study',source_file:'plants.pdf',index_status:'ready',
   reading_cards:[{card_status:'draft',execution_mode:'manual_fixture'}]};
 assert.equal(matchesPaper(doc,'botanical','reading'),false);
 assert.equal(matchesPaper(doc,'plants','unread'),true);
 assert.equal(matchesPaper(doc,'missing','all'),false);
 doc.reading_cards.push({card_status:'draft',execution_mode:'real_api'});
 assert.equal(matchesPaper(doc,'botanical','reading'),true);
 assert.equal(isRealCard({card_status:'failed',execution_mode:'real_api'}),false);
});
test('hidden-viewer source navigation waits for load, visibility and final layout',async()=>{
 const order=[];let visible=false,geometryReady=false;
 await revealThenNavigate({
   load:async()=>{order.push('loaded');},
   show:()=>{visible=true;order.push('visible');},
   settle:async()=>{assert.ok(visible);await Promise.resolve();geometryReady=true;order.push('geometry');},
   navigate:()=>{assert.ok(visible&&geometryReady);order.push('page9');}
 });
 assert.deepEqual(order,['loaded','visible','geometry','page9']);
});
test('a queued resize must not restore page1 after a citation already selected page9',()=>{
 const frames=[];let page=1;const positions=[];
 scheduleReaderRefit({schedule:fn=>frames.push(fn),currentPage:()=>page,
   refit:()=>{},navigate:p=>positions.push(p)});
 page=9;
 frames[0]();
 assert.deepEqual(positions,[9]);
});

test('restored reader positions reject other versions, invalid pages and corrupted fractions',()=>{
 const pos={version:'P05-A',page:9,fraction:.42};
 assert.ok(validPosition(pos,'P05-A',11));
 for(const changed of [{version:'P07-B'},{page:12},{page:0},{fraction:NaN},{fraction:1.2}])
  assert.equal(validPosition({...pos,...changed},'P05-A',11),false);
 const container={clientWidth:900,getBoundingClientRect:()=>({top:200})};
 const page={dataset:{pageNumber:'9'},getBoundingClientRect:()=>({top:-220,height:1000})};
 assert.deepEqual(readerPosition(container,page,'P05-A'),pos);
 assert.equal(readerPosition({...container,clientWidth:0},page,'P05-A'),null);
});

test('a superseded PDF.js 6 document releases its loading task, not a missing proxy method',async()=>{
 let released=false;const proxy={loadingTask:{destroy:async()=>{released=true;}}};
 assert.equal(proxy.destroy,undefined);await releasePdfDocument(proxy);assert.ok(released);
});
