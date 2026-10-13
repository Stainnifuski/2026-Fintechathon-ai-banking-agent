import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// 启动时固定来源；默认比赛事实，--mode fixture 明确隔离。
export default defineConfig(({ mode }) => {
  const fixture = mode === 'fixture'; const backend = fixture ? 'http://127.0.0.1:8051' : 'http://127.0.0.1:8052';
  return { plugins: [react()], define: { __PORTFOLIO_FIXTURE__: JSON.stringify(fixture) },
    server: { host: '127.0.0.1', port: fixture ? 3021 : 3022, strictPort: true, proxy: {
      ...(!fixture ? { '/api/v1/auth': 'http://127.0.0.1:8010' } : {}), '/api': backend, '/dev': backend,
    } }, build: { outDir: `../.deps/wealth_portfolio_step10/build-${fixture ? 'fixture' : 'bank'}`, emptyOutDir: true, rollupOptions: { input: 'portfolio-dev.html' } } };
});
