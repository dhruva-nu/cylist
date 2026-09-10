import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API is same-origin in production. Proxying in development keeps the
// session cookie first-party, so cookie behaviour matches production.
// Running under compose, the API is a sibling container rather than localhost,
// so the target is configurable — see docker-compose.yml.
const apiTarget = process.env.CYLIST_API_PROXY ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Bind every interface, not just loopback: the dev server is reached over
    // the LAN and the tailnet, and from inside a container it has to be.
    host: true,
    // Vite rejects requests whose Host header it does not recognise. These are
    // the names this machine answers to.
    allowedHosts: ['dnu-home-1', 'dnu-home-1.tail222f46.ts.net'],
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: false,
        // The board holds a WebSocket under /api/v1. Vite only registers an
        // `upgrade` handler for a proxy entry that asks for one, and without
        // it the handshake 404s and the board falls back to polling —
        // silently, and only in development, which is the worst place for a
        // difference from production to hide.
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
