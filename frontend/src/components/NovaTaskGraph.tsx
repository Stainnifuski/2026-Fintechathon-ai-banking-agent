import { useState } from 'react';
import type { NovaTaskGraph as Graph, NovaTaskGraphNode } from '../services/nova';
import './novaTaskGraph.css';

const KIND: Record<NovaTaskGraphNode['kind'], string> = {
  query: '查询', prepare: '准备任务卡', await_user: '待你确认', answer: '分析回答', clarify: '向你确认',
};
const STATE: Record<Graph['state'], string> = {
  planned: '规划中', completed: '已完成', waiting_user: '等待你确认', failed: '部分步骤失败',
  cancelled: '已取消', partially_cancelled: '部分已取消', incomplete: '部分步骤未完成',
};
const STATUS: Record<NovaTaskGraphNode['status'], string> = {
  planned: '', done: '已完成', waiting_user: '待你确认', cancelled: '已取消', failed: '失败',
  skipped: '已跳过', blocked: '等待前置步骤', not_started: '未开始',
};
const W = 148, H = 64, GAP_X = 14, GAP_Y = 34, PAD = 8;

/** 只读任务图：按依赖层级自上而下排列，节点是子目标而非工具调用。 */
export default function NovaTaskGraph({ graph, streaming }: { graph: Graph; streaming?: boolean }) {
  const [open, setOpen] = useState(Boolean(streaming));
  const [selected, setSelected] = useState<string | null>(null);
  const rows = new Map<number, NovaTaskGraphNode[]>();
  for (const node of graph.nodes) rows.set(node.level, [...(rows.get(node.level) ?? []), node]);
  const widest = Math.max(...[...rows.values()].map(row => row.length));
  const width = PAD * 2 + widest * W + (widest - 1) * GAP_X;
  const height = PAD * 2 + rows.size * H + (rows.size - 1) * GAP_Y;
  const place = new Map<string, { x: number; y: number }>();
  for (const [level, row] of rows) {
    const offset = (width - (row.length * W + (row.length - 1) * GAP_X)) / 2;
    row.forEach((node, index) => place.set(node.id, { x: offset + index * (W + GAP_X), y: PAD + level * (H + GAP_Y) }));
  }
  const detail = graph.nodes.find(node => node.id === selected);
  const markerId = `nova-graph-arrow-${graph.graph_id}-${graph.version}`;

  return <details className="nova-task-graph" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>
      <span>任务规划 · {graph.nodes.length} 步{graph.version > 1 ? ` · 第 ${graph.version} 版` : ''}</span>
      <span className={`nova-task-graph-outcome is-${graph.state}`}>{STATE[graph.state] ?? graph.state}</span>
    </summary>
    {graph.rationale && <p className="nova-task-graph-rationale">{graph.rationale}</p>}
    <div className="nova-task-graph-scroll">
      <div className="nova-task-graph-canvas" style={{ width, height }}>
        <svg width={width} height={height} aria-hidden="true">
          <defs><marker id={markerId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" /></marker></defs>
          {graph.nodes.flatMap(node => node.depends_on.map(dep => {
            const from = place.get(dep), to = place.get(node.id);
            if (!from || !to) return null;
            const x1 = from.x + W / 2, y1 = from.y + H, x2 = to.x + W / 2, y2 = to.y;
            const mid = (y1 + y2) / 2;
            return <path key={`${dep}-${node.id}`} d={`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2 - 2}`} markerEnd={`url(#${markerId})`} />;
          }))}
        </svg>
        {graph.nodes.map(node => {
          const at = place.get(node.id)!;
          return <button key={node.id} type="button" className={`nova-task-graph-node is-${node.kind} status-${node.status}${selected === node.id ? ' is-selected' : ''}`}
            style={{ left: at.x, top: at.y, width: W, height: H }} aria-pressed={selected === node.id}
            onClick={() => setSelected(current => current === node.id ? null : node.id)}>
            <small>{STATUS[node.status] ? `${STATUS[node.status]} · ` : ''}{KIND[node.kind] ?? node.kind}{node.domain_names.length ? ` · ${node.domain_names.join('、')}` : ''}</small>
            <span>{node.goal}</span>
          </button>;
        })}
      </div>
    </div>
    {detail && <dl className="nova-task-graph-detail">
      <dt>子目标</dt><dd>{detail.goal}</dd>
      <dt>完成标准</dt><dd>{detail.done_when}</dd>
      {STATUS[detail.status] && <><dt>当前状态</dt><dd>{STATUS[detail.status]}</dd></>}
      <dt>前置步骤</dt><dd>{detail.depends_on.length
        ? detail.depends_on.map(id => graph.nodes.find(node => node.id === id)?.goal ?? id).join('；') : '无，可直接开始'}</dd>
    </dl>}
  </details>;
}
