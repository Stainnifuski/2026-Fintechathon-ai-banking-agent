import type { AuditEvent } from '../../services/audit';
import { formatFen } from '../../utils/money';

export const domains: Record<string, string> = {
  transfer: '转账', payments: '付款与退款', wealth: '理财', cards: '卡片', subscriptions: '订阅',
  shops: '生活服务', bills: '账单', contacts: '联系人', calendar: '日历', identity: '身份与安全',
  assistant: '智能助手', accounts: '账户', bank: '银行处理', audit: '记录查询', system: '系统',
};
export const outcomes: Record<string, string> = {
  success: '成功', failed: '失败', cancelled: '已取消', blocked: '已阻止', unknown: '待核对',
  partial: '部分完成', pending_confirmation: '待本人确认', pending_verification: '待身份验证',
  request_completed: '请求已完成', committed: '变更已提交', checked: '校验已完成', started: '已开始',
  completed: '传输已完成', disconnected: '连接已断开', interrupted: '执行中断', simulated: '模拟结果',
};
export const phases: Record<string, string> = {
  start: '开始', request: '收到请求', decision: '决策与校验', state: '任务推进',
  mutation: '变更记录', result: '执行结果', response: '接口结果', transport: '连接状态', legacy: '历史记录',
};
const actions: Record<string, string> = {
  create_transfer_task: '创建转账任务', confirm_task: '确认转账', accept_task: '受理转账',
  dispatch_task: '提交转账', reconcile_task: '核对原交易', cancel_task: '取消转账', modify_task: '修改转账',
  verify_task: '验证转账身份', submit_transfer: '银行处理转账', 'transfer.risk': '转账风险校验',
  chat: '助手处理需求', route_request: '确定任务与依赖', execute_tool: '调用业务工具',
  'agent.tool_policy': '核验工具权限', 'agent.security': '请求安全校验', 'answer.evidence': '核对回答依据',
  CONFIRMED: '本人确认', MFA_VERIFIED: '身份验证通过', CREATED: '创建任务',
  transfer: '转账', bank_transfer: '银行处理转账', investment_accept: '银行受理理财', investment_verification: '理财身份验证',
  investment_pricing: '理财计价', investment_cash: '理财资金入账', investment_quick_cash: '快速赎回到账',
  security_locked: '安全锁定', security_recovered: '恢复操作权限', security_attempt_rejected: '拒绝异常请求',
  'wealth.archive_cleanup': '清理演示轮次', execute_due_scheduled_transfers: '执行到期转账', tick: '推进理财任务',
};
const tables: Record<string, string> = {
  transfer_tasks: '转账任务', transfer_plans: '转账计划', transfer_operations: '转账操作',
  transfer_authorizations: '转账授权', bank_operations: '银行操作', accounts: '账户', transactions: '流水',
  wealth_tasks: '理财任务', wealth_task_nodes: '理财步骤', wealth_plans: '理财计划',
  wealth_orders: '理财成交', wealth_authorizations: '理财授权', wealth_lots: '持仓', wealth_settlements: '理财结算',
  calendar_events: '日历事件', contacts: '联系人', cards: '卡片', card_operations: '卡片操作',
  subscriptions: '订阅', payment_requests: '收款申请', flower_orders: '鲜花订单', cake_orders: '蛋糕订单',
  nova_conversations: '对话', nova_task_sessions: '对话任务', security_states: '安全状态', sms_challenges: '短信验证',
};
export function actionLabel(event: AuditEvent): string {
  if (actions[event.action]) return actions[event.action];
  const [table, mutation] = event.action.split('.');
  if (tables[table]) return `${tables[table]}${({ created: '已创建', updated: '已更新', deleted: '已清理' } as Record<string, string>)[mutation] || ''}`;
  return `${domains[event.domain] || '业务'} · ${phases[event.phase] || '处理记录'}`;
}
export function permissionLabel(level: string): string {
  return ({ green: '自动执行', yellow: '本人确认', red: '强验证', delegated: '关联授权', unknown: '未定级' } as Record<string, string>)[level] || level;
}
export function evidenceSummary(event: AuditEvent): string[] {
  const facts = event.facts;
  const params = (facts.params || facts.after || {}) as Record<string, unknown>;
  const result = (facts.result || {}) as Record<string, unknown>;
  const values = { ...params, ...result, ...facts };
  const notes: string[] = [];
  if (typeof values.amount_fen === 'number') notes.push(`金额 ${formatFen(values.amount_fen)}`);
  if (values.currency) notes.push(`币种 ${values.currency}`);
  if (values.rule_id) notes.push(`依据 ${values.rule_id}`);
  if (values.mfa_method) notes.push(`验证方式 ${values.mfa_method === 'sms' ? '短信' : values.mfa_method === 'totp' ? '验证器' : values.mfa_method}`);
  if (values.user_confirmed === true) notes.push('已记录本人确认');
  if (values.tool) notes.push(`调用 ${values.tool}`);
  if (typeof values.result_count === 'number') notes.push(`返回 ${values.result_count} 项`);
  if (event.error_code) notes.push(`原因 ${event.error_code}`);
  return notes;
}
