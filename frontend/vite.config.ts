import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => ({
  base: (loadEnv(mode, '.', 'WORDS_').WORDS_BASE_PATH || '').replace(/\/$/, '') + '/',
  plugins: [vue()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
}))
