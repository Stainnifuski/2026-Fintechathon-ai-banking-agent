import type {Assets,Command,WealthTask,PortfolioPlan,PortfolioSnapshot,PortfolioSelection} from './wealth';
import {readPortfolioPlan,readReviewSnapshot,planProductSelection} from './wealthPortfolioReview.ts';
export interface ReviewCommand {command:Command;status:string}
export interface OperatorContext {
  task:WealthTask;
  conversation:{history:{user:string;reply:{message:string}}[]}|null;
  plan:{plan_id:string;version:number;status:string;data:{items:ReviewCommand[]}}|null;
  portfolio?:{plan:PortfolioPlan;snapshot:PortfolioSnapshot;notice:string}|null;
}
/** Bind read-only bank/context results to the exact task the operator actually owns. */
export function checkOperatorReview(task:WealthTask,operator:string,facts:Assets,context:OperatorContext){
  if(task.control_mode!=='HUMAN'||task.owner_id!==operator||context.task.task_id!==task.task_id
    ||context.task.version!==task.version||context.task.control_mode!=='HUMAN'||context.task.owner_id!==operator
    ||context.task.account_id!==task.account_id||context.task.context_id!==task.context_id
    ||facts.context_id!==task.context_id||!facts.cash.some(a=>a.account_id===task.account_id))
    throw new Error('任务归属、版本或资金范围已变化，请刷新后重新核对。');
  return context;
}
export function operatorPortfolio(context:OperatorContext,facts:Assets){
  if(!context.portfolio)return null;
  const plan=readPortfolioPlan(context.portfolio.plan,context.task.context_id);
  const snapshot=readReviewSnapshot(context.portfolio.snapshot,plan);
  if(!snapshot.account_ids.includes(context.task.account_id)||snapshot.account_ids.some(id=>!facts.cash.some(a=>a.account_id===id)))
    throw new Error('原整体快照的账户范围已变化，请重新核对。');
  const selection=planProductSelection(plan);
  if(selection.items.length!==plan.selected_positions.length)throw new Error('原方案具体产品资料缺失，不能整理比例调整。');
  return {plan,snapshot,selection:{...selection,scopeKey:plan.asset_scope_id}};
}
export function portfolioAdjustmentNote(plan:PortfolioPlan,selection:PortfolioSelection):string{
  if(selection.portfolio_id!==plan.portfolio_id||selection.expected_version!==plan.version||selection.snapshot_id!==plan.snapshot_id||selection.context_id!==plan.context_id
    ||selection.items.length!==plan.selected_positions.length)throw new Error('调整建议与原整体方案版本不一致。');
  const seen=new Set<string>();
  const notes=selection.items.map(item=>{
    const original=plan.selected_positions.find(p=>p.product_id===item.product_id&&p.product_version===item.product_version&&p.share_class===item.share_class);
    if(!original||seen.has(item.product_id)||!/^\d+(\.\d{1,4})?$/.test(item.target_ratio_total)||Number(item.target_ratio_total)>1)throw new Error('建议比例或产品引用不合法。');
    seen.add(item.product_id);
    return `${item.product_id} v${item.product_version}：${Number((Number(item.target_ratio_total)*100).toFixed(2))}%`;
  });
  const note=`整体方案 ${plan.portfolio_id} v${plan.version}（快照 ${plan.snapshot_id}）待本人核对的比例建议，未校验、未修改方案、未授权：\n${notes.join('；')}。交还后须重读资料并由本人调用配置服务校验。`;
  if(note.length>1000)throw new Error('建议超过1000字，请简化后填写处理说明。');
  return note;
}
export function reviewCommands(context:OperatorContext):ReviewCommand[]{
  return context.plan?.data.items??context.task.nodes.filter(n=>n.kind==='SUBMIT'&&n.command)
    .map(n=>({command:n.command!,status:n.status}));
}
/** A proposed adjustment is explanatory text only, never a bank command. */
export function adjustmentNote(rows:ReviewCommand[],values:Record<string,string>):string{
  const notes:string[]=[];
  for(const {command} of rows){
    const value=values[command.operation_id]?.trim();if(!value)continue;
    if(command.direction==='purchase'){
      if(!/^\d+(\.\d{1,2})?$/.test(value))throw new Error('拟申购金额须大于零，最多两位小数。');
      const [whole,fraction='']=value.split('.');const fen=BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));
      if(fen<=0n||fen>2000000000n)throw new Error('拟申购金额超出允许范围。');
      notes.push(`${command.product_id} v${command.product_version}（原号 ${command.operation_id}）：建议本人重新核对申购金额 ${value} 元`);
    }else{
      if(!/^\d+(\.\d{1,4})?$/.test(value)||Number(value)<=0||!Number.isFinite(Number(value)))throw new Error('拟赎回份额须大于零，最多四位小数。');
      notes.push(`${command.product_id} v${command.product_version}（原号 ${command.operation_id}）：建议本人重新核对赎回 ${value} 份`);
    }
  }
  if(!notes.length)throw new Error('请先填写至少一项待本人核对的调整。');
  const result='待本人核对的调整（未修改订单、未授权）：\n'+notes.join('；\n');
  if(result.length>1000)throw new Error('调整说明超过1000字，请分次核对。');
  return result;
}
