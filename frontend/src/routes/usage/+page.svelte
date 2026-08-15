<script lang="ts">
  import { base } from '$app/paths';
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api';
  let usage: any = $state(null); let billing: any = $state(null); let prices: any[] = $state([]); let email = $state(''); let error = $state('');
  const percent = (used: number, limit: number) => limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  onMount(async () => { try { usage = await get('/api/usage'); billing = await get('/api/billing'); prices = await get('/api/billing/prices'); } catch (e: any) { error = e.message; } });
  async function checkout(price: any) { try { const result = await post('/api/billing/checkout', { price_mapping_uid: price.price_mapping_uid, email, success_url: `${location.origin}/usage?checkout=success`, cancel_url: `${location.origin}/usage?checkout=cancel` }); location.href = result.checkout_url; } catch (e: any) { error = e.message; } }
</script>

<svelte:head><title>Usage & plan · Dietary Recall</title></svelte:head>
<div class="platform-page-head"><div><span class="kicker">Project capacity</span><h1>Usage & plan</h1><p>Review the shared project allowance and its append-only usage ledger. Research methods do not change between plans.</p></div><a class="btn secondary" href={`${base}/pricing`}>Compare research plans</a></div>
{#if error}<div class="error platform-error" role="alert">{error}</div>{/if}
{#if usage}
  <section class="usage-hero"><div><span class="kicker">Current subscription</span><h2>{usage.plan_name}</h2><p>Period {usage.period} · allowance shared by every active project member</p></div><span class="usage-plan-code">{usage.plan_code}</span></section>
  <section class="usage-meter-grid">
    <article><span>Committed import rows</span><div><strong>{usage.import_rows.used}</strong><small>of {usage.import_rows.limit}</small></div><div class="progress"><i style={`width:${percent(usage.import_rows.used, usage.import_rows.limit)}%`}></i></div><p>{usage.import_rows.remaining} rows remain in this period.</p></article>
    <article><span>Calculation & inference runs</span><div><strong>{usage.calculation_runs.used}</strong><small>of {usage.calculation_runs.limit}</small></div><div class="progress"><i style={`width:${percent(usage.calculation_runs.used, usage.calculation_runs.limit)}%`}></i></div><p>{usage.calculation_runs.remaining} runs remain in this period.</p></article>
    <article><span>Project collaboration</span><div><strong>{usage.max_members}</strong><small>member limit</small></div><div class="member-rule"><i></i><i></i><i></i><i></i><i></i></div><p>Roles share the project allowance and retain attributed actions.</p></article>
  </section>
  <section class="usage-policy"><div><span class="kicker">Counting policy</span><h2>Allowance follows committed evidence.</h2></div><div><p><b>Import rows</b> are counted only after a staged batch validates and commits.</p><p><b>Calculation runs</b> are counted only when a reproducible result is saved.</p><p>Validation failures and failed strict recipe calculations consume nothing.</p></div></section>
{/if}
{#if prices.length}<section class="billing-panel"><div><span class="kicker">Managed upgrade</span><h2>Change this project’s capacity</h2><p>Entitlements change only after a signed provider webhook has been verified and processed once.</p>{#if billing?.subscription}<span class="status">{billing.subscription.status}</span>{/if}</div><div><div class="field"><label for="billing-email">Billing email</label><input id="billing-email" type="email" bind:value={email} required /></div><div class="billing-price-list">{#each prices as price}<button disabled={!email} onclick={() => checkout(price)}><span><b>{price.display_name}</b><small>{price.import_rows_limit} imports · {price.calculation_runs_limit} calculations</small></span><i>Continue →</i></button>{/each}</div></div></section>{/if}
