<script lang="ts">
  let { compact = false, inverse = false } = $props<{ compact?: boolean; inverse?: boolean }>();
</script>

<a class:compact class:inverse class="brand-lockup" href="/" aria-label="Dietary Recall home">
  <span class="brand-symbol" aria-hidden="true"><i></i><b>DR</b></span>
  <span class="brand-copy"><strong>Dietary Recall</strong><small>Research Platform</small></span>
</a>
