import adapter from '@sveltejs/adapter-static';

export default {
  kit: {
    adapter: adapter({ fallback: 'index.html' }),
    paths: { base: process.env.SVELTE_BASE || '', relative: true }
  }
};
