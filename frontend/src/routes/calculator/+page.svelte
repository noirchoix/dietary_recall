<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api';

  type Portion = { food_uid: string; grams: number | string };

  let foods: any[] = $state([]);
  let items: Portion[] = $state([{ food_uid: '', grams: 100 }]);
  let result: any = $state(null);
  let error = $state('');
  let busy = $state(false);
  let group = $state('all');

  onMount(async () => {
    try {
      foods = await get('/api/foods?limit=500');
      if (foods.length) items[0].food_uid = foods[0].food_uid;
    } catch (cause: any) {
      error = cause.message;
    }
  });

  function addPortion() {
    items = [...items, { food_uid: foods[0]?.food_uid || '', grams: 100 }];
  }

  function removePortion(index: number) {
    if (items.length === 1) return;
    items = items.filter((_, itemIndex) => itemIndex !== index);
  }

  async function calculate(event: SubmitEvent) {
    event.preventDefault();
    error = '';
    result = null;
    busy = true;
    try {
      result = await post('/api/composition/calculate', {
        items: items.map((item) => ({ food_uid: item.food_uid, grams: Number(item.grams) }))
      });
    } catch (cause: any) {
      error = cause.message;
    } finally {
      busy = false;
    }
  }

  const groups = $derived(
    result ? Array.from(new Set(result.results.map((row: any) => row.component_group))) as string[] : []
  );
  const visibleResults = $derived(
    result?.results?.filter((row: any) => group === 'all' || row.component_group === group) || []
  );
</script>

<svelte:head><title>Composition calculator · Dietary Recall</title></svelte:head>

<div class="page-head calculator-head">
  <div><span class="kicker">Direct portion calculation</span><h1>Food composition calculator</h1><p>Select one or more foods and enter the amount consumed. The calculator scales only the composition values already stored for the selected project.</p></div>
  <span class="status">Preview · not saved</span>
</div>

<div class="calculator-boundary" role="note">
  <b>Formula</b><code>grams × stored per-100 g value ÷ 100</code>
  <span>Missing values are not converted to zero. The synthetic demo values are illustrative, not reference composition or clinical evidence.</span>
</div>

{#if error}<div class="error" role="alert">{error}</div>{/if}

<section class="calculator-layout">
  <form class="card portion-builder" onsubmit={calculate}>
    <div class="split"><div><h2>Food portions</h2><p class="card-sub">Use gram weights on an edible-portion basis.</p></div><button class="btn secondary small" type="button" onclick={addPortion}>＋ Add food</button></div>
    <div class="portion-list">
      {#each items as item, index}
        <div class="portion-row">
          <div class="field"><label for={`calculator-food-${index}`}>Food</label><select id={`calculator-food-${index}`} bind:value={item.food_uid} required><option value="">Select food…</option>{#each foods as food}<option value={food.food_uid}>{food.legacy_food_id} · {food.food_name}</option>{/each}</select></div>
          <div class="field"><label for={`calculator-grams-${index}`}>Amount (g)</label><input id={`calculator-grams-${index}`} type="number" min="0.01" step="any" bind:value={item.grams} required /></div>
          <button class="close" type="button" disabled={items.length === 1} aria-label={`Remove food portion ${index + 1}`} onclick={() => removePortion(index)}>×</button>
        </div>
      {/each}
    </div>
    <div class="panel-actions"><button class="btn" type="submit" disabled={busy || !foods.length}>{busy ? 'Calculating…' : 'Calculate composition'}</button></div>
  </form>

  <aside class="card calculator-explainer">
    <span class="kicker">What this result means</span><h2>Scaling, not nutrient inference</h2>
    <ol><li>The platform reads each selected food’s current stored component rows.</li><li>It scales non-missing values to the gram amount entered.</li><li>It sums like components only when their units match.</li></ol>
    <p>No vitamin, mineral, clinical suitability or adequacy value is invented from the food name.</p>
  </aside>
</section>

{#if result}
  <section class="card calculator-result">
    <div class="split result-heading"><div><span class="kicker">Calculated preview</span><h2>{result.results.length} available components</h2><p class="card-sub">Snapshot <code>{result.snapshot_hash}</code></p></div><div class="field result-filter"><label for="result-group">Component group</label><select id="result-group" bind:value={group}><option value="all">All available groups</option>{#each groups as option}<option value={option}>{option.replaceAll('_', ' ')}</option>{/each}</select></div></div>
    {#each result.warnings as warning}<div class="notice warn">{warning}</div>{/each}
    <div class="table-wrap"><table class="data-table"><thead><tr><th>Component</th><th>Group</th><th>Type</th><th>Calculated amount</th></tr></thead><tbody>{#each visibleResults as row}<tr><td><b>{row.display_name}</b></td><td>{row.component_group.replaceAll('_', ' ')}</td><td><span class="tag">{row.component_kind}</span></td><td>{Number(row.value).toLocaleString(undefined, { maximumFractionDigits: 4 })} {row.unit}</td></tr>{/each}</tbody></table></div>
  </section>

  <section class="calculator-coverage">
    {#each result.items as item}
      <article><span>{item.legacy_food_id}</span><h2>{item.food_name}</h2><p>{item.grams} g · {item.available_component_count} available · {item.missing_component_count} missing</p>{#if item.missing_component_count}<details><summary>View missing components</summary><ul>{#each item.missing_components as missing}<li>{missing.display_name}</li>{/each}</ul></details>{/if}</article>
    {/each}
  </section>
{/if}

<style>
  .calculator-head { align-items: end; }
  .calculator-boundary { display: grid; grid-template-columns: auto auto 1fr; gap: 12px 18px; align-items: center; margin-bottom: 18px; padding: 14px 16px; border: 1px solid #cfe0d7; border-radius: 11px; background: #f2f7f4; color: #46615a; font-size: 11px; }
  .calculator-boundary b { color: #1c4f45; text-transform: uppercase; letter-spacing: .08em; }
  .calculator-boundary code { padding: 5px 8px; border-radius: 6px; background: #deebe4; color: #1a5145; }
  .calculator-layout { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(260px, .65fr); gap: 18px; }
  .portion-list { display: grid; gap: 10px; margin-top: 18px; }
  .portion-row { display: grid; grid-template-columns: minmax(0, 2fr) minmax(120px, .7fr) 34px; gap: 10px; align-items: end; padding: 12px; border: 1px solid #dce6e1; border-radius: 10px; background: #fafcfa; }
  .portion-row .close { margin-bottom: 2px; }
  .calculator-explainer { background: #173d38; color: #eaf4ef; }
  .calculator-explainer .kicker { color: #bdd66f; }
  .calculator-explainer h2 { margin: 10px 0 18px; color: #fff; font-size: 21px; }
  .calculator-explainer ol { display: grid; gap: 14px; padding-left: 20px; color: #bfd0ca; font-size: 11px; line-height: 1.6; }
  .calculator-explainer p { margin-top: 20px; padding-top: 16px; border-top: 1px solid rgba(255,255,255,.12); color: #bdd66f; font-size: 10px; line-height: 1.6; }
  .calculator-result { margin-top: 18px; }
  .result-heading { align-items: end; margin-bottom: 14px; }
  .result-heading code { font-size: 8px; word-break: break-all; }
  .result-filter { min-width: 210px; }
  .calculator-result .notice { margin-bottom: 8px; }
  .calculator-coverage { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 14px; }
  .calculator-coverage article { padding: 16px; border: 1px solid #dbe5e0; border-radius: 11px; background: #fff; }
  .calculator-coverage article > span { color: #43806f; font: 800 9px monospace; }
  .calculator-coverage h2 { margin: 7px 0 5px; font-size: 14px; }
  .calculator-coverage p, .calculator-coverage summary, .calculator-coverage li { color: #6c7d78; font-size: 10px; }
  .calculator-coverage details { margin-top: 11px; }
  .calculator-coverage ul { max-height: 140px; overflow: auto; columns: 2; padding-left: 18px; }
  @media (max-width: 820px) { .calculator-layout { grid-template-columns: 1fr; } .calculator-boundary { grid-template-columns: 1fr; } }
  @media (max-width: 560px) { .portion-row { grid-template-columns: 1fr 1fr; } .portion-row .field:first-child { grid-column: 1 / -1; } .result-heading { align-items: stretch; } .result-filter { min-width: 0; } }
</style>
