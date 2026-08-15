import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig, loadEnv, type ProxyOptions } from 'vite';

const DEFAULT_API_TARGET = 'http://127.0.0.1:8765';

function apiTarget(value: string | undefined): string {
  const configured = value?.trim() || DEFAULT_API_TARGET;
  const parsed = new URL(configured);
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('DIETARY_RECALL_API_PROXY_TARGET must use http or https');
  }
  if (parsed.pathname !== '/' || parsed.search || parsed.hash) {
    throw new Error('DIETARY_RECALL_API_PROXY_TARGET must be an origin without a path, query, or fragment');
  }
  return parsed.origin;
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'DIETARY_RECALL_');
  const proxy: Record<string, string | ProxyOptions> = {
    '/api': {
      target: apiTarget(env.DIETARY_RECALL_API_PROXY_TARGET),
      changeOrigin: false
    }
  };

  return {
    plugins: [sveltekit()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy
    },
    preview: {
      host: '127.0.0.1',
      port: 4173,
      strictPort: true,
      proxy
    }
  };
});
