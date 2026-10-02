import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': process.env.FIGLOOM_API_URL || process.env.PAPER_STUDIO_API_URL || 'http://127.0.0.1:8008',
      '/health': process.env.FIGLOOM_API_URL || process.env.PAPER_STUDIO_API_URL || 'http://127.0.0.1:8008',
    },
  },
  build: { outDir: process.env.FIGLOOM_DIST_DIR || process.env.PAPER_STUDIO_DIST_DIR || 'dist', emptyOutDir: true },
});
