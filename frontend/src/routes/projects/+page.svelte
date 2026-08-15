<script lang="ts">
  import { base } from '$app/paths';
  import { onMount } from 'svelte';
  import { get, post, setPlatformContext } from '$lib/api';

  let projects: any[] = $state([]); let show = $state(false); let error = $state(''); let busy = $state(false);
  let form: any = $state({ project_name: '', project_code: '', description: '', country_code: 'NG', plan_code: 'student' });
  async function load() { try { projects = await get('/api/projects'); error = ''; } catch (e: any) { error = e.message; } }
  async function create() { if (busy) return; busy = true; try { const project = await post('/api/projects', form); setPlatformContext(project.project_uid); window.location.href = `${base}/workspace`; } catch (e: any) { error = e.message; busy = false; } }
  function open(project: any) { setPlatformContext(project.project_uid); window.location.href = `${base}/workspace`; }
  onMount(load);
</script>

<svelte:head><title>Project spaces · Dietary Recall</title></svelte:head>
<div class="platform-page-head"><div><span class="kicker">Research portfolio</span><h1>Your project spaces</h1><p>Open a study workspace or create a separate governed record for a new research group.</p></div><button class="btn" onclick={() => (show = true)}>＋ Create project</button></div>
{#if error}<div class="error platform-error" role="alert">{error}</div>{/if}
<section class="project-space-grid">
  {#each projects as project}
    <button class="project-space-card" onclick={() => open(project)}><div class="project-card-top"><span class="project-code">{project.project_code}</span><span class="status">{project.project_role}</span></div><div class="project-glyph" aria-hidden="true"><span></span><i></i><b></b></div><h2>{project.project_name}</h2><p>{project.description || 'Research workspace'}</p><div class="project-card-meta"><span>{project.country_code} · {project.research_domain}</span><b>{project.plan_code}</b></div><div class="project-open">Open scientific workspace <span>→</span></div></button>
  {:else}
    <div class="project-empty"><span>PS</span><h2>No project spaces are available</h2><p>Create a governed workspace to begin structuring foods, participants and research evidence.</p><button class="btn" onclick={() => (show = true)}>Create the first project</button></div>
  {/each}
  <button class="new-project-card" onclick={() => (show = true)}><span>＋</span><b>New research project</b><small>Start with an independent record and member list</small></button>
</section>
<section class="portfolio-note"><div><span class="kicker">How spaces work</span><h2>Each study keeps its own scientific context.</h2></div><div><span><b>01</b>Records and contributor roles stay project-scoped.</span><span><b>02</b>Import and calculation allowances are shared by the group.</span><span><b>03</b>Audit, provenance and review decisions remain attributable.</span></div></section>

{#if show}
  <div class="drawer-backdrop" role="presentation"><section class="drawer" aria-labelledby="create-project-title"><div class="drawer-head"><div><span class="kicker">New governed workspace</span><h2 id="create-project-title">Create project space</h2></div><button class="close" aria-label="Close project form" onclick={() => (show = false)}>×</button></div><div class="form-grid">
    <div class="field span-2"><label for="project-name">Project name</label><input id="project-name" bind:value={form.project_name} placeholder="Nigerian prepared foods study" required /></div>
    <div class="field"><label for="project-code">Project code</label><input id="project-code" bind:value={form.project_code} placeholder="NPF-2026" required /></div>
    <div class="field"><label for="project-country">Country</label><input id="project-country" bind:value={form.country_code} /></div>
    <div class="field span-2"><label for="project-description">Description</label><textarea id="project-description" bind:value={form.description}></textarea></div>
    <div class="field span-2"><label for="project-plan">Research level</label><select id="project-plan" bind:value={form.plan_code}><option value="student">Student · 50 imports / 50 calculations</option><option value="independent">Independent · 100 / 100</option><option value="paid">Research group · 1,000 / 1,000 default</option></select></div>
  </div><div class="notice project-form-notice">Quotas apply to the project, not to individual contributors. Contracted group overrides remain above 100.</div><div class="panel-actions"><button class="btn secondary" onclick={() => (show = false)}>Cancel</button><button class="btn" disabled={busy || !form.project_name || !form.project_code} onclick={create}>{busy ? 'Creating…' : 'Create and open'}</button></div></section></div>
{/if}
