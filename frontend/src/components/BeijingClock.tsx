import { useEffect, useRef, useState } from 'react';
import { apiClient } from '../services/api';
import { beijingTime } from '../utils/time';
import { calibrateTime, serverInstant, type TimeAnchor } from '../utils/serverClock';

interface ServerTime { server_time_utc: string; business_timezone: 'Asia/Shanghai' }

export default function BeijingClock() {
  const origin = useRef<TimeAnchor | null>(null);
  const [display, setDisplay] = useState('北京时间获取中');
  const [stale, setStale] = useState(false);
  useEffect(() => {
    let alive = true;
    let syncing = false;
    const tick = () => {
      const point = origin.current;
      if (point && alive) setDisplay(`北京时间 ${beijingTime(serverInstant(point,performance.now()))}（UTC+8）`);
    };
    const sync = async () => {
      if (syncing) return;
      syncing = true;
      const sent = performance.now();
      try {
        const { data } = await apiClient.get<ServerTime>('/system/time');
        const received = performance.now();
        const anchor=calibrateTime(data.server_time_utc,sent,received);
        if (data.business_timezone !== 'Asia/Shanghai') throw new Error('invalid time');
        if (alive) {
          origin.current = anchor;
          setStale(false); tick();
        }
      } catch { if (alive) setStale(true); }
      finally { syncing = false; }
    };
    const visible = () => { if (document.visibilityState === 'visible') void sync(); };
    void sync();
    const seconds = window.setInterval(tick, 1000);
    const calibration = window.setInterval(() => { void sync(); }, 60000);
    window.addEventListener('focus', visible);
    document.addEventListener('visibilitychange', visible);
    return () => {
      alive = false; window.clearInterval(seconds); window.clearInterval(calibration);
      window.removeEventListener('focus', visible);
      document.removeEventListener('visibilitychange', visible);
    };
  }, []);
  return <div className="beijing-clock" role="timer">{display}{stale && <small> · 待校时</small>}</div>;
}
