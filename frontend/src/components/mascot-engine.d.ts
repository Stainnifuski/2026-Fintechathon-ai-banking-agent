export function createMascot(canvas: HTMLCanvasElement, options?: {style?: number;seed?: number}): {
  destroy(): void;
  setBusy(value: boolean): void;
  readonly phase?: number;
};
