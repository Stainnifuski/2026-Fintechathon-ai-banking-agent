export const VOICE_MAX_SECONDS = 45;
export const VOICE_MAX_BYTES = 4 * 1024 * 1024;
export const NOVA_MAX_INPUT = 2000;

/** Never truncate a spoken amount/code or erase text the user already typed. */
export function appendVoiceText(current: string, transcript: string): string | null {
  const words = transcript.trim();
  if (!words) return null;
  const next = current.trim() ? `${current.trimEnd()} ${words}` : words;
  return next.length <= NOVA_MAX_INPUT ? next : null;
}

export function recordingMime(supported: (mime: string) => boolean): string | undefined {
  return ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/webm'].find(supported);
}
