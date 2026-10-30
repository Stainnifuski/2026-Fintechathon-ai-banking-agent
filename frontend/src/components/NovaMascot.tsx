import { useEffect, useRef } from 'react';
import { createMascot } from './mascot-engine.js';

export type NovaMascotState = 'waiting' | 'typing' | 'thinking';

/** 画布固定为这个尺寸，只用 transform 平移和缩放；移动中不改变画布大小，引擎不会重建画布。 */
const BASE = 480;
// Orbit renderer: purple body radius 58, drawing scale BASE / 410.
const PURPLE_RADIUS_RATIO = 58 / 410;
/** 换位置时的移动时长 */
const DURATION = 380;
const ease = (t: number) => 1 - Math.pow(1 - t, 3);

interface Box { x: number; y: number; size: number }

/**
 * NOVA 吉祥物（动画来自 mascot-engine）。
 * 第一个 div 是落点：仍由原有的 .nova-mascot-* 样式决定位置和大小，本身不画任何东西。
 * 第二个 div 是唯一的球：每一帧读取落点，位置一变就从当前位置沿直线平滑移过去，不会闪现；
 * 移动途中落点再变化（例如面板宽度改变）时从当前位置继续，轨迹保持连续。
 */
export default function NovaMascot() {
  const layerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<ReturnType<typeof createMascot>>();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = createMascot(canvas, { style: 4, seed: 826917 });
    rendererRef.current = renderer;
    return () => {
      renderer.destroy();
      rendererRef.current = undefined;
    };
  }, []);

  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const desktop = window.matchMedia('(min-width:1101px)');
    let raf = 0;
    let current: Box | null = null;
    let goal: Box | null = null;
    let expanded: boolean | undefined;
    let tween: { from: Box; start: number } | null = null;
    let visualState = '';
    let authMoving = false;
    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      const anchor = document.querySelector<HTMLDivElement>('.login-orb-anchor, .home-workspace .nova-mascot-anchor'), layer = layerRef.current;
      if (!layer) return;
      if (!anchor) { layer.style.opacity='0'; return; }
      layer.style.opacity='';
      const state=anchor.dataset.state??'waiting';
      if(state!==visualState){rendererRef.current?.setState?.(state as NovaMascotState);layer.dataset.state=state;visualState=state;}
      const moving=document.documentElement.dataset.novaAuth==='moving';
      if(moving&&!authMoving&&current)tween={from:{...current},start:now};
      authMoving=moving;
      const r = anchor.getBoundingClientRect();
      if (!r.width || !r.height) {layer.style.opacity='0';return;}
      const size = Math.min(r.width, r.height);
      const next: Box = { x: r.left + (r.width - size) / 2, y: r.top + (r.height - size) / 2, size };
      const nextExpanded = anchor.closest('.home-workspace')?.classList.contains('nova-expanded') ?? false;
      if (nextExpanded && desktop.matches && anchor.classList.contains('nova-mascot-started')) next.x += size * PURPLE_RADIUS_RATIO;
      if (nextExpanded !== expanded) {
        if (current && !reduced.matches) tween = { from: { ...current }, start: now };
        expanded = nextExpanded;
      }
      if (!goal || Math.abs(next.x - goal.x) > 0.5 || Math.abs(next.y - goal.y) > 0.5 || Math.abs(next.size - goal.size) > 0.5) {
        // Follow the moving panel without restarting the clock on every frame.
        if (current && !reduced.matches && !tween) tween = { from: { ...current }, start: now };
        goal = next;
      }
      if (reduced.matches) tween = null;
      let box = goal;
      if (tween && current) {
        const t = Math.min(1, (now - tween.start) / (moving?500:DURATION));
        const k = ease(t);
        box = {
          x: tween.from.x + (goal.x - tween.from.x) * k,
          y: tween.from.y + (goal.y - tween.from.y) * k,
          size: tween.from.size + (goal.size - tween.from.size) * k,
        };
        if (t >= 1) tween = null;
      }
      current = box;
      // A fixed layer keeps the canvas visible while the panel changes width.
      layer.style.transform = `translate3d(${box.x}px, ${box.y}px, 0) scale(${box.size / BASE})`;
      layer.dataset.ready = 'true';
      if(moving&&!tween){document.documentElement.dataset.novaAuth='revealing';window.dispatchEvent(new Event('nova-auth-arrived'));}
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <div ref={layerRef} className="nova-mascot nova-mascot-layer nova-global-orb" aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>;
}

export function NovaMascotTarget({state,started}:{state:NovaMascotState;started:boolean}){
 const placement=!started&&state==='typing'?'waiting':state;
 return <div data-state={state} className={`nova-mascot nova-mascot-anchor nova-mascot-${placement}${started?' nova-mascot-started':''}`} aria-hidden="true"/>;
}
