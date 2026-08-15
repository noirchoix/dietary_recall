<script lang="ts">
  import { onMount } from 'svelte';
  import { fileToBase64, get, post } from '$lib/api';
  let packages: any[] = $state([]); let licenses: any[] = $state([]); let error = $state(''); let busy = $state(false); let file: File | null = $state(null);
  async function load() { packages = await get('/api/datasets'); licenses = await get('/api/licenses'); }
  onMount(async () => { try { await load(); } catch (e: any) { error = e.message; } });
  async function stage() { if (!file) return; busy=true; error=''; try { await post('/api/datasets/stage',{filename:file.name,content_base64:await fileToBase64(file)}); await load(); } catch(e:any){error=e.message} finally{busy=false} }
  async function commit(uid:string){busy=true;error='';try{await post(`/api/datasets/${uid}/commit`,{});await load()}catch(e:any){error=e.message}finally{busy=false}}
</script>
<div class="page-head"><div><span class="kicker">Licensed composition registry</span><h1>Dataset packages</h1><p>Ingest authorized Nigerian or international composition data only through a checksum manifest and recorded licence acceptance.</p></div></div>
<div class="notice warn"><b>No composition data is bundled.</b> Obtain lawful access from the publisher. Staging validates the package; only an all-valid atomic commit consumes import allowance.</div>
{#if error}<div class="error">{error}</div>{/if}
<section class="card" style="margin:16px 0"><h2>Stage signed package</h2><div class="form-grid"><label>Dataset ZIP<input type="file" accept=".zip,application/zip" onchange={(e)=>file=e.currentTarget.files?.[0]||null}/></label><button class="btn primary" disabled={!file||busy} onclick={stage}>{busy?'Working…':'Validate package'}</button></div></section>
<section class="card"><div class="split"><h2>Package registry</h2><span class="status">{packages.length} packages</span></div><div class="table-wrap"><table><thead><tr><th>Dataset</th><th>Source</th><th>Rows</th><th>Validation</th><th>Status</th><th></th></tr></thead><tbody>{#each packages as item}<tr><td><b>{item.dataset_code}</b><small>{item.source_filename}</small></td><td>{item.source_name}<small>{item.release_label}</small></td><td>{item.row_count}</td><td>{item.valid_count} valid / {item.invalid_count} invalid</td><td><span class="tag">{item.status}</span></td><td>{#if item.status==='staged'}<button class="btn" disabled={busy} onclick={()=>commit(item.dataset_package_uid)}>Commit</button>{/if}</td></tr>{/each}</tbody></table></div></section>
<section class="card" style="margin-top:16px"><h2>Licence acceptances</h2><p class="card-sub">{licenses.length} immutable acceptance record(s). Redistribution permission is tracked separately from research-use permission.</p></section>
