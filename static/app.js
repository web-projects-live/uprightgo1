"use strict";

// ── Exercise data ─────────────────────────────────────────────────────────────
const EXERCISES = [
  {
    emoji: "🤸",
    title: "Chin Tucks",
    desc:  "Corrects forward head posture. 10 reps, 3× daily.",
    q:     "chin+tuck+exercise+forward+head+posture+correction"
  },
  {
    emoji: "🧱",
    title: "Wall Angels",
    desc:  "Opens shoulders and mobilises the thoracic spine.",
    q:     "wall+angels+exercise+posture+upper+back"
  },
  {
    emoji: "💪",
    title: "Shoulder Blade Squeeze",
    desc:  "Strengthens mid-back to hold an upright position.",
    q:     "shoulder+blade+squeeze+exercise+posture+correction"
  },
  {
    emoji: "🙆",
    title: "Chest Opener",
    desc:  "Stretches chest muscles shortened by hunching forward.",
    q:     "chest+stretch+posture+correction+desk+worker"
  },
  {
    emoji: "🔄",
    title: "Thoracic Extension",
    desc:  "Restores upper-back mobility lost from prolonged sitting.",
    q:     "thoracic+extension+exercise+upper+back+mobility"
  },
  {
    emoji: "🏋️",
    title: "Dead Bug (Core)",
    desc:  "Builds the deep core strength that keeps you upright all day.",
    q:     "dead+bug+exercise+core+posture+stability"
  }
];

// Render exercise cards
const grid = document.getElementById("exercisesGrid");
EXERCISES.forEach(ex => {
  const a = document.createElement("a");
  a.className = "ex-card";
  a.href      = `https://www.youtube.com/results?search_query=${ex.q}`;
  a.target    = "_blank";
  a.rel       = "noopener noreferrer";
  a.innerHTML =
    `<span class="ex-emoji">${ex.emoji}</span>` +
    `<span class="ex-title">${ex.title}</span>` +
    `<span class="ex-desc">${ex.desc}</span>` +
    `<span class="ex-cta">▶ Search on YouTube</span>`;
  grid.appendChild(a);
});

// ── History chart ─────────────────────────────────────────────────────────────
let chart = null;

function scoreColor(s) {
  if (s >= 70) return { bg: "#00d4aa44", border: "#00d4aa" };
  if (s >= 40) return { bg: "#ffa50244", border: "#ffa502" };
  return             { bg: "#ff475744", border: "#ff4757" };
}

function renderHistory(rows) {
  const labels  = rows.map(r => r.date.slice(5));
  const scores  = rows.map(r => r.score);
  const bgCol   = scores.map(s => scoreColor(s).bg);
  const brdCol  = scores.map(s => scoreColor(s).border);

  const ctx = document.getElementById("historyChart").getContext("2d");
  if (chart) chart.destroy();
  chart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Posture Score %",
        data: scores,
        backgroundColor: bgCol,
        borderColor: brdCol,
        borderWidth: 2,
        borderRadius: 6,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: {
          min: 0, max: 100,
          ticks: { color: "#8892a4", callback: v => v + "%" },
          grid:  { color: "#ffffff0d" },
        },
        x: {
          ticks: { color: "#8892a4" },
          grid:  { display: false },
        }
      }
    }
  });

  const tbody = document.getElementById("historyBody");
  tbody.innerHTML = "";
  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="empty">No history yet — start a session!</td></tr>';
    return;
  }
  rows.forEach(r => {
    const c  = scoreColor(r.score).border;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${r.date}</td>` +
      `<td style="color:${c};font-weight:600">${r.score}%</td>` +
      `<td>${r.duration_minutes}</td>` +
      `<td>${r.slouch_count}</td>`;
    tbody.appendChild(tr);
  });
}

async function loadHistory() {
  try {
    const res = await fetch("/api/history");
    renderHistory(await res.json());
  } catch (e) {
    console.error("History load failed", e);
  }
}

async function loadAllTime() {
  try {
    const res = await fetch("/api/alltime");
    const d   = await res.json();
    document.getElementById("atDays").textContent  = d.days;
    document.getElementById("atHours").textContent = d.total_hours + "h";
    document.getElementById("atScore").textContent = d.avg_score + "%";
  } catch (e) {
    console.error("All-time load failed", e);
  }
}

// ── Live posture via SSE ──────────────────────────────────────────────────────
const statusDot    = document.getElementById("statusDot");
const statusText   = document.getElementById("statusText");
const postureRing  = document.getElementById("postureRing");
const postureIcon  = document.getElementById("postureIcon");
const postureLabel = document.getElementById("postureLabel");
const scoreValue   = document.getElementById("scoreValue");
const durationVal  = document.getElementById("durationValue");
const slouchValue  = document.getElementById("slouchValue");

function applyState(d) {
  // Connection indicator
  statusDot.className  = "dot " + (d.connected ? "ok" : "err");
  statusText.textContent = d.connected ? "Connected" : "Searching…";

  // Posture ring
  const cls = d.connected ? d.posture : "";
  postureRing.className = "posture-ring " + cls;

  if (!d.connected) {
    postureIcon.textContent  = "📡";
    postureLabel.textContent = "Searching for device…";
  } else if (d.posture === "good") {
    postureIcon.textContent  = "✓";
    postureLabel.textContent = "Great posture!";
  } else if (d.posture === "slouching") {
    postureIcon.textContent  = "!";
    postureLabel.textContent = "Sit up straight";
  } else {
    postureIcon.textContent  = "?";
    postureLabel.textContent = "Calibrating…";
  }

  // Stats
  scoreValue.textContent  = d.connected ? d.score + "%"          : "—";
  durationVal.textContent = d.connected ? d.total_minutes + "m"  : "—";
  slouchValue.textContent = d.connected ? d.slouch_count         : "—";
}

const evtSource = new EventSource("/api/stream");
evtSource.onmessage = e => { try { applyState(JSON.parse(e.data)); } catch {} };
evtSource.onerror   = ()  => {
  statusDot.className    = "dot err";
  statusText.textContent = "App offline";
};

// ── Settings ──────────────────────────────────────────────────────────────────
const cooldownRange   = document.getElementById("cooldownRange");
const cooldownDisplay = document.getElementById("cooldownDisplay");
const saveBtn         = document.getElementById("saveBtn");

async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const s   = await res.json();
    cooldownRange.value        = s.buzz_cooldown;
    cooldownDisplay.textContent = s.buzz_cooldown;
  } catch (e) {
    console.error("Settings load failed", e);
  }
}

cooldownRange.addEventListener("input", () => {
  cooldownDisplay.textContent = cooldownRange.value;
});

saveBtn.addEventListener("click", async () => {
  await fetch("/api/settings", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ buzz_cooldown: parseFloat(cooldownRange.value) }),
  });
  saveBtn.textContent = "Saved!";
  saveBtn.className   = "btn saved";
  setTimeout(() => { saveBtn.textContent = "Save settings"; saveBtn.className = "btn"; }, 2000);
});

// ── Init ──────────────────────────────────────────────────────────────────────
loadHistory();
loadAllTime();
loadSettings();
setInterval(() => { loadHistory(); loadAllTime(); }, 5 * 60 * 1000);
