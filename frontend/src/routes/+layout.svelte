<script lang="ts">
  import '../styles.css';
  import '../platform.css';
  import '../auth.css';
  import '../experience.css';
  import { page } from '$app/state';
  import { base } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { authStatus, get, logout, restorePlatformContext, setPlatformContext } from '$lib/api';
  import PublicHeader from '$lib/components/PublicHeader.svelte';
  import PublicFooter from '$lib/components/PublicFooter.svelte';
  import PlatformHeader from '$lib/components/PlatformHeader.svelte';
  import WorkspaceSidebar from '$lib/components/WorkspaceSidebar.svelte';

  type AuthState = { authenticated: boolean; actor?: string; deployment_mode?: string };
  type ProjectSummary = { project_uid: string; project_name: string };
  type RouteKind = 'public' | 'platform' | 'workspace';

  let { children } = $props();
  let auth: AuthState | null = $state(null);
  let deploymentMode = $state('standard');
  let authReady = $state(false);
  let projects: ProjectSummary[] = $state([]);
  let currentProject = $state('proj_legacy_phd_research');
  let context: any = $state(null);
  let sidebarCollapsed = $state(false);
  let mobileOpen = $state(false);

  const pathWithoutBase = $derived(page.url.pathname.slice(base.length) || '/');
  const routeKind = $derived(routeType(pathWithoutBase));
  const isLogin = $derived(pathWithoutBase === '/login');
  const isDemo = $derived(deploymentMode === 'ephemeral_synthetic_demo');

  function routeType(path: string): RouteKind {
    if (['/', '/features', '/pricing', '/login'].includes(path)) return 'public';
    if (['/projects', '/usage'].includes(path)) return 'platform';
    return 'workspace';
  }

  async function loadProjects() {
    const saved = restorePlatformContext();
    currentProject = saved.projectUid;
    try {
      projects = await get<ProjectSummary[]>('/api/projects');
      if (!projects.some((item) => item.project_uid === currentProject) && projects.length) {
        currentProject = projects[0].project_uid;
        setPlatformContext(currentProject);
      }
      context = await get('/api/project');
    } catch {
      projects = [];
      context = null;
    }
  }

  onMount(async () => {
    sidebarCollapsed = localStorage.getItem('dietary-recall-sidebar') === 'collapsed';
    try { auth = (await authStatus()) as AuthState; } catch { auth = { authenticated: false }; }
    deploymentMode = auth.deployment_mode || 'standard';
    authReady = true;
    if (auth.authenticated) {
      await loadProjects();
      if (pathWithoutBase === '/login') await goto(`${base}/projects`, { replaceState: true });
    } else if (routeKind !== 'public') {
      await goto(`${base}/login`, { replaceState: true });
    }
  });

  $effect(() => {
    if (authReady && !auth?.authenticated && routeKind !== 'public') {
      void goto(`${base}/login`, { replaceState: true });
    }
  });

  function toggleSidebar() {
    sidebarCollapsed = !sidebarCollapsed;
    localStorage.setItem('dietary-recall-sidebar', sidebarCollapsed ? 'collapsed' : 'expanded');
  }

  async function signOut() {
    authReady = false;
    try { await logout(); } finally {
      auth = { authenticated: false };
      authReady = true;
      await goto(`${base}/login`, { replaceState: true });
    }
  }

  function switchProject(value: string) {
    currentProject = value;
    setPlatformContext(value);
    window.location.reload();
  }
</script>

<svelte:head><title>Dietary Recall Research Platform</title></svelte:head>
<a class="skip-link" href="#main-content">Skip to main content</a>

{#if routeKind === 'public'}
  {#if isLogin}
    {@render children()}
  {:else}
    <div class="website-shell">
      <PublicHeader authenticated={auth?.authenticated || false} />
      <main id="main-content">{@render children()}</main>
      <PublicFooter />
    </div>
  {/if}
{:else if !authReady || !auth?.authenticated}
  <div class="auth-gate" role="status" aria-live="polite" aria-label="Checking your session">
    <div class="auth-gate-mark">DR</div>
    <div class="auth-gate-copy"><strong>Dietary Recall</strong><span>Checking your secure session…</span></div>
    <span class="auth-gate-spinner" aria-hidden="true"></span>
  </div>
{:else if routeKind === 'platform'}
  <div class="platform-shell">
    <PlatformHeader actor={auth.actor} onSignOut={signOut} />
    {#if isDemo}<div class="demo-banner"><b>Synthetic demo</b><span>Changes are temporary and reset when the free service restarts.</span></div>{/if}
    <main id="main-content" class="platform-main">{@render children()}</main>
  </div>
{:else}
  <div class:rail-collapsed={sidebarCollapsed} class="workspace-shell">
    <WorkspaceSidebar
      collapsed={sidebarCollapsed}
      {mobileOpen}
      {projects}
      {currentProject}
      {context}
      actor={auth.actor}
      onCollapse={toggleSidebar}
      onMobileClose={() => (mobileOpen = false)}
      onProjectChange={switchProject}
      onSignOut={signOut}
    />
    <section class="workspace-stage">
      <header class="workspace-topbar">
        <button class="workspace-mobile-menu" aria-label="Open project navigation" onclick={() => (mobileOpen = true)}>☰</button>
        <div><span>Project workspace</span><b>{context?.project_name || projects.find((item) => item.project_uid === currentProject)?.project_name || 'Nigerian prepared foods'}</b></div>
        <div class="workspace-topbar-meta">{#if isDemo}<span class="demo-chip">Synthetic demo</span>{/if}<span class="evidence-chip"><i></i>Evidence boundary active</span><span class="avatar">{(auth.actor || 'DR').slice(0, 2).toUpperCase()}</span></div>
      </header>
      {#if isDemo}<div class="demo-banner workspace-demo"><b>Disposable workspace</b><span>Use every workflow freely; records reset on service restart.</span></div>{/if}
      <main id="main-content" class="workspace-main">{@render children()}</main>
    </section>
  </div>
{/if}
