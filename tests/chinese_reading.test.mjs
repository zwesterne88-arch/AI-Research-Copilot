import test from 'node:test';
import assert from 'node:assert/strict';
import {readingProgress,createChineseReading} from '../web/chinese-reading.mjs';
test('reading completion and interruption stay distinct from semantic quality',()=>{
 const base={completed:['a'],retained:['formula'],total:3};
 assert.deepEqual(readingProgress({...base,status:'paused'}),{label:'任务已暂停',done:2,total:3});
 assert.match(readingProgress({...base,status:'interrupted'}).label,/继续/);
 assert.match(readingProgress({...base,status:'completed'}).label,/处理完成/);
 assert.match(readingProgress({...base,status:'running',stage:'saving'}).label,/保存/);
});

test('one click sends the paper scope directly without opening a confirmation dialog',async()=>{
 const requests=[];
 const state={paper:{document:{document_version_id:'paper-one'}},view:'reading'};
 const el=(tag,cls,text)=>({tag,cls,text,children:[],append(...items){this.children.push(...items);this.lastChild=items.at(-1);},setAttribute(){}});
 const job={job_id:'job-one',status:'completed',completed:['a'],retained:[],total:1,failed:{}};
 const c={state,el,button:(label,action)=>({label,action}),notice:()=>({}),renderPanel:async()=>{},toast:()=>{},
  api:async(path,body)=>{requests.push({path,body});return {job,book:{id:'translated-book'}};}};
 const reading=createChineseReading(c,{scope:()=>({version:'paper-one',card_id:'card-one',snapshot_id:'old-book'}),key:()=> 'paper-one',remember:()=>{}});
 const parent=el('main');reading.render(parent);
 await parent.children[0].action();
 assert.deepEqual(requests,[{path:'/api/chinese-reading/begin',body:{version:'paper-one',card_id:'card-one'}}]);
 assert.equal(state.sourceBook.id,'translated-book');
});
