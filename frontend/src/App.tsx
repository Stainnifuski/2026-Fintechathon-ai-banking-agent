import { FormEvent, useEffect, useState } from 'react';
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
  const [fromAccount, setFromAccount] = useState('');
  const [toUserId, setToUserId] = useState('');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [status, setStatus] = useState('');
  const [chatInput, setChatInput] = useState('');
  const [chat, setChat] = useState<string[]>([]);
  const [activeTask, setActiveTask] = useState<bank.TaskSnapshot | null>(null);
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isActing, setIsActing] = useState(false);

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
      const [a, r, h] = await Promise.all([bank.accounts(user.user_id), bank.recipients(), bank.history()]);
      setAccounts(a); setRecipients(r); setTransactions(h);
      const defaultFrom = a.find(acc => acc.account_type === 'savings')?.account_id || a[0]?.account_id || '';
      setFromAccount(current => current && a.some(acc => acc.account_id === current) ? current : defaultFrom);
      setToUserId(current => current && r.some(rec => rec.user_id === current) ? current : r[0]?.user_id || '');
    } catch (error) { setStatus(message(error)); }
  };

  useEffect(() => { load(); }, [user?.user_id]); // eslint-disable-line react-hooks/exhaustive-deps
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
    setUser(null); setAccounts([]); setChat([]); setActiveTask(null);
  };

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
