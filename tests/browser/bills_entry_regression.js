async page => {
  const results = [];
  const runtimeErrors = [];
  const writes = [];
  const check = (condition, name) => { if (!condition) throw new Error(name); results.push(name); };
  const onError = error => runtimeErrors.push(error.message);
  const onRequest = request => {
    if (request.method() !== 'GET' && /\/(tasks\/transfers|cards\/operations|subscriptions\/[^/]+\/(cancel|simulate-charge)|wealth\/orders)/.test(request.url())) writes.push(request.url());
  };
  page.on('pageerror', onError);
  page.on('request', onRequest);
  const pattern = '**/api/v1/bills/analysis**';
  const nav = name => page.getByRole('navigation', {name:'主要功能'}).getByRole('button', {name,exact:true});
  const waitCharts = async count => {
    await page.getByRole('heading', {name:'账单分析',exact:true}).waitFor();
    await page.waitForFunction(minimum => document.querySelectorAll('.bills canvas').length >= minimum, count);
  };
  try {
    await page.setViewportSize({width:1440,height:1000});
    await page.reload();
    await nav('◫ 账单洞察').click();
    await waitCharts(2);
    check(await page.getByText('暂无消费数据',{exact:true}).isVisible(), '真实空账单正常显示，趋势与现金流图可初始化');
    await page.screenshot({path:'output/playwright/nova20-bills-fixed-empty-desktop.png'});
    for (let i=0;i<3;i++) {
      await nav('⌂ 概览').click();
      await nav('◫ 账单洞察').click();
      await waitCharts(2);
    }
    check(true,'首页与账单往返三次不白屏');
    await page.reload();
    await nav('◫ 账单洞察').click();
    await waitCharts(2);
    check(true,'刷新后重新进入账单不白屏');
    await page.getByRole('button',{name:'✦ 问 NOVA',exact:true}).click();
    check(await page.getByRole('complementary',{name:'NOVA 对话'}).isVisible(),'账单页可展开NOVA');
    check((await page.locator('.nova-context').innerText()).includes('账单洞察'),'NOVA仍取得当前账单上下文');
    await page.getByRole('button',{name:'收起 NOVA',exact:true}).click();
    check(await page.locator('.bills canvas').count() >= 2,'收起助手后图表仍存在');
    await page.route(pattern, async route => {
      const response = await route.fetch();
      const data = await response.json();
      data.totals.consumption_fen = 12300;
      data.totals.net_consumption_fen = 12300;
      data.categories = [{category:'dining',label:'餐饮（UI隔离样例）',amount_fen:12300,count:1,share_percent:100,refund_fen:0,net_amount_fen:12300}];
      data.trend = data.trend.map((point,index) => ({...point,consumption_fen:index === 0 ? 12300 : 0}));
      data.report = ['UI隔离非空图表样例：只验证显示与组件注册，不是银行实际交易。'];
      await route.fulfill({response,json:data});
    });
    await nav('⌂ 概览').click();
    await nav('◫ 账单洞察').click();
    await waitCharts(3);
    check(await page.getByText('UI隔离非空图表样例：只验证显示与组件注册，不是银行实际交易。',{exact:true}).isVisible(),'明确标注的非空饼图、趋势图与现金流图正常显示');
    await page.screenshot({path:'output/playwright/nova20-bills-ui-fixture-nonempty-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('button',{name:'✦ 问 NOVA',exact:true}).click();
    const g=await page.evaluate(() => {
      const input=document.querySelector('.agent-compose input').getBoundingClientRect();
      const close=document.querySelector('.agent-head .icon-button').getBoundingClientRect();
      return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,inputBottom:input.bottom,closeBottom:close.bottom};
    });
    check(g.width === g.scrollWidth && g.inputBottom <= 844 && g.closeBottom <= 844,'390px账单加助手无文档横向溢出，输入关闭可达');
    await page.screenshot({path:'output/playwright/nova20-bills-ui-fixture-mobile-nova.png'});
    await page.getByRole('button',{name:'收起 NOVA',exact:true}).click();
    check(await page.getByRole('heading',{name:'账单分析',exact:true}).isVisible(),'手机关闭助手后账单仍显示');
    await page.unroute(pattern);
    await page.route(pattern, route => route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:{message:'UI隔离：账单读取失败，请重试。'}})}));
    await nav('⌂ 概览').click();
    await nav('◫ 账单洞察').click();
    await page.getByRole('alert').filter({hasText:'UI隔离：账单读取失败，请重试。'}).waitFor();
    check(await page.getByRole('heading',{name:'查看支出与变化',exact:true}).isVisible(),'接口失败保留账单外壳与错误提示，不白屏');
    check(await page.getByRole('navigation',{name:'主要功能'}).isVisible(),'接口失败仍能跨页导航');
    await page.screenshot({path:'output/playwright/nova20-bills-ui-fixture-failure-mobile.png'});
    await page.unroute(pattern);
    await page.getByRole('button',{name:'查看账单',exact:true}).click();
    await waitCharts(2);
    check(await page.getByText('暂无消费数据',{exact:true}).isVisible(),'移除拦截后点击查看账单恢复真实空报告');
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:'output/playwright/nova20-bills-fixed-final-desktop.png'});
    check(runtimeErrors.length === 0,'整个修复后回归没有未捕获运行时异常');
    check(writes.length === 0,'没有发起资金交易、卡片变更或订阅取消');
    return {results,runtimeErrors,financialWrites:writes,fixtures:['非空图表样例','账单503失败'],geometry:g};
  } finally {
    await page.unroute(pattern);
    page.off('pageerror',onError);
    page.off('request',onRequest);
    await page.setViewportSize({width:1440,height:1000});
  }
}
