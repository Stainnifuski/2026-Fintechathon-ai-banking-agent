import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));
await page.route('**/api/**',route=>route.abort());
try{
 await page.goto('http://127.0.0.1:3019/tests/cardRefreshHarness.html');
 const title=page.getByLabel('事件名称',{exact:true});const limit=page.getByLabel(/^单笔限额/);const contact=page.getByLabel('测试联系人',{exact:true});
 await title.fill('本人未提交的日程');await limit.fill('123');await contact.check();
 await page.getByRole('button',{name:'模拟无关刷新',exact:true}).click();
 assert.equal(await title.inputValue(),'本人未提交的日程');assert.equal(await limit.inputValue(),'123');assert.equal(await contact.isChecked(),true);
 await contact.uncheck();await page.getByRole('button',{name:'模拟真实字段更新',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('input[maxlength="200"]')?.value==='模型修改日程');
 assert.equal(await title.inputValue(),'模型修改日程');assert.equal(await limit.inputValue(),'150');assert.equal(await contact.isChecked(),true);
 assert.deepEqual(errors,[]);console.log('PASS: calendar, collection and bank-card drafts survive unrelated refresh; actual model updates still apply; no bank requests');
}finally{await browser.close();}
