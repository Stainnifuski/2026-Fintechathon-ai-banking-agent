/** Server UTC plus monotonic elapsed time; the device wall clock is not a time source. */
export interface TimeAnchor { utc: number; mono: number }
export function calibrateTime(serverUtc: string, sent: number, received: number): TimeAnchor {
  const utc=Date.parse(serverUtc);
  if(!Number.isFinite(utc) || !Number.isFinite(sent) || !Number.isFinite(received) || received<sent)
    throw new Error('invalid server calibration');
  return {utc:utc+(received-sent)/2,mono:received};
}
export function serverInstant(anchor: TimeAnchor, mono: number): number {
  return anchor.utc+Math.max(0,mono-anchor.mono);
}
