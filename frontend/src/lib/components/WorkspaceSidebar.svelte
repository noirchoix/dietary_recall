<script lang="ts">
  import { base } from '$app/paths';
  import { page } from '$app/state';
  import BrandLockup from './BrandLockup.svelte';

  type ProjectSummary = { project_uid: string; project_name: string };
  type Context = { membership?: { project_role?: string }; usage?: { plan_name?: string } };
  type NavItem = { path: string; label: string; code: string };
  type NavGroup = { label: string; items: NavItem[] };

  let { collapsed = false, mobileOpen = false, projects = [], currentProject, context = null, actor = 'Researcher', onCollapse, onMobileClose, onProjectChange, onSignOut } = $props<{
    collapsed?: boolean; mobileOpen?: boolean; projects?: ProjectSummary[]; currentProject: string; context?: Context | null; actor?: string;
    onCollapse: () => void; onMobileClose: () => void; onProjectChange: (value: string) => void; onSignOut: () => void;
  }>();

  const groups: NavGroup[] = [
    { label: 'Data collection', items: [
      { path: '/foods', label: 'Food manager', code: 'FD' }, { path: '/participants', label: 'Participants', code: 'PT' },
      { path: '/experiments', label: 'Experiments', code: 'EX' }, { path: '/recalls', label: 'Recall entry', code: 'RC' },
      { path: '/imports', label: 'Batch import', code: 'IM' }
    ]},
    { label: 'Composition science', items: [
      { path: '/calculator', label: 'Composition calculator', code: 'CA' }, { path: '/ontology', label: 'Nutrient ontology', code: 'ON' }, { path: '/sources', label: 'Sources & provenance', code: 'SP' },
      { path: '/matching', label: 'Food matching', code: 'FM' }, { path: '/recipes', label: 'Recipes & retention', code: 'RR' }
    ]},
    { label: 'Governance', items: [
      { path: '/contributors', label: 'Contributors', code: 'CO' }, { path: '/datasets', label: 'Licensed datasets', code: 'DS' },
      { path: '/review', label: 'Scientific review', code: 'SR' }, { path: '/audit', label: 'Audit trail', code: 'AU' }
    ]},
    { label: 'Analysis', items: [{ path: '/inference', label: 'Research analytics', code: 'AN' }] }
  ];

  function active(path: string) { return page.url.pathname === `${base}${path}`; }
  function groupOpen(group: NavGroup) { return group.items.some((item) => active(item.path)); }
</script>

<aside class:collapsed class:mobile-open={mobileOpen} class="workspace-sidebar" aria-label="Project workspace navigation">
  <div class="workspace-brand-row"><BrandLockup compact={collapsed} inverse={true} /><button class="collapse-button" aria-label={collapsed ? 'Expand project navigation' : 'Collapse project navigation'} onclick={onCollapse}>{collapsed ? '›' : '‹'}</button></div>
  <a class:active={active('/workspace')} class="workspace-overview-link" href={`${base}/workspace`} onclick={onMobileClose}><span>OV</span><b>Project overview</b></a>
  {#if !collapsed}
    <div class="workspace-project-select">
      <label for="workspace-project">Active project</label>
      <select id="workspace-project" value={currentProject} onchange={(event) => onProjectChange(event.currentTarget.value)}>
        {#if projects.length === 0}<option value={currentProject}>Nigerian prepared foods</option>{/if}
        {#each projects as project}<option value={project.project_uid}>{project.project_name}</option>{/each}
      </select>
      <small>{context?.membership?.project_role || 'member'} · {context?.usage?.plan_name || 'research plan'}</small>
    </div>
  {/if}
  <nav>
    {#each groups as group}
      <details open={groupOpen(group) || !collapsed}>
        <summary><span>{group.label}</span><i aria-hidden="true">⌄</i></summary>
        <div>{#each group.items as item}<a class:active={active(item.path)} href={`${base}${item.path}`} title={collapsed ? item.label : undefined} onclick={onMobileClose}><span>{item.code}</span><b>{item.label}</b></a>{/each}</div>
      </details>
    {/each}
  </nav>
  <div class="workspace-sidebar-footer">
    <a href={`${base}/projects`} onclick={onMobileClose}><span>PS</span><b>Project spaces</b></a>
    <button onclick={onSignOut}><span>↗</span><b>Sign out</b></button>
    {#if !collapsed}<small>{actor}</small>{/if}
  </div>
</aside>
{#if mobileOpen}<button class="workspace-scrim" aria-label="Close project navigation" onclick={onMobileClose}></button>{/if}
