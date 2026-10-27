import {useTaskField} from './NovaTaskPresentation';
import { useEffect, useState, type FormEvent } from 'react';
import AddContactForm from './AddContactForm';
import { type NovaCollectionCard as CollectionCard, type NovaTaskSession,
  refreshNovaCollection, sendNovaCollection, getNovaTaskSession } from '../services/nova';
import { cardTypeLabel } from '../utils/accountType';

type Props = { card: CollectionCard; session: NovaTaskSession;manual?:boolean;
  onUpdated: (update: Pick<NovaTaskSession, 'session_id' | 'session_version' | 'collection_cards'>) => void;
  onActivity?: () => void; disabled: boolean };

function errorText(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail;
  return detail?.message || (error instanceof Error ? error.message : '操作失败，请稍后重试。');
}

export default function NovaCollectionCard({ card, session, onUpdated, onActivity, disabled,manual }: Props) {
  const [selected, setSelected] = useTaskField('selected',card.selected_payer_user_ids);
  const [account, setAccount] = useTaskField('account',card.receive_account_id);
  const [amount, setAmount] = useTaskField('amount',card.amount_fen ? (card.amount_fen / 100).toFixed(2) : '');
  const [split, setSplit] = useTaskField('split',card.split_equally);
  const [note, setNote] = useTaskField('note',card.note);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { setSelected(card.selected_payer_user_ids); }, [card.selected_payer_user_ids]);
  useEffect(() => { setAccount(card.receive_account_id); }, [card.receive_account_id]);
  useEffect(() => {
    setAmount(card.amount_fen ? (card.amount_fen / 100).toFixed(2) : '');
  }, [card.amount_fen]);
  useEffect(() => { setSplit(card.split_equally); }, [card.split_equally]);
  useEffect(() => { setNote(card.note); }, [card.note]);

  async function refresh() {
    const latest=manual?await getNovaTaskSession(session.session_id):session;
    const updated = await refreshNovaCollection(session.session_id, card.collection_id, latest.session_version);
    onUpdated(updated);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || disabled || card.status !== 'DRAFT') return;
    setBusy(true); setError('');
    try {
      const latest=manual?await getNovaTaskSession(session.session_id):session;
      if(manual){const current=latest.collection_cards.find(item=>item.collection_id===card.collection_id);if(!current||current.status!=='DRAFT')throw new Error('原收款任务已变化，请重新核对，未重复发送。');}
      const updated = await sendNovaCollection(session.session_id, card.collection_id, {
        session_version: latest.session_version, payer_user_ids: selected,
        receive_account_id: account, amount: Number(amount), split_equally: split, note: note.trim(),
      });
      onUpdated(updated); onActivity?.();
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  }

  const cents = Math.round(Number(amount) * 100);
  const exact = Number.isFinite(cents) && cents > 0 && (!split || cents % selected.length === 0);
  const unresolvedNames = card.ambiguous_names.filter(name => new Set(card.contacts
    .filter(person => (person.name === name || person.nickname === name) && selected.includes(person.user_id))
    .map(person => person.user_id)).size !== 1);
  const ready = card.status === 'DRAFT' && selected.length > 0 && !!account && exact &&
    card.unknown_names.length === 0 && unresolvedNames.length === 0;

  return <article className="card nova-transfer-card nova-collection-card" aria-label="收款申请草稿">
    <div className="nova-transfer-card-head"><div><strong>收款申请</strong>
      <small>{card.status === 'SENT' ? '申请已发出，请在收款申请列表核对状态' :
        card.status === 'CANCELLED' ? '草稿已撤销 · 未发出申请' : '草稿 · 尚未向对方发出申请'}</small></div></div>
    {card.status === 'SENT' ? <p>已向 {card.selected_payer_user_ids.length} 位联系人发出收款申请。发出申请不代表已经到账。</p> :
      card.status === 'CANCELLED' ? <p>这张收款草稿已撤销，不能再发出申请。</p> : <>
      <form className="nova-collection-form" onSubmit={event => void submit(event)}>
        <fieldset disabled={busy || disabled}><legend>向谁收款（已保存联系人，可多选）</legend>
          {card.contacts.length === 0 && <p className="hint">暂无可用的已保存联系人</p>}
          {card.contacts.map(person => <label key={person.contact_id} className="check-option">
            <input type="checkbox" checked={selected.includes(person.user_id)} onChange={event =>
              setSelected(previous => event.target.checked
                ? [...new Set([...previous, person.user_id])]
                : previous.filter(id => id !== person.user_id))} />
            {person.label}{person.nickname ? `（${person.name}）` : ''}
          </label>)}
        </fieldset>
        {unresolvedNames.length > 0 && <p className="hint">同名联系人需先明确勾选其中一位：{unresolvedNames.join('、')}</p>}
        <label>收款账户<select value={account} onChange={event => setAccount(event.target.value)} disabled={busy || disabled}>
          <option value="">请选择银行卡</option>
          {card.accounts.map(item => <option key={item.account_id} value={item.account_id}>
            {cardTypeLabel(item.account_type)} · 尾号 {item.card_masked}
          </option>)}
        </select></label>
        <label>{split ? 'AA 总金额（元）' : '每人收款金额（元）'}<input required type="number" min="0.01" step="0.01"
          value={amount} onChange={event => setAmount(event.target.value)} disabled={busy || disabled} placeholder="请输入金额" /></label>
        <label className="nova-collection-check"><input type="checkbox" checked={split} disabled={busy || disabled}
          onChange={event => setSplit(event.target.checked)} />AA 均分总额</label>
        {split && selected.length > 0 && cents > 0 && cents % selected.length !== 0 &&
          <p className="error">总金额无法精确均分到分，请调整金额。</p>}
        <label>备注（可选）<input maxLength={256} value={note} onChange={event => setNote(event.target.value)} disabled={busy || disabled} /></label>
        <button type="submit" disabled={!ready || busy || disabled}>{busy ? '正在发出…' : '发出收款申请'}</button>
      </form>
      {card.unknown_names.map(name => <section className="nova-collection-contact" key={name}>
        <h4>添加联系人：{name}</h4>
        <p className="hint">请填写完整手机号或银行卡号；备注名可选。添加后会自动勾选此人。</p>
        <AddContactForm initialName={name} onAdded={refresh} />
      </section>)}
    </>}
    {error && <p className="error" role="alert">{error}</p>}
  </article>;
}
