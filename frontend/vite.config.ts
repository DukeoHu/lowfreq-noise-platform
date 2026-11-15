import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // plotly 体积大，单独成块；路由懒加载时只有图表页才会请求它
        manualChunks(id) {
          if (id.includes('plotly.js-dist-min') || id.includes('react-plotly.js') || id.includes('plotly.js')) {
            return 'plotly'
          }
          if (id.includes('node_modules')) {
            return 'vendor'
          }
          return undefined
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
