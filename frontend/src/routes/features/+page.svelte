<script lang="ts">
  type CapabilityItem = { label: string; status: 'Available' | 'Data-dependent' | 'Setup required'; note: string };
  type CapabilityArea = { id: string; label: string; code: string; title: string; copy: string; items: CapabilityItem[] };

  let selected = $state('calculation');
  const areas: CapabilityArea[] = [
    { id: 'calculation', label: 'Calculate', code: 'CA', title: 'Calculate the composition of a selected food amount.', copy: 'Choose one or more project foods, enter grams and scale only the non-missing per-100 g component values. This preview does not require a participant and does not create a recall.', items: [
      { label: 'Direct gram-weight calculator', status: 'Available', note: 'Returns totals and the component snapshot used.' },
      { label: 'Missingness report', status: 'Available', note: 'Missing component rows are listed and never changed to zero.' },
      { label: 'Reference-quality Nigerian values', status: 'Setup required', note: 'The public build contains synthetic values, not a licensed food-composition table.' }
    ]},
    { id: 'collection', label: 'Research records', code: 'RD', title: 'A structured operational record for each study.', copy: 'Manage foods, participants, experiments and dietary recalls inside a named project. Single-entry forms and staged imports share validation and audit controls.', items: [
      { label: 'Food, participant and experiment managers', status: 'Available', note: 'Project-scoped CRUD and version history are implemented.' },
      { label: 'CSV and flat-sheet XLSX staging', status: 'Available', note: 'Stage, validate and atomically commit supported entities.' },
      { label: 'Multi-user collaboration', status: 'Setup required', note: 'Roles exist; the public demo has one credentialed account.' }
    ]},
    { id: 'composition', label: 'Evidence', code: 'FC', title: 'Keep composition sources and interpretations separate.', copy: 'Canonical nutrients, explicit units and source releases provide a vocabulary without silently correcting legacy results or filling gaps.', items: [
      { label: 'Canonical nutrient ontology and units', status: 'Available', note: 'Ontology and compatible unit conversion are implemented.' },
      { label: 'Source releases and candidate food matching', status: 'Available', note: 'Candidates require a human decision before use.' },
      { label: 'Licensed Nigerian dataset content', status: 'Setup required', note: 'Only reference metadata is bundled; institutions must supply authorized data.' }
    ]},
    { id: 'recipes', label: 'Recipes', code: 'RE', title: 'Recipe calculations with visible assumptions.', copy: 'Model ingredient weights, edible fractions, final cooked weight and nutrient retention while recording the exact inputs used.', items: [
      { label: 'Yield and ingredient contribution calculation', status: 'Available', note: 'Strict and best-available policies are implemented.' },
      { label: 'Retention-factor provenance', status: 'Data-dependent', note: 'A source release and applicable factors must be registered.' },
      { label: 'Complete cooking transformation model', status: 'Setup required', note: 'Water and fat changes beyond measured final weight are not inferred.' }
    ]},
    { id: 'analytics', label: 'Analytics', code: 'AN', title: 'Descriptive analysis before machine learning.', copy: 'Run cohort summaries and quality-control flags. Models assist review queues; they do not invent chemistry, merge foods or make clinical decisions.', items: [
      { label: 'Recorded-day cohort descriptives', status: 'Data-dependent', note: 'Requires normalized recalls and reviewed nutrient mappings.' },
      { label: 'Median/MAD laboratory flags', status: 'Data-dependent', note: 'Requires sufficient experiment results within a nutrient and unit.' },
      { label: 'Usual-intake or clinical prediction', status: 'Setup required', note: 'Not implemented and not claimed by this build.' }
    ]},
    { id: 'review', label: 'Review', code: 'RV', title: 'Independent scientific decisions require independent identities.', copy: 'The data model supports specialist profiles, review cases and append-only decisions. The public single-account demo cannot complete independent verification by itself.', items: [
      { label: 'Specialist profile and decision contract', status: 'Available', note: 'Backend authorization and immutable decisions are implemented.' },
      { label: 'Independent verification in this demo', status: 'Setup required', note: 'A separate administrator and specialist account are required.' },
      { label: 'Automated credential verification', status: 'Setup required', note: 'Professional registration is not checked automatically.' }
    ]}
  ];
  const current = $derived(areas.find((area) => area.id === selected) || areas[0]);
</script>

<svelte:head><title>Capabilities · Dietary Recall</title></svelte:head>

<section class="public-page-intro"><span class="public-eyebrow">Current build capability map</span><h1>What works now,<br/>and what still needs evidence.</h1><p>Status labels separate callable functionality from workflows that need real data, additional identities or institutional configuration.</p></section>

<section class="capability-explorer truthful-capabilities">
  <div class="capability-tabs" role="tablist" aria-label="Capability areas">{#each areas as area}<button class:active={selected === area.id} role="tab" aria-selected={selected === area.id} onclick={() => (selected = area.id)}><span>{area.code}</span>{area.label}</button>{/each}</div>
  <article class="capability-detail"><div class="capability-code">{current.code}</div><div><span class="public-eyebrow">{current.label}</span><h2>{current.title}</h2><p>{current.copy}</p><div class="capability-status-list">{#each current.items as item}<div><span class:available={item.status === 'Available'} class:dependent={item.status === 'Data-dependent'}>{item.status}</span><p><b>{item.label}</b><small>{item.note}</small></p></div>{/each}</div></div></article>
</section>

<section class="public-section boundary-grid"><article><span>CALCULATION</span><h2>Stored values only.</h2><p>Food amount × per-100 g composition is implemented. No missing vitamin or mineral is inferred.</p></article><article><span>DEMO DATA</span><h2>Synthetic, sparse and disposable.</h2><p>The public database demonstrates behavior; it is not the PhD archive or a Nigerian reference table.</p></article><article><span>CLINICAL BOUNDARY</span><h2>Research output, not advice.</h2><p>The build does not determine glycaemic effect, treatment suitability or personalized dietary recommendations.</p></article></section>

<style>
  .truthful-capabilities .capability-detail { align-items: start; }
  .capability-status-list { display: grid; gap: 10px; margin-top: 22px; }
  .capability-status-list > div { display: grid; grid-template-columns: 108px 1fr; gap: 12px; align-items: start; padding-top: 10px; border-top: 1px solid #dde6e1; }
  .capability-status-list > div > span { padding: 5px 7px; border-radius: 5px; background: #f5e8dd; color: #875a39; font-size: 8px; font-weight: 850; letter-spacing: .06em; text-align: center; text-transform: uppercase; }
  .capability-status-list > div > span.available { background: #e2efe7; color: #286454; }
  .capability-status-list > div > span.dependent { background: #eef0d9; color: #667139; }
  .capability-status-list p, .capability-status-list small { margin: 0; }
  .capability-status-list b, .capability-status-list small { display: block; }
  .capability-status-list b { color: #24423d; font-size: 11px; }
  .capability-status-list small { margin-top: 3px; color: #74837f; font-size: 9px; line-height: 1.5; }
  @media (max-width: 620px) { .capability-status-list > div { grid-template-columns: 1fr; } .capability-status-list > div > span { width: max-content; } }
</style>
