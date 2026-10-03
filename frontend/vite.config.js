import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    allowedHosts: ['park-vis.edgecompute.space'],
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'https://localhost:8000',
        changeOrigin: true,
        secure: false, // Accept self-signed certificates in dev
        ws: true
      }
    }
  }
})
