import assert from 'node:assert/strict';
import test from 'node:test';
import {localDateTimeInput,scheduledInputInstant} from '../src/utils/scheduledTaskTime.ts';

test('a later browser-local time stays later after conversion to UTC',()=>{
  const local=new Date(Date.now()+10*60_000);
  const value=localDateTimeInput(local);
  const converted=new Date(scheduledInputInstant(value));
  assert.ok(converted.getTime()>Date.now());
  assert.equal(localDateTimeInput(converted),value);
});

test('the same clock entry maps to the selected device timezone',()=>{
  const zone=Intl.DateTimeFormat().resolvedOptions().timeZone;
  if(zone==='America/New_York')assert.equal(scheduledInputInstant('2026-10-28T07:00'),'2026-10-28T11:00:00.000Z');
  if(zone==='Asia/Shanghai')assert.equal(scheduledInputInstant('2026-10-28T07:00'),'2026-10-27T23:00:00.000Z');
});

test('nonexistent local time during daylight-saving jump is rejected',()=>{
  if(Intl.DateTimeFormat().resolvedOptions().timeZone!=='America/New_York')return;
  assert.throws(()=>scheduledInputInstant('2026-03-08T02:30'),/执行时间无效/);
});
