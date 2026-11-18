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
    // Playwright drives real browsers from e2e/ and e2e-db/; Vitest must not pick those up.
    exclude: [...configDefaults.exclude, 'e2e/**', 'e2e-db/**'],
  },
})
