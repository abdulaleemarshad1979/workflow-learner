const $ = s => document.querySelector(s);
const token = $('meta[name=wl-token]').content;
let data = {}, selected = null, editingSteps = [], reteach = null;
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, method = 'GET', body) {
  const res = await fetch('/api/' + path, {method, headers: {'Content-Type':'application/json','X-WL-Token':token}, body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await res.json();
  if (!res.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail));
  $('#error').classList.add('hidden');
  return value;
}
function error(e) { $('#error').textContent = e.message; $('#error').classList.remove('hidden'); }
function toast(message) { $('#toast').textContent = message; $('#toast').classList.remove('hidden'); setTimeout(() => $('#toast').classList.add('hidden'), 4500); }
function view(name) {
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('hidden', el.id !== name));
  document.querySelectorAll('nav button').forEach(el => el.classList.toggle('active', el.dataset.view === name));
  $('#view-title').textContent = {overview:'Overview',workflows:'My workflows',patterns:'Learned patterns',activity:'Run history',setup:'Setup & privacy'}[name];
}
function empty() { return '<div class="empty"><div><h3>Your first workflow starts with a demonstration.</h3><p>Connect your browser, then show Workflow Learner how you get it done.</p></div><button data-view="setup">Connect browser ↗</button></div>'; }
function autopilotText(w) {
  const ap = w.autopilot || {};
  if (!ap.enabled) return '';
  if (ap.paused_reason) return `<p class="ap-paused">Autopilot paused · ${esc(ap.paused_reason)}</p>`;
  if (!w.reviewed) return '<p class="ap-paused">Autopilot waiting · review this revision</p>';
  const when = ap.every_minutes ? `every ${ap.every_minutes} min` : ap.daily_at ? `daily at ${esc(ap.daily_at)}` : 'on demand';
  return `<p class="ap-on">● Autopilot on · ${when}${ap.last_started ? ' · last ' + esc(new Date(ap.last_started * 1000).toLocaleString()) : ''}</p>`;
}
function card(w) {
  return `<article class="card"><div class="card-icon">⇄</div><span class="tag">${w.reviewed ? 'Reviewed · ready to replay' : 'Needs your review'}</span>${autopilotText(w)}<h3>${esc(w.name)}</h3><p>${w.steps.length} steps · ${w.demonstrations} demonstrations · revision ${w.revision}</p><p>${w.successes} completed runs · ${w.failures} failed runs</p><div class="card-actions"><button data-edit="${w.id}">Edit steps</button><button data-teach="${w.id}">Teach again</button>${!w.reviewed ? `<button data-review="${w.id}">Mark reviewed</button>` : ''}<button data-autopilot="${w.id}">Autopilot</button><button class="primary" data-run="${w.id}">Replay ↗</button><button class="danger" data-delete="${w.id}" aria-label="Delete ${esc(w.name)}">Delete</button></div></article>`;
}
function setHTML(el, value) { if (el._rendered !== value) { el.innerHTML = value; el._rendered = value; } }
function render() {
  const workflows = data.workflows || [];
  const browserName = /brave/i.test(data.companion_browser || '') ? 'Brave' : /chrom/i.test(data.companion_browser || '') ? 'Chrome' : 'browser';
  $('#connection').textContent = data.extension_connected ? `● ${browserName} companion connected` : data.companion_browser ? `○ ${browserName} closed · opens automatically on replay` : '○ Browser companion not installed';
  $('#connection').classList.toggle('no-connect', !data.extension_connected);
  $('#nav-count').textContent = $('#stat-workflows').textContent = workflows.length;
  $('#stat-demos').textContent = workflows.reduce((a,w) => a + w.demonstrations, 0);
  $('#stat-runs').textContent = workflows.reduce((a,w) => a + w.successes, 0);
  setHTML($('#overview-cards'), workflows.length ? workflows.slice(0, 3).map(card).join('') : empty());
  setHTML($('#workflow-cards'), workflows.length ? workflows.map(card).join('') : empty());
  setHTML($('#pattern-cards'), data.patterns?.length ? data.patterns.map((p, i) => `<article class="card"><span class="tag">Seen in ${p.observations} demonstrations</span><h3>${p.steps.length} steps in a familiar order</h3><div class="pattern-sequence">${p.steps.map(s => esc(s.label || s.kind)).join(' → ')}</div><div class="card-actions"><button data-adopt="${i}">Save as workflow</button></div></article>`).join('') : '<div class="empty"><div><h3>Patterns grow with practice.</h3><p>Demonstrate a routine twice. Repeated sequences will appear here.</p></div></div>');
  setHTML($('#run-history'), data.runs?.length ? data.runs.map(r => `<article class="history-row"><div><b>${esc(r.name)}</b><small>${esc(new Date(r.created_at).toLocaleString())} · revision ${r.revision} · ${r.mode}</small>${r.error ? `<p>${esc(r.error)}</p>` : ''}<details><summary>Execution log</summary><small>${r.log.map(l => `Step ${l.index + 1}: ${esc(l.status)}`).join('<br>')}</small></details></div><span>${r.cursor}/${r.total} steps</span><span class="badge">${esc(r.status)}</span></article>`).join('') : '<div class="empty"><h3>No workflows have run yet.</h3></div>');
  $('#data-location').textContent = 'Local data folder: ' + data.data_dir;
  $('#native-status').textContent = 'Desktop adapter: ' + data.native?.status + (data.native?.error ? ' · ' + data.native.error : '');
  const rec = data.recording;
  $('#recording-banner').classList.toggle('hidden', !rec);
  if (rec) setHTML($('#recording-banner'), `<span><b>${rec.status === 'paused' ? 'Ⅱ Paused' : '● Recording'}: ${esc(rec.name)}</b> · ${rec.steps.length} steps captured</span><button data-record-control="pause">${rec.status === 'paused' ? 'Resume' : 'Pause'}</button><button class="primary" data-record-control="stop">Stop & save</button>`);
  const r = data.run, active = r && ['running','waiting','paused'].includes(r.status);
  $('#run-banner').classList.toggle('hidden', !active);
  if (active) setHTML($('#run-banner'), `<span><b>${esc(r.name)}</b> · Step ${r.cursor + 1}/${r.total}: ${esc(r.current?.label || r.current?.kind)}<br><small>${r.status === 'waiting' ? (r.current?.kind === 'manual' ? 'Complete this step yourself, then confirm.' : 'Waiting for your approval.') : r.status === 'paused' ? 'Paused. An action already dispatched may finish.' : 'Running. Keep the browser companion connected.'}</small></span>${r.status === 'waiting' ? `<button class="primary" data-run-control="approve">${r.current?.kind === 'manual' ? 'I completed this step' : 'Approve this step'}</button>` : ''}<button data-run-control="${r.status === 'paused' ? 'resume' : 'pause'}">${r.status === 'paused' ? 'Resume' : 'Pause'}</button><button class="danger" data-run-control="stop">Stop</button>`);
}
async function refresh() { try { data = await api('state'); render(); } catch(e) { error(e); } }
function openRecord(id = null) {
  reteach = id;
  $('#record-form').reset();
  if (id) $('#record-form').elements.name.value = data.workflows.find(w => w.id === id).name;
  $('#record-dialog').showModal();
}
function readSteps() {
  document.querySelectorAll('#step-list .step').forEach((el, i) => {
    for (const field of ['kind','label','url','selector','value']) editingSteps[i][field] = el.querySelector(`[data-field="${field}"]`).value;
    editingSteps[i].confirm = el.querySelector('[data-field=confirm]').checked;
  });
  return editingSteps;
}
function renderSteps() {
  const kinds = ['navigate','click','fill','select','check','press','manual','native_focus','native_fill','native_action','native_launch'];
  $('#step-list').innerHTML = editingSteps.map((s,i) => `<article class="step"><div class="step-header"><b>${i+1}</b><input aria-label="Step ${i+1} label" data-field="label" value="${esc(s.label)}"><button type="button" data-remove="${i}" aria-label="Delete step ${i+1}">×</button></div><div class="step-fields"><label>Action<select data-field="kind">${kinds.map(k=>`<option ${k===s.kind?'selected':''}>${k}</option>`).join('')}</select></label><label>Value or {{parameter}}<input data-field="value" value="${esc(s.value)}"></label><label>Website URL<input data-field="url" value="${esc(s.url)}"></label><label>Control selector<input data-field="selector" value="${esc(s.selector)}"></label><label class="checkbox"><input type="checkbox" data-field="confirm" ${s.confirm?'checked':''}>Ask first — approve on every replay</label></div>${s.app ? `<p class="muted">Desktop: ${esc(s.app)} · ${esc(s.role)} · path ${esc(JSON.stringify(s.path))}</p>` : ''}</article>`).join('');
  $('#step-json').value = JSON.stringify(editingSteps, null, 2);
}
function edit(id) {
  selected = data.workflows.find(w => w.id === id);
  editingSteps = structuredClone(selected.steps);
  $('#edit-name').value = selected.name;
  $('#editor-title').textContent = selected.name;
  renderSteps(); $('#editor-dialog').showModal();
}
function run(id) {
  selected = data.workflows.find(w => w.id === id);
  $('#run-title').textContent = selected.name;
  $('#run-form').elements.mode.value = 'bypass';
  $('#run-form').elements.mode.querySelector('[value=automatic]').disabled = !selected.reviewed;
  const saved = new Set(selected.saved_inputs || []);
  $('#parameters').innerHTML = selected.parameters.map(p => `<label>${esc(p)}${saved.has(p) ? ' <small>saved · leave blank to reuse</small>' : ''}<input data-param="${esc(p)}" ${saved.has(p) ? '' : 'required'} autocomplete="off"></label>`).join('');
  $('#run-dialog').showModal();
}
function autopilot(id) {
  selected = data.workflows.find(w => w.id === id);
  const ap = selected.autopilot || {}, f = $('#autopilot-form').elements;
  $('#autopilot-title').textContent = selected.name;
  f.enabled.checked = Boolean(ap.enabled);
  f.when.value = ap.every_minutes ? 'every' : ap.daily_at ? 'daily' : 'demand';
  f.every.value = ap.every_minutes || 60; f.daily.value = ap.daily_at || '09:00';
  const saved = new Set(selected.saved_inputs || []);
  $('#autopilot-inputs').innerHTML = selected.parameters.length ? selected.parameters.map(p => `<label>${esc(p)}${saved.has(p) ? ' <small>saved</small>' : ''}<input data-param="${esc(p)}" autocomplete="off" placeholder="${saved.has(p) ? 'Leave blank to keep saved value' : 'Value used on every autopilot run'}"></label>`).join('') : '<p class="muted">This workflow has no inputs.</p>';
  $('#autopilot-dialog').showModal();
}
document.addEventListener('click', async event => {
  const b = event.target.closest('button'); if (!b) return;
  try {
    if (b.dataset.view) view(b.dataset.view);
    if (b.hasAttribute('data-close')) b.closest('dialog').close();
    if (b.dataset.action === 'record') openRecord();
    if (b.dataset.action === 'practice') window.open('/practice','_blank','noopener');
    if (b.dataset.edit) edit(b.dataset.edit);
    if (b.dataset.teach) openRecord(b.dataset.teach);
    if (b.dataset.run) run(b.dataset.run);
    if (b.dataset.autopilot) autopilot(b.dataset.autopilot);
    if (b.dataset.delete) {
      const w = data.workflows.find(w => w.id === b.dataset.delete);
      if (confirm(`Delete “${w.name}”?\n\nThis removes its steps, demonstrations, revisions, saved inputs, autopilot schedule and run history. It cannot be undone.`)) {
        await api('workflows/' + w.id, 'DELETE'); toast(`Deleted “${w.name}”.`); await refresh();
      }
    }
    if (b.dataset.adopt !== undefined) {
      const name = prompt('Name this learned routine', 'Learned routine');
      if (name) { await api(`patterns/${b.dataset.adopt}/adopt`, 'POST', {name}); toast('Saved. Review its steps, then turn on autopilot.'); view('workflows'); await refresh(); }
    }
    if (b.dataset.review) {
      const w = data.workflows.find(w=>w.id===b.dataset.review);
      if (confirm(`Mark revision ${w.revision} of “${w.name}” reviewed?\n\nCheck its steps and “Ask first” settings before enabling automatic replay.`)) {
        await api(`workflows/${w.id}/review`,'POST',{}); await refresh();
      }
    }
    if (b.dataset.recordControl) { await api('recordings/'+b.dataset.recordControl,'POST',{}); await refresh(); }
    if (b.dataset.runControl) { await api('run/'+b.dataset.runControl,'POST',{}); await refresh(); }
    if (b.dataset.remove !== undefined) { readSteps(); editingSteps.splice(Number(b.dataset.remove),1); renderSteps(); }
  } catch(e) { error(e); }
});
$('#record-form').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target.elements;
  try {
    await api('recordings','POST',{name:f.name.value,workflow_id:reteach,origins:f.origins.value.split(/[\n,]/).map(x=>x.trim()).filter(Boolean),native:f.native.checked});
    $('#record-dialog').close(); toast('Recording started. Switch to your task and demonstrate it.'); await refresh();
  } catch(e) { error(e); }
});
$('#editor-form').addEventListener('submit', async e => {
  e.preventDefault();
  try { await api('workflows/'+selected.id,'PUT',{name:$('#edit-name').value,steps:readSteps()}); $('#editor-dialog').close(); toast('New revision saved. Review it before automatic replay.'); await refresh(); } catch(e) { error(e); }
});
$('#add-step').onclick = () => { readSteps(); editingSteps.push({kind:'manual',label:'Complete this step manually',value:'',url:'',selector:'',confirm:true}); renderSteps(); };
$('#apply-json').onclick = () => { try { const steps = JSON.parse($('#step-json').value); if(!Array.isArray(steps)) throw new Error('Expected an array'); editingSteps=steps; renderSteps(); } catch(e) { error(e); } };
$('#delete-workflow').onclick = async () => { if(confirm('Delete this workflow, its demonstrations, revisions and run history?')) { try {await api('workflows/'+selected.id,'DELETE'); $('#editor-dialog').close(); await refresh();}catch(e){error(e);} } };
$('#run-form').addEventListener('submit',async e => {
  e.preventDefault();
  const parameters = Object.fromEntries([...$('#parameters').querySelectorAll('input')].map(el=>[el.dataset.param,el.value]));
  try {await api('workflows/'+selected.id+'/runs','POST',{mode:e.target.elements.mode.value,parameters,remember:e.target.elements.remember.checked}); $('#run-dialog').close(); $('#run-form').reset(); $('#parameters').replaceChildren(); await refresh();}catch(e){error(e);}
});
$('#autopilot-form').addEventListener('submit', async e => {
  e.preventDefault(); const f = e.target.elements;
  const values = Object.fromEntries([...$('#autopilot-inputs').querySelectorAll('input')].map(el => [el.dataset.param, el.value]).filter(([, v]) => v !== ''));
  try {
    if (Object.keys(values).length) await api(`workflows/${selected.id}/inputs`, 'PUT', values);
    await api(`workflows/${selected.id}/autopilot`, 'PUT', {enabled: f.enabled.checked, every_minutes: f.when.value === 'every' ? Number(f.every.value) : null, daily_at: f.when.value === 'daily' ? f.daily.value : null});
    $('#autopilot-dialog').close(); toast(f.enabled.checked ? 'Autopilot on. It runs by itself; Stop is always on the banner.' : 'Autopilot off.'); await refresh();
  } catch(e) { error(e); }
});
$('#autopilot-now').onclick = async () => {
  try { await api(`workflows/${selected.id}/runs`, 'POST', {mode: 'bypass', parameters: {}}); $('#autopilot-dialog').close(); await refresh(); } catch(e) { error(e); }
};
$('#forget-inputs').onclick = async () => {
  if (!confirm('Delete saved input values for this workflow? Autopilot will turn off until you save new ones.')) return;
  try { await api(`workflows/${selected.id}/inputs`, 'DELETE'); await api(`workflows/${selected.id}/autopilot`, 'PUT', {enabled: false}); $('#autopilot-dialog').close(); await refresh(); } catch(e) { error(e); }
};
$('#copy-key').onclick = async () => { try {await navigator.clipboard.writeText(data.adapter_token); toast('Pairing key copied. Paste it into the browser companion.');}catch(e){error(e);} };
refresh(); setInterval(refresh, 2000);
