/** Run last, against the independent bill-workspace demo only. This moves
 * fictional merchant metadata and checks that the same-page candidates refresh. */
async page => {
  const token=await page.evaluate(()=>localStorage.getItem('token'));
  const read=async path=>{
    const response=await page.request.get(new URL('/api/v1'+path,page.url()).href,{headers:{Authorization:'Bearer '+token}});
    if(!response.ok())throw new Error('isolated API failed '+response.status());
    return response.json();
  };
  const balances=(await read('/accounts/U001/accounts')).map(a=>[a.account_id,a.balance_fen]);
  await page.setViewportSize({width:1440,height:1000});await page.reload();
  await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'◫ 账单洞察',exact:true}).click();
  await page.getByRole('tab',{name:'账单整理',exact:true}).click();
  await page.getByLabel('保留商户',{exact:true}).selectOption({label:'演示数码商城'});
  await page.getByText('按明确流水拆分商户',{exact:true}).click();
  const candidates=page.getByRole('group',{name:'选择待拆分交易'});
  await candidates.getByRole('checkbox').first().check();
  await page.getByLabel('新商户名称',{exact:true}).fill('演示拆分刷新验证');
  await page.getByRole('button',{name:'预览拆分',exact:true}).click();
  const preview=page.getByRole('group',{name:'确认拆分商户',exact:true});
  await preview.getByRole('button',{name:'确认拆分',exact:true}).click();
  await candidates.getByText('此商户暂无可选择的已完成流水。',{exact:true}).waitFor();
  if(await candidates.getByRole('checkbox').count()!==0)throw new Error('拆分后原商户不能保留旧交易选择');
  const catalog=await read('/bills/foundations');
  const merchant=catalog.merchants.find(item=>item.name==='演示拆分刷新验证');
  if(!merchant)throw new Error('新商户应在真实目录中可查询');
  const moved=await read('/bills/merchants/'+encodeURIComponent(merchant.merchant_id)+'/split-preview');
  if(moved.total!==1)throw new Error('新商户应包含所选交易');
  if(JSON.stringify((await read('/accounts/U001/accounts')).map(a=>[a.account_id,a.balance_fen]))!==JSON.stringify(balances))throw new Error('商户整理不得改变账户余额');
  return {checks:['拆分保存后同页候选列表自动刷新','真实目录与交易归属同步','账户余额不变'],bank:'real independent fictional database',model:'not invoked'};
}
