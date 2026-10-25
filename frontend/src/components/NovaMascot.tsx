import { useEffect, useRef } from 'react';
import { createMascot } from './mascot-engine.js';

export type NovaMascotState = 'waiting' | 'typing' | 'thinking';

export default function NovaMascot({ state, started }: { state: NovaMascotState; started: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<ReturnType<typeof createMascot>>();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = createMascot(canvas, { style: 3, seed: 826917 });
    rendererRef.current = renderer;
    return () => {
      renderer.destroy();
      rendererRef.current = undefined;
    };
  }, []);

  useEffect(() => {
    rendererRef.current?.setBusy(state === 'thinking');
  }, [state]);

  return (
    <div className={`nova-mascot nova-mascot-${state}${started ? ' nova-mascot-started' : ''}`} aria-hidden="true">
      <canvas ref={canvasRef} />
    </div>
  );
}
