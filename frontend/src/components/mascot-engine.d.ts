export function createMascot(canvas: HTMLCanvasElement, options?: {style?: number;seed?: number}): {
  destroy(): void;
  setBusy(value: boolean): void;
  setState?(value: 'waiting'|'typing'|'thinking'): void;
  readonly phase?: number;
};
