import { useState, type ReactNode, type KeyboardEvent } from 'react';
import type { NovaPageContext } from '../../services/nova';

export const sections = [
  ['overview', '账单总览'], ['transactions', '交易明细'], ['budgets', '预算管理'],
  ['checks', '异常与订阅核对'], ['forecast', '周期与预测'], ['organize', '账单整理'],
] as const;
export type BillSection = typeof sections[number][0];
export type BillSelection = NonNullable<NonNullable<NovaPageContext['bill_workspace']>['selected']>;
export const submenus = {
  checks: [['anomalies','异常核对'],['subscriptions','订阅核对']],
  forecast: [['recurring','周期收支'],['cashflow','现金流预测'],['history','预测复盘']],
  organize: [['merchants','商户管理'],['rules','分类与标签']],
} as const;

export function BillTabs({ items, value, onChange, label, prefix }: {
  items: readonly (readonly [string, string])[]; value: string; onChange: (value: string) => void; label: string; prefix: string;
}) {
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    if (event.key === 'ArrowRight') next = (index+1)%items.length;
    else if (event.key === 'ArrowLeft') next = (index+items.length-1)%items.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = items.length-1;
    else return;
    event.preventDefault(); onChange(items[next][0]);
    document.getElementById(`${prefix}-tab-${items[next][0]}`)?.focus();
  };
  return <div role="tablist" aria-label={label} className={prefix==='bill'?'bill-tabs':'bill-subtabs'}>
    {items.map(([key,title], index) => <button type="button" role="tab" key={key} id={`${prefix}-tab-${key}`}
      aria-selected={value===key} aria-controls={`${prefix}-panel-${key}`} tabIndex={value===key?0:-1}
      onKeyDown={event=>keyDown(event,index)} onClick={()=>onChange(key)}>{title}</button>)}
  </div>;
}

/** Mount on first use, then keep drafts and chart instances across navigation. */
export function BillPanel({ active, id, labelledBy, children }: {active:boolean;id:string;labelledBy:string;children:ReactNode}) {
  const [visited, setVisited] = useState(active);
  if (active && !visited) setVisited(true);
  return <div role="tabpanel" id={id} aria-labelledby={labelledBy} hidden={!active} tabIndex={0}>
    {(active || visited) && children}
  </div>;
}

export function BillDisclosure({ id, title, description, open, onToggle, onFocus, children }: {
  id:string;title:string;description:string;open:boolean;onToggle:(open:boolean)=>void;onFocus:()=>void;children:ReactNode;
}) {
  const [visited,setVisited] = useState(open);
  if(open&&!visited)setVisited(true);
  return <details id={`bill-feature-${id}`} className="bill-feature" data-bill-feature={id} open={open}
    onToggle={event=>{if(event.currentTarget.open!==open)onToggle(event.currentTarget.open);}}
    onFocusCapture={onFocus}>
    <summary><span><strong>{title}</strong><small>{description}</small></span><span className="bill-expand" aria-hidden="true">⌄</span></summary>
    <div className="bill-feature-content">{(open||visited)&&children}</div>
  </details>;
}
