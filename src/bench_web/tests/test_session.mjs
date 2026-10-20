import {test} from 'node:test';
import assert from 'node:assert/strict';
import {selectedRunId,frontendLink,rememberRun} from '../../../engine/web/session.mjs';

test('frontend links retain the same selected run without execution parameters',()=>{
  const id='run-'+'a'.repeat(32);
  assert.equal(frontendLink('/stats/',id),'/stats/?run='+id);
  assert.equal(frontendLink('/',id),'/?run='+id);
  assert.equal(selectedRunId('?run='+id),id);
  assert.equal(selectedRunId('?run=../../private'),null);
  assert.equal(frontendLink('/stats/','bad'),'/stats/');
});
test('recording the selected run preserves the page and filters',()=>{
  globalThis.location={href:'http://localhost:8765/stats/?layer=linear#layers'};
  let saved;
  globalThis.history={replaceState:(_,__,url)=>{saved=url;}};
  rememberRun('run-'+'b'.repeat(32));
  assert.equal(saved.pathname,'/stats/');assert.equal(saved.hash,'#layers');
  assert.equal(saved.searchParams.get('layer'),'linear');
  assert.equal(selectedRunId(saved.search),'run-'+'b'.repeat(32));
});
