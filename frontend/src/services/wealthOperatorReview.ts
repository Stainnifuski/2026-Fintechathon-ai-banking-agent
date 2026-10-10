import type {Assets,Command,WealthTask} from './wealth';
export interface ReviewCommand {command:Command;status:string}
export interface OperatorContext {
  task:WealthTask;
  conversation:{history:{user:string;reply:{message:string}}[]}|null;
  plan:{plan_id:string;version:number;status:string;data:{items:ReviewCommand[]}}|null;
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
