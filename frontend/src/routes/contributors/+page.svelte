<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, put } from '$lib/api';

  let rows: any[] = $state([]);
  let show = $state(false);
  let selected: any = $state(null);
  let error = $state('');
  let message = $state('');
  let form: any = $state({ email: '', display_name: '', project_role: 'contributor' });

  async function load() { try { rows = await get('/api/members'); error = ''; } catch (e: any) { error = e.message; } }
  async function save() {
    try {
      if (selected) {
        await put(`/api/members/${selected.membership_uid}`, { project_role: form.project_role, status: 'active' });
        message = 'Contributor role updated';
      } else {
        await post('/api/members', form);
        message = 'Contributor added to this project';
      }
      show = false; selected = null; await load();
    } catch (e: any) { error = e.message; }
  }
  async function suspend(member: any) {
    try { await put(`/api/members/${member.membership_uid}`, { status: 'suspended' }); await load(); } catch (e: any) { error = e.message; }
  }
  function edit(member: any) { selected = member; form = { email: member.email, display_name: member.display_name, project_role: member.project_role }; show = true; }
  onMount(load);
</script>

<div class="page-head">
  <div><span class="kicker">Research group</span><h1>Contributors</h1><p>Global identities join project spaces through owner, admin, contributor, analyst or viewer memberships. Participant records remain separate.</p></div>
  <button class="btn" onclick={() => { selected = null; form = { email: '', display_name: '', project_role: 'contributor' }; show = true; }}>＋ Add contributor</button>
</div>
<div class="notice warn" style="margin-bottom:14px">Adding a membership record does not create login credentials. The public demo exposes one credentialed account; production collaboration requires each contributor to be provisioned through the authentication service.</div>
{#if error}<div class="error" style="margin-bottom:12px">{error}</div>{/if}{#if message}<div class="success" style="margin-bottom:12px">{message}</div>{/if}
<div class="card"><div class="table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Email</th><th>Project role</th><th>Status</th><th>Actions</th></tr></thead><tbody>
  {#each rows as member}<tr><td>{member.display_name}</td><td>{member.email}</td><td><span class="status">{member.project_role}</span></td><td>{member.status}</td><td><button class="btn secondary small" onclick={() => edit(member)}>Edit</button> {#if member.project_role !== 'owner'}<button class="btn secondary small" onclick={() => suspend(member)}>Suspend</button>{/if}</td></tr>{/each}
</tbody></table></div></div>
{#if show}<div class="drawer-backdrop"><section class="drawer">
  <div class="drawer-head"><div><span class="kicker">Project membership</span><h2>{selected ? 'Change role' : 'Add contributor'}</h2></div><button class="close" onclick={() => (show = false)}>×</button></div>
  <div class="form-grid"><div class="field span-2"><label>Email <input type="email" bind:value={form.email} disabled={!!selected} /></label></div><div class="field"><label>Display name <input bind:value={form.display_name} disabled={!!selected} /></label></div><div class="field"><label>Project role <select bind:value={form.project_role}><option value="admin">Admin</option><option value="contributor">Contributor</option><option value="analyst">Analyst</option><option value="viewer">Viewer</option></select></label></div></div>
  <div class="panel-actions"><button class="btn secondary" onclick={() => (show = false)}>Cancel</button><button class="btn" onclick={save}>Save membership</button></div>
</section></div>{/if}
