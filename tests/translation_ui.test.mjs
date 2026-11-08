import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {GROUPS} from '../web/ux.mjs';
import {providerReason} from '../web/translation-ui.mjs';

test('all actual browser entry modules parse',()=>{
 for(const file of ['web/app.js','web/workbench.mjs','web/translation-ui.mjs','web/ux.mjs','web/source-reading.mjs','web/aliyun-reading.mjs','web/chinese-reading.mjs','web/account.mjs','web/login.mjs','web/settings.mjs','web/usage.mjs']){
  const r=spawnSync(process.execPath,['--check',file],{encoding:'utf8'});
  assert.equal(r.status,0,r.stderr);
 }
});
test('research concepts retain separate modules and author list precedes method',()=>{
 assert.equal(GROUPS.length,7);
 assert.deepEqual(GROUPS.slice(0,5).map(([,fields])=>fields[0]),['research_questions','background','prior_work_gaps','motivation','claimed_contributions']);
});
test('provider unavailable offers an explicit configuration reason',()=>{
 assert.match(providerReason(null),/设置/);
 assert.equal(providerReason({configured:false,reason:'尚未配置服务端'}),'尚未配置服务端');
 assert.match(providerReason({id:'mock'}),/模拟演示/);
});
test('configured providers are available without session or repeated consent prompts',()=>{
 for(const id of ['aliyun','deepseek','deepl','azure']){
  const text=providerReason({id,configured:true});
  assert.match(text,/已配置/);
  assert.doesNotMatch(text,/授权|预算|收费关闭/);
 }
});
