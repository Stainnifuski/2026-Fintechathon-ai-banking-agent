import test from 'node:test';
import assert from 'node:assert/strict';
import {navPerformance,performanceLabel} from '../src/components/Wealth/navPerformance.ts';

test('selected window starts at zero and shows NAV-relative percentage, not raw NAV',()=>{
  const rows=navPerformance([{date:'2026-04-14',nav:'1'},{date:'2026-04-15',nav:'1.1084'},{date:'2026-04-16',nav:'.9'}]);
  assert.equal(rows[0].percent,0);assert.equal(performanceLabel(rows[1].percent),'+10.84%');
  assert.equal(performanceLabel(rows[2].percent),'-10.00%');
  assert.equal(performanceLabel(-.000001),'0.00%');
});
test('changing windows rebases to that windows first valid NAV',()=>{
  assert.equal(performanceLabel(navPerformance([{date:'2026-04-15',nav:'2'},{date:'2026-04-16',nav:'2.2'}])[1].percent),'+10.00%');
});
test('missing or malformed observations do not fabricate a zero return chart',()=>{
  assert.deepEqual(navPerformance([]),[]);
  assert.deepEqual(navPerformance([{date:'2026-04-15',nav:'1'}]),[]);
  for(const nav of ['0','-1','NaN','Infinity'])assert.deepEqual(navPerformance([{date:'2026-04-15',nav},{date:'2026-04-16',nav:'1'}]),[]);
});
