async page => {
  const results=[], errors=[];
  const check=(ok,label)=>{if(!ok)throw Error(label);results.push(label);};
  const panel=page.getByRole('complementary',{name:'NOVA 对话'});
  const onError=e=>errors.push(e.message);page.on('pageerror',onError);
  try {
    await page.reload();
    await page.getByRole('button',{name:'✦ 问 NOVA',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('.agent-compose input').disabled);
    await panel.locator('.nova-conversation-list button').filter({hasText:'现在北京时间几点？'}).first().click();
    await page.waitForFunction(()=>document.querySelectorAll('.nova-assistant').length===3&&!document.querySelector('.agent-compose input').disabled);
    check(await panel.locator('.nova-assistant').count()===3,'刷新及切换恢复真实服务端三轮历史，未保存UI隔离长文本');
    await panel.getByRole('button',{name:'＋ 新建对话',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('.agent-compose input').disabled&&document.querySelectorAll('.nova-message').length===0);
    check(await panel.locator('.nova-quick-prompts button').count()===2,'新建对话恢复紧凑开场');
    await panel.getByRole('textbox',{name:'向 NOVA 提问'}).fill('现在北京时间几点？');
    await panel.getByRole('button',{name:'发送',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.nova-assistant').length===1&&!document.querySelector('.agent-compose input').disabled);
    await panel.locator('.nova-conversation-list button').filter({hasText:'我想转账'}).click();
    await page.waitForFunction(()=>document.querySelector('.nova-assistant')?.textContent.includes('当前为离线模式')&&!document.querySelector('.agent-compose input').disabled);
    check(await panel.locator('.nova-assistant').count()===1,'历史会话切换不混入其他会话消息');
    await page.setViewportSize({width:390,height:560});
    const geometry=await page.evaluate(()=>{
      const box=s=>document.querySelector(s).getBoundingClientRect();
      return {inputBottom:box('.agent-compose input').bottom,closeBottom:box('.agent-head .icon-button').bottom,bodyHeight:box('.agent-body').height,width:innerWidth,scrollWidth:document.documentElement.scrollWidth};
    });
    check(geometry.inputBottom<=560&&geometry.closeBottom<=560&&geometry.bodyHeight>=65&&geometry.scrollWidth<=390,'390×560历史栏保留输入、关闭和消息区');
    await page.screenshot({path:'output/playwright/main-sync-nova-history-mobile.png'});
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:'output/playwright/main-sync-nova-history-desktop.png'});
    await panel.getByRole('button',{name:'收起 NOVA',exact:true}).click();
    await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'◫ 账单洞察',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.bills canvas').length>=2);
    check(errors.length===0,'会话历史切换及返回账单无未捕获异常');
    return {results,errors,geometry,limitation:'真实转账任务卡需要model模式；本次offline预览不覆盖任务卡端到端'};
  }finally{page.off('pageerror',onError);await page.setViewportSize({width:1440,height:1000});}
}
