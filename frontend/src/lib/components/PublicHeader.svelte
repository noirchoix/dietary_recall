<script lang="ts">
  import { base } from '$app/paths';
  import { page } from '$app/state';
  import BrandLockup from './BrandLockup.svelte';

  let { authenticated = false } = $props<{ authenticated?: boolean }>();
  let menuOpen = $state(false);
  const links = [[`${base}/features`, 'Capabilities'], [`${base}/pricing`, 'Research plans']];
</script>

<header class="public-header">
  <div class="public-nav-shell">
    <BrandLockup />
    <button class="public-menu-button" aria-label="Toggle website navigation" aria-expanded={menuOpen} onclick={() => (menuOpen = !menuOpen)}><span></span><span></span><span></span></button>
    <nav class:open={menuOpen} aria-label="Website navigation">
      {#each links as link}<a class:active={page.url.pathname === link[0]} href={link[0]} onclick={() => (menuOpen = false)}>{link[1]}</a>{/each}
      <a class="public-nav-quiet" href={`${base}/login`} onclick={() => (menuOpen = false)}>{authenticated ? 'Switch account' : 'Sign in'}</a>
      <a class="public-nav-cta" href={`${base}/projects`} onclick={() => (menuOpen = false)}>{authenticated ? 'Open project spaces' : 'Explore the demo'}</a>
    </nav>
  </div>
</header>
