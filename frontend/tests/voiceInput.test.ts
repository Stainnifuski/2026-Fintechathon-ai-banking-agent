import test from 'node:test';
import assert from 'node:assert/strict';
import {appendVoiceText,recordingMime} from '../src/utils/voiceInput.ts';

test('spoken text appends to an existing draft without rewriting amounts or codes',()=>{
  assert.equal(appendVoiceText('保留现金1000元','购买010969，金额200元'),'保留现金1000元 购买010969，金额200元');
  assert.equal(appendVoiceText('','  查看我的持仓  '),'查看我的持仓');
});
test('empty and overlong transcripts do not erase or silently truncate the draft',()=>{
  assert.equal(appendVoiceText('原金额100元','  '),null);
  assert.equal(appendVoiceText('x'.repeat(1998),'100元'),null);
  assert.equal(appendVoiceText('','x'.repeat(2000))?.length,2000);
});
test('recording uses an actual supported MIME and permits browser default fallback',()=>{
  assert.equal(recordingMime(mime=>mime==='audio/mp4'),'audio/mp4');
  assert.equal(recordingMime(()=>false),undefined);
});
