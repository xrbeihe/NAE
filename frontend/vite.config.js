import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',
  base: '/',
  server: {
    port: 5173,
    strictPort: true,
    // Proxy API requests to the Python backend during development
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
      },
      '/sessions': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://127.0.0.1:8002',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../backend/ane/static',
    emptyOutDir: true,
  },
});
