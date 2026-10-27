/** 流水类型和状态的中文标签；类型取值见 backend/app/models/transaction.py。 */
const OUTGOING = new Set(['transfer_out', 'purchase', 'subscription', 'investment_purchase', 'refund_out']);
const TYPES: Record<string, string> = {
  transfer_out: '转出', transfer_in: '转入', purchase: '消费', merchant_sale: '商户收款',
  refund: '退款', refund_out: '退款支出', subscription: '订阅扣费', salary: '工资',
  investment_purchase: '理财申购', investment_redemption: '理财赎回到账', simulation_credit: '模拟入账',
};
const STATUS: Record<string, [string, 'ok' | 'warn' | 'danger' | 'plain']> = {
  completed: ['已完成', 'ok'], pending: ['处理中', 'warn'], failed: ['失败', 'danger'], cancelled: ['已取消', 'plain'],
};

export const isOutgoing = (type: string) => OUTGOING.has(type);
export const transactionTypeLabel = (type: string) => TYPES[type] ?? '其他交易';
export const transactionStatus = (status: string) => STATUS[status] ?? ['状态未知', 'plain'] as [string, 'plain'];
