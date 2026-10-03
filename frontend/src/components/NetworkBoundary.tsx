import { ReactNode, useEffect, useRef, useState } from 'react';

/** 恢复网络后显式刷新，不自动重发可能已经成交的请求。 */
export default function NetworkBoundary({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'ready' | 'offline' | 'refresh'>(navigator.onLine ? 'ready' : 'offline');
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const offline = () => setState('offline');
    const online = () => setState(current => current === 'ready' ? current : 'refresh');
    window.addEventListener('offline', offline);
    window.addEventListener('online', online);
    return () => { window.removeEventListener('offline', offline); window.removeEventListener('online', online); };
  }, []);
  useEffect(() => {
    if (content.current) content.current.inert = state !== 'ready';
  }, [state]);
  return <>
    {state !== 'ready' && <div role="alert" style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 3000,
      padding: '16px', background: '#fff4da', borderBottom: '1px solid #d69e36', textAlign: 'center' }}>
      <strong>{state === 'offline' ? '无网络连接' : '网络已恢复，请刷新页面恢复操作'}</strong>
      <p>已显示的记录仅供查看。已提交操作可能已受理，刷新后请先核对原订单，不要重复提交。</p>
      {state === 'refresh' && <button onClick={() => window.location.reload()}>刷新恢复</button>}
    </div>}
    <div ref={content} style={state === 'ready' ? undefined : { paddingTop: 150 }}>{children}</div>
  </>;
}
