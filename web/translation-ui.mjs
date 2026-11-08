/** Native provider controls over the existing source-bound translation workflow. */
import {createAliyunReading} from './aliyun-reading.mjs';
export function providerReason(provider){
  if(!provider)return '请在接口设置中选择翻译服务。';
  if(provider.id==='mock')return '模拟演示 · 配置翻译接口后可获取真实译文。';
  return provider.configured?(provider.label||provider.id)+' · 已配置':provider.reason||'请在接口设置中填写凭证。';
}
export function createTranslationUI(c){
  const {state,el,button,row,notice,select,api,toast,locate,renderPanel,translateSelection,setView}=c;
  const aliyun=createAliyunReading(c);
  function render(p){
    p.append(el('h2','','学术划词翻译'),el('p','muted','选择原文，即可获取中文译文。'));
    const providers=state.translationProviders?.providers||[{id:'mock',label:'Mock · 本地交互演示',configured:true}];
    const selected=state.translationProvider||'mock',provider=providers.find(x=>x.id===selected);
    const mode=select(providers.map(x=>[x.id,x.label]),v=>{state.translationProvider=v;localStorage.setItem('translation-provider',v);state.translationConsent=false;state.translation=null;state.translationError=null;renderPanel();},'翻译服务');
    mode.id='translation-mode';mode.value=selected;p.append(mode,notice(providerReason(provider,state.paid)),button('接口设置',()=>setView('settings'),'text-btn'));
    if(provider?.id==='aliyun'&&!state.selection){aliyun.render(p);return;}
    if(provider?.id==='aliyun')p.append(button('查看当前页与全文翻译',()=>{state.selection=null;state.translation=null;renderPanel();},'text-btn'));
    if(state.translationError)p.append(notice(state.translationError,true));
    const r=state.translation,a=r?.anchor||state.selection;
    if(!a)p.append(el('div','pending-card','请在PDF中选中英文段落，再点击悬浮工具条“学术翻译”。若无法选择，检查PDF是否包含文本层。'));
    else{
      p.append(el('h3','','英文原文 · PDF物理第'+(a.pdf_page||a.page)+'页'),el('div','original',a.text));
      if(r){
        p.append(row(el('h3','','中文译文'),el('span','badge'+(r.mode==='mock'?' warn':' green'),r.mode==='mock'?'Mock · 非真实翻译':(r.provider||'DeepSeek')+' · 未人工审核')),
          el('p','muted',r.from_history?'已保存记录 · 0次新请求':r.cache_hit?'缓存命中 · 0次新请求':'生成记录 · '+(r.usage?.input_characters?r.usage.input_characters+' 字符':'用量见请求记录')),
          el('div','translation-result',r.translation));
        p.append(row(button('复制译文',async()=>{await navigator.clipboard.writeText(r.translation);toast('已复制');}),button('定位原文',()=>locate(a)),button('重新翻译',()=>translateSelection(true))));
        const details=el('details');details.append(el('summary','','人工修订译文 · 保留原记录'));
        const input=el('textarea');input.setAttribute('aria-label','人工修订译文');input.value=state.paper.translation_revisions?.find(x=>x.translation_id===r.id)?.text||r.translation;
        details.append(input,button('保存修订',async()=>{
          const saved=await api('/api/translation-revision',{version:state.paper.document.document_version_id,translation_id:r.id,text:input.value});state.paper.translation_revisions.unshift(saved);toast('修订已追加，原译文和缓存保留；不计为独立人工评测标签');
        }));p.append(details);
        for(const note of r.terminology_notes||[])p.append(el('p','muted',note));
        if(a.math_layout_risk)p.append(notice('选段含变量或公式，请对照PDF核查版式。',true));
        if(r.numeric_preservation_warnings?.length)p.append(notice('以下数字可能未保留：'+r.numeric_preservation_warnings.join('、'),true));
      }
      p.append(button('翻译当前选区',()=>translateSelection(),'primary'));
    }
    const glossary=el('details','section-card');glossary.append(el('summary','','本篇论文术语表'));
    const input=el('textarea');input.setAttribute('aria-label','本篇论文术语表');input.value=Object.entries(state.glossary).map(([k,v])=>k+'：'+v).join('\n');
    glossary.append(el('p','muted','当前基础NMT不执行强制术语；术语表供阅读和授权的LLM精译参考。变化后缓存隔离。'),input,button('保存术语表',async()=>{
      const terms={};for(const line of input.value.split('\n')){const i=line.search(/[:：]/);if(i>0)terms[line.slice(0,i).trim()]=line.slice(i+1).trim();}
      await api('/api/glossary',{version:state.paper.document.document_version_id,glossary:terms});state.glossary=terms;toast('术语表已保存');
    }));p.append(glossary);
    if(state.paper.translations.length){const history=el('details');history.append(el('summary','','已保存翻译记录'));state.paper.translations.slice(0,12).forEach(x=>history.append(button((x.provider||x.mode)+' · p.'+x.anchor.pdf_page+' · '+x.anchor.text.slice(0,40),()=>{state.translation={...x,from_history:true};state.selection={...x.anchor,version:x.anchor.document_version_id,page:x.anchor.pdf_page};renderPanel();},'text-btn')));p.append(history);}
  }
  function settings(){location.href='/settings';}
  return {render,settings};
}
