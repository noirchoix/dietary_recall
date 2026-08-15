<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api';
  let status = $state('candidate'); let rows: any[] = $state([]); let error = $state(''); let message = $state('');
  async function load() { try { rows = await get(`/api/matches?status=${status}`); error = ''; } catch (e: any) { error = e.message; } }
  async function generate() { try { const result = await post('/api/matches/generate', { minimum_score: 0.35 }); message = `${result.candidate_writes} candidate comparisons written; no records merged`; await load(); } catch (e: any) { error = e.message; } }
  async function review(uid: string, decision: string) { try { await post(`/api/matches/${uid}/review`, { decision, notes: 'Reviewed in platform' }); await load(); } catch (e: any) { error = e.message; } }
  onMount(load);
</script>
<div class="page-head"><div><span class="kicker">FAO/INFOODS-aligned review lane</span><h1>Food matching</h1><p>Name similarity creates candidates only. A curator must accept or reject each match; acceptance never overwrites measured project values.</p></div><button class="btn" onclick={generate}>Generate candidates</button></div>
<div class="notice warn" style="margin-bottom:14px"><b>No automatic merge.</b> Matching scores are a triage aid, not scientific validation. Food identity, preparation, edible portion, geography and source documentation still require human review.</div>
{#if error}<div class="error" style="margin-bottom:12px">{error}</div>{/if}{#if message}<div class="success" style="margin-bottom:12px">{message}</div>{/if}
<div class="card"><div class="toolbar"><select bind:value={status} onchange={load}><option value="candidate">Candidates</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option><option value="superseded">Superseded</option></select><span class="status neutral">{rows.length} records</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>Research food</th><th>External food</th><th>Source</th><th>Score</th><th>Method</th><th>Decision</th></tr></thead><tbody>{#each rows as match}<tr><td>{match.research_food_name}</td><td>{match.external_food_name}{#if match.local_name}<br/><small>{match.local_name}</small>{/if}</td><td>{match.source_name}</td><td><b>{Math.round(match.score * 100)}%</b></td><td class="mono">{match.match_method}</td><td>{#if status === 'candidate'}<button class="btn small" onclick={() => review(match.match_uid, 'accepted')}>Accept</button> <button class="btn secondary small" onclick={() => review(match.match_uid, 'rejected')}>Reject</button>{:else}<span class="status">{match.review_status}</span>{/if}</td></tr>{/each}</tbody></table></div></div>

