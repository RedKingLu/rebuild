import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// R4 最小前后端联调（C-B）：dev server 将 /api 反向代理到后端，
// 前端用同源相对路径 `/api` 调用，免 CORS，且不绑定具体端口。
// 后端地址可用 VITE_BACKEND_TARGET 覆盖（默认 http://localhost:8000）。
const backendTarget = process.env.VITE_BACKEND_TARGET || 'http://localhost:8765'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
})
