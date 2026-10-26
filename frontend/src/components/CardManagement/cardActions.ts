import type * as bank from '../../services/bank';

export const ACTION_LABELS: Record<bank.CardAction, string> = {
  card_apply_virtual: '申请虚拟卡', card_set_transaction_limits: '设置交易限额',
  card_adjust_credit_limit: '调整信用额度', card_unlock: '解锁卡片', card_report_lost: '挂失',
  card_lock: '锁定卡片', card_cancel_virtual: '注销虚拟卡', card_reissue: '挂失补卡', card_set_channels: '设置交易渠道',
};

export const ACTION_HINTS: Partial<Record<bank.CardAction, string>> = {
  card_report_lost: '挂失后该卡永久停用，不能解锁恢复；可随后申请补卡。如只是暂时找不到卡，可先锁卡。',
  card_cancel_virtual: '注销后该虚拟卡永久停用，不能恢复。',
  card_lock: '锁定后该卡所有交易都会被拒绝，解锁需要确认并通过验证码。',
  card_reissue: '新卡沿用原卡的限额、渠道设置和信用额度，银行签发新卡号。',
};

export const CHANNELS: [bank.CardChannelSwitch, string][] = [
  ['online_enabled', '线上支付'], ['atm_enabled', 'ATM取现'], ['overseas_enabled', '境外交易'], ['contactless_enabled', '免密/闪付'],
];

export const STATUS_LABELS: Record<string, string> = {
  ACTIVE: '正常', LOCKED: '已锁定', LOST: '已挂失', CANCELLED: '已注销',
};

export const CARD_ACTION_OPTIONS: { action: bank.CardAction; label: string; danger?: boolean }[] = [
  { action: 'card_set_transaction_limits', label: '交易限额' },
  { action: 'card_adjust_credit_limit', label: '调整额度' },
  { action: 'card_set_channels', label: '交易渠道' },
  { action: 'card_lock', label: '锁卡' },
  { action: 'card_unlock', label: '解锁' },
  { action: 'card_cancel_virtual', label: '注销', danger: true },
  { action: 'card_report_lost', label: '挂失', danger: true },
  { action: 'card_reissue', label: '补卡' },
];

export function availableCardActions(card: bank.BankCard): bank.CardAction[] {
  const active = card.status === 'ACTIVE';
  const closed = card.status === 'LOST' || card.status === 'CANCELLED';
  return CARD_ACTION_OPTIONS.filter(({ action }) => {
    switch (action) {
      case 'card_set_transaction_limits':
      case 'card_set_channels':
      case 'card_lock': return active;
      case 'card_adjust_credit_limit': return active && card.card_type === 'CREDIT';
      case 'card_unlock': return card.status === 'LOCKED';
      case 'card_cancel_virtual': return !closed && card.is_virtual;
      case 'card_report_lost': return !closed;
      case 'card_reissue': return card.status === 'LOST' && !card.replaced_by_card_id;
      default: return false;
    }
  }).map(({ action }) => action);
}

export function initialCardAction(card: bank.BankCard | null | undefined,
                                  requested: bank.CardAction): bank.CardAction {
  if (!card) return requested;
  const available = availableCardActions(card);
  return available.includes(requested) ? requested : available[0] ?? requested;
}
