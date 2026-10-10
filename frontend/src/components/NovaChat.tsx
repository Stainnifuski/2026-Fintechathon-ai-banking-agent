import { FormEvent, useEffect, useRef, useState } from 'react';
import { askNova, NovaMessage, NovaReply } from '../services/nova';

type Entry = NovaMessage & { details?: NovaReply };

export default function NovaChat({ name, open, onClose, onNavigate }: {
  name: string; open: boolean; onClose: () => void;
  onNavigate: (section: 'transfer' | 'insights' | 'wealth') => void;
}) {
  const [messages, setMessages] = useState<Entry[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const request = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => () => { request.current?.abort(); }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'nearest' }); }, [messages, busy, open]);

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const history = messages.slice(-12).map(({ role, content }) => ({ role, content }));
    setMessages(previous => [...previous, { role: 'user', content }]);
    setInput(''); setError(''); setBusy(true);
    try {
      const reply = await askNova(content, history, controller.signal);
      if (!controller.signal.aborted) {
        setMessages(previous => [...previous, { role: 'assistant', content: reply.reply, details: reply }]);
      }
    } catch (failure: unknown) {
      if (!controller.signal.aborted) {
        const detail = (failure as { response?: { data?: { detail?: { message?: string; code?: string } } } }).response?.data?.detail;
        setError(`${detail?.message || '查询失败，请重新发送。'}${detail?.code ? `（${detail.code}）` : ''}`);
        setInput(content);
        // 失败请求不进入下一轮上下文。
        setMessages(previous => previous.slice(0, -1));
      }
    } finally {
      if (!controller.signal.aborted) { setBusy(false); request.current = null; }
    }
  }

  function submit(event: FormEvent) { event.preventDefault(); void send(input); }
  if (!open) return null;
  return <div className="agent-scrim" onClick={onClose}>
    <aside className="agent-drawer" aria-label="NOVA 对话" onClick={event => event.stopPropagation()}>
      <div className="agent-head"><span className="agent-orb">✦</span><div><strong>NOVA</strong><small>时间查询助手</small></div><button className="icon-button" aria-label="关闭 NOVA" onClick={onClose}>×</button></div>
      <div className="agent-body">
        <p className="agent-greeting">你好，{name}。可以问我现在的时间。</p>
        <p className="hint">默认查询北京时间；其他时区查询需要启用模型。银行业务可进入对应工作台。</p>
        <button className="agent-prompt" disabled={busy} onClick={() => void send('现在北京时间几点？')}>现在北京时间几点？ <span>→</span></button>
        {(['transfer', 'insights', 'wealth'] as const).map((section, index) => <button key={section} className="agent-prompt" onClick={() => onNavigate(section)}>{['转账与收款', '账单洞察', '理财规划'][index]} <span>→</span></button>)}
        <div className="nova-messages" aria-live="polite">
          {messages.map((entry, index) => <div key={index} className={`nova-message nova-${entry.role}`}>
            <small>{entry.role === 'user' ? '你' : 'NOVA'}</small><p>{entry.content}</p>
            {entry.details && <small>{entry.details.mode === 'offline' ? '离线规则' : '模型回答'}{entry.details.tool_calls.length > 0 && ` · 工具调用 ${entry.details.tool_calls.length} 次`}</small>}
            {entry.details?.tool_calls.map((call, callIndex) => <details key={callIndex}><summary>查询记录{call.result.error ? '（失败）' : ''}</summary><small>{call.result.error || `${call.result.local_time} · ${call.result.timezone}`}</small></details>)}
          </div>)}
          {busy && <p className="hint" role="status">NOVA 正在处理查询…</p>}
          {error && <p className="nova-error" role="alert">{error}</p>}
          <div ref={bottom} />
        </div>
      </div>
      <form className="agent-compose" onSubmit={submit}><input aria-label="向 NOVA 提问" maxLength={2000} value={input} disabled={busy} onChange={event => setInput(event.target.value)} placeholder="现在几点了？"/><button type="submit" aria-label="发送" disabled={busy || !input.trim()}>↑</button></form>
    </aside>
  </div>;
}
