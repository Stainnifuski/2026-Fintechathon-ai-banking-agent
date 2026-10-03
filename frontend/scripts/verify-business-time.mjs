import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';

async function actualModule(path) {
  const source=await readFile(new URL(path,import.meta.url),'utf8');
  const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
  return import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
}
const time=await actualModule('../src/utils/time.ts');
const clock=await actualModule('../src/utils/serverClock.ts');
const realNow=Date.now;
try {
  // Test actual shared modules under three host TZs and a deliberately wrong device wall clock.
  Date.now=()=>realNow()+366*86400000;
  for(const host of ['UTC','Asia/Shanghai','America/Toronto']) {
    process.env.TZ=host;
    const anchor=clock.calibrateTime('2026-10-03T15:59:58.500Z',20,1020);
    assert.equal(time.beijingTime(clock.serverInstant(anchor,1020)),'2026-10-03 23:59:59');
    assert.equal(time.beijingTime(clock.serverInstant(anchor,2020)),'2026-10-04 00:00:00');
    assert.equal(time.beijingTime(clock.serverInstant(anchor,6020)),'2026-10-04 00:00:04'); // subsequent monotonic ticks
    const resync=clock.calibrateTime('2026-10-04T01:00:00Z',6020,6020);
    assert.equal(time.beijingTime(clock.serverInstant(resync,7020)),'2026-10-04 09:00:01');
    assert.equal(time.beijingTime('2026-11-01T06:00:00Z'),'2026-11-01 14:00:00'); // Toronto DST boundary
    assert.equal(time.fromBeijingInput('2026-10-04T00:00'),'2026-10-03T16:00:00.000Z');
    assert.equal(time.beijingInput(new Date('2026-10-03T16:00:00Z')),'2026-10-04T00:00');
    assert.throws(()=>clock.calibrateTime('invalid',0,1));
    console.log(host+': midnight, DST, wall-clock skew, monotonic continuation, resync and input conversion PASS');
  }
} finally {Date.now=realNow;}
