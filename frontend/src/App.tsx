import { FormEvent, useEffect, useRef, useState } from 'react';
import * as bank from './services/bank';

const newId = () => crypto.randomUUID?.() ?? `id-${Date.now()}-${Math.random()}`;
const localDateTimeInput = (value: Date) => {
  const localValue = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return localValue.toISOString().slice(0, 16);
};
const suggestedScheduleTime = () => localDateTimeInput(new Date(Date.now() + 2 * 60_000));

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
  const [subscriptions, setSubscriptions] = useState<bank.Subscription[]>([]);
  const [pendingSubscription, setPendingSubscription] = useState<bank.Subscription | null>(null);
  const [pendingChargeSubscription, setPendingChargeSubscription] = useState<bank.Subscription | null>(null);
  const [subscriptionChargeId, setSubscriptionChargeId] = useState('');
  const [fromAccount, setFromAccount] = useState('');
  const [toUserId, setToUserId] = useState('');
  const [recipientMode, setRecipientMode] = useState<'name' | 'phone'>('name');
  const [recipientPhone, setRecipientPhone] = useState('');
  const [phoneCandidates, setPhoneCandidates] = useState<bank.PhoneRecipientCandidate[]>([]);
  const [selectedPhoneAccount, setSelectedPhoneAccount] = useState('');
  const [lookedUpPhone, setLookedUpPhone] = useState('');
  const [clearNote, setClearNote] = useState(false);
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const [editNoteCleared, setEditNoteCleared] = useState(false);
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [transferMode, setTransferMode] = useState<'instant' | 'scheduled'>('instant');
  const [scheduledAt, setScheduledAt] = useState('');
  const [requestPayerIds, setRequestPayerIds] = useState<string[]>([]);
  const [requestAccountId, setRequestAccountId] = useState('');
  const [requestAmount, setRequestAmount] = useState('');
  const [requestNote, setRequestNote] = useState('');
  const [splitEqually, setSplitEqually] = useState(false);
  const [status, setStatus] = useState('');
  const [chatInput, setChatInput] = useState('');
  const [chat, setChat] = useState<string[]>([]);
  const [activeTask, setActiveTask] = useState<bank.TaskSnapshot | null>(null);
  const [conversation, setConversation] = useState<bank.ChatReply | null>(null);
  const [isChatting, setIsChatting] = useState(false);
  const [retryChat, setRetryChat] = useState<bank.ChatRequest | null>(null);

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
      const [a, r, h, requests, subscriptionItems] = await Promise.all([bank.accounts(user.user_id), bank.recipients(), bank.history(), bank.paymentRequests(), bank.listSubscriptions()]);
      setAccounts(a); setRecipients(r); setTransactions(h); setPaymentRequests(requests); setSubscriptions(subscriptionItems);
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
    if (!activeTask || !['WAITING_EXECUTION', 'EXECUTING', 'RECONCILING'].includes(activeTask.phase)) return;
    const timer = window.setInterval(async () => {
      try {
        const task = await bank.getTask(activeTask.task_id);
        setActiveTask(task); setStatus(task.message);
        if (task.phase === 'FINISHED') {
          if (user) localStorage.removeItem(`scheduled-task-${user.user_id}`);
          await load();
        }
      } catch (error) { setStatus(message(error)); }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [activeTask?.task_id, activeTask?.phase]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setConversation(null); setActiveTask(null); setRetryChat(null); setChat([]);
    if (!user) return;
    let cancelled = false;
    const scheduledTaskId = localStorage.getItem(`scheduled-task-${user.user_id}`);
    const activeTaskId = localStorage.getItem(`active-task-${user.user_id}`);
    const saved = localStorage.getItem(`transfer-session-${user.user_id}`);
    if (saved) bank.getConversation(saved).then(reply => {
      if (cancelled) return;
      setConversation(reply); if (!scheduledTaskId) setActiveTask(reply.task || null); setChat([`系统：${reply.message}`]);
    }).catch(error => { if (!cancelled) setStatus(message(error)); });
    const pending = localStorage.getItem(`transfer-pending-${user.user_id}`);
    if (pending) setRetryChat(JSON.parse(pending));
    if (activeTaskId && !saved && !scheduledTaskId) bank.getTask(activeTaskId).then(task => {
      if (!cancelled) setActiveTask(task);
    }).catch(() => localStorage.removeItem(`active-task-${user.user_id}`));
    if (scheduledTaskId) bank.getTask(scheduledTaskId).then(task => {
      if (cancelled) return;
      if (task.phase === 'FINISHED') localStorage.removeItem(`scheduled-task-${user.user_id}`);
      else setActiveTask(task);
    }).catch(() => localStorage.removeItem(`scheduled-task-${user.user_id}`));
    return () => { cancelled = true; };
  }, [user?.user_id]);

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
    const phoneChoice = phoneCandidates.find(item => item.account_id === selectedPhoneAccount);
    if (!fromAccount || (recipientMode === 'name' && !recipient)
      || (recipientMode === 'phone' && (!phoneChoice || lookedUpPhone !== recipientPhone))) {
      return setStatus('请选择付款账户并核验收款人；手机号变更后请重新查询');
    }
    if (transferMode === 'scheduled') {
      if (!scheduledAt) return setStatus('请选择定时执行时间');
      if (new Date(scheduledAt).getTime() <= Date.now()) return setStatus('定时执行时间必须晚于当前时间');
    }
    try {
      const task = await bank.createTransferTask({
        request_id: newId(), operation_id: newId(), from_account: fromAccount,
        to_account: recipientMode === 'phone' ? phoneChoice!.account_id : recipient!.account_id,
        recipient_phone: recipientMode === 'phone' ? recipientPhone : undefined,
        amount: Number(amount), note: clearNote ? null : (note || '转账'),
        note_source: clearNote ? 'cleared' : note ? 'manual' : 'automatic',
        scheduled_at: transferMode === 'scheduled' ? new Date(scheduledAt).toISOString() : undefined,
      });
      if (user && transferMode === 'scheduled') {
        localStorage.setItem(`scheduled-task-${user.user_id}`, task.task_id);
      }
      setActiveTask(task); setStatus(task.message);
      if (user) localStorage.setItem(`active-task-${user.user_id}`, task.task_id);
    } catch (error) { setStatus(message(error)); }
  };

  const lookupPhone = async () => {
    setPhoneCandidates([]); setSelectedPhoneAccount(''); setLookedUpPhone('');
    try {
      const candidates = await bank.lookupPhoneRecipients(recipientPhone);
      setPhoneCandidates(candidates); setLookedUpPhone(recipientPhone);
      setSelectedPhoneAccount(candidates.length === 1 ? candidates[0].account_id : '');
      if (!candidates.length) setStatus('未找到可收款账户，请核对完整手机号');
    } catch (error) { setStatus(message(error)); }
  };

  const saveEditedNote = async () => {
    if (!activeTask || editingNote === null) return;
    setIsActing(true);
    try {
      const updated = await bank.modifyTask(activeTask, {
        operation_id: newId(), from_account: activeTask.operation.from_account,
        to_account: activeTask.operation.to_account, amount: activeTask.operation.amount,
        note: editNoteCleared ? null : editingNote,
        note_source: editNoteCleared ? 'cleared' : 'manual',
      });
      setActiveTask(updated); setEditingNote(null); setStatus('备注已更新，请重新确认新计划');
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
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

  const confirmSubscriptionCancellation = async () => {
    if (!pendingSubscription || isActing) return;
    setIsActing(true); setStatus('');
    try {
      const updated = await bank.cancelSubscription(pendingSubscription);
      setSubscriptions(items => items.map(item => item.subscription_id === updated.subscription_id ? updated : item));
      setPendingSubscription(null);
      setStatus(`已取消 ${updated.merchant_name} 订阅，后续不会再自动扣费`);
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const confirmSubscriptionCharge = async () => {
    if (!pendingChargeSubscription || !subscriptionChargeId || isActing) return;
    setIsActing(true); setStatus('');
    try {
      const result = await bank.simulateSubscriptionCharge(pendingChargeSubscription, subscriptionChargeId);
      setSubscriptions(items => items.map(item => item.subscription_id === result.subscription.subscription_id ? result.subscription : item));
      setPendingChargeSubscription(null); setSubscriptionChargeId(''); setStatus(result.message);
      await load();
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const sendChat = async (candidateId?: string, retry = false) => {
    if (!user || isChatting || (!candidateId && !retry && !chatInput.trim())) return;
    const input = candidateId ? '选择候选' : chatInput.trim();
    const payload: bank.ChatRequest = retry && retryChat ? retryChat : {
      request_id: newId(), operation_id: newId(), message: input,
      session_id: conversation?.session_id, session_version: conversation?.session_version || 0,
      candidate_id: candidateId, task_id: conversation?.task?.task_id,
      state_version: activeTask?.task_id === conversation?.task?.task_id ? activeTask?.state_version : conversation?.task?.state_version,
    };
    setIsChatting(true); setRetryChat(payload);
    localStorage.setItem(`transfer-pending-${user.user_id}`, JSON.stringify(payload));
    try {
      const reply = await bank.createChatTask(payload);
      setConversation(reply); setChatInput(''); setRetryChat(null);
      localStorage.removeItem(`transfer-pending-${user.user_id}`);
      localStorage.setItem(`transfer-session-${user.user_id}`, reply.session_id);
      setChat(items => [...items, `你：${payload.message}`, `系统：${reply.message}`]);
      if (reply.task) setActiveTask(reply.task);
      setStatus(reply.message);
    } catch (error) {
      setStatus(message(error));
      const code = (error as { response?: { status?: number } }).response?.status;
      if (code && code < 500) {
        setRetryChat(null); localStorage.removeItem(`transfer-pending-${user.user_id}`);
        if (conversation) {
          const latest = await bank.getConversation(conversation.session_id).catch(() => null);
          if (latest) { setConversation(latest); if (latest.task) setActiveTask(latest.task); }
        }
      }
    } finally { setIsChatting(false); }
  };

  const newConversation = () => {
    if (!user || retryChat || isChatting) return;
    localStorage.removeItem(`transfer-session-${user.user_id}`);
    setConversation(null); setActiveTask(null); setChat([]); setChatInput('');
  };

  const actOnTask = async (action: 'confirm' | 'cancel' | 'reconcile' | 'verify') => {
    if (!activeTask || isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')) return;
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
        if (user) {
          localStorage.removeItem(`scheduled-task-${user.user_id}`);
          localStorage.removeItem(`active-task-${user.user_id}`);
        }
        setAmount(''); setNote(''); setScheduledAt(''); await load();
      }
    } catch (error) { setStatus(message(error)); }
    finally { setIsActing(false); }
  };

  const logout = () => {
    localStorage.removeItem('token'); localStorage.removeItem('bank-user');
    setUser(null); setAccounts([]); setRecipients([]); setTransactions([]); setPaymentRequests([]); setSubscriptions([]); setPendingSubscription(null); setPendingChargeSubscription(null); setSubscriptionChargeId('');
    setChat([]); setActiveTask(null); setConversation(null); setRetryChat(null); setStatus('');
    setFromAccount(''); setToUserId(''); setRequestPayerIds([]); setRequestAccountId(''); setScheduledAt('');
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
        <label>收款方式<select value={recipientMode} onChange={e => setRecipientMode(e.target.value as 'name' | 'phone')}><option value="name">按姓名选择</option><option value="phone">按手机号查询</option></select></label>
        {recipientMode === 'name'
          ? <label>收款人<select value={toUserId} onChange={e => setToUserId(e.target.value)}>{recipients.map(r => <option value={r.user_id} key={r.user_id}>{r.name} · {r.bank}</option>)}</select></label>
          : <><label>完整手机号<input value={recipientPhone} onChange={e => { setRecipientPhone(e.target.value); setPhoneCandidates([]); setSelectedPhoneAccount(''); setLookedUpPhone(''); }} placeholder="+86 138-0000-0002" /></label>
            <button type="button" className="secondary" onClick={lookupPhone}>查询收款账户</button>
            {phoneCandidates.length > 0 && <label>收款账户<select value={selectedPhoneAccount} onChange={e => setSelectedPhoneAccount(e.target.value)}><option value="">请选择脱敏候选</option>{phoneCandidates.map(candidate => <option value={candidate.account_id} key={candidate.account_id}>{candidate.label}</option>)}</select></label>}</>}
        <label>金额（元）<input required min="0.01" step="0.01" type="number" value={amount} onChange={e => setAmount(e.target.value)} /></label>
        <label>备注（留空自动填“转账”）<input maxLength={256} disabled={clearNote} value={note} onChange={e => setNote(e.target.value)} /></label>
        <label><input type="checkbox" checked={clearNote} onChange={e => setClearNote(e.target.checked)} />不要备注</label>
        <label>执行方式<select value={transferMode} onChange={e => { const mode = e.target.value as 'instant' | 'scheduled'; setTransferMode(mode); if (mode === 'scheduled' && !scheduledAt) setScheduledAt(suggestedScheduleTime()); }}><option value="instant">立即转账</option><option value="scheduled">定时转账</option></select></label>
        {transferMode === 'scheduled' && <><label>执行时间<input required min={localDateTimeInput(new Date())} type="datetime-local" value={scheduledAt} onChange={e => setScheduledAt(e.target.value)} /><small>点击右侧日历图标选择日期和时间，例如：2026/09/26 21:31。系统按本机时间执行。</small></label><button type="button" className="secondary" onClick={() => setScheduledAt(suggestedScheduleTime())}>快速设置为约 2 分钟后</button></>}
        <button disabled={!fromAccount || (recipientMode === 'name' ? !toUserId : !selectedPhoneAccount) || isActing}>生成确认信息</button>
      </form></div>
    </section>
    <section className="card subscription-card">
      <h2>订阅管理</h2>
      {subscriptions.length === 0 ? <p className="hint">暂未识别到订阅扣费</p> : <ul className="history">{subscriptions.map(subscription => {
        const active = subscription.status === 'ACTIVE';
        return <li key={subscription.subscription_id}><span><strong>{subscription.merchant_name}</strong><small>{subscription.debit_account_label} · 每月 ¥{subscription.amount.toFixed(2)}</small><small>{active ? `下次扣费：${new Date(subscription.next_charge_at).toLocaleString()}` : `已于 ${subscription.cancelled_at ? new Date(subscription.cancelled_at).toLocaleString() : '-'} 取消`}</small>{subscription.source_transaction_id && <small>识别依据：历史流水 {subscription.source_transaction_id}</small>}</span><div className="subscription-action"><strong className={active ? 'in' : 'hint'}>{active ? '生效中' : '已取消'}</strong>{active ? <><button disabled={isActing} onClick={() => { setPendingSubscription(null); setPendingChargeSubscription(subscription); setSubscriptionChargeId(newId()); }}>模拟扣费</button><button className="secondary" disabled={isActing} onClick={() => { setPendingChargeSubscription(null); setSubscriptionChargeId(''); setPendingSubscription(subscription); }}>取消订阅</button></> : <small>不会继续扣费</small>}</div></li>;
      })}</ul>}
      {pendingSubscription && <div className="subscription-confirm"><p><strong>确认取消 {pendingSubscription.merchant_name}？</strong></p><p className="hint">确认后将停止后续模拟自动扣费，历史扣费流水不会删除。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => setPendingSubscription(null)}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCancellation}>{isActing ? '正在取消…' : '确认取消'}</button></div></div>}
      {pendingChargeSubscription && <div className="subscription-confirm"><p><strong>模拟扣除 {pendingChargeSubscription.merchant_name} ¥{pendingChargeSubscription.amount.toFixed(2)}？</strong></p><p className="hint">确认后将真实减少演示账户余额、生成订阅流水，并把下次扣费日推进一个月。</p><div className="confirm"><button className="secondary" disabled={isActing} onClick={() => { setPendingChargeSubscription(null); setSubscriptionChargeId(''); }}>返回</button><button disabled={isActing} onClick={confirmSubscriptionCharge}>{isActing ? '正在扣费…' : '确认模拟扣费'}</button></div></div>}
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
      })}</ul>}<p className="hint">接受申请后先核对转账任务，再确认付款；需要强验证的任务会停留在身份验证阶段。</p></div>
    </section>
    {activeTask && <section className="card">
      <h2>转账任务 · {activeTask.phase}</h2><p>{activeTask.message}</p>
      {activeTask.confirmation_card && <p>从 {activeTask.confirmation_card.from_account} 向 {activeTask.confirmation_card.recipient_name}
        {activeTask.confirmation_card.recipient_phone_masked && `（${activeTask.confirmation_card.recipient_phone_masked}）`}
        转入 {activeTask.confirmation_card.to_account} <strong>¥{activeTask.confirmation_card.amount.toFixed(2)}</strong> CNY
        · 备注：{activeTask.confirmation_card.note ?? '无'}
        {activeTask.confirmation_card.note_source === 'automatic' && '（自动生成）'}
        · 风险级别 {activeTask.confirmation_card.risk_level}</p>}
      {activeTask.confirmation_card && activeTask.allowed_actions.includes('modify')
        && conversation?.task?.task_id !== activeTask.task_id && <>
        {editingNote === null
          ? <button className="secondary" onClick={() => { setEditingNote(activeTask.confirmation_card?.note || ''); setEditNoteCleared(activeTask.confirmation_card?.note_source === 'cleared'); }}>修改或清空备注</button>
          : <div className="row"><input maxLength={256} disabled={editNoteCleared} value={editingNote} onChange={e => setEditingNote(e.target.value)} />
            <label><input type="checkbox" checked={editNoteCleared} onChange={e => setEditNoteCleared(e.target.checked)} />清空</label>
            <button disabled={isActing || (!editNoteCleared && !editingNote.trim())} onClick={saveEditedNote}>保存并重新确认</button>
            <button className="secondary" onClick={() => setEditingNote(null)}>返回</button></div>}
      </>}
      {activeTask.scheduled_at && <p className="notice">计划执行时间：{new Date(activeTask.scheduled_at).toLocaleString()}</p>}
      <p className="hint">应用操作：{activeTask.operation.status} · 银行状态：{activeTask.operation.bank_status || '尚未受理'} {activeTask.operation.bank_tx_id && `· 银行流水 ${activeTask.operation.bank_tx_id}`} · 最终备注：{activeTask.operation.note ?? '无'}</p>
      <div className="confirm">
        {activeTask.allowed_actions.includes('cancel') && <button className="secondary" disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('cancel')}>取消任务</button>}
        {activeTask.allowed_actions.includes('confirm') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('confirm')}>{activeTask.scheduled_at ? '确认定时转账' : '确认并提交'}</button>}
        {activeTask.allowed_actions.includes('verify') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('verify')}>身份验证</button>}
        {activeTask.allowed_actions.includes('reconcile') && <button disabled={isActing || isChatting || (conversation?.task?.task_id === activeTask.task_id && conversation.kind === 'clarification')} onClick={() => actOnTask('reconcile')}>核对银行结果</button>}
      </div>
    </section>}
    <section className="grid">
      <div className="card"><h2>即时转账对话</h2><p className="hint">例如：给手机号13800000002转300元，备注房租；改成500元；清空备注。当前模式：{conversation?.mode === 'model' ? '模型理解' : '离线规则（有限表达）'}。</p>
        <button className="secondary" disabled={isChatting || !!retryChat} onClick={newConversation}>新建转账会话</button>
        <div className="chat">{chat.map((item, index) => <p key={index}>{item}</p>)}</div>
        {conversation?.candidates.map(candidate => <button key={candidate.candidate_id} disabled={isChatting || !!retryChat} onClick={() => sendChat(candidate.candidate_id)}>{candidate.label}</button>)}
        <div className="row"><input value={chatInput} disabled={isChatting || !!retryChat} onChange={e => setChatInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && !retryChat && sendChat()} placeholder="给手机号13800000002转300元，备注聚餐" /><button disabled={isChatting || !!retryChat} onClick={() => sendChat()}>发送</button></div>
        {retryChat && !isChatting && <button onClick={() => sendChat(undefined, true)}>重试原消息（避免重复建单）</button>}
      </div>
      <div className="card"><h2>最近流水</h2>{transactions.length === 0 ? <p className="hint">暂无流水</p> : <ul className="history">{transactions.slice(0, 8).map(tx => {
        const outgoing = ['transfer_out', 'purchase', 'subscription'].includes(tx.type);
        const label = tx.type === 'transfer_out' ? '转出' : tx.type === 'transfer_in' ? '转入' : tx.type === 'subscription' ? '订阅扣费' : tx.type === 'purchase' ? '消费' : tx.type;
        const counterparty = recipients.find(recipient => recipient.account_id === tx.counterparty)?.name || tx.counterparty;
        return <li key={tx.tx_id}><span>{label}{counterparty && ` · ${counterparty}`} {tx.note && `· ${tx.note}`}<small>{new Date(tx.timestamp).toLocaleString()}</small></span><strong className={outgoing ? 'out' : 'in'}>{outgoing ? '-' : '+'}¥{tx.amount.toFixed(2)}</strong></li>;
      })}</ul>}</div>
    </section>
  </main>;
}

export default App;
