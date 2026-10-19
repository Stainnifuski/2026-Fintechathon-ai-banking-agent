import { useState, type FormEvent } from 'react';
import * as bank from '../services/bank';

interface Props { onAdded: () => Promise<void>; }

export default function AddContactForm({ onAdded }: Props) {
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [cardNumber, setCardNumber] = useState('');
  const [nickname, setNickname] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      await bank.createManualContact({
        name: name.trim(), phone: phone.trim() || undefined,
        card_number: cardNumber.trim() || undefined, nickname: nickname.trim() || undefined,
      });
      setName(''); setPhone(''); setCardNumber(''); setNickname('');
      await onAdded();
    } catch (cause) {
      const detail = (cause as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail;
      setError(detail?.message || (cause instanceof Error ? cause.message : '添加联系人失败'));
    } finally { setBusy(false); }
  };

  return <form className="add-contact-form" onSubmit={submit}>
    <label>姓名<input required maxLength={64} value={name} onChange={event => setName(event.target.value)} /></label>
    <label>手机号<input inputMode="tel" maxLength={32} value={phone} onChange={event => setPhone(event.target.value)} placeholder="手机号或银行卡号至少填写一项" /></label>
    <label>银行卡号<input inputMode="numeric" maxLength={32} value={cardNumber} onChange={event => setCardNumber(event.target.value)} placeholder="完整的16位模拟银行卡号" /></label>
    <label>备注名（可选）<input maxLength={64} value={nickname} onChange={event => setNickname(event.target.value)} /></label>
    <button type="submit" disabled={busy || !name.trim() || (!phone.trim() && !cardNumber.trim())}>{busy ? '正在添加…' : '添加联系人'}</button>
    {error && <p className="error" role="alert">{error}</p>}
  </form>;
}
