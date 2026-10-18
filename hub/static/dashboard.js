const state = {
  machines: [],
  cameras: [],
  history: {},
  alerts: [],
  feed: [],
  notifPermission: 'default',
  referenceTargetCameraId: null,
};

const MAX_ALERTS = 25;
const MAX_FEED = 60;

// Names/locations/diagnoses are user- or camera-supplied text: always escape before putting into innerHTML.
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function fmt(n, d = 2) { return (n === null || n === undefined) ? '--' : Number(n).toFixed(d); }
function timeAgo(ts) { return new Date(ts * 1000).toLocaleTimeString([], { hour12: false }); }
function machineById(id) { return state.machines.find(m => m.id === id); }
function cameraById(id) { for (const m of state.machines) { const c = m.cameras.find(c => c.id === id); if (c) return c; } return null; }

function sparklineSVG(values, tolerance, width = 290, height = 44) {
  if (!values.length) return `<svg width="${width}" height="${height}"></svg>`;
  const lo = Math.min(-tolerance, ...values);
  const hi = Math.max(tolerance, ...values);
  const pad = (hi - lo) * 0.2 || 1;
  const yMin = lo - pad, yMax = hi + pad;
  const yScale = v => height - ((v - yMin) / (yMax - yMin)) * height;
  const xScale = i => values.length === 1 ? width / 2 : (i / (values.length - 1)) * width;

  const bandTop = yScale(tolerance);
  const bandBot = yScale(-tolerance);
  const zeroY = yScale(0);
  const points = values.map((v, i) => `${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`).join(' ');
  const dots = values.map((v, i) => {
    const inTol = Math.abs(v) <= tolerance;
    return `<circle cx="${xScale(i).toFixed(1)}" cy="${yScale(v).toFixed(1)}" r="2.3" fill="${inTol ? 'var(--pass)' : 'var(--fail)'}" />`;
  }).join('');

  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
    <rect x="0" y="${bandTop.toFixed(1)}" width="${width}" height="${(bandBot - bandTop).toFixed(1)}" fill="rgba(47,191,113,0.08)" />
    <line x1="0" y1="${zeroY.toFixed(1)}" x2="${width}" y2="${zeroY.toFixed(1)}" stroke="var(--text-mute)" stroke-width="1" stroke-dasharray="3,3" opacity="0.5" />
    <polyline points="${points}" fill="none" stroke="var(--accent)" stroke-width="1.4" opacity="0.85" />
    ${dots}
  </svg>`;
}

function renderSummary() {
  const allCameras = state.cameras;
  const online = allCameras.filter(c => c.online).length;
  const allStats = allCameras.map(c => c.stats).filter(Boolean);
  const total = allStats.reduce((s, x) => s + (x.total || 0), 0);
  const passes = allStats.reduce((s, x) => s + (x.passes || 0), 0);
  const rate = total ? (100 * passes / total).toFixed(1) : '--';

  document.getElementById('summary-row').innerHTML = `
    <div class="summary-card"><div class="label">Machines</div><div class="value">${state.machines.length}</div></div>
    <div class="summary-card"><div class="label">Cameras Online</div><div class="value">${online} / ${allCameras.length}</div></div>
    <div class="summary-card pass"><div class="label">Overall Pass Rate</div><div class="value">${rate}${rate !== '--' ? '%' : ''}</div></div>
    <div class="summary-card ${state.alerts.length ? 'fail' : ''}"><div class="label">Active Alerts</div><div class="value">${state.alerts.length}</div></div>
  `;
}

function renderMachines() {
  const container = document.getElementById('machines-container');
  const emptyState = document.getElementById('empty-state');

  if (!state.machines.length) {
    container.innerHTML = '';
    emptyState.classList.remove('hidden');
    return;
  }
  emptyState.classList.add('hidden');

  container.innerHTML = state.machines.slice().sort((a, b) => a.id - b.id).map(m => `
    <section class="machine-section">
      <div class="machine-section-head">
        <h3>${esc(m.name)}</h3>
        <span class="loc">${esc(m.location || '')}</span>
        <span class="tol">Max error &plusmn; ${fmt(m.max_error_percent, 1)}%</span>
      </div>
      <div class="cameras-grid">
        ${m.cameras.length ? m.cameras.map(c => renderCameraCard(m, c)).join('') : `<p class="empty-state">No camera assigned to this machine yet.</p>`}
      </div>
    </section>
  `).join('');

  container.querySelectorAll('[data-open-image]').forEach(node => node.addEventListener('click', () => openImageModal(node.dataset.openImage)));
  container.querySelectorAll('[data-set-reference]').forEach(node => node.addEventListener('click', () => openReferenceModal(Number(node.dataset.setReference))));
  container.querySelectorAll('[data-capture]').forEach(node => node.addEventListener('click', () => requestCapture(Number(node.dataset.capture), node)));
  container.querySelectorAll('[data-review-preview]').forEach(node => node.addEventListener('click', () => openPreviewModal(Number(node.dataset.reviewPreview))));
  container.querySelectorAll('[data-auto-toggle]').forEach(node => node.addEventListener('change', () => saveAuto(Number(node.dataset.autoToggle))));
  container.querySelectorAll('[data-auto-mode]').forEach(node => node.addEventListener('change', () => saveAuto(Number(node.dataset.autoMode))));
  container.querySelectorAll('[data-auto-interval]').forEach(node => node.addEventListener('change', () => saveAuto(Number(node.dataset.autoInterval))));
}

// ---------- device control: capture / preview / auto-test ----------
async function requestCapture(cameraId, btn) {
  btn.disabled = true; btn.textContent = 'Waiting for camera…';
  await fetch(`/api/cameras/${cameraId}/capture`, { method: 'POST' });
  setTimeout(() => { btn.disabled = false; btn.innerHTML = '&#128247; Capture photo'; }, 6000);
}

async function saveAuto(cameraId) {
  const toggle = document.querySelector(`[data-auto-toggle="${cameraId}"]`);
  const interval = document.querySelector(`[data-auto-interval="${cameraId}"]`);
  const fd = new FormData();
  fd.append('enabled', toggle.checked ? 'true' : 'false');
  fd.append('interval_sec', interval.value || '10');
  fd.append('mode', document.querySelector(`[data-auto-mode="${cameraId}"]`).value);
  await fetch(`/api/cameras/${cameraId}/auto`, { method: 'POST', body: fd });
}

let previewCameraId = null;
function openPreviewModal(cameraId) {
  const c = cameraById(cameraId);
  if (!c || !c.preview_image_path) return;
  previewCameraId = cameraId;
  document.getElementById('preview-title').textContent = `New photo — ${esc(c.name)}`;
  document.getElementById('preview-img').src = `/api/images/${c.preview_image_path}`;
  document.getElementById('preview-status').textContent = '';
  document.getElementById('preview-test-btn').disabled = !c.has_reference;
  document.getElementById('preview-modal').classList.remove('hidden');
}
function closePreviewModal() { document.getElementById('preview-modal').classList.add('hidden'); previewCameraId = null; }
document.getElementById('preview-close').addEventListener('click', closePreviewModal);

async function previewAction(path, okText, body) {
  const status = document.getElementById('preview-status');
  status.textContent = 'Working…';
  const res = await fetch(`/api/cameras/${previewCameraId}/preview/${path}`, { method: 'POST', body });
  const data = await res.json().catch(() => ({}));
  if (res.ok) { status.textContent = okText(data); setTimeout(closePreviewModal, 1200); }
  else status.textContent = data.detail || 'Failed';
}
document.getElementById('preview-ref-btn').addEventListener('click', () => {
  const fd = new FormData();
  const w = document.getElementById('preview-w-mm').value, h = document.getElementById('preview-h-mm').value;
  if (w) fd.append('width_mm', w);
  if (h) fd.append('height_mm', h);
  previewAction('use-as-reference', d => `Reference set: ${d.width_px.toFixed(0)} x ${d.height_px.toFixed(0)} px`, fd);
});
document.getElementById('preview-test-btn').addEventListener('click', () =>
  previewAction('test', d => `${d.result}: ${d.error_pct === null ? '--' : d.error_pct.toFixed(2)}% error`));
document.getElementById('preview-discard-btn').addEventListener('click', async () => {
  await fetch(`/api/cameras/${previewCameraId}/preview`, { method: 'DELETE' });
  closePreviewModal();
});

function cpkBadge(cap) {
  if (!cap || cap.cpk === null || cap.cpk === undefined) return '<span class="cpk none" title="Needs at least 10 measured parts">Cpk n/a</span>';
  const cls = cap.cpk >= 1.33 ? 'good' : (cap.cpk >= 1.0 ? 'ok' : 'bad');
  return `<span class="cpk ${cls}" title="Process capability of the last ${cap.width.n} parts vs the tolerance. >=1.33 good, <1.0 not capable. With one part left still under the camera this is a gauge-repeatability check.">Cpk ${cap.cpk.toFixed(2)}</span>`;
}

function renderCameraCard(m, c) {
  const hist = (state.history[c.id] || []).map(r => {
    const we = r.width_error_pct, he = r.height_error_pct;
    if (we === null && he === null) return null;
    return Math.abs(we || 0) >= Math.abs(he || 0) ? we : he;
  }).filter(v => v !== null && v !== undefined);

  const latest = c.latest;
  const result = latest ? latest.result : null;
  let severity = 'info';
  if (result === 'FAIL') severity = 'critical';
  else if (latest && (latest.diagnosis || '').toLowerCase().includes('trend')) severity = 'warning';

  let body;
  if (!c.has_reference) {
    body = `
      <div class="no-reference-box">
        No reference part captured yet on this camera.
        <div><button class="primary-btn" data-set-reference="${c.id}">Set reference part</button></div>
      </div>`;
  } else {
    const refThumb = c.reference_image_path
      ? `<img src="/api/images/${c.reference_image_path}" data-open-image="/api/images/${c.reference_image_path}">`
      : `<div class="thumb-placeholder">No image</div>`;
    const curThumb = latest && latest.image_path
      ? `<img src="/api/images/${latest.image_path}" data-open-image="/api/images/${latest.image_path}">`
      : `<div class="thumb-placeholder">Waiting for first inspection</div>`;

    body = `
      <div class="thumb-pair">
        <div class="thumb-box">${refThumb}<div class="thumb-label">Reference</div></div>
        <div class="thumb-box">${curThumb}<div class="thumb-label">Latest capture</div></div>
      </div>
      <div class="reading-row">
        <span class="reading-value">${latest && latest.error_pct !== null && latest.error_pct !== undefined ? fmt(latest.error_pct) : '--.--'}</span>
        <span class="reading-unit">% error</span>
        ${result ? `<span class="reading-badge ${result}">${result}</span>` : ''}
      </div>
      ${latest && latest.width_mm !== null && latest.width_mm !== undefined
        ? `<div class="mm-line">${fmt(latest.width_mm, 2)} &times; ${fmt(latest.height_mm, 2)} mm</div>` : ''}
      <div class="tolerance-line">Machine max error: &plusmn; ${fmt(m.max_error_percent, 1)}%
        &middot; ${cpkBadge(c.capability)}</div>
      <div class="spark-wrap">
        <div class="spark-label">Last ${hist.length} readings vs. tolerance band</div>
        ${sparklineSVG(hist, m.max_error_percent)}
      </div>
      ${latest && latest.diagnosis ? `
        <div class="diagnosis-box ${severity}">
          <div class="d-title">${esc(latest.diagnosis)}</div>
          <div class="d-rec">${esc(latest.recommendation || '')}</div>
        </div>` : `<div class="diagnosis-box"><div class="d-title">Waiting for first inspection…</div></div>`}
    `;
  }

  return `
    <div class="camera-card ${result === 'FAIL' ? 'state-fail' : ''}" id="card-${c.id}">
      <div class="camera-card-head">
        <p class="camera-name">${esc(c.name)}</p>
        <span class="status-pill ${c.online ? 'online' : ''}"><span class="dot"></span>${c.online ? 'Online' : 'Offline'}</span>
      </div>
      ${body}
      <div class="device-bar">
        <button class="ghost-btn accent-btn" data-capture="${c.id}" ${c.online ? '' : 'title="Camera is offline - the request will wait until it connects"'}>&#128247; Capture photo</button>
        ${c.has_preview ? `<button class="ghost-btn" data-review-preview="${c.id}">Review new photo</button>` : ''}
        <div class="auto-box">
          <label class="switch-label"><input type="checkbox" data-auto-toggle="${c.id}" ${c.auto_enabled ? 'checked' : ''} ${c.has_reference && c.machine_id ? '' : 'disabled title="Set a reference first"'}> Auto-test</label>
          <select class="interval-input" data-auto-mode="${c.id}" title="Timer = every N seconds (bench/demo). Sensor = when the photo-eye sees a part (real line).">
            <option value="timer" ${c.trigger_mode !== 'sensor' ? 'selected' : ''}>every</option>
            <option value="sensor" ${c.trigger_mode === 'sensor' ? 'selected' : ''}>on sensor</option>
          </select>
          <input type="number" min="2" max="3600" value="${c.auto_interval_sec || 10}" class="interval-input" data-auto-interval="${c.id}" ${c.trigger_mode === 'sensor' ? 'disabled' : ''}> s
        </div>
      </div>
      <div class="card-footer">
        <span>${c.stats && c.stats.total ? `${c.stats.pass_rate}% pass rate (last ${c.stats.total})` : 'No history yet'}</span>
        ${c.has_reference ? `<button class="text-link" data-set-reference="${c.id}">Update reference &#8635;</button>` : ''}
      </div>
    </div>
  `;
}

function renderAlerts() {
  const list = document.getElementById('alerts-list');
  if (!state.alerts.length) { list.innerHTML = `<p class="empty-state">No active alerts.</p>`; return; }
  list.innerHTML = state.alerts.slice(0, MAX_ALERTS).map(a => `
    <div class="alert-row">
      <div class="alert-sev ${a.severity}"></div>
      <div class="alert-body">
        <span class="alert-machine">${esc(a.camera_name)} &middot; ${esc(a.machine_name)}</span><span class="alert-time">${timeAgo(a.ts)}</span>
        <div class="alert-text">${esc(a.diagnosis)}</div>
        <div class="alert-rec">${esc(a.recommendation)}</div>
      </div>
    </div>
  `).join('');
}

function renderFeed() {
  document.getElementById('feed-list').innerHTML = state.feed.slice(0, MAX_FEED).map(f => `
    <div class="feed-row">
      <span class="feed-time">${timeAgo(f.ts)}</span>
      <span class="feed-machine">${esc(f.camera_name || f.machine_name)}</span>
      <span class="feed-value">${f.error_pct !== null && f.error_pct !== undefined ? fmt(f.error_pct) + '%' : '--'}</span>
      <span class="feed-badge ${f.result}">${f.result}</span>
    </div>
  `).join('');
}

function openImageModal(src) {
  document.getElementById('image-modal-img').src = src;
  document.getElementById('image-modal').classList.remove('hidden');
}
document.getElementById('image-modal-close').addEventListener('click', () => document.getElementById('image-modal').classList.add('hidden'));
document.getElementById('image-modal').addEventListener('click', (e) => { if (e.target.id === 'image-modal') e.target.classList.add('hidden'); });

async function loadAll() {
  if (!state.hubUrl) {
    try { state.hubUrl = (await fetch('/api/hub-info').then(r => r.json())).camera_hub_url; } catch (e) {}
  }
  state.machines = await fetch('/api/machines').then(r => r.json());
  state.cameras = state.machines.flatMap(m => m.cameras);
  for (const c of state.cameras) {
    state.history[c.id] = await fetch(`/api/cameras/${c.id}/history?limit=25`).then(r => r.json());
  }
  const feed = await fetch('/api/feed?limit=60').then(r => r.json());
  state.feed = feed.map(f => ({ ...f, machine_name: machineById(f.machine_id)?.name || `Machine ${f.machine_id}`, camera_name: cameraById(f.camera_id)?.name || `Camera ${f.camera_id}` }));
  state.alerts = feed
    .filter(f => f.diagnosis && (f.result === 'FAIL' || (f.diagnosis || '').toLowerCase().includes('trend')))
    .map(f => ({ severity: f.result === 'FAIL' ? 'critical' : 'warning', machine_name: machineById(f.machine_id)?.name || `Machine ${f.machine_id}`, camera_name: cameraById(f.camera_id)?.name || `Camera ${f.camera_id}`, ts: f.ts, diagnosis: f.diagnosis, recommendation: f.recommendation }));

  renderSummary(); renderMachines(); renderAlerts(); renderFeed();
  renderAdminMachines(); renderAdminCameras(); populateCameraMachineSelect();
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connectWS, 2000); };
  ws.onerror = () => ws.close();

  ws.onmessage = async (evt) => {
    const msg = JSON.parse(evt.data);
    if (msg.type === 'state_changed') { await loadAll(); return; }
    if (msg.type === 'camera_offline') {
      beep();
      showToast(`Camera OFFLINE — ${msg.camera_name}`, `${msg.machine_name || 'Unassigned'}: no signal. Parts are NOT being inspected. Check power / WiFi.`);
      await loadAll(); return;
    }
    if (msg.type === 'preview') { await loadAll(); openPreviewModal(msg.camera_id); return; }
    if (msg.type !== 'inspection') return;

    await loadAll();  // simplest correct refresh given nested machine->camera structure

    if (msg.result === 'FAIL' || (msg.diagnosis || '').toLowerCase().includes('trend')) {
      // avoid double counting: loadAll already rebuilt state.alerts/feed from server, just notify + flash
    }

    const card = document.getElementById(`card-${msg.camera_id}`);
    if (card) { card.classList.add('flash'); setTimeout(() => card.classList.remove('flash'), 900); }

    if (msg.result === 'FAIL') notifyFail(msg);
  };
}

function setConn(online) {
  const badge = document.getElementById('conn-status');
  badge.classList.toggle('online', online);
  badge.classList.toggle('offline', !online);
  document.getElementById('conn-label').textContent = online ? 'live' : 'reconnecting…';
}

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = 880; osc.type = 'square';
    gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
    osc.start(); osc.stop(ctx.currentTime + 0.3);
  } catch (e) {}
}

function showToast(title, body) {
  const stack = document.getElementById('toast-stack');
  const toast = document.createElement('div');
  toast.className = 'toast';
  const t = document.createElement('div'); t.className = 't-title'; t.textContent = title;
  const b = document.createElement('div'); b.className = 't-body'; b.textContent = body;
  toast.append(t, b);
  stack.appendChild(toast);
  while (stack.children.length > 3) stack.firstChild.remove();     // never bury the dashboard in alerts
  setTimeout(() => toast.remove(), 6000);
}

function notifyFail(msg) {
  beep();
  showToast(`FAIL — ${msg.camera_name} (${msg.machine_name})`, msg.diagnosis || 'Out of tolerance');
  if (state.notifPermission === 'granted') {
    try { new Notification(`FAIL — ${msg.camera_name}`, { body: msg.diagnosis || 'Out of tolerance' }); } catch (e) {}
  }
}

document.getElementById('notif-btn').addEventListener('click', async () => {
  if (!('Notification' in window)) { showToast('Not supported', 'Your browser does not support desktop notifications; on-page alerts still work.'); return; }
  const perm = await Notification.requestPermission();
  state.notifPermission = perm;
  const btn = document.getElementById('notif-btn');
  if (perm === 'granted') { btn.classList.add('active'); document.getElementById('notif-label').textContent = 'Alerts on'; }
  else { document.getElementById('notif-label').textContent = 'Enable alerts'; }
});

document.getElementById('admin-btn').addEventListener('click', () => document.getElementById('admin-modal').classList.remove('hidden'));
document.getElementById('empty-admin-btn').addEventListener('click', () => document.getElementById('admin-modal').classList.remove('hidden'));
document.getElementById('admin-close').addEventListener('click', () => document.getElementById('admin-modal').classList.add('hidden'));
document.getElementById('admin-modal').addEventListener('click', (e) => { if (e.target.id === 'admin-modal') e.target.classList.add('hidden'); });

document.querySelectorAll('.admin-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.admin-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.admin-pane').forEach(p => p.classList.add('hidden'));
    tab.classList.add('active');
    document.getElementById(`tab-${tab.dataset.tab}`).classList.remove('hidden');
  });
});

function renderAdminMachines() {
  const list = document.getElementById('admin-machines-list');
  if (!state.machines.length) { list.innerHTML = `<p class="empty-state">No machines added yet.</p>`; return; }
  list.innerHTML = state.machines.map(m => `
    <div class="admin-row">
      <div class="admin-row-main">
        <div class="name">${esc(m.name)}</div>
        <div class="meta">${esc(m.location || 'No location set')} &middot; ${m.cameras.length} camera(s)</div>
      </div>
      <label style="display:flex;align-items:center;gap:5px;color:var(--text-mute);font-size:11px;">
        Max error
        <input type="number" step="0.1" min="0.1" value="${m.max_error_percent}" style="width:60px" data-tolerance-for="${m.id}">%
      </label>
      <button class="danger-btn" data-delete-machine="${m.id}">Delete</button>
    </div>
  `).join('');

  list.querySelectorAll('[data-tolerance-for]').forEach(inp => {
    inp.addEventListener('change', async () => {
      await fetch(`/api/machines/${inp.dataset.toleranceFor}`, { method: 'PATCH', body: new URLSearchParams({ max_error_percent: inp.value }) });
    });
  });
  list.querySelectorAll('[data-delete-machine]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this machine? Its cameras become unassigned; inspection history stays in the database.')) return;
      await fetch(`/api/machines/${btn.dataset.deleteMachine}`, { method: 'DELETE' });
    });
  });
}

function renderAdminCameras() {
  const list = document.getElementById('admin-cameras-list');
  if (!state.cameras.length) { list.innerHTML = `<p class="empty-state">No cameras added yet.</p>`; return; }
  list.innerHTML = `<p class="hint-text">Cameras connect to: <b>${esc(state.hubUrl || '')}</b> &mdash; use this as HUB_URL
    (not localhost / 0.0.0.0). It changes if this computer joins a different network.</p>` + state.cameras.map(c => `
    <div class="admin-row">
      <div class="admin-row-main">
        <div class="name"><span class="tiny-status ${c.online ? 'online' : ''}"></span> ${esc(c.name)}</div>
        <div class="meta"><span class="token-badge">${c.api_key}</span> &middot; ${c.has_reference ? 'reference set' : 'no reference yet'}</div>
      </div>
      <select data-reassign-camera="${c.id}">
        <option value="">Unassigned</option>
        ${state.machines.map(m => `<option value="${m.id}" ${c.machine_id === m.id ? 'selected' : ''}>${esc(m.name)}</option>`).join('')}
      </select>
      <button class="text-link" data-set-reference="${c.id}">Reference</button>
      <button class="danger-btn" data-delete-camera="${c.id}">Delete</button>
    </div>
  `).join('');

  list.querySelectorAll('[data-reassign-camera]').forEach(sel => {
    sel.addEventListener('change', async () => {
      await fetch(`/api/cameras/${sel.dataset.reassignCamera}`, { method: 'PATCH', body: new URLSearchParams({ machine_id: sel.value || 'null' }) });
    });
  });
  list.querySelectorAll('[data-set-reference]').forEach(btn => btn.addEventListener('click', () => openReferenceModal(Number(btn.dataset.setReference))));
  list.querySelectorAll('[data-delete-camera]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this camera registration?')) return;
      await fetch(`/api/cameras/${btn.dataset.deleteCamera}`, { method: 'DELETE' });
    });
  });
}

function populateCameraMachineSelect() {
  const sel = document.querySelector('#add-camera-form select[name="machine_id"]');
  const current = sel.value;
  sel.innerHTML = `<option value="">Unassigned for now</option>` + state.machines.map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join('');
  sel.value = current;
}

document.getElementById('add-machine-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  await fetch('/api/machines', { method: 'POST', body: new FormData(form) });
  form.reset();
  form.max_error_percent.value = '2.0';
});

document.getElementById('add-camera-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const res = await fetch('/api/cameras', { method: 'POST', body: new FormData(form) });
  const data = await res.json();
  form.reset();
  showToast('Camera added', `API key: ${data.api_key}   |   HUB_URL: ${state.hubUrl || 'http://<this-computer-ip>:8000'}   (put both in the camera's secrets.h)`);
});

function openReferenceModal(cameraId) {
  state.referenceTargetCameraId = cameraId;
  const c = cameraById(cameraId);
  document.getElementById('reference-modal-title').textContent = `Set reference part — ${c ? c.name : ''}`;
  document.getElementById('reference-file-input').value = '';
  document.getElementById('reference-preview').classList.add('hidden');
  document.getElementById('reference-upload-btn').disabled = true;
  document.getElementById('reference-status').textContent = '';
  document.getElementById('admin-modal').classList.add('hidden');
  document.getElementById('reference-modal').classList.remove('hidden');
}
document.getElementById('reference-close').addEventListener('click', () => document.getElementById('reference-modal').classList.add('hidden'));
document.getElementById('reference-modal').addEventListener('click', (e) => { if (e.target.id === 'reference-modal') e.target.classList.add('hidden'); });

document.getElementById('reference-file-input').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const img = document.getElementById('reference-preview-img');
  img.src = URL.createObjectURL(file);
  document.getElementById('reference-preview').classList.remove('hidden');
  document.getElementById('reference-upload-btn').disabled = false;
});

document.getElementById('reference-upload-btn').addEventListener('click', async () => {
  const file = document.getElementById('reference-file-input').files[0];
  if (!file || !state.referenceTargetCameraId) return;
  const status = document.getElementById('reference-status');
  status.textContent = 'Measuring…';
  const fd = new FormData();
  fd.append('image', file);
  const res = await fetch(`/api/cameras/${state.referenceTargetCameraId}/reference`, { method: 'POST', body: fd });
  if (res.ok) {
    const data = await res.json();
    status.textContent = `Reference set: ${data.width_px.toFixed(0)} x ${data.height_px.toFixed(0)} px.`;
    setTimeout(() => document.getElementById('reference-modal').classList.add('hidden'), 900);
  } else {
    const err = await res.json().catch(() => ({}));
    status.textContent = `Failed: ${err.detail || 'could not measure the part in this photo.'}`;
  }
});

setInterval(() => { document.getElementById('clock').textContent = new Date().toLocaleTimeString([], { hour12: false }); }, 1000);
setInterval(() => { loadAll().catch(() => {}); }, 15000);

loadAll().then(connectWS);
