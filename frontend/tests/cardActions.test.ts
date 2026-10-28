import test from 'node:test';
import assert from 'node:assert/strict';
import type { BankCard, CardAction } from '../src/services/bank.ts';
import { availableCardActions, initialCardAction } from '../src/components/CardManagement/cardActions.ts';

const base: BankCard = {
  card_id: 'C1', account_id: 'A1', account_label: '****8842', card_type: 'DEBIT',
  is_virtual: false, card_number: '****8842', status: 'ACTIVE', used_credit_fen: 0,
  single_txn_limit_fen: 10000, daily_txn_limit_fen: 50000,
  single_txn_cap_fen: 100000, daily_txn_cap_fen: 200000, state_version: 1,
  online_enabled: true, atm_enabled: true, overseas_enabled: false, contactless_enabled: true,
};
const actions = (card: Partial<BankCard>): CardAction[] => availableCardActions({ ...base, ...card });

test('the card task and card management expose the same valid actions by card state', () => {
  assert.deepEqual(actions({ status: 'LOST' }), ['card_reissue']);
  assert.deepEqual(actions({ status: 'LOST', replaced_by_card_id: 'C2' }), []);
  assert.deepEqual(actions({ status: 'CANCELLED' }), []);
  assert.deepEqual(actions({ status: 'LOCKED' }), ['card_unlock', 'card_report_lost']);
  assert.deepEqual(actions({ status: 'LOCKED', is_virtual: true }),
    ['card_unlock', 'card_cancel_virtual', 'card_report_lost']);
  assert.deepEqual(actions({ card_type: 'CREDIT' }),
    ['card_set_transaction_limits', 'card_adjust_credit_limit', 'card_set_channels',
      'card_lock', 'card_report_lost']);
  assert.deepEqual(actions({ is_virtual: true }),
    ['card_set_transaction_limits', 'card_set_channels', 'card_lock',
      'card_cancel_virtual', 'card_report_lost']);
});

test('a stale requested action opens on an available action', () => {
  assert.equal(initialCardAction({ ...base, status: 'LOST' }, 'card_lock'), 'card_reissue');
  assert.equal(initialCardAction({ ...base, status: 'LOST' }, 'card_reissue'), 'card_reissue');
  assert.equal(initialCardAction(base, 'card_set_channels'), 'card_set_channels');
});
