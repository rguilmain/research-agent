import react from '@vitejs/plugin-react'
import { configDefaults, defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' },
      '/auth': { target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Playwright drives a real browser from e2e/; Vitest must not pick those files up.
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
