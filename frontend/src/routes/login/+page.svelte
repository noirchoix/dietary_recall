<script lang="ts">
  import { base } from '$app/paths';
  import { onMount } from 'svelte';
  import { getWithStartupRetry, login } from '$lib/api';

  let email = $state('');
  let password = $state('');
  let error = $state('');
  let busy = $state(false);
  let showPassword = $state(false);
  let demoMode = $state(false);
  let serviceState = $state<'checking' | 'waking' | 'ready' | 'unavailable'>('checking');

  async function loadConfig() {
    serviceState = 'checking';
    try {
      const config = await getWithStartupRetry<any>('/api/config', () => (serviceState = 'waking'));
      demoMode = config.ephemeral_demo === true;
      if (demoMode && config.demo_email) email = config.demo_email;
      serviceState = 'ready';
    } catch {
      serviceState = 'unavailable';
    }
  }

  onMount(loadConfig);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (busy) return;
    error = '';
    busy = true;
    try {
      await login(email.trim(), password);
      window.location.replace(`${base}/projects`);
    } catch (cause: unknown) {
      error = cause instanceof Error ? cause.message : 'Sign-in failed. Please try again.';
      password = '';
      busy = false;
    }
  }
</script>

<svelte:head><title>Sign in · Dietary Recall Research Platform</title></svelte:head>

<main class="login-page">
  <section class="login-shell" aria-labelledby="login-heading">
    <aside class="login-context" aria-label="Platform information">
      <div class="context-brand">
        <div class="context-mark">DR</div>
        <div><strong>Dietary Recall</strong><span>Research Platform</span></div>
      </div>

      <div class="context-copy">
        <span class="context-kicker">Nigerian food research</span>
        <h2>A governed workspace for dietary evidence.</h2>
        <p>Manage prepared foods, participant recalls, laboratory results and composition sources without altering the historical research record.</p>
      </div>

      <div class="context-features">
        <div><span aria-hidden="true">✓</span><p><b>Immutable source evidence</b><small>The original research database remains protected.</small></p></div>
        <div><span aria-hidden="true">✓</span><p><b>Project-scoped collaboration</b><small>Contributors work only within permitted study spaces.</small></p></div>
        <div><span aria-hidden="true">✓</span><p><b>Audited research changes</b><small>Imports, calculations and review decisions retain provenance.</small></p></div>
      </div>

      <div class="context-footer">Research schema v4 <i></i> Validated evidence layer</div>
    </aside>

    <div class="login-panel">
      <div class="mobile-brand">
        <div class="context-mark">DR</div>
        <div><strong>Dietary Recall</strong><span>Research Platform</span></div>
      </div>

      <div class="login-content">
        <span class="login-kicker">Secure workspace access</span>
        <h1 id="login-heading">Welcome back</h1>
        <p class="login-intro">Sign in to continue to your research project.</p>

        {#if serviceState === 'waking'}
          <div class="demo-login-note"><span>WAIT</span><p><b>Starting the demonstration API</b><small>The free service can take about a minute to wake. This page is checking again automatically.</small></p></div>
        {:else if serviceState === 'unavailable'}
          <div class="demo-login-note service-unavailable"><span>API</span><p><b>The research API is not ready</b><small>Retry the connection. If this persists, verify that the static-site <code>/api/*</code> rewrite points to the API service.</small><button type="button" onclick={loadConfig}>Retry connection</button></p></div>
        {:else if demoMode}<div class="demo-login-note"><span>DEMO</span><p><b>Disposable synthetic workspace</b><small>The email is filled in for you. Use the demonstration password shared with you; changes reset when the service restarts.</small></p></div>{/if}

        {#if error}
          <div class="login-error" role="alert">
            <span aria-hidden="true">!</span><p><b>Unable to sign in</b><small>{error}</small></p>
          </div>
        {/if}

        <form onsubmit={submit} aria-describedby="account-help">
          <div class="login-field">
            <label for="login-email">Email address</label>
            <input
              id="login-email"
              type="email"
              bind:value={email}
              autocomplete="username"
              inputmode="email"
              placeholder="name@example.org"
              required
            />
          </div>

          <div class="login-field">
            <div class="field-heading"><label for="login-password">Password</label><span>Minimum 12 characters</span></div>
            <div class="password-control">
              <input
                id="login-password"
                type={showPassword ? 'text' : 'password'}
                bind:value={password}
                autocomplete="current-password"
                minlength="12"
                required
              />
              <button type="button" class="password-toggle" onclick={() => (showPassword = !showPassword)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          <button class="login-button" type="submit" disabled={busy}>
            {#if busy}<span class="button-spinner" aria-hidden="true"></span>{/if}
            {busy ? 'Signing in…' : 'Sign in to platform'}
          </button>
        </form>

        <div class="account-help" id="account-help">
          <span aria-hidden="true">i</span>
          <p><b>Which account should I use?</b><small>Use the email and password created when this installation was set up. If you configured it yourself, this is the email you supplied to <code>auth-set-password</code>.</small></p>
        </div>

        <p class="separation-note">Login credentials are stored separately from participant and dietary-recall records.</p>
      </div>
    </div>
  </section>
</main>

<style>
  .login-page {
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: clamp(18px, 4vw, 48px);
    background:
      radial-gradient(circle at 12% 16%, rgba(184, 212, 106, .22), transparent 28%),
      radial-gradient(circle at 88% 88%, rgba(55, 118, 100, .16), transparent 32%),
      #e8efea;
    color: #153331;
  }

  .login-shell {
    width: min(1020px, 100%);
    min-height: min(620px, calc(100vh - 48px));
    display: grid;
    grid-template-columns: minmax(340px, .9fr) minmax(430px, 1.1fr);
    overflow: hidden;
    border: 1px solid rgba(31, 78, 69, .14);
    border-radius: 26px;
    background: #fff;
    box-shadow: 0 30px 80px rgba(21, 51, 49, .16), 0 3px 12px rgba(21, 51, 49, .06);
  }

  .login-context {
    position: relative;
    display: flex;
    flex-direction: column;
    padding: clamp(34px, 4.5vw, 56px);
    overflow: hidden;
    color: #edf6f1;
    background:
      linear-gradient(150deg, rgba(184, 212, 106, .12), transparent 36%),
      linear-gradient(155deg, #173d38 0%, #0f2928 100%);
  }

  .login-context::after {
    content: '';
    position: absolute;
    right: -110px;
    bottom: -140px;
    width: 330px;
    height: 330px;
    border: 1px solid rgba(184, 212, 106, .14);
    border-radius: 50%;
    box-shadow: 0 0 0 44px rgba(184, 212, 106, .035), 0 0 0 88px rgba(184, 212, 106, .025);
  }

  .context-brand, .mobile-brand {
    position: relative;
    z-index: 1;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .context-mark {
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    border-radius: 13px;
    background: linear-gradient(145deg, #c5df77, #83b978);
    color: #102b29;
    font-size: 13px;
    font-weight: 900;
    letter-spacing: -.03em;
    box-shadow: 0 9px 24px rgba(0, 0, 0, .16);
  }

  .context-brand strong, .mobile-brand strong { display: block; font-size: 14px; line-height: 1.2; }
  .context-brand span, .mobile-brand span { display: block; margin-top: 3px; color: #9eb7af; font-size: 10px; letter-spacing: .04em; }
  .mobile-brand { display: none; }

  .context-copy { position: relative; z-index: 1; margin-top: clamp(52px, 9vh, 86px); }
  .context-kicker, .login-kicker { font-size: 10px; font-weight: 850; letter-spacing: .15em; text-transform: uppercase; }
  .context-kicker { color: #bdd66f; }
  .context-copy h2 { max-width: 420px; margin: 14px 0 18px; font-size: clamp(29px, 3.2vw, 40px); line-height: 1.08; letter-spacing: -.04em; }
  .context-copy > p { max-width: 420px; margin: 0; color: #b9cbc5; font-size: 13px; line-height: 1.75; }

  .context-features { position: relative; z-index: 1; display: grid; gap: 17px; margin-top: 42px; }
  .context-features > div { display: grid; grid-template-columns: 25px 1fr; gap: 11px; align-items: start; }
  .context-features > div > span { width: 23px; height: 23px; display: grid; place-items: center; border: 1px solid rgba(184, 212, 106, .35); border-radius: 50%; background: rgba(184, 212, 106, .1); color: #c6df78; font-size: 11px; font-weight: 900; }
  .context-features p { margin: 0; }
  .context-features b { display: block; font-size: 11px; }
  .context-features small { display: block; margin-top: 3px; color: #91aaa3; font-size: 10px; line-height: 1.45; }
  .context-footer { position: relative; z-index: 1; display: flex; align-items: center; gap: 9px; margin-top: auto; padding-top: 30px; color: #819b94; font-size: 9px; text-transform: uppercase; letter-spacing: .09em; }
  .context-footer i { width: 4px; height: 4px; border-radius: 50%; background: #9ab95e; }

  .login-panel { display: grid; place-items: center; padding: clamp(36px, 6vw, 76px); background: #fff; }
  .login-content { width: min(400px, 100%); }
  .login-kicker { color: #397666; }
  .login-content h1 { margin: 12px 0 8px; font-size: clamp(32px, 4vw, 44px); line-height: 1.08; letter-spacing: -.045em; color: #123230; }
  .login-intro { margin: 0 0 34px; color: #6b7e79; font-size: 13px; line-height: 1.6; }
  .demo-login-note { display: grid; grid-template-columns: 43px 1fr; gap: 11px; margin: -16px 0 24px; padding: 11px; border: 1px solid #dce2b8; border-radius: 9px; background: #f7f7e4; }
  .demo-login-note > span { align-self: start; padding: 4px 5px; border-radius: 4px; background: #64714a; color: #fff; font-size: 7px; font-weight: 900; letter-spacing: .1em; text-align: center; }
  .demo-login-note p { margin: 0; }
  .demo-login-note b, .demo-login-note small { display: block; }
  .demo-login-note b { color: #4d5a37; font-size: 9px; }
  .demo-login-note small { margin-top: 3px; color: #747b5a; font-size: 8px; line-height: 1.5; }
  .demo-login-note button { margin-top: 7px; padding: 0; border: 0; background: transparent; color: #315e53; font-size: 9px; font-weight: 850; text-decoration: underline; }
  .demo-login-note code { font-size: 8px; }
  .service-unavailable { border-color: #edcfca; background: #fff5f3; }
  form { display: grid; gap: 20px; }
  .login-field { display: grid; gap: 8px; }
  .login-field label { color: #2c4843; font-size: 12px; font-weight: 750; }
  .field-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; }
  .field-heading span { color: #8a9995; font-size: 9px; }

  .login-field input {
    width: 100%;
    height: 48px;
    padding: 0 14px;
    border: 1px solid #cbd8d3;
    border-radius: 10px;
    outline: none;
    background: #fbfcfb;
    color: #183835;
    font-size: 13px;
    transition: border-color .16s, box-shadow .16s, background .16s;
  }

  .login-field input::placeholder { color: #a0ada9; }
  .login-field input:hover { border-color: #aebfba; background: #fff; }
  .login-field input:focus { border-color: #3e806e; background: #fff; box-shadow: 0 0 0 4px rgba(62, 128, 110, .12); }
  .password-control { position: relative; }
  .password-control input { padding-right: 68px; }
  .password-toggle { position: absolute; top: 50%; right: 7px; transform: translateY(-50%); min-width: 51px; padding: 8px 9px; border: 0; border-radius: 7px; background: transparent; color: #356e60; font-size: 10px; font-weight: 800; }
  .password-toggle:hover { background: #edf4f0; }
  .password-toggle:focus-visible { outline: 2px solid #4a8b78; outline-offset: 1px; }

  .login-button {
    width: 100%;
    min-height: 48px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 9px;
    margin-top: 3px;
    padding: 0 18px;
    border: 0;
    border-radius: 10px;
    background: linear-gradient(135deg, #1d6253, #164a42);
    color: #fff;
    font-size: 12px;
    font-weight: 850;
    box-shadow: 0 10px 24px rgba(25, 83, 72, .22);
    transition: transform .16s, box-shadow .16s, opacity .16s;
  }
  .login-button:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 13px 28px rgba(25, 83, 72, .28); }
  .login-button:focus-visible { outline: 3px solid rgba(67, 139, 119, .3); outline-offset: 3px; }
  .login-button:disabled { cursor: wait; opacity: .72; }
  .button-spinner { width: 14px; height: 14px; border: 2px solid rgba(255,255,255,.42); border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; }

  .account-help, .login-error { display: grid; grid-template-columns: 24px 1fr; gap: 10px; align-items: start; border-radius: 10px; }
  .account-help { margin-top: 25px; padding: 13px; border: 1px solid #dce7e1; background: #f4f8f5; }
  .account-help > span, .login-error > span { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; font-size: 10px; font-weight: 900; }
  .account-help > span { background: #dfece5; color: #2c6b5b; }
  .account-help p, .login-error p { margin: 0; }
  .account-help b, .login-error b { display: block; color: #38534e; font-size: 10px; }
  .account-help small, .login-error small { display: block; margin-top: 3px; color: #70817d; font-size: 9px; line-height: 1.55; }
  .account-help code { padding: 1px 4px; border-radius: 4px; background: #e4ede8; color: #315e53; font-size: 9px; }
  .login-error { margin: -13px 0 21px; padding: 12px; border: 1px solid #edcfca; background: #fff5f3; }
  .login-error > span { background: #f2d8d3; color: #934d45; }
  .login-error b { color: #7f403a; }
  .login-error small { color: #8b5e59; }
  .separation-note { margin: 19px 0 0; text-align: center; color: #94a19e; font-size: 9px; line-height: 1.5; }

  @keyframes spin { to { transform: rotate(360deg); } }

  @media (max-width: 820px) {
    .login-page { place-items: start center; padding: 0; background: #fff; }
    .login-shell { min-height: 100vh; grid-template-columns: 1fr; border: 0; border-radius: 0; box-shadow: none; }
    .login-context { display: none; }
    .login-panel { align-content: start; padding: 28px clamp(22px, 7vw, 52px) 44px; }
    .mobile-brand { display: flex; width: 100%; margin-bottom: clamp(62px, 12vh, 110px); }
    .mobile-brand .context-mark { width: 40px; height: 40px; }
    .mobile-brand span { color: #71847f; }
    .login-content { width: min(430px, 100%); }
  }

  @media (max-width: 430px) {
    .login-panel { padding-inline: 20px; }
    .mobile-brand { margin-bottom: 56px; }
    .login-content h1 { font-size: 34px; }
    .field-heading span { display: none; }
  }

  @media (prefers-reduced-motion: reduce) {
    .login-button, .login-field input { transition: none; }
    .button-spinner { animation-duration: 1.4s; }
  }
</style>
