/*
Claude's Unconscious
Copyright 2026 Otis Ranson. Licensed under the Apache License, Version 2.0.
*/

const SETTINGS_KEYS = ["anthropic_api_key", "openweather_api_key", "weather_city", "news_rss_url"];

let allEntries = [];
let activeFilter = "";

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

// ---- startup banner -------------------------------------------------------

async function loadStartup() {
  const banner = document.getElementById("startup-banner");
  const messageEl = document.getElementById("startup-message");
  try {
    const result = await api("/startup");
    banner.hidden = false;
    messageEl.textContent = result.ran
      ? "The unconscious woke up before you spoke."
      : result.message;
    if (result.ran && result.message) {
      messageEl.title = result.message;
    }
  } catch (e) {
    banner.hidden = true;
  }
}

// ---- corpus strip: complete image history, most recent on the far left --

function renderCorpusStrip() {
  const stripEl = document.getElementById("corpus-strip");
  stripEl.innerHTML = "";
  [...allEntries].reverse().forEach((entry) => {
    const img = document.createElement("img");
    img.src = entry.image_url;
    img.alt = entry.claude_caption || entry.source;
    img.title = `${entry.source}: ${entry.claude_caption || ""}`;
    img.addEventListener("click", () => window.open(entry.image_url, "_blank"));
    stripEl.appendChild(img);
  });
}

// ---- timeline ---------------------------------------------------------

function badge(text, cls) {
  const span = document.createElement("span");
  span.className = `badge ${cls}`;
  span.textContent = text;
  return span;
}

function renderEntryCard(entry) {
  const card = document.createElement("div");
  card.className = "entry-card" + (entry.trigger === "startup" ? " startup-arrival" : "");

  const img = document.createElement("img");
  img.src = entry.image_url;
  img.alt = entry.claude_caption || entry.source;
  img.addEventListener("click", () => window.open(entry.image_url, "_blank"));
  card.appendChild(img);

  const body = document.createElement("div");
  body.className = "entry-body";

  const meta = document.createElement("div");
  meta.className = "entry-meta";
  meta.appendChild(badge(entry.source, `source-${entry.source}`));
  if (entry.trigger === "startup") {
    const b = badge("arrived before you", "trigger-startup");
    meta.appendChild(b);
  }
  const time = document.createElement("span");
  time.textContent = new Date(entry.timestamp).toLocaleString();
  meta.appendChild(time);
  const grammarTag = document.createElement("span");
  grammarTag.textContent = `grammar v${entry.grammar_version}`;
  meta.appendChild(grammarTag);
  body.appendChild(meta);

  if (entry.prompt) {
    const p = document.createElement("p");
    p.className = "entry-prompt";
    p.textContent = entry.prompt;
    body.appendChild(p);
  }

  if (entry.claude_caption) {
    const cap = document.createElement("p");
    cap.className = "entry-caption";
    cap.textContent = entry.claude_caption;
    body.appendChild(cap);
  }

  const annotationWrap = document.createElement("div");
  annotationWrap.className = "entry-annotation";

  if (entry.user_annotation) {
    const saved = document.createElement("p");
    saved.className = "saved";
    saved.textContent = `Your read: ${entry.user_annotation}`;
    annotationWrap.appendChild(saved);
  }

  const textarea = document.createElement("textarea");
  textarea.rows = 2;
  textarea.placeholder = "Add your own interpretation…";
  annotationWrap.appendChild(textarea);

  const actions = document.createElement("div");
  actions.className = "entry-actions";

  const annotateBtn = document.createElement("button");
  annotateBtn.textContent = "Save annotation";
  annotateBtn.addEventListener("click", async () => {
    if (!textarea.value.trim()) return;
    await api(`/annotate/${entry.id}`, {
      method: "POST",
      body: JSON.stringify({ annotation: textarea.value.trim() }),
    });
    await refreshHistory();
  });
  actions.appendChild(annotateBtn);

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "delete-btn";
  deleteBtn.textContent = "Prune";
  deleteBtn.addEventListener("click", async () => {
    if (!confirm("Remove this entry from the corpus? This can't be undone.")) return;
    await api(`/entry/${entry.id}`, { method: "DELETE" });
    await refreshHistory();
  });
  actions.appendChild(deleteBtn);

  annotationWrap.appendChild(actions);
  body.appendChild(annotationWrap);

  card.appendChild(body);
  return card;
}

function renderTimeline() {
  const el = document.getElementById("timeline");
  el.innerHTML = "";
  const filtered = activeFilter
    ? allEntries.filter((e) => e.source === activeFilter)
    : allEntries;
  [...filtered].reverse().forEach((entry) => el.appendChild(renderEntryCard(entry)));
}

async function refreshHistory() {
  allEntries = await api("/history");
  renderTimeline();
  renderCorpusStrip();
}

function setupFilters() {
  document.querySelectorAll(".filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      activeFilter = chip.dataset.source;
      renderTimeline();
    });
  });
}

// ---- prompt form --------------------------------------------------------

function setupPromptForm() {
  const form = document.getElementById("prompt-form");
  const input = document.getElementById("prompt-input");
  const submitBtn = document.getElementById("prompt-submit");
  const statusEl = document.getElementById("now-status");
  const resultEl = document.getElementById("now-result");
  const resultImg = document.getElementById("now-image");
  const resultCaption = document.getElementById("now-caption");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const prompt = input.value.trim();
    if (!prompt) return;

    submitBtn.disabled = true;
    statusEl.textContent = "The unconscious is drawing…";
    resultEl.hidden = true;

    try {
      const entry = await api("/prompt", {
        method: "POST",
        body: JSON.stringify({ prompt }),
      });
      resultImg.src = entry.image_url;
      resultCaption.textContent = entry.claude_caption;
      resultEl.hidden = false;
      statusEl.textContent = "";
      input.value = "";
      await refreshHistory();
    } catch (err) {
      statusEl.textContent = `Nothing came: ${err.message}`;
    } finally {
      submitBtn.disabled = false;
    }
  });
}

// ---- settings panel -------------------------------------------------------
// Each field's text box always mirrors the currently stored value. Save
// always overwrites with exactly what's in the box: type something new and
// save to change it, clear the box and save to delete it.

async function loadSettings() {
  const settings = await api("/settings");
  SETTINGS_KEYS.forEach((key) => {
    document.getElementById(`${key}-input`).value = settings[key] || "";
  });
}

function setupSettingsPanel() {
  const panel = document.getElementById("settings-panel");
  document.getElementById("settings-toggle").addEventListener("click", async () => {
    panel.hidden = false;
    await loadSettings();
  });
  document.getElementById("settings-close").addEventListener("click", () => {
    panel.hidden = true;
  });
  panel.addEventListener("click", (e) => {
    if (e.target === panel) panel.hidden = true;
  });

  const ENV_TEST_KEYS = new Set(["openweather_api_key", "weather_city", "news_rss_url"]);

  document.querySelectorAll(".save-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const key = btn.dataset.key;
      const input = document.getElementById(`${key}-input`);
      await api("/settings", {
        method: "PUT",
        body: JSON.stringify({ [key]: input.value.trim() }),
      });
      await loadSettings();
      if (ENV_TEST_KEYS.has(key)) await testEnvironmentSignals();
    });
  });
}

async function testEnvironmentSignals() {
  const resultsEl = document.getElementById("env-test-results");
  resultsEl.hidden = false;
  resultsEl.textContent = "Checking weather and news…";
  try {
    const result = await api("/settings/test");
    resultsEl.innerHTML = "";
    const weatherLine = document.createElement("p");
    weatherLine.textContent = `Weather: ${result.weather}`;
    const newsLine = document.createElement("p");
    newsLine.textContent = `News: ${result.news}`;
    resultsEl.appendChild(weatherLine);
    resultsEl.appendChild(newsLine);
  } catch (err) {
    resultsEl.textContent = `Test failed: ${err.message}`;
  }
}

// ---- init -----------------------------------------------------------------

(async function init() {
  setupFilters();
  setupPromptForm();
  setupSettingsPanel();
  await loadStartup();
  await refreshHistory();
})();
