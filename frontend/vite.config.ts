import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The project keeps a single .env at the repository root. Point Vite at it so
// the frontend does not need its own env file; only VITE_* values are exposed
// to the browser.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', '')
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://localhost:8000'

  return {
    plugins: [react()],
    envDir: '..',
    server: {
      port: 3000,
      proxy: {
        // Proxy API calls to the FastAPI backend.
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/health': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
