<script lang="ts">
  import { base } from '$app/paths';
  import { page } from '$app/state';
  import BrandLockup from './BrandLockup.svelte';
  let { actor = 'Researcher', onSignOut } = $props<{ actor?: string; onSignOut: () => void }>();
</script>

<header class="platform-header">
  <BrandLockup compact={true} />
  <nav aria-label="Platform navigation">
    <a class:active={page.url.pathname === `${base}/projects`} href={`${base}/projects`}>Project spaces</a>
    <a class:active={page.url.pathname === `${base}/usage`} href={`${base}/usage`}>Usage & plan</a>
    <a href={`${base}/pricing`}>Plans</a>
  </nav>
  <div class="platform-account"><span class="avatar">{actor.slice(0, 2).toUpperCase()}</span><span class="platform-account-copy"><b>{actor}</b><small>Research account</small></span><button class="text-button" onclick={onSignOut}>Sign out</button></div>
</header>
