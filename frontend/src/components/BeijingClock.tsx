import { useEffect, useRef, useState } from 'react';
import { apiClient } from '../services/api';
import { beijingTime } from '../utils/time';

interface ServerTime { server_time_utc: string; business_timezone: 'Asia/Shanghai' }

export default function BeijingClock() {
  const origin = useRef<{ utc: number; mono: number } | null>(null);
  const [display, setDisplay] = useState('北京时间获取中');
  const [stale, setStale] = useState(false);
  useEffect(() => {
    let alive = true;
    let syncing = false;
    const tick = () => {
      const point = origin.current;
      if (point && alive) setDisplay(`北京时间 ${beijingTime(point.utc + performance.now() - point.mono)}（UTC+8）`);
    };
    const sync = async () => {
      if (syncing) return;
      syncing = true;
      const sent = performance.now();
      try {
        const { data } = await apiClient.get<ServerTime>('/system/time');
        const received = performance.now();
        const utc = Date.parse(data.server_time_utc);
        if (!Number.isFinite(utc) || data.business_timezone !== 'Asia/Shanghai') throw new Error('invalid time');
        if (alive) {
          origin.current = { utc: utc + (received - sent) / 2, mono: received };
          setStale(false); tick();
        }
      } catch { if (alive) setStale(true); }
      finally { syncing = false; }
    };
    const visible = () => { if (document.visibilityState === 'visible') void sync(); };
    const offline = () => { setStale(true); };
    void sync();
    const seconds = window.setInterval(tick, 1000);
    const calibration = window.setInterval(() => { void sync(); }, 60000);
    window.addEventListener('focus', visible);
    window.addEventListener('online', visible);
    window.addEventListener('offline', offline);
    document.addEventListener('visibilitychange', visible);
    return () => {
      alive = false; window.clearInterval(seconds); window.clearInterval(calibration);
      window.removeEventListener('focus', visible); window.removeEventListener('online', visible);
      window.removeEventListener('offline', offline);
      document.removeEventListener('visibilitychange', visible);
    };
  }, []);
  return <div className="beijing-clock" role="timer">{display}{stale && <small> · 待校时</small>}</div>;
}
