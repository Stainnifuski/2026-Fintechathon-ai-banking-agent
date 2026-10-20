async page => {
  const results = [];
  const check = (condition, name) => { if (!condition) throw new Error(name); results.push(name); };
  const panel = page.getByRole('complementary', { name: 'NOVA 对话' });
  let queries = 0;
  const watch = request => { if (request.url().endsWith('/nova/chat/stream')) queries++; };
  page.on('request', watch);
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    if (!await panel.isVisible()) await page.getByRole('button', { name: '✦ 问 NOVA', exact: true }).click();
    check(await panel.locator('.nova-quick-prompts button').count() === 2, '首次仅显示两个快捷问题');
    check(await panel.locator('.nova-messages .nova-message').count() === 0, '首次消息区没有示例占位消息');
    await page.screenshot({ path: 'output/playwright/nova20-after-desktop-empty.png' });
    await panel.getByRole('button', { name: '更多示例', exact: true }).click();
    check(await panel.getByRole('button', { name: '问：北京时间', exact: true }).isVisible(), '展开后保留时间查询');
    check(await panel.locator('.nova-workspace-links button').count() === 3, '保留三个明确的工作台跳转');
    await page.screenshot({ path: 'output/playwright/nova20-after-desktop-more.png' });
    await panel.getByRole('button', { name: '打开转账与收款', exact: true }).click();
    await page.getByRole('heading', { name: '转账与收款', exact: true }).waitFor();
    check(queries === 0, '打开工作台不发送聊天请求');
    check(!await panel.locator('#nova-help').count(), '跳转后自动收起示例');
    await page.getByRole('navigation', { name: '主要功能' }).getByRole('button', { name: '▣ 卡片', exact: true }).click();
    await page.getByRole('heading', { name: '管理卡片', exact: true }).waitFor();
    await page.getByRole('button', { name: '选择供 NOVA 查询', exact: true }).first().click();
    check((await panel.locator('.nova-context').innerText()).includes('已选卡片'), '切换页面和选卡后更新上下文提示');
    check(await panel.locator('.nova-quick-prompts button').first().innerText() === '问：这张卡的限额', '选中对象的问题优先');
    await panel.getByRole('button', { name: '更多示例', exact: true }).click();
    check(await panel.getByRole('button', { name: '问：锁卡预览', exact: true }).isVisible(), '保留卡片预览示例，未执行');
    await page.getByRole('navigation', { name: '主要功能' }).getByRole('button', { name: '↻ 订阅', exact: true }).click();
    await page.getByRole('button', { name: '选择这项订阅问 NOVA', exact: true }).first().click();
    check((await panel.locator('.nova-context').innerText()).includes('已选订阅'), '切换订阅和选中项后提示更新');
    check(!await panel.locator('#nova-help').count(), '换页面和选中对象不保留旧展开内容');
    await page.setViewportSize({ width: 390, height: 844 });
    const geometry = async () => page.evaluate(() => {
      const rect = selector => { const r = document.querySelector(selector).getBoundingClientRect(); return { x:r.x, y:r.y, right:r.right, bottom:r.bottom, width:r.width, height:r.height }; };
      return { viewport: innerWidth, document: document.documentElement.scrollWidth, panel:rect('#nova-panel'), input:rect('.agent-compose input'), close:rect('.agent-head .icon-button'), body:rect('.agent-body') };
    });
    let g = await geometry();
    check(g.document <= 390 && g.panel.right <= 390.5, '390px页面和助手无横向溢出');
    check(g.input.x >= 0 && g.input.right <= 390 && g.input.bottom <= 844 && g.close.bottom <= 844, '390px输入框和关闭按钮可达');
    await page.screenshot({ path: 'output/playwright/nova20-after-mobile-empty.png' });
    await panel.getByRole('button', { name: '更多示例', exact: true }).click();
    g = await geometry();
    check(g.body.height >= 65 && g.input.bottom <= 844, '窄屏展开示例时仍保留消息区和输入区');
    await page.screenshot({ path: 'output/playwright/nova20-after-mobile-more.png' });
    await panel.getByRole('button', { name: '收起示例', exact: true }).click();
    await panel.getByRole('button', { name: '收起 NOVA', exact: true }).click();
    check(!await panel.isVisible(), '窄屏关闭入口可操作');
    await page.getByRole('button', { name: '✦ 问 NOVA', exact: true }).click();
    return { results, geometry: g, chatRequests: queries };
  } finally {
    page.off('request', watch);
  }
}
