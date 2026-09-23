import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The demo SPA. Builds straight into app/static/web, which FastAPI serves —
// one origin for the shell, /ws and /healthz (HAD-10), as in fanmoot's AD-1.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // fileURLToPath, not URL.pathname: pathname percent-encodes, so a project
    // path containing a space ('Gensix Solutions') resolves to
    // '.../Gensix%20Solutions/...' and every aliased import fails.
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: '../app/static/web',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        // The framework in chunks of its own, as in fanmoot: a deploy that
        // changes only the demo leaves these hashes, and a judge's cache, alone.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) {
            return 'vendor-react'
          }
          if (/[\\/]node_modules[\\/](framer-motion|motion-dom|motion-utils)[\\/]/.test(id)) {
            return 'vendor-motion'
          }
          return undefined
        },
      },
    },
  },
  server: {
    // 5174, not Vite's 5173: fanmoot's own PWA dev server takes that one, and
    // both are often running at once. app/settings.py allows this origin on
    // /ws in development.
    port: 5174,
    strictPort: true,
    proxy: {
      '/ws': { target: 'ws://127.0.0.1:8090', ws: true },
      '/healthz': 'http://127.0.0.1:8090',
      '/api': 'http://127.0.0.1:8090',
    },
  },
})
