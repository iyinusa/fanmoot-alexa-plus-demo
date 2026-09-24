import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

import { budget } from './budget'

// The demo SPA. Builds straight into app/static/web, which FastAPI serves —
// one origin for the shell, /ws and /healthz (HAD-10), as in fanmoot's AD-1.
export default defineConfig({
  // `budget()` fails the build when the JavaScript outgrows W5-3's budget.
  plugins: [react(), tailwindcss(), budget()],
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
        // React in a chunk of its own, as in fanmoot: a deploy that changes
        // only the demo leaves its hash, and a judge's cache, alone.
        //
        // Framer Motion is deliberately *not* pinned to a chunk (W5-3). The
        // entry uses `m` with `domAnimation`; layout and drag are imported
        // only by the lazy cards and trace chunks. A manual chunk would put
        // every module of the library in one file on the critical path, and
        // undo exactly that split.
        manualChunks(id) {
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'vendor-react'
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
      // The sign-in popup's start, callback and client metadata (W1-7).
      '/oauth': 'http://127.0.0.1:8090',
      // The sandbox proxy the server's ui:// views render inside (W2-6).
      '/sandbox': 'http://127.0.0.1:8090',
    },
  },
})
