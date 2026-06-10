'use strict';

// ── Boot: detect AP vs STA mode ───────────────────────────────────────────────
(async function boot() {
  let wifi;
  try {
    const r = await fetch('/api/wifi');
    wifi = await r.json();
  } catch (_) {
    // Can't reach API — assume main dashboard (STA mode, page just loaded)
    showMain();
    return;
  }

  if (wifi.mode === 'ap') {
    showSetup();
  } else {
    showMain();
    initDashboard(wifi);
  }
})();

function showSetup() {
  document.getElementById('setup-screen').style.display = 'flex';
}
function showMain() {
  document.getElementById('main-screen').style.display = 'block';
}

// ── Setup portal ──────────────────────────────────────────────────────────────
async function submitSetup() {
  const ssid = document.getElementById('setup-ssid').value.trim();
  const pass = document.getElementById('setup-pass').value;
  const errEl = document.getElementById('setup-error');

  if (!ssid) {
    errEl.textContent = 'Please enter a network name.';
    errEl.style.display = 'block';
    return;
  }
  errEl.style.display = 'none';
  document.getElementById('setup-form-wrap').style.display = 'none';
  document.getElementById('setup-connecting').style.display = 'block';

  try {
    await fetch('/api/wifi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid, pass }),
    });
  } catch (_) {
    // Device reboots and drops the connection — fetch will throw. That's OK.
  }

  // Show success state with the addresses the user should save
  document.getElementById('setup-connecting').style.display = 'none';
  document.getElementById('setup-success').style.display = 'block';

  const mdnsHref = 'http://uprightgo.local';
  // We don't know the IP yet (device is rebooting) — tell them mDNS is the safe bet
  const mdnsLink = document.getElementById('setup-mdns-link');
  mdnsLink.href        = mdnsHref;
  mdnsLink.textContent = mdnsHref;

  const ipLink = document.getElementById('setup-ip-link');
  ipLink.href        = '#';
  ipLink.textContent = 'IP address shown on serial / check your router';
  ipLink.style.opacity = '0.6';
  ipLink.style.pointerEvents = 'none';
}

// ── Dashboard init ────────────────────────────────────────────────────────────
let g_settings = {};
let g_chart    = null;

async function initDashboard(wifi) {
  updateWifiChip(wifi);
  await loadSettings();
  await loadHistory();
  startSSE();
}

// ── SSE live updates ──────────────────────────────────────────────────────────
function startSSE() {
  const es = new EventSource('/api/stream');
  es.addEventListener('state', e => {
    try { applyState(JSON.parse(e.data)); } catch(_) {}
  });
  es.onerror = () => {
    // EventSource auto-reconnects; no action needed
  };
}

// ── State rendering ───────────────────────────────────────────────────────────
function applyState(s) {
  // BLE status chip
  const bleChip = document.getElementById('hdr-ble');
  if (s.connected) {
    bleChip.textContent = '● Connected';
    bleChip.className   = 'status-chip connected';
  } else {
    bleChip.textContent = '● Scanning…';
    bleChip.className   = 'status-chip';
  }

  // Posture ring
  const ring  = document.getElementById('posture-ring');
  const icon  = document.getElementById('ring-icon');
  const label = document.getElementById('ring-label');

  if (!s.connected) {
    ring.className  = 'posture-ring state-waiting';
    icon.textContent  = '📡';
    label.textContent = 'Searching for device…';
  } else if (s.mode !== 'desk') {
    ring.className  = 'posture-ring state-idle';
    icon.textContent  = s.mode === 'break' ? '☕' : '🚶';
    label.textContent = s.mode === 'break' ? 'On break' : 'Moving';
  } else if (s.is_slouching) {
    ring.className  = 'posture-ring state-slouch';
    icon.textContent  = '!';
    label.textContent = 'Sit up straight!';
  } else {
    ring.className  = 'posture-ring state-good';
    icon.textContent  = '✓';
    label.textContent = 'Good posture';
  }

  // Stats
  const score = s.total_seconds > 0
    ? Math.round((s.good_seconds / s.total_seconds) * 100) : 0;
  document.getElementById('stat-score').textContent =
    s.total_seconds > 0 ? score + '%' : '—';
  document.getElementById('stat-duration').textContent =
    s.total_seconds > 0 ? fmtDuration(s.total_seconds) : '—';
  document.getElementById('stat-alerts').textContent =
    s.total_seconds > 0 ? s.slouch_count : '—';

  // Pressure bar
  const pct    = Math.round((s.slouch_ratio || 0) * 100);
  const thresh = Math.round((g_settings.threshold || 0.6) * 100);
  const fill   = document.getElementById('pressure-fill');
  const marker = document.getElementById('pressure-marker');
  fill.style.width      = pct + '%';
  fill.style.background = pct >= thresh ? '#e85' : '#4c8';
  marker.style.left     = thresh + '%';

  // Mode buttons
  document.querySelectorAll('.mode-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.mode === s.mode);
  });

  // Wi-Fi status line in settings panel
  if (s.wifi_connected) {
    document.getElementById('wifi-status-line').innerHTML =
      `<span class="wifi-ok">✓ Connected to <strong>${escHtml(s.wifi_ssid)}</strong> —
       <a href="http://uprightgo.local" target="_blank">uprightgo.local</a> · ${escHtml(s.ip)}</span>`;
  } else {
    document.getElementById('wifi-status-line').innerHTML =
      '<span class="wifi-warn">⚠ Not connected to a network</span>';
  }
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    g_settings = await r.json();
    applySettingsToUI(g_settings);
  } catch(_) {}
}

function applySettingsToUI(s) {
  g_settings = s;
  // Preset buttons
  document.querySelectorAll('.preset-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.preset === s.sensitivity);
  });
  document.getElementById('custom-settings').style.display =
    s.sensitivity === 'custom' ? 'block' : 'none';

  // Sliders
  setSlider('sl-angle',  'lbl-angle',  s.slouch_angle,   v => v + '°');
  setSlider('sl-window', 'lbl-window', s.window_s,       v => v + ' s');
  setSlider('sl-thresh', 'lbl-thresh', Math.round((s.threshold||0.6)*100), v => v + '%');
  setSlider('sl-cool',   'lbl-cool',   s.cooldown_s,     v => v + ' s');
  setSlider('sl-goal',   'lbl-goal',   s.daily_goal_min, v => v + ' min');
}

function setSlider(id, lblId, val, fmt) {
  const el  = document.getElementById(id);
  const lbl = document.getElementById(lblId);
  if (el)  el.value      = val;
  if (lbl) lbl.textContent = fmt(val);
}

async function applyPreset(preset) {
  try {
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sensitivity: preset }),
    });
    const s = await r.json();
    applySettingsToUI(s);
  } catch(_) {}
}

async function saveCustomSettings() {
  const body = {
    sensitivity:    'custom',
    slouch_angle:   +document.getElementById('sl-angle').value,
    window_s:       +document.getElementById('sl-window').value,
    threshold:       +document.getElementById('sl-thresh').value / 100,
    cooldown_s:     +document.getElementById('sl-cool').value,
    daily_goal_min: +document.getElementById('sl-goal').value,
  };
  try {
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    g_settings = await r.json();
  } catch(_) {}
}

// ── Mode ──────────────────────────────────────────────────────────────────────
async function setMode(mode) {
  await fetch('/api/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  }).catch(() => {});
}

// ── Session reset ─────────────────────────────────────────────────────────────
async function resetSession() {
  await fetch('/api/reset', { method: 'POST' }).catch(() => {});
  await loadHistory();
}

// ── Wi-Fi settings ────────────────────────────────────────────────────────────
async function saveWifi() {
  const ssid = document.getElementById('wifi-ssid').value.trim();
  const pass = document.getElementById('wifi-pass').value;
  if (!ssid) { alert('Enter a network name.'); return; }
  if (!confirm(`Connect to "${ssid}" and reboot?`)) return;
  await fetch('/api/wifi', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ssid, pass }),
  }).catch(() => {});
  document.getElementById('wifi-status-line').innerHTML =
    '<span>Rebooting… reconnect to your network and visit <a href="http://uprightgo.local">uprightgo.local</a></span>';
}

async function forgetWifi() {
  if (!confirm('Forget this network and restart setup?')) return;
  await fetch('/api/wifi', { method: 'DELETE' }).catch(() => {});
}

// ── History chart ─────────────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const r = await fetch('/api/history');
    const records = await r.json();
    renderHistory(records);
  } catch(_) {}
}

function renderHistory(records) {
  const last7 = records.slice(-7);
  const labels = last7.map(r => r.date);
  const scores = last7.map(r => Math.round(r.score));
  const colors = scores.map(s =>
    s >= 70 ? '#4c8' : s >= 40 ? '#ea5' : '#e55');

  if (g_chart) g_chart.destroy();
  const ctx = document.getElementById('history-chart').getContext('2d');
  g_chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data: scores,
        backgroundColor: colors,
        borderRadius: 4,
      }]
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        y: {
          min: 0, max: 100,
          ticks: { color: '#aaa', callback: v => v + '%' },
          grid:  { color: 'rgba(255,255,255,.07)' },
        },
        x: { ticks: { color: '#aaa' }, grid: { display: false } },
      }
    }
  });

  const tbody = document.querySelector('#history-table tbody');
  tbody.innerHTML = records.slice(-14).reverse().map(r => {
    const cls = r.score >= 70 ? 'good' : r.score >= 40 ? 'mid' : 'bad';
    return `<tr>
      <td>${escHtml(r.date)}</td>
      <td class="${cls}">${Math.round(r.score)}%</td>
      <td>${fmtDuration(r.duration * 60)}</td>
      <td>${r.slouch_count}</td>
    </tr>`;
  }).join('');
}

// ── Wi-Fi chip (header) ───────────────────────────────────────────────────────
function updateWifiChip(wifi) {
  const chip = document.getElementById('hdr-wifi');
  if (wifi.mode === 'sta') {
    chip.textContent = '📶 ' + escHtml(wifi.ssid);
    chip.title       = wifi.ip + ' · uprightgo.local';
  } else {
    chip.textContent = '📶 Setup AP';
  }
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function fmtDuration(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function updateLabel(id, val) {
  document.getElementById(id).textContent = val;
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
