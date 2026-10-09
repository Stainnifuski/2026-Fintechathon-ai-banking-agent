interface Props {label:string;total:number;selected:number;page:number;pages:number;pageSize:number;onPage:(page:number)=>void;onSize:(size:number)=>void;onClear:()=>void}
export default function TablePager(p:Props){return <div className="table-pager" aria-label={`${p.label}分页`}>
  <span>共 {p.total} 行 · 已选 {p.selected} 行</span><label>每页<select aria-label={`${p.label}每页行数`} value={p.pageSize} onChange={e=>p.onSize(Number(e.target.value))}>{[5,10,20].map(n=><option key={n} value={n}>{n}行</option>)}</select></label>
  <button className="secondary" disabled={p.page<=1} onClick={()=>p.onPage(p.page-1)}>上一页</button><span>第 {p.page} / {p.pages} 页</span><button className="secondary" disabled={p.page>=p.pages} onClick={()=>p.onPage(p.page+1)}>下一页</button>
  <button className="secondary" disabled={!p.selected} onClick={p.onClear}>清空勾选</button>
</div>;}
