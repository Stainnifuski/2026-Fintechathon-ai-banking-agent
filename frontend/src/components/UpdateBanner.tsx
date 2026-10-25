import { useEffect, useState } from 'react';

const CHECK_INTERVAL_MS = 60_000;
// Builds without the version plugin (other Vite configs) never prompt.
const CURRENT = typeof __APP_BUILD_ID__ === 'string' ? __APP_BUILD_ID__ : '';

async function deployedBuild(): Promise<string | null> {
  try {
    const response = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) return null;
    const body = await response.json() as { build_id?: unknown };
    return typeof body.build_id === 'string' ? body.build_id : null;
  } catch { return null; }
}

/** 前端重新部署后提示刷新；未刷新的页面仍运行旧代码，看不到新功能。 */
export default function UpdateBanner() {
  const [outdated, setOutdated] = useState(false);

  useEffect(() => {
    if (!CURRENT || import.meta.env.DEV) return;
    let live = true;
    const check = async () => {
      const deployed = await deployedBuild();
      if (live && deployed && deployed !== CURRENT) setOutdated(true);
    };
    const onVisible = () => { if (document.visibilityState === 'visible') void check(); };
    const timer = window.setInterval(() => void check(), CHECK_INTERVAL_MS);
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      live = false;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, []);

  if (!outdated) return null;
  return <div className="app-update-banner" role="status">
    <span>NOVA 已更新到新版本，刷新页面后才能使用最新功能。</span>
    <button type="button" onClick={() => window.location.reload()}>刷新页面</button>
  </div>;
}
