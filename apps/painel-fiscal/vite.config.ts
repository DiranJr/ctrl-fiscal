import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  base: '/fiscal/',
  plugins: [react()],
  resolve: {
    alias: {
      '@ctrl-fiscal/ui/tokens.css': path.resolve(__dirname, '../../packages/ui/src/tokens.css'),
      '@ctrl-fiscal/ui': path.resolve(__dirname, '../../packages/ui/src/index.ts'),
      '@ctrl-fiscal/api-client': path.resolve(__dirname, '../../packages/api-client/src/index.ts'),
    },
  },
  server: {
    port: 3001,
    host: true,
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
