<script lang="ts">
  import { base } from '$app/paths';
  import BrandLockup from './BrandLockup.svelte';
</script>

<footer class="public-footer">
  <div class="public-footer-grid">
    <div><BrandLockup inverse={true} /><p>Research software for governed Nigerian prepared-food composition and dietary-recall workflows.</p></div>
    <div><strong>Platform</strong><a href={`${base}/features`}>Capabilities</a><a href={`${base}/pricing`}>Research plans</a><a href={`${base}/login`}>Sign in</a></div>
    <div><strong>Scientific boundary</strong><p>Provenance first. Missing values remain missing. Provisional mappings require review.</p></div>
  </div>
  <div class="public-footer-base"><span>Dietary Recall Research Platform · v0.5.0</span><span>Research use · Not clinical advice</span></div>
</footer>
