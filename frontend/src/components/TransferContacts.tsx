import { useEffect, useState } from 'react';
import * as bank from '../services/bank';
import AddContactForm from './AddContactForm';

export type TransferChoice = { name: string; cardNumber: string | null; phoneNumber: string | null; accountId: string; cardAccountId: string | null; phoneAccountId: string | null };
interface Props { userId: string; mode: 'people' | 'settings'; onChoose: (choice: TransferChoice) => void; onChanged: () => Promise<void>; }
const errorMessage = (error: unknown) =>
  (error as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail?.message
  || (error instanceof Error ? error.message : '操作失败，请重试');
const knownNumbers = (item: { phone_masked: string | null; card_masked?: string | null }) =>
  [item.phone_masked && `手机号 ${item.phone_masked}`,
    item.card_masked && `银行卡尾号 ${item.card_masked.slice(-4)}`].filter(Boolean).join(' · ');

export default function TransferContacts({ userId, mode, onChoose, onChanged }: Props) {
  const [contacts, setContacts] = useState<bank.TransferContact[]>([]);
  const [history, setHistory] = useState<bank.TransferHistoryRecipient[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState('');
  const [nickname, setNickname] = useState('');
  const [saving, setSaving] = useState('');
  const [saveNickname, setSaveNickname] = useState('');

  const reload = async () => {
    const [saved, recent] = await Promise.all([bank.listContacts(), bank.transferRecipientHistory()]);
    setContacts(saved); setHistory(recent);
  };
  useEffect(() => {
    let alive = true;
    Promise.all([bank.listContacts(), bank.transferRecipientHistory()])
      .then(([saved, recent]) => { if (alive) { setContacts(saved); setHistory(recent); } })
      .catch(cause => { if (alive) setError(errorMessage(cause)); });
    return () => { alive = false; };
  }, [userId]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await action(); } catch (cause) { setError(errorMessage(cause)); }
    finally { setBusy(false); }
  };
  const chooseContact = (item: bank.TransferContact) => run(async () => {
    const found = await bank.resolveContact(item.contact_id);
    if (found.length > 0) {
      if (!found[0].card_number && !found[0].phone_number) {
        throw new Error('旧联系人号码来源无法确认，请在“添加联系人”中重新填写已知号码');
      }
      onChoose({ name: item.name, cardNumber: found[0].card_number || null,
        phoneNumber: found[0].phone_number || null, accountId: found[0].account_id,
        cardAccountId: found[0].card_account_id || null,
        phoneAccountId: found[0].phone_account_id || null });
    }
  });
  const addHistory = (item: bank.TransferHistoryRecipient) => run(async () => {
    if (item.card_number && item.phone_number) {
      await bank.createManualContact({ name: item.name, card_number: item.card_number,
        phone: item.phone_number, nickname: saveNickname });
    } else {
      await bank.createContact(item.card_number ? { card_number: item.card_number }
        : { phone: item.phone_number! }, item.account_id, saveNickname);
    }
    setSaving(''); setSaveNickname(''); await reload(); await onChanged();
  });
  const rename = (item: bank.TransferContact) => run(async () => {
    await bank.renameContact(item.contact_id, nickname); setEditing(''); await reload(); await onChanged();
  });
  const remove = (item: bank.TransferContact) => {
    void run(async () => { await bank.deleteContact(item.contact_id); await reload(); await onChanged(); });
  };
  const clearHistory = () => {
    if (!window.confirm('清除历史转账人列表？交易流水仍会保留。')) return;
    void run(async () => { await bank.clearTransferRecipientHistory(); setHistory([]); await onChanged(); });
  };

  return <div className="transfer-contacts">
    {mode === 'people' ? <>
      <h3>已保存的联系人</h3>
      {contacts.length === 0 && <p className="hint">暂无已保存的转账人</p>}
      {contacts.map(item => <button type="button" className="recipient-entry" key={item.contact_id} disabled={busy} onClick={() => chooseContact(item)}>
        <strong>{item.nickname || item.name}</strong><span>{[item.nickname && item.name, knownNumbers(item)].filter(Boolean).join(' · ') || '号码来源待确认'}</span>
      </button>)}
      <h3>历史转账人</h3>
      {history.length === 0 && <p className="hint">暂无历史转账人</p>}
      {history.map(item => <div className="recipient-history-entry" key={item.account_id}>
        <button type="button" className="recipient-entry" onClick={() => onChoose({ name: item.name, cardNumber: item.card_number, phoneNumber: item.phone_number, accountId: item.account_id, cardAccountId: item.card_account_id, phoneAccountId: item.phone_account_id })}>
          <strong>{item.name}</strong><span>{knownNumbers({ phone_masked: item.phone_masked, card_masked: item.card_number })}</span>
        </button>
        {contacts.some(contact => contact.name === item.name && ((item.phone_masked && contact.phone_masked === item.phone_masked) || (item.card_number && contact.card_masked?.slice(-4) === item.card_number.slice(-4))))
          ? <small>已保存</small>
          : saving === item.account_id ? <div className="recipient-save"><label>备注名（可选）<input maxLength={64} value={saveNickname} onChange={event => setSaveNickname(event.target.value)} /></label><button type="button" disabled={busy} onClick={() => addHistory(item)}>保存</button><button type="button" className="secondary" onClick={() => setSaving('')}>取消</button></div>
          : <button type="button" className="secondary" onClick={() => { setSaving(item.account_id); setSaveNickname(''); }}>加到联系人</button>}
      </div>)}
      <details className="add-contact-details"><summary>＋ 添加联系人</summary><AddContactForm onAdded={async () => { await reload(); await onChanged(); }} /></details>
    </> : <>
      <h3>转账人设置</h3>
      <button type="button" className="secondary" disabled={busy || history.length === 0} onClick={clearHistory}>清除历史转账人</button>
      <p className="hint">只清除快捷选择记录，交易流水仍会保留。</p>
      <h3>管理联系人</h3>
      {contacts.length === 0 && <p className="hint">暂无联系人</p>}
      {contacts.map(item => <div className="recipient-manage" key={item.contact_id}>
        <span><strong>{item.nickname || item.name}</strong> · {item.name}{knownNumbers(item) && ` · ${knownNumbers(item)}`}</span>
        {editing === item.contact_id ? <div className="recipient-save"><label>备注名<input maxLength={64} value={nickname} onChange={event => setNickname(event.target.value)} /></label><button type="button" disabled={busy} onClick={() => rename(item)}>保存</button><button type="button" className="secondary" onClick={() => setEditing('')}>取消</button></div>
          : <div className="recipient-save"><button type="button" className="secondary" disabled={busy} onClick={() => { setEditing(item.contact_id); setNickname(item.nickname || ''); }}>修改备注</button><button type="button" className="secondary" disabled={busy} onClick={() => remove(item)}>删除</button></div>}
      </div>)}
    </>}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
