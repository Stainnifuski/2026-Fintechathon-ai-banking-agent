import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// Every build gets its own id; the running page compares it with /version.json to detect a redeploy.
const buildId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
const versionFile = (): Plugin => ({
  name: 'app-version-file',
  apply: 'build',
  generateBundle() {
    this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify({ build_id: buildId }) });
  },
});

export default defineConfig({
  plugins: [react(), versionFile()],
  define: { __APP_BUILD_ID__: JSON.stringify(buildId) },
  // Worktrees and baseline previews may share node_modules, never optimizer state.
  cacheDir: path.resolve(__dirname, '../.deps/vite-cache'),
  optimizeDeps: {
    // Keep ECharts registration and initialization in one prebundle graph.
    include: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.BANK_API_BASE_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
        proxyTimeout: 65000,
        timeout: 65000,
      },
      '/health': { target: process.env.BANK_API_BASE_URL || 'http://127.0.0.1:8000' },
      '/docs': { target: process.env.BANK_API_BASE_URL || 'http://127.0.0.1:8000' },
      '/openapi.json': { target: process.env.BANK_API_BASE_URL || 'http://127.0.0.1:8000' },
    },
  },
});
