import { FormEvent, useEffect, useRef, useState } from 'react';
import './LoginPage.css';

type Props = {
  mode: 'login' | 'register'; phone: string; password: string; name: string;
  busy: boolean; status: string;
  onPhone: (value: string) => void; onPassword: (value: string) => void;
  onName: (value: string) => void; onMode: () => void;
  onSubmit: (event: FormEvent) => void;
};
type FieldName = 'name' | 'phone' | 'password';
const modules = [
  ['↗', '转账与收款'], ['✦', '理财'], ['▥', '账单洞察'], ['◈', '财务概览'],
  ['▣', '卡片'], ['↻', '订阅'], ['✿', '生活服务'],
];

export default function LoginPage(props: Props) {
  const { mode, phone, password, name, busy, status } = props;
  const [visiblePassword, setVisiblePassword] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [typing, setTyping] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<FieldName, string>>>({});
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const form = useRef<HTMLFormElement>(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const state = focused ? typing ? 'typing' : 'focus' : hovered ? 'hover' : 'idle';

  function inputChanged(field: FieldName, value: string) {
    ({ name: props.onName, phone: props.onPhone, password: props.onPassword })[field](value);
    setErrors(previous => ({ ...previous, [field]: undefined }));
    clearTimeout(timer.current); setTyping(true);
    timer.current = setTimeout(() => setTyping(false), 360);
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const next: Partial<Record<FieldName, string>> = {};
    if (mode === 'register' && !name.trim()) next.name = '请输入姓名';
    if (!phone.trim()) next.phone = '请输入手机号';
    else if (!/^\+?\d{6,20}$/.test(phone.trim())) next.phone = '请输入有效手机号';
    if (!password) next.password = '请输入密码';
    else if (password.length < 8) next.password = '密码至少需要 8 位';
    setErrors(next);
    const first = (['name', 'phone', 'password'] as const).find(field => next[field]);
    if (first) { form.current?.querySelector<HTMLInputElement>(`#login-${first}`)?.focus(); return; }
    props.onSubmit(event);
  }
  function field(fieldName: FieldName, label: string, placeholder: string) {
    const isPassword = fieldName === 'password';
    return <div className="login-field" key={fieldName}>
      <label htmlFor={`login-${fieldName}`}>{label}</label>
      <div className={`login-input-wrap${isPassword ? ' login-password' : ''}`}>
        <input id={`login-${fieldName}`} name={fieldName} required disabled={busy}
          type={isPassword ? visiblePassword ? 'text' : 'password' : fieldName === 'phone' ? 'tel' : 'text'}
          inputMode={fieldName === 'phone' ? 'tel' : undefined}
          autoComplete={isPassword ? mode === 'register' ? 'new-password' : 'current-password' : fieldName === 'phone' ? 'username' : 'name'}
          value={{ name, phone, password }[fieldName]} placeholder={placeholder}
          minLength={isPassword ? 8 : undefined}
          aria-invalid={!!errors[fieldName]} aria-describedby={errors[fieldName] ? `login-${fieldName}-error` : undefined}
          onChange={event => inputChanged(fieldName, event.target.value)}
          onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)}
          onFocus={() => setFocused(true)} onBlur={event => {
            if (event.relatedTarget instanceof HTMLInputElement && form.current?.contains(event.relatedTarget)) return;
            setFocused(false); setTyping(false); clearTimeout(timer.current);
          }} />
        {isPassword && <button type="button" className="login-show" disabled={busy}
          aria-label={visiblePassword ? '隐藏密码' : '显示密码'} aria-controls="login-password"
          onClick={() => setVisiblePassword(value => !value)}>{visiblePassword ? '隐藏' : '显示'}</button>}
      </div>
      {errors[fieldName] && <p className="login-field-error" id={`login-${fieldName}-error`}>{errors[fieldName]}</p>}
    </div>;
  }
  return <div className="login-page">
    <header className="login-header"><div className="login-brand"><span className="login-brand-mark">N</span><div>NOVA<small>AI BANKING</small></div></div><div className="login-header-note">模拟银行体验</div></header>
    <main className="login-stage" data-state={state}>
      <div className="login-decorations" aria-hidden="true">{modules.map(([icon, label], index) =>
        <div key={label} className={`login-tag login-t${index + 1}`}><div className="login-pill"><i>{icon}</i>{label}</div></div>)}</div>
      <section className="login-center" aria-labelledby="login-title">
        <p className="login-eyebrow">AI BANKING AGENT</p>
        <h1 id="login-title">NOVA<span>你的银行智能体</span></h1>
        <p className="login-subtitle">查账、转账、管理财务，直接告诉 NOVA。</p>
        <form className="login-form" ref={form} noValidate onSubmit={submit} aria-busy={busy}>
          {mode === 'register' && field('name', '姓名', '请输入姓名')}
          {field('phone', '手机号', '请输入手机号')}
          {field('password', '密码', mode === 'register' ? '设置至少 8 位密码' : '请输入密码')}
          <button className="login-submit" type="submit" disabled={busy}>{busy ? mode === 'login' ? '正在登录…' : '正在注册…' : mode === 'login' ? '登录 NOVA' : '注册并登录'} <span aria-hidden="true">↗</span></button>
          <div className="login-signup">{mode === 'login' ? '第一次来？' : '已有账号？'}<button type="button" disabled={busy} onClick={() => {
            setErrors({}); setVisiblePassword(false); setHovered(false); setFocused(false); setTyping(false); clearTimeout(timer.current); props.onMode();
          }}>{mode === 'login' ? '创建账号' : '返回登录'}</button></div>
          <p className="login-demo-note">比赛模拟环境 · 资金与交易均为虚拟</p>
          <div className="login-feedback" aria-live="polite">{status && <p role="alert">{status}</p>}</div>
          <details className="login-help"><summary>演示账号帮助</summary><p>演示账号须已完成当前数据库初始化；通用种子示例：13800000001 / password123</p><button type="button" disabled={busy} onClick={() => {
            props.onPhone('13800000001'); props.onPassword('password123'); setErrors({});
          }}>填入演示账号</button></details>
        </form>
      </section>
      <footer className="login-footer">NOVA · AI BANKING AGENT</footer>
    </main>
  </div>;
}
