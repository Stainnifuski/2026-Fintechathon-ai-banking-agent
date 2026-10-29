import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage();
const errors=[];page.on('pageerror',e=>errors.push(String(e)));
await page.route('**/api/**',route=>route.abort());
try{
 await page.goto('http://127.0.0.1:3019/tests/targetFlowHarness.html');
 const input=page.getByRole('textbox');
 await input.waitFor();
 assert.equal(await input.isVisible(),true,'invalid restored completion reopens amount entry');
 await input.fill('0');assert.equal(await page.getByRole('button',{name:'继续',exact:true}).count(),0);
 await input.fill('100');await page.getByRole('button',{name:'继续',exact:true}).click();
 const prepare=page.getByRole('button',{name:'统一申购全部已选（1只）',exact:true});
 assert.equal(await prepare.isEnabled(),true);
 await prepare.click();
 await page.getByRole('heading',{name:'银行交易方案',exact:true}).waitFor();
 assert.equal(await page.getByTestId('version').textContent(),'2');
 await page.getByRole('button',{name:'恢复已保存金额',exact:true}).click();
 await prepare.waitFor();assert.equal(await prepare.isEnabled(),true,'restored amount remains purchasable');
 await prepare.click();await page.getByRole('heading',{name:'银行交易方案',exact:true}).waitFor();
 assert.equal(await page.getByTestId('preparations').textContent(),'2');
 assert.deepEqual(errors,[]);
 console.log('PASS: invalid progress recovery, positive-amount gating, preparation -> verification, restored amounts; no bank requests');
}finally{await browser.close();}
