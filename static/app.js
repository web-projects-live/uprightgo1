"use strict";

// ── Exercises ────────────────────────────────────────────────────────────────
const EXERCISES = [
  { em:"🤸", ttl:"Chin Tucks",           dsc:"Corrects forward head posture. 10 reps, 3x daily.", q:"chin+tuck+exercise+forward+head+posture" },
  { em:"🧱", ttl:"Wall Angels",           dsc:"Opens shoulders and mobilises the thoracic spine.", q:"wall+angels+exercise+posture+shoulder" },
  { em:"💪", ttl:"Shoulder Blade Squeeze",dsc:"Strengthens mid-back to hold an upright position.", q:"shoulder+blade+squeeze+exercise+posture" },
  { em:"🙆", ttl:"Chest Opener",          dsc:"Stretches chest muscles shortened by hunching.",    q:"chest+stretch+posture+correction+desk" },
  { em:"🔄", ttl:"Thoracic Extension",    dsc:"Restores upper-back mobility lost from sitting.",   q:"thoracic+extension+exercise+upper+back" },
  { em:"🏋️", ttl:"Dead Bug (Core)",       dsc:"Builds the deep core strength for all-day posture.",q:"dead+bug+exercise+core+posture" },
];

const exGrid = document.getElementById("exGrid");
EXERCISES.forEach(ex => {
  const a = document.createElement("a");
  a.className = "ex-card";
  a.href      = `https://www.youtube.com/results?search_query=${ex.q}`;
  a.target    = "_blank"; a.rel = "noopener noreferrer";
  a.innerHTML = `<span class="ex-em">${ex.em}</span><span class="ex-ttl">${ex.ttl}</span>`
              + `<span class="ex-dsc">${ex.dsc}</span><span class="ex-cta">▶ Search on YouTube</span>`;
  exGrid.appendChild(a);
});

// ── Preset descriptions ───────────────────────────────────────────────────────
const PRESET_DESCS = {
  lenient: "Alerts after ~8s of slouching. 30s between alerts. Best for beginners.",
  normal:  "Alerts after ~5s of slouching. 15s between alerts. Recommended.",
  strict:  "Alerts after ~2.5s of slouching. 8s between alerts. Sharp feedback.",
  custom:  "Adjust the sliders below to your preference.",
};
const PRESET_VALS = {
  lenient: { window_s: 8.0,  threshold: 0.70, cooldown: 30 },
  normal:  { window_s: 5.0,  threshold: 0.60, cooldown: 15 },
  strict:  { window_s: 2.5,  threshold: 0.50, cooldown:  8 },
};

// ── State ────────────────────────────────────────────────────────────────────
let profiles       = [];
let activeProfile  = null;
let currentThreshold = 0.60;  // used to position pressure marker

// ── Profiles ─────────────────────────────────────────────────────────────────
async function loadProfiles() {
  try {
    const res = await fetch("/api/profiles");
    profiles  = await res.json();

    if (profiles.length === 0) {
      showWizard();
      return;
    }

    // Pick the first as active (server already does this, just sync UI)
    const statusRes = await fetch("/api/status");
    const status    = await statusRes.json();
    activeProfile   = status.profile || profiles[0];

    renderProfileDropdown();
    updateProfileBtn(activeProfile ? activeProfile.name : "—");
    await loadSettings();
  } catch (e) {
    console.error("loadProfiles", e);
  }
}

function renderProfileDropdown() {
  const list = document.getElementById("profileList");
  list.innerHTML = "";
  profiles.forEach(p => {
    const btn = document.createElement("button");
    btn.className = "drop-item" + (activeProfile && p.id === activeProfile.id ? " active" : "");
    btn.textContent = p.name;
    if (activeProfile && p.id === activeProfile.id) {
      const check = document.createElement("span");
      check.textContent = "✓"; check.style.color = "var(--accent)";
      btn.appendChild(check);
    }
    btn.addEventListener("click", () => switchProfile(p.id));
    list.appendChild(btn);
  });
}

function updateProfileBtn(name) {
  document.getElementById("profileBtnName").textContent = name || "—";
}

async function switchProfile(pid) {
  try {
    await fetch(`/api/profiles/${pid}/activate`, { method: "POST" });
    const p = profiles.find(x => x.id === pid);
    if (p) { activeProfile = p; updateProfileBtn(p.name); }
    closeProfileDrop();
    renderProfileDropdown();
    await loadSettings();
    await loadCoaching();
    await loadHistory();
    await loadAllTime();
  } catch (e) { console.error("switchProfile", e); }
}

// Profile dropdown toggle
const profileBtn  = document.getElementById("profileBtn");
const profileDrop = document.getElementById("profileDrop");
profileBtn.addEventListener("click", e => {
  e.stopPropagation();
  profileDrop.classList.toggle("hidden");
});
document.addEventListener("click", () => closeProfileDrop());
profileDrop.addEventListener("click", e => e.stopPropagation());
function closeProfileDrop() { profileDrop.classList.add("hidden"); }

// Add profile via dropdown
document.getElementById("dropAddProfile").addEventListener("click", () => {
  closeProfileDrop();
  openAddProfile();
});

// Add profile dialog
function openAddProfile() {
  document.getElementById("addProfileName").value = "";
  document.getElementById("addProfileOverlay").classList.remove("hidden");
}
document.getElementById("addProfileCancel").addEventListener("click", () => {
  document.getElementById("addProfileOverlay").classList.add("hidden");
});
document.getElementById("addProfileSave").addEventListener("click", async () => {
  const name = document.getElementById("addProfileName").value.trim();
  if (!name) return;
  try {
    const res = await fetch("/api/profiles", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const newProfile = await res.json();
    document.getElementById("addProfileOverlay").classList.add("hidden");
    profiles = await (await fetch("/api/profiles")).json();
    await switchProfile(newProfile.id);
  } catch (e) { console.error("addProfile", e); }
});

// ── Wizard ───────────────────────────────────────────────────────────────────
let wizSens = "normal";

function showWizard() {
  document.getElementById("wizardOverlay").classList.remove("hidden");
  document.getElementById("wizStep1").classList.remove("hidden");
  document.getElementById("wizStep2").classList.add("hidden");
  document.getElementById("wizStep3").classList.add("hidden");
}
function hideWizard() {
  document.getElementById("wizardOverlay").classList.add("hidden");
}

document.getElementById("wizNext1").addEventListener("click", () => {
  const name = document.getElementById("wizName").value.trim();
  if (!name) { document.getElementById("wizName").focus(); return; }
  document.getElementById("wizNameShow").textContent = name;
  document.getElementById("wizStep1").classList.add("hidden");
  document.getElementById("wizStep2").classList.remove("hidden");
});

document.querySelectorAll("#wizSensCards .sens-card").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#wizSensCards .sens-card").forEach(b => {
      b.classList.remove("active");
      b.querySelector("strong").textContent = b.dataset.sens.charAt(0).toUpperCase() + b.dataset.sens.slice(1);
    });
    wizSens = btn.dataset.sens;
    btn.classList.add("active");
    btn.querySelector("strong").textContent =
      btn.dataset.sens.charAt(0).toUpperCase() + btn.dataset.sens.slice(1) + " ✓";
  });
});

document.getElementById("wizNext2").addEventListener("click", () => {
  document.getElementById("wizStep2").classList.add("hidden");
  document.getElementById("wizStep3").classList.remove("hidden");
});

document.getElementById("wizDone").addEventListener("click", async () => {
  const name = document.getElementById("wizName").value.trim();
  if (!name) return;
  try {
    const res  = await fetch("/api/profiles", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const prof = await res.json();
    await fetch(`/api/profiles/${prof.id}/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sensitivity: wizSens }),
    });
    profiles = await (await fetch("/api/profiles")).json();
    activeProfile = profiles.find(p => p.id === prof.id) || profiles[0];
    updateProfileBtn(activeProfile.name);
    renderProfileDropdown();
    await loadSettings();
    await loadCoaching();
    hideWizard();
  } catch (e) { console.error("wizDone", e); }
});

// ── Mode ─────────────────────────────────────────────────────────────────────
document.querySelectorAll(".mode-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    const mode = btn.dataset.mode;
    await fetch("/api/mode", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  });
});

// ── Settings ─────────────────────────────────────────────────────────────────
const windowRange    = document.getElementById("windowRange");
const thresholdRange = document.getElementById("thresholdRange");
const cooldownRange  = document.getElementById("cooldownRange");
const goalRange      = document.getElementById("goalRange");

windowRange.addEventListener("input",    () => { document.getElementById("windowDisplay").textContent    = windowRange.value; });
thresholdRange.addEventListener("input", () => { document.getElementById("thresholdDisplay").textContent = thresholdRange.value; });
cooldownRange.addEventListener("input",  () => { document.getElementById("cooldownDisplay").textContent  = cooldownRange.value; });
goalRange.addEventListener("input",      () => { document.getElementById("goalDisplay").textContent      = goalRange.value; });

document.querySelectorAll(".preset-btn").forEach(btn => {
  btn.addEventListener("click", () => applyPreset(btn.dataset.preset));
});

function applyPreset(preset) {
  document.querySelectorAll(".preset-btn").forEach(b => b.classList.toggle("active", b.dataset.preset === preset));
  document.getElementById("presetDesc").textContent = PRESET_DESCS[preset] || "";
  const isCustom = preset === "custom";
  document.getElementById("customGroup").style.opacity = "1";

  if (!isCustom && PRESET_VALS[preset]) {
    const v = PRESET_VALS[preset];
    windowRange.value    = v.window_s;   document.getElementById("windowDisplay").textContent    = v.window_s;
    thresholdRange.value = v.threshold * 100; document.getElementById("thresholdDisplay").textContent = v.threshold * 100;
    cooldownRange.value  = v.cooldown;   document.getElementById("cooldownDisplay").textContent  = v.cooldown;
  }
  // Disable sliders for non-custom presets
  [windowRange, thresholdRange, cooldownRange].forEach(r => r.disabled = !isCustom);
}

async function loadSettings() {
  if (!activeProfile) return;
  try {
    const res = await fetch(`/api/profiles/${activeProfile.id}/settings`);
    const s   = await res.json();
    const sens = s.sensitivity || "normal";

    applyPreset(sens);
    windowRange.value    = s.slouch_window_s   || 5;
    thresholdRange.value = (s.slouch_threshold || 0.6) * 100;
    cooldownRange.value  = s.buzz_cooldown     || 15;
    goalRange.value      = s.daily_goal_min    || 20;
    document.getElementById("windowDisplay").textContent    = windowRange.value;
    document.getElementById("thresholdDisplay").textContent = thresholdRange.value;
    document.getElementById("cooldownDisplay").textContent  = cooldownRange.value;
    document.getElementById("goalDisplay").textContent      = goalRange.value;

    currentThreshold = parseFloat(thresholdRange.value) / 100;
    updatePressureMarker();
  } catch (e) { console.error("loadSettings", e); }
}

document.getElementById("saveBtn").addEventListener("click", async () => {
  if (!activeProfile) return;
  const preset    = document.querySelector(".preset-btn.active")?.dataset.preset || "normal";
  const payload   = {
    sensitivity:      preset,
    buzz_cooldown:    parseFloat(cooldownRange.value),
    slouch_window_s:  parseFloat(windowRange.value),
    slouch_threshold: parseFloat(thresholdRange.value) / 100,
    daily_goal_min:   parseInt(goalRange.value),
  };
  await fetch(`/api/profiles/${activeProfile.id}/settings`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  currentThreshold = payload.slouch_threshold;
  updatePressureMarker();
  const btn = document.getElementById("saveBtn");
  btn.textContent = "Saved!"; btn.className = "btn saved";
  setTimeout(() => { btn.textContent = "Save settings"; btn.className = "btn"; }, 2000);
  await loadCoaching(); // refresh goal display
});

// ── Coaching ─────────────────────────────────────────────────────────────────
async function loadCoaching() {
  try {
    const res  = await fetch("/api/coaching");
    if (!res.ok) return;
    const d    = await res.json();

    // Phase badge
    const badge = document.getElementById("phaseBadge");
    badge.textContent = d.phase.charAt(0).toUpperCase() + d.phase.slice(1);
    badge.className   = `phase-badge ${d.phase}`;

    // Graduation segments
    const segs = document.querySelectorAll(".grad-seg");
    segs.forEach((seg, i) => seg.classList.toggle("done", i < d.graduation_days));
    document.getElementById("gradDays").textContent = d.graduation_days;

    // Mini-stats
    document.getElementById("streakVal").textContent = d.streak_days;
    document.getElementById("avgVal").textContent    = d.seven_day_avg + "%";
    document.getElementById("bestVal").textContent   = d.best_score + "%";

    const trendEl = document.getElementById("trendVal");
    if (d.trend === "improving") { trendEl.textContent = "↑"; trendEl.className = "c-val trend-up"; }
    else if (d.trend === "declining") { trendEl.textContent = "↓"; trendEl.className = "c-val trend-down"; }
    else { trendEl.textContent = "→"; trendEl.className = "c-val trend-flat"; }

    // Today's goal
    document.getElementById("todayMin").textContent = d.today_minutes;
    document.getElementById("goalMin").textContent  = d.daily_goal_min;
    const pct = Math.min(100, d.today_minutes / d.daily_goal_min * 100);
    document.getElementById("goalFill").style.width = pct + "%";

    document.getElementById("tipText").textContent = d.tip;
  } catch (e) { console.error("loadCoaching", e); }
}

// ── History chart ─────────────────────────────────────────────────────────────
let chart = null;
function scoreColor(s) {
  return s >= 70 ? { bg:"#00d4aa44", bd:"#00d4aa" }
       : s >= 40 ? { bg:"#ffa50244", bd:"#ffa502" }
                 : { bg:"#ff475744", bd:"#ff4757" };
}
async function loadHistory() {
  try {
    const rows = await (await fetch("/api/history")).json();
    const labels = rows.map(r => r.date.slice(5));
    const scores = rows.map(r => r.score);
    const ctx    = document.getElementById("historyChart").getContext("2d");
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label:"Posture Score %", data: scores,
        backgroundColor: scores.map(s => scoreColor(s).bg),
        borderColor:     scores.map(s => scoreColor(s).bd),
        borderWidth: 2, borderRadius: 6 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { min:0, max:100, ticks:{ color:"#8892a4", callback: v => v+"%" }, grid:{ color:"#ffffff0d" } },
          x: { ticks:{ color:"#8892a4" }, grid:{ display:false } }
        }
      }
    });
    const tbody = document.getElementById("historyBody");
    tbody.innerHTML = "";
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">No history yet — start a session!</td></tr>';
      return;
    }
    rows.forEach(r => {
      const c = scoreColor(r.score).bd;
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.date}</td><td style="color:${c};font-weight:600">${r.score}%</td>`
                   + `<td>${r.duration_minutes}</td><td>${r.slouch_count}</td>`;
      tbody.appendChild(tr);
    });
  } catch (e) { console.error("loadHistory", e); }
}

async function loadAllTime() {
  try {
    const d = await (await fetch("/api/alltime")).json();
    document.getElementById("atDays").textContent  = d.days;
    document.getElementById("atHours").textContent = d.total_hours + "h";
    document.getElementById("atScore").textContent = d.avg_score + "%";
  } catch (e) { console.error("loadAllTime", e); }
}

// ── Pressure bar helpers ──────────────────────────────────────────────────────
function updatePressureMarker() {
  const marker = document.getElementById("pressureMarker");
  marker.style.left = (currentThreshold * 100) + "%";
}

function updatePressureBar(ratio) {
  const pct  = Math.round(ratio * 100);
  const fill = document.getElementById("pressureFill");
  fill.style.width      = pct + "%";
  fill.style.background = ratio >= currentThreshold ? "var(--bad)" : "var(--good)";
  document.getElementById("pressurePct").textContent = pct + "%";
}

// ── SSE live stream ───────────────────────────────────────────────────────────
const statusDot    = document.getElementById("statusDot");
const statusText   = document.getElementById("statusText");
const postureRing  = document.getElementById("postureRing");
const postureIcon  = document.getElementById("postureIcon");
const postureLabel = document.getElementById("postureLabel");

function applyLiveState(d) {
  statusDot.className  = "dot " + (d.connected ? "ok" : "err");
  statusText.textContent = d.connected ? "Connected" : "Searching…";

  postureRing.className = "posture-ring " + (d.connected ? d.posture : "");
  if (!d.connected) {
    postureIcon.textContent  = "📡"; postureLabel.textContent = "Searching for device…";
  } else if (d.posture === "good") {
    postureIcon.textContent  = "✓";  postureLabel.textContent = "Great posture!";
  } else if (d.posture === "slouching") {
    postureIcon.textContent  = "!";  postureLabel.textContent = "Sit up straight";
  } else {
    postureIcon.textContent  = "?";  postureLabel.textContent = "Calibrating…";
  }

  document.getElementById("scoreValue").textContent    = d.connected ? d.score + "%" : "—";
  document.getElementById("durationValue").textContent = d.connected ? d.total_minutes + "m" : "—";
  document.getElementById("slouchValue").textContent   = d.connected ? d.slouch_count : "—";

  updatePressureBar(d.slouch_ratio || 0);

  // Sync mode buttons
  if (d.mode) {
    document.querySelectorAll(".mode-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === d.mode));
  }
  // Sync profile name in header
  if (d.profile_name) updateProfileBtn(d.profile_name);
}

const evtSource = new EventSource("/api/stream");
evtSource.onmessage = e => { try { applyLiveState(JSON.parse(e.data)); } catch {} };
evtSource.onerror   = () => { statusDot.className = "dot err"; statusText.textContent = "App offline"; };

// ── Init ──────────────────────────────────────────────────────────────────────
(async () => {
  updatePressureMarker();
  await loadProfiles();
  await loadCoaching();
  await loadHistory();
  await loadAllTime();
  setInterval(async () => { await loadCoaching(); await loadHistory(); await loadAllTime(); }, 5 * 60 * 1000);
})();
