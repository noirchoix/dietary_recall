<script lang="ts">
  import { base } from '$app/paths';
  const plans = [
    { name: 'Student', audience: 'Small supervised project', allowance: '50', imports: '50 committed import rows', calculations: '50 saved calculation or inference runs', status: 'Selectable for a new local project' },
    { name: 'Independent', audience: 'Individual researcher or small study', allowance: '100', imports: '100 committed import rows', calculations: '100 saved calculation or inference runs', status: 'Current synthetic demo allowance' },
    { name: 'Research group', audience: 'Institution-managed capacity', allowance: '100+', imports: 'Contracted import allowance above 100', calculations: 'Contracted calculation allowance above 100', status: 'Requires verified billing or operator grant' }
  ];
</script>

<svelte:head><title>Capacity model · Dietary Recall</title></svelte:head>

<section class="public-page-intro pricing-intro"><span class="public-eyebrow">Capacity model · not a live storefront</span><h1>Scale project capacity,<br/>not scientific rules.</h1><p>The application models shared project allowances. The public deployment does not sell subscriptions, and its single demo account does not represent a production billing setup.</p></section>

<div class="pricing-truth" role="note"><b>Public demo</b><span>The synthetic workspace uses the Independent allowance. Research-group capacity cannot be self-provisioned from the project form.</span></div>

<section class="pricing-grid">{#each plans as plan}<article class:featured={plan.name === 'Independent'} class="pricing-card"><span class="plan-name">{plan.name}</span><p>{plan.audience}</p><div class="plan-allowance"><strong>{plan.allowance}</strong><span>imports<br/>and saved runs / month</span></div><ul><li><i>✓</i>{plan.imports}</li><li><i>✓</i>{plan.calculations}</li><li><i>✓</i>Versioning, provenance and audit</li></ul><div class="plan-availability">{plan.status}</div></article>{/each}</section>

<section class="pricing-policy"><div><span class="public-eyebrow">How usage is counted</span><h2>Only completed, metered actions consume allowance.</h2></div><ul><li>File staging and validation failures do not consume import rows.</li><li>Failed strict recipe calculations do not consume saved runs.</li><li>Committed rows and saved calculations are recorded atomically.</li><li>The direct composition preview does not save a result or consume a run.</li></ul></section>

<section class="public-cta compact-pricing-cta"><div><span class="public-eyebrow">Evaluate the workflow</span><h2>Use the synthetic build before considering deployment capacity.</h2><p>Start with the direct portion calculator, then inspect recalls, provenance, recipes and data-dependent analytics.</p></div><a class="public-button light" href={`${base}/projects`}>Open the demonstration <span>→</span></a></section>

<style>
  .pricing-truth { width: min(1180px, calc(100% - 40px)); display: flex; gap: 14px; margin: -28px auto 28px; padding: 13px 16px; border: 1px solid #d7e3dd; border-radius: 10px; background: #f2f7f4; color: #536c65; font-size: 10px; line-height: 1.55; }
  .pricing-truth b { flex: 0 0 auto; color: #276654; text-transform: uppercase; letter-spacing: .08em; }
  .plan-availability { margin-top: 18px; padding: 9px; border-radius: 7px; background: #edf3ef; color: #436158; font-size: 9px; font-weight: 750; line-height: 1.4; }
  .compact-pricing-cta { margin-top: 30px; }
  @media (max-width: 620px) { .pricing-truth { width: min(100% - 26px, 1180px); display: grid; } }
</style>
