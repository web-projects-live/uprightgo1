"use strict";

// ── Preset config ─────────────────────────────────────────────────────────────
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

let currentThreshold = 0.60;

// ── Settings ──────────────────────────────────────────────────────────────────
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
  if (!isCustom && PRESET_VALS[preset]) {
    const v = PRESET_VALS[preset];
    windowRange.value    = v.window_s;
    thresholdRange.value = v.threshold * 100;
    cooldownRange.value  = v.cooldown;
    document.getElementById("windowDisplay").textContent    = v.window_s;
    document.getElementById("thresholdDisplay").textContent = v.threshold * 100;
    document.getElementById("cooldownDisplay").textContent  = v.cooldown;
  }
  [windowRange, thresholdRange, cooldownRange].forEach(r => r.disabled = !isCustom);
}

async function loadSettings() {
  try {
    const s    = await (await fetch("/api/settings")).json();
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
  const preset  = document.querySelector(".preset-btn.active")?.dataset.preset || "normal";
  const payload = {
    sensitivity:      preset,
    buzz_cooldown:    parseFloat(cooldownRange.value),
    slouch_window_s:  parseFloat(windowRange.value),
    slouch_threshold: parseFloat(thresholdRange.value) / 100,
    daily_goal_min:   parseInt(goalRange.value),
  };
  await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  currentThreshold = payload.slouch_threshold;
  updatePressureMarker();
  const btn = document.getElementById("saveBtn");
  btn.textContent = "Saved!"; btn.className = "btn saved";
  setTimeout(() => { btn.textContent = "Save settings"; btn.className = "btn"; }, 2000);
  await loadCoaching();
});

// ── Mode ──────────────────────────────────────────────────────────────────────
document.querySelectorAll(".mode-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    await fetch("/api/mode", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: btn.dataset.mode }),
    });
    document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
  });
});

// ── Coaching ──────────────────────────────────────────────────────────────────
async function loadCoaching() {
  try {
    const d = await (await fetch("/api/coaching")).json();
    const badge = document.getElementById("phaseBadge");
    badge.textContent = d.phase.charAt(0).toUpperCase() + d.phase.slice(1);
    badge.className   = "phase-badge " + d.phase;
    document.querySelectorAll(".grad-seg").forEach((seg, i) => seg.classList.toggle("done", i < d.graduation_days));
    document.getElementById("gradDays").textContent  = d.graduation_days;
    document.getElementById("streakVal").textContent = d.streak_days;
    document.getElementById("avgVal").textContent    = d.seven_day_avg + "%";
    document.getElementById("bestVal").textContent   = d.best_score + "%";
    const trendEl = document.getElementById("trendVal");
    if (d.trend === "improving")       { trendEl.textContent = "↑"; trendEl.className = "c-val trend-up"; }
    else if (d.trend === "declining")  { trendEl.textContent = "↓"; trendEl.className = "c-val trend-down"; }
    else                               { trendEl.textContent = "→"; trendEl.className = "c-val trend-flat"; }
    document.getElementById("todayMin").textContent = d.today_minutes;
    document.getElementById("goalMin").textContent  = d.daily_goal_min;
    const pct = Math.min(100, d.today_minutes / d.daily_goal_min * 100);
    document.getElementById("goalFill").style.width = pct + "%";
    document.getElementById("tipText").textContent  = d.tip;
  } catch (e) { console.error("loadCoaching", e); }
}

// ── History ───────────────────────────────────────────────────────────────────
let chart = null;
function scoreColor(s) {
  return s >= 70 ? { bg: "#00d4aa44", bd: "#00d4aa" }
       : s >= 40 ? { bg: "#ffa50244", bd: "#ffa502" }
                 : { bg: "#ff475744", bd: "#ff4757" };
}
async function loadHistory() {
  try {
    const rows   = await (await fetch("/api/history")).json();
    const labels = rows.map(r => r.date.slice(5));
    const scores = rows.map(r => r.score);
    const ctx    = document.getElementById("historyChart").getContext("2d");
    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "bar",
      data: { labels, datasets: [{ label: "Posture Score %", data: scores,
        backgroundColor: scores.map(s => scoreColor(s).bg),
        borderColor:     scores.map(s => scoreColor(s).bd),
        borderWidth: 2, borderRadius: 6 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { min: 0, max: 100, ticks: { color: "#8892a4", callback: v => v + "%" }, grid: { color: "#ffffff0d" } },
          x: { ticks: { color: "#8892a4" }, grid: { display: false } }
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
      const c  = scoreColor(r.score).bd;
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

// ── Pressure bar ──────────────────────────────────────────────────────────────
function updatePressureMarker() {
  document.getElementById("pressureMarker").style.left = (currentThreshold * 100) + "%";
}
function updatePressureBar(ratio) {
  const pct  = Math.round(ratio * 100);
  const fill = document.getElementById("pressureFill");
  fill.style.width      = pct + "%";
  fill.style.background = ratio >= currentThreshold ? "var(--bad)" : "var(--good)";
  document.getElementById("pressurePct").textContent = pct + "%";
}

// ── Live polling (replaces SSE — simpler on ESP32) ────────────────────────────
const statusDot    = document.getElementById("statusDot");
const statusText   = document.getElementById("statusText");
const postureRing  = document.getElementById("postureRing");
const postureIcon  = document.getElementById("postureIcon");
const postureLabel = document.getElementById("postureLabel");

async function pollStatus() {
  try {
    const d = await (await fetch("/api/status")).json();
    statusDot.className    = "dot " + (d.connected ? "ok" : "err");
    statusText.textContent = d.connected ? "Connected" : "Searching…";
    postureRing.className  = "posture-ring " + (d.connected ? d.posture : "");
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
    if (d.mode) {
      document.querySelectorAll(".mode-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === d.mode));
    }
  } catch (e) {
    statusDot.className    = "dot err";
    statusText.textContent = "Device offline";
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
(async () => {
  updatePressureMarker();
  await loadSettings();
  await loadCoaching();
  await loadHistory();
  await loadAllTime();
  setInterval(pollStatus, 500);
  setInterval(async () => { await loadCoaching(); await loadHistory(); await loadAllTime(); }, 5 * 60 * 1000);
})();
