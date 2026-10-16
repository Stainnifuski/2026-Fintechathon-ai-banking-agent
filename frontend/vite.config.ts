import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: process.env.BANK_API_BASE_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
        proxyTimeout: 65000,
        timeout: 65000,
      },
    },
  },
});
