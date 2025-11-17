import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const API = process.env.ARRANGEMENT_API ?? 'http://127.0.0.1:8848'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5273,
    proxy: {
      '/api': { target: API, changeOrigin: true, ws: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1200 },
})
