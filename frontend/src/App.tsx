import { FormEvent, useEffect, useRef, useState } from 'react';
import * as bank from './services/bank';

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;

function App() {
  const [user, setUser] = useState<bank.User | null>(() => JSON.parse(localStorage.getItem('bank-user') || 'null'));
  const [loginMode, setLoginMode] = useState<'login' | 'register'>('login');
  const [phone, setPhone] = useState('13800000001');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('password123');
  const [accounts, setAccounts] = useState<bank.Account[]>([]);
  const [recipients, setRecipients] = useState<bank.Recipient[]>([]);
  const [transactions, setTransactions] = useState<bank.Transaction[]>([]);
  const [paymentRequests, setPaymentRequests] = useState<bank.PaymentRequest[]>([]);
  const [fromAccount, setFromAccount] = useState('');
  const [toUserId, setToUserId] = useState('');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [requestPayerIds, setRequestPayerIds] = useState<string[]>([]);
  const [requestAccountId, setRequestAccountId] = useState('');
  const [requestAmount, setRequestAmount] = useState('');
  const [requestNote, setRequestNote] = useState('');
  const [splitEqually, setSplitEqually] = useState(false);
  const [status, setStatus] = useState('');
  const [chatInput, setChatInput] = useState('');
  const [chat, setChat] = useState<string[]>([]);
  const [activeTask, setActiveTask] = useState<bank.TaskSnapshot | null>(null);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isActing, setIsActing] = useState(false);
  const payerSelectorRef = useRef<HTMLDetailsElement>(null);

  const message = (error: unknown) => {
    const detail = (error as { response?: { data?: { detail?: string | { message?: string; capability?: string } } } })?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail?.message) return detail.message;
    if (detail?.capability) return `${detail.capability} 暂未实现`;
    return error instanceof Error ? error.message : '请求失败，请稍后重试';
  };

  const load = async () => {
    if (!user) return;
    try {
      const [a, r, h, requests] = await Promise.all([bank.accounts(user.user_id), bank.recipients(), bank.history(), bank.paymentRequests()]);
      setAccounts(a); setRecipients(r); setTransactions(h); setPaymentRequests(requests);
      const defaultFrom = a.find(acc => acc.account_type === 'savings')?.account_id || a[0]?.account_id || '';
      setFromAccount(current => current && a.some(acc => acc.account_id === current) ? current : defaultFrom);
      setToUserId(current => current && r.some(rec => rec.user_id === current) ? current : r[0]?.user_id || '');
      setRequestPayerIds(current => current.filter(id => r.some(rec => rec.user_id === id)));
      setRequestAccountId(current => current && a.some(acc => acc.account_id === current) ? current : defaultFrom);
    } catch (error) { setStatus(message(error)); }
  };

  useEffect(() => { load(); }, [user?.user_id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const closePayerSelector = (event: MouseEvent) => {
      const selector = payerSelectorRef.current;
      if (selector?.open && event.target instanceof Node && !selector.contains(event.target)) {
        selector.open = false;
      }
    };
    document.addEventListener('mousedown', closePayerSelector);
    return () => document.removeEventListener('mousedown', closePayerSelector);
  }, []);
  useEffect(() => {
    if (!activeTask || !['EXECUTING', 'RECONCILING'].includes(activeTask.phase)) return;
    const timer = window.setInterval(async () => {
      try {
        const task = await bank.getTask(activeTask.task_id);
        setActiveTask(task); setStatus(task.message);
        if (task.phase === 'FINISHED') await load();
      } catch (error) { setStatus(message(error)); }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeTask?.task_id, activeTask?.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  const authenticate = async (event: FormEvent) => {
    event.preventDefault(); setStatus(''); setIsAuthenticating(true);
    try {
      const result = loginMode === 'login' ? await bank.login(phone, password) : await bank.register(name, phone, password);
      localStorage.setItem('token', result.access_token);
      localStorage.setItem('bank-user', JSON.stringify(result.user));
      setUser(result.user);
    } catch (error) { setStatus(`登录失败：${message(error)}`); }
    finally { setIsAuthenticating(false); }
  };

  const submitTransfer = async (event: FormEvent) => {
    event.preventDefault(); setStatus('');
    const recipient = recipients.find(item => item.user_id === toUserId);
    if (!fromAccount || !recipient) return setStatus('请选择付款账户和收款人');
    try {
      const task = await bank.createTransferTask({
        request_id: newId(), operation_id: newId(), from_account: fromAccount,
        to_account: recipient.account_id, amount: Number(amount), note: note || undefined,
      });
      setActiveTask(task); setStatus(task.message);
    } catch (error) { setStatus(message(error)); }
  };

  const submitPaymentRequest = async (event: FormEvent) => {
    event.preventDefault(); setStatus('');
    if (requestPayerIds.length === 0 || !requestAccountId) return setStatus('请至少选择一位付款人和收款账户');
    const amountFen = Math.round(Number(requestAmount) * 100);
    if (splitEqually && amountFen % requestPayerIds.length !== 0) {
      return setStatus('总金额无法精确均分到分，请调整金额');
    }
    setIsActing(true);
    try {
      await bank.createPaymentRequestBatch({
        payer_user_ids: requestPayerIds, receive_account_id: requestAccountId,
        amount: Number(requestAmount), split_equally: splitEqually,
        note: requestNote || undefined, idempotency_key: newId(),
      });
      setRequestAmount(''); setRequestNote(''); setStatus(`已向 ${requestPayerIds.length} 人发出收款申请`); await load();
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const actOnPaymentRequest = async (request: bank.PaymentRequest, action: 'accept' | 'reject' | 'cancel') => {
    if (isActing) return;
    setIsActing(true); setStatus('');
    try {
      if (action === 'accept') {
        if (!fromAccount) throw new Error('请选择付款账户');
        let task = await bank.acceptPaymentRequest(request, fromAccount);
        // 第一次点击仅创建并展示安全转账任务；第二次“确认付款”才记录授权。
        // 低风险授权后立即提交银行，大额则停在 MFA 验证阶段。
        if (request.status === 'ACCEPTED' && task.allowed_actions.includes('confirm')) {
          task = await bank.confirmTask(task, newId());
        }
        if (request.status === 'ACCEPTED' && task.allowed_actions.includes('accept')) {
          task = await bank.acceptTask(task, newId());
        }
        setActiveTask(task);
        setStatus(task.message);
        await load();
        return;
      }
      else if (action === 'reject') await bank.rejectPaymentRequest(request);
      else await bank.cancelPaymentRequest(request);
      setStatus(action === 'reject' ? '已拒绝收款申请' : '已取消收款申请');
      await load();
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const sendChat = async () => {
    if (!chatInput.trim()) return;
    const input = chatInput; setChatInput('');
    try {
      const task = await bank.createChatTask({ request_id: newId(), operation_id: newId(), message: input });
      setChat(items => [...items, `你：${input}`, `系统：${task.message}`]);
      setActiveTask(task); setStatus(task.message);
    } catch (error) { setChat(items => [...items, `你：${input}`, `系统：${message(error)}`]); }
  };

  const actOnTask = async (action: 'confirm' | 'cancel' | 'reconcile' | 'verify') => {
    if (!activeTask || isActing) return;
    setIsActing(true);
    try {
      let task = action === 'confirm' ? await bank.confirmTask(activeTask, newId())
        : action === 'cancel' ? await bank.cancelTask(activeTask, newId())
        : action === 'reconcile' ? await bank.reconcileTask(activeTask, newId())
        : await bank.verifyTask(activeTask, newId());
      if (action === 'confirm' && task.allowed_actions.includes('accept')) {
        task = await bank.acceptTask(task, newId());
      }
      setActiveTask(task); setStatus(task.message);
      if (task.phase === 'FINISHED') {
        setAmount(''); setNote(''); await load();
      }
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const logout = () => {
    localStorage.removeItem('token'); localStorage.removeItem('bank-user');
    setUser(null); setAccounts([]); setPaymentRequests([]); setChat([]); setActiveTask(null);
  };

  const requestAmountNumber = Number(requestAmount) || 0;
  const splitIsExact = requestPayerIds.length > 0
    && Math.round(requestAmountNumber * 100) % requestPayerIds.length === 0;
  const perPersonAmount = splitEqually && requestPayerIds.length > 0
    ? requestAmountNumber / requestPayerIds.length
    : requestAmountNumber;
  const requestTotal = splitEqually
    ? requestAmountNumber
    : requestAmountNumber * requestPayerIds.length;

  if (!user) return <main className="auth"><section className="card"><h1>AI 模拟银行</h1><p>任务状态机转账演示</p><form onSubmit={authenticate}>
    {loginMode === 'register' && <label>姓名<input required value={name} onChange={e => setName(e.target.value)} /></label>}
    <label>手机号<input required value={phone} onChange={e => setPhone(e.target.value)} /></label>
    <label>密码<input required minLength={8} type="password" value={password} onChange={e => setPassword(e.target.value)} /></label>
    <button disabled={isAuthenticating}>{isAuthenticating ? '正在登录…' : loginMode === 'login' ? '登录' : '注册并登录'}</button>
  </form><button className="link" onClick={() => setLoginMode(loginMode === 'login' ? 'register' : 'login')}>{loginMode === 'login' ? '没有账号？注册' : '已有账号？登录'}</button>
  <p className="hint">演示账户：张三 13800000001 / password123</p>{status && <p className="notice">{status}</p>}</section></main>;

  return <main className="app">
    <header><div><strong>AI 模拟银行</strong><span>您好，{user.name}</span></div><button className="secondary" onClick={logout}>退出登录</button></header>
    {status && <p className="notice">{status}</p>}
    <section className="grid">
      <div className="card"><h2>我的账户</h2>{accounts.map(a => <div className="account" key={a.account_id}><span>{a.account_type === 'savings' ? '活期账户' : a.account_type} · {a.card_number}</span><strong>¥{a.balance.toFixed(2)}</strong><small>今日已转 ¥{a.daily_transfer_used.toFixed(2)} / ¥{a.daily_transfer_limit.toFixed(2)}</small></div>)}</div>
      <div className="card"><h2>创建转账任务</h2><form onSubmit={submitTransfer}>
        <label>付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}（¥{a.balance.toFixed(2)}）</option>)}</select></label>
        <label>收款人<select value={toUserId} onChange={e => setToUserId(e.target.value)}>{recipients.map(r => <option value={r.user_id} key={r.user_id}>{r.name} · {r.bank}</option>)}</select></label>
        <label>金额（元）<input required min="0.01" step="0.01" type="number" value={amount} onChange={e => setAmount(e.target.value)} /></label>
        <label>备注（可选）<input value={note} onChange={e => setNote(e.target.value)} /></label>
        <button disabled={!fromAccount || !toUserId || isActing}>生成确认信息</button>
      </form></div>
    </section>
    <section className="grid">
      <div className="card"><h2>发起收款申请</h2><form onSubmit={submitPaymentRequest}>
        <label>向谁收款（可多选）<details className="multi-select" ref={payerSelectorRef}><summary>{requestPayerIds.length ? `已选择 ${requestPayerIds.length} 人：${recipients.filter(r => requestPayerIds.includes(r.user_id)).map(r => r.name).join('、')}` : '请选择联系人'}</summary><div>{recipients.map(r => <label className="check-option" key={r.user_id}><input type="checkbox" checked={requestPayerIds.includes(r.user_id)} onChange={e => setRequestPayerIds(ids => e.target.checked ? [...ids, r.user_id] : ids.filter(id => id !== r.user_id))} />{r.name} · {r.phone}</label>)}</div></details></label>
        <label>收款账户<select value={requestAccountId} onChange={e => setRequestAccountId(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}</option>)}</select></label>
        <label>{splitEqually ? 'AA 总金额（元）' : '每人收款金额（元）'}<input required min="0.01" step="0.01" type="number" value={requestAmount} onChange={e => setRequestAmount(e.target.value)} /></label>
        <button type="button" className={splitEqually ? '' : 'secondary'} onClick={() => setSplitEqually(value => !value)}>AA 均分：{splitEqually ? '已开启' : '未开启'}</button>
        {requestPayerIds.length > 0 && requestAmountNumber > 0 && <p className={splitEqually && !splitIsExact ? 'error' : 'notice'}>{splitEqually && !splitIsExact ? '该金额无法精确均分到分，请调整金额' : `将向每人收款 ¥${perPersonAmount.toFixed(2)}，共 ${requestPayerIds.length} 人，总申请金额 ¥${requestTotal.toFixed(2)}`}</p>}
        <label>备注（可选）<input maxLength={256} value={requestNote} onChange={e => setRequestNote(e.target.value)} /></label>
        <button disabled={requestPayerIds.length === 0 || !requestAccountId || (splitEqually && !splitIsExact) || isActing}>发出申请</button>
      </form></div>
      <div className="card"><h2>收款申请</h2>{paymentRequests.some(request => ['PENDING', 'ACCEPTED'].includes(request.status) && request.payer_user_id === user.user_id) && <label>付款账户<select value={fromAccount} onChange={e => setFromAccount(e.target.value)}>{accounts.map(a => <option value={a.account_id} key={a.account_id}>{a.card_number}（¥{a.balance.toFixed(2)}）</option>)}</select></label>}{paymentRequests.length === 0 ? <p className="hint">暂无收款申请</p> : <ul className="history">{paymentRequests.map(request => {
        const incoming = request.payer_user_id === user.user_id;
        const canPay = incoming && ['PENDING', 'ACCEPTED'].includes(request.status);
        return <li key={request.payment_request_id}><span>{incoming ? `${request.requester_name} 向你收款` : `向 ${request.payer_name} 收款`} {request.note && `· ${request.note}`}<small>{request.status} · {new Date(request.created_at).toLocaleString()}</small></span><strong>¥{request.amount.toFixed(2)}</strong>{(canPay || request.status === 'PENDING') && <div className="confirm">{canPay ? <><button disabled={isActing} onClick={() => actOnPaymentRequest(request, 'accept')}>{request.status === 'ACCEPTED' ? '确认付款' : '接受申请'}</button>{request.status === 'PENDING' && <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'reject')}>拒绝</button>}</> : <button className="secondary" disabled={isActing} onClick={() => actOnPaymentRequest(request, 'cancel')}>取消</button>}</div>}</li>;
      })}</ul>}<p className="hint">接受申请后，所选付款账户将扣款并立即生成双方流水。</p></div>
    </section>
    {activeTask && <section className="card">
      <h2>转账任务 · {activeTask.phase}</h2><p>{activeTask.message}</p>
      {activeTask.confirmation_card && <p>从 {activeTask.confirmation_card.from_account} 向 {activeTask.confirmation_card.recipient_name} 转账 <strong>¥{activeTask.confirmation_card.amount.toFixed(2)}</strong> · 风险级别 {activeTask.confirmation_card.risk_level}</p>}
      <p className="hint">应用操作：{activeTask.operation.status} · 银行状态：{activeTask.operation.bank_status || '尚未受理'} {activeTask.operation.bank_tx_id && `· 银行流水 ${activeTask.operation.bank_tx_id}`}</p>
      <div className="confirm">
        {activeTask.allowed_actions.includes('cancel') && <button className="secondary" disabled={isActing} onClick={() => actOnTask('cancel')}>取消任务</button>}
        {activeTask.allowed_actions.includes('confirm') && <button disabled={isActing} onClick={() => actOnTask('confirm')}>确认并提交</button>}
        {activeTask.allowed_actions.includes('verify') && <button disabled={isActing} onClick={() => actOnTask('verify')}>身份验证</button>}
        {activeTask.allowed_actions.includes('reconcile') && <button disabled={isActing} onClick={() => actOnTask('reconcile')}>核对银行结果</button>}
      </div>
    </section>}
    <section className="grid">
      <div className="card"><h2>聊天创建任务</h2><p className="hint">可输入：转给 李四 100元。解析后进入同一持久化任务流程。</p><div className="chat">{chat.map((item, index) => <p key={`${index}-${item}`}>{item}</p>)}</div><div className="row"><input value={chatInput} onChange={e => setChatInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && sendChat()} placeholder="转给 李四 100元" /><button onClick={sendChat}>发送</button></div></div>
      <div className="card"><h2>最近流水</h2>{transactions.length === 0 ? <p className="hint">暂无流水</p> : <ul className="history">{transactions.slice(0, 8).map(tx => <li key={tx.tx_id}><span>{tx.type === 'transfer_out' ? '转出' : tx.type === 'transfer_in' ? '转入' : tx.type} {tx.note && `· ${tx.note}`}<small>{new Date(tx.timestamp).toLocaleString()}</small></span><strong className={tx.type === 'transfer_out' ? 'out' : 'in'}>{tx.type === 'transfer_out' ? '-' : '+'}¥{tx.amount.toFixed(2)}</strong></li>)}</ul>}</div>
    </section>
  </main>;
}

export default App;
