import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const api = 'http://127.0.0.1:7878'
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    proxy: {
      '/api': api,
      '/pages': api,
      '/hook': api,
      '/ws': { target: api.replace('http', 'ws'), ws: true },
    },
  },
})
