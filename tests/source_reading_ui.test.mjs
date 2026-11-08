import test from 'node:test';
import assert from 'node:assert/strict';
import {readingStatus,unitPresentation,primaryUnits,readingProse} from '../web/source-reading.mjs';
test('English provenance is continuous prose while conditions and real paragraph breaks remain',()=>{
 assert.equal(readingProse('We pro-\npose a single-\npoint method [3].\nIt may not apply.\n\nFor x = 12.5.'),'We propose a single-point method [3]. It may not apply.\n\nFor x = 12.5.');
});
test('located unreviewed original is readable and is not a missing field',()=>{
 const u={original_text:'Complete original paragraph with conditions [2].',human_review:'unreviewed'};
 assert.equal(readingStatus({units:[u],location:'located'}),'');
 assert.equal(unitPresentation(u).text,u.original_text);
});
test('machine translation is primary, summaries and placeholder Mock are never substitutes',()=>{
 const u={original_text:'Original',translation:{status:'complete',text:'译文',source_fingerprint:'x',mode:'machine'}};
 assert.equal(unitPresentation(u).language,'zh');
 assert.match(unitPresentation(u).note,/尚未人工校对/);
 u.translation.is_placeholder=true;assert.equal(unitPresentation(u).text,'Original');
 u.translation.status='partial';assert.equal(unitPresentation(u).text,'Original');
});
test('reviewed and conflict states remain distinct from absent evidence',()=>{
 assert.match(readingStatus({conflict:true,units:[{}]}),/冲突/);
 assert.match(readingStatus({units:[]}),/检索|检查范围/);
 assert.match(readingStatus({units:[{}],coverage:'partial_related_scope'}),/部分/);
 assert.equal(unitPresentation({translation:{status:'complete',text:'译文',mode:'machine',human_review:'reviewed'}}).note,'用户已独立校对');
});
test('folding never truncates or drops source paragraphs and retains different result settings',()=>{
 const units=[{section:'Dataset A',original_text:'a'.repeat(4000)},{section:'Dataset A',original_text:'Conditions'},
 {section:'Dataset B',original_text:'Different baseline'}];
 const [main,more]=primaryUnits('findings',units);
 assert.deepEqual(main.map(u=>u.section),['Dataset A','Dataset B']);assert.equal(more.length,1);
 assert.equal(main[0].original_text.length,4000);
});
