import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API is same-origin in production. Proxying in development keeps the
// session cookie first-party, so cookie behaviour matches production.
// Running under compose, the API is a sibling container rather than localhost,
// so the target is configurable — see docker-compose.yml.
const apiTarget = process.env.CYLIST_API_PROXY ?? 'http://localhost:8000'

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // A relative base for the build, so one bundle serves the site root and any
  // dev slot (`/dev_1/`) alike: the bundle's imports resolve against the file
  // they are in, and the backend writes the base path into index.html's own
  // asset URLs when it serves it (`backend/app/spa.py`). The dev server keeps
  // the root, where `./` would mean nothing.
  base: command === 'build' ? './' : '/',
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
      // The MCP tools, served beside the API rather than under it. Proxied so
      // the line the Agents page builds from this origin works in development.
      '/mcp': { target: apiTarget, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    // Off unless asked for. The maps are three and a half times the code they
    // describe and are served to everyone beside it; whoever needs to read a
    // production stack trace builds with `CYLIST_SOURCEMAP=1` and keeps the
    // maps on their own machine.
    sourcemap: process.env.CYLIST_SOURCEMAP === '1',
    rollupOptions: {
      output: {
        // The libraries change when a dependency does, the app every deploy, so
        // a returning visitor fetches the app again and keeps the libraries.
        manualChunks: {
          react: ['react', 'react-dom'],
          tanstack: ['@tanstack/react-query', '@tanstack/react-router'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
}))
