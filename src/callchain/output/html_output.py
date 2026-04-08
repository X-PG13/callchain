"""Interactive HTML report output."""

from __future__ import annotations

import json
from pathlib import Path

from callchain.core.models import AnalysisResult


def write_html(result: AnalysisResult, output_path: str | Path) -> Path:
    """Generate a self-contained interactive HTML report."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data = result.to_dict()
    data_json = json.dumps(data, ensure_ascii=False)
    # Escape script-breaking sequences and unsupported JS line separators.
    data_json = data_json.replace("</", "<\\/").replace("<!--", "<\\!--")
    data_json = data_json.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    html = _HTML_TEMPLATE.replace("__DATA_JSON__", data_json)
    path.write_text(html, encoding="utf-8")
    return path


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CallChain Analysis Report</title>
<style>
:root {
  --bg: #f4efe6;
  --surface: rgba(255, 252, 247, 0.92);
  --surface-strong: #fffdf9;
  --border: #d7cbbc;
  --text: #213038;
  --muted: #6a7880;
  --accent: #0e7c86;
  --accent-soft: #dceff0;
  --accent-strong: #0a4f57;
  --green: #266f3f;
  --red: #b24a36;
  --yellow: #9d6d16;
  --shadow: 0 16px 40px rgba(33, 48, 56, 0.08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--text);
  font-family: "Avenir Next", "IBM Plex Sans", "Segoe UI", sans-serif;
  line-height: 1.6;
  background:
    radial-gradient(circle at top left, rgba(14, 124, 134, 0.16), transparent 28%),
    radial-gradient(circle at top right, rgba(178, 74, 54, 0.12), transparent 22%),
    linear-gradient(180deg, #fbf7ef 0%, #f3ebde 100%);
}
.page {
  max-width: 1240px;
  margin: 0 auto;
  padding: 28px 18px 48px;
}
.card,
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 18px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(8px);
}
.hero {
  padding: 24px 24px 18px;
  margin-bottom: 18px;
}
.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent-strong);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 11px;
  font-weight: 700;
}
h1 {
  margin: 14px 0 6px;
  font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  font-size: clamp(2rem, 4vw, 3.4rem);
  line-height: 1.05;
}
.hero-copy {
  margin: 0;
  max-width: 760px;
  color: var(--muted);
  font-size: 15px;
}
#project-path {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid rgba(14, 124, 134, 0.16);
  color: var(--muted);
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}
.toolbar {
  padding: 16px 18px;
  margin-bottom: 18px;
}
.toolbar-grid {
  display: grid;
  grid-template-columns: minmax(220px, 2fr) repeat(2, minmax(180px, 1fr));
  gap: 12px;
  align-items: end;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field label {
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.field input,
.field select,
.toolbar button {
  min-height: 42px;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: var(--surface-strong);
  color: var(--text);
  font: inherit;
}
.field input,
.field select {
  padding: 10px 12px;
}
.field input:focus,
.field select:focus,
.toolbar button:focus {
  outline: 2px solid rgba(14, 124, 134, 0.28);
  outline-offset: 1px;
}
.toolbar-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 12px;
}
.toolbar button {
  padding: 0 14px;
  cursor: pointer;
  transition: transform 120ms ease, border-color 120ms ease, background 120ms ease;
}
.toolbar button:hover {
  transform: translateY(-1px);
  border-color: var(--accent);
  background: #f8fffe;
}
#filter-summary {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}
.stat-card {
  padding: 18px;
}
.stat-number {
  display: block;
  font-size: 2rem;
  line-height: 1;
  font-weight: 800;
  color: var(--accent-strong);
}
.stat-label {
  display: block;
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.panel {
  margin-bottom: 16px;
  overflow: hidden;
}
.section-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  border: 0;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 1rem;
  font-weight: 800;
}
.section-title::before {
  content: "+";
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-size: 16px;
  line-height: 1;
}
.panel.is-open .section-title::before {
  content: "-";
}
.section-count {
  padding: 4px 10px;
  border-radius: 999px;
  background: #f2e7da;
  color: var(--accent-strong);
  font-size: 11px;
  font-weight: 700;
}
.panel-body {
  padding: 0 20px 18px;
  border-top: 1px solid rgba(14, 124, 134, 0.12);
}
.panel-body[hidden] {
  display: none;
}
.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.tag {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  border: 1px solid rgba(0, 0, 0, 0.04);
}
.tag-py { background: #3974a5; color: #fff; }
.tag-js { background: #f1de5b; color: #1b1b1b; }
.tag-ts { background: #3178c6; color: #fff; }
.tag-java { background: #b07219; color: #fff; }
.tag-go { background: #00add8; color: #08323a; }
.tag-rust { background: #dea584; color: #2b2018; }
.tag-c { background: #5a7fb2; color: #fff; }
.tag-cpp { background: #2c5cc5; color: #fff; }
.bars {
  display: grid;
  gap: 10px;
}
.bar-row {
  display: grid;
  grid-template-columns: minmax(130px, 180px) 1fr auto;
  gap: 10px;
  align-items: center;
}
.bar-label,
.bar-value {
  font-size: 13px;
}
.bar-track {
  width: 100%;
  height: 14px;
  border-radius: 999px;
  overflow: hidden;
  background: #ece2d5;
}
.bar-fill {
  height: 100%;
  border-radius: 999px;
}
.bar-low { background: linear-gradient(90deg, #2d8b52, #61b57c); }
.bar-med { background: linear-gradient(90deg, #a8780f, #d8aa3b); }
.bar-high { background: linear-gradient(90deg, #c06033, #d88a63); }
.bar-vhigh { background: linear-gradient(90deg, #9e4030, #d05e4d); }
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 8px;
  background: var(--surface-strong);
  border-radius: 14px;
  overflow: hidden;
}
th,
td {
  padding: 11px 12px;
  border-bottom: 1px solid #eadfce;
  text-align: left;
  vertical-align: top;
  font-size: 13px;
}
th {
  color: var(--muted);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
tbody tr:hover {
  background: #fcf5ec;
}
tbody tr:last-child td {
  border-bottom: 0;
}
code {
  font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
}
.empty-state,
.note {
  margin-top: 10px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #f9f1e5;
  color: var(--muted);
  font-size: 13px;
}
.cycle-list,
.hierarchy-list,
.chain-list {
  display: grid;
  gap: 12px;
  margin-top: 10px;
}
.cycle-item,
.hierarchy-item,
.chain-item,
.warning-item {
  padding: 14px;
  border-radius: 14px;
  border: 1px solid #eadfce;
  background: var(--surface-strong);
}
.hierarchy-base,
.chain-meta,
.warning-meta {
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}
.hierarchy-children {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.chain-flow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.chain-node {
  display: inline-flex;
  align-items: center;
  padding: 7px 10px;
  border-radius: 12px;
  background: #fff8ef;
  border: 1px solid #e7d7c0;
}
.chain-arrow {
  color: var(--muted);
  font-weight: 700;
}
.chain-cross {
  color: var(--red);
}
.right {
  text-align: right;
}
@media (max-width: 900px) {
  .toolbar-grid {
    grid-template-columns: 1fr;
  }
  .bar-row {
    grid-template-columns: 1fr;
  }
}
</style>
</head>
<body>
<div class="page">
  <header class="hero card">
    <div class="eyebrow">Static Analysis Snapshot</div>
    <h1>CallChain Report</h1>
    <p class="hero-copy">Search and slice hotspots, dead code, imports, inheritance, and cross-file chains without leaving the page.</p>
    <p id="project-path"></p>
  </header>

  <section class="toolbar card">
    <div class="toolbar-grid">
      <div class="field">
        <label for="global-search">Search</label>
        <input id="global-search" type="text" placeholder="Search functions, files, imports, cycles, or chains">
      </div>
      <div class="field">
        <label for="language-filter">Language</label>
        <select id="language-filter"></select>
      </div>
      <div class="field">
        <label for="file-filter">File</label>
        <select id="file-filter"></select>
      </div>
    </div>
    <div class="toolbar-actions">
      <button id="expand-all" type="button">Expand All</button>
      <button id="collapse-all" type="button">Collapse All</button>
      <button id="clear-filters" type="button">Clear Filters</button>
    </div>
    <p id="filter-summary"></p>
  </section>

  <section class="stats-grid" id="summary-cards"></section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="languages-body" aria-expanded="true">
      <span class="section-title">Languages Detected</span>
      <span class="section-count" id="count-languages"></span>
    </button>
    <div class="panel-body" id="languages-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="warnings-body" aria-expanded="true">
      <span class="section-title">Parse Warnings</span>
      <span class="section-count" id="count-warnings"></span>
    </button>
    <div class="panel-body" id="warnings-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="complexity-body" aria-expanded="true">
      <span class="section-title">Complexity Distribution</span>
      <span class="section-count" id="count-complexity"></span>
    </button>
    <div class="panel-body" id="complexity-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="hotspots-body" aria-expanded="true">
      <span class="section-title">Hotspot Functions</span>
      <span class="section-count" id="count-hotspots"></span>
    </button>
    <div class="panel-body" id="hotspots-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="coupling-body" aria-expanded="true">
      <span class="section-title">Module Coupling</span>
      <span class="section-count" id="count-coupling"></span>
    </button>
    <div class="panel-body" id="coupling-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="cycles-body" aria-expanded="true">
      <span class="section-title">Circular Dependencies</span>
      <span class="section-count" id="count-cycles"></span>
    </button>
    <div class="panel-body" id="cycles-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="imports-body" aria-expanded="true">
      <span class="section-title">Unused Imports</span>
      <span class="section-count" id="count-imports"></span>
    </button>
    <div class="panel-body" id="imports-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="hierarchy-body" aria-expanded="true">
      <span class="section-title">Class Hierarchy</span>
      <span class="section-count" id="count-hierarchy"></span>
    </button>
    <div class="panel-body" id="hierarchy-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="dead-body" aria-expanded="true">
      <span class="section-title">Dead Functions</span>
      <span class="section-count" id="count-dead"></span>
    </button>
    <div class="panel-body" id="dead-body"></div>
  </section>

  <section class="panel is-open">
    <button class="section-toggle" type="button" data-target="chains-body" aria-expanded="true">
      <span class="section-title">Call Chains</span>
      <span class="section-count" id="count-chains"></span>
    </button>
    <div class="panel-body" id="chains-body"></div>
  </section>
</div>

<script>
const D = __DATA_JSON__;
const state = { query: "", language: "all", file: "all" };
const fileLanguageMap = {};
const classIndex = {};

(D.modules || []).forEach((module) => {
  fileLanguageMap[module.file_path] = module.language;
  (module.classes || []).forEach((cls) => {
    classIndex[cls.qualified_name] = { file: cls.file_path, language: module.language };
  });
});

const tagClassMap = {
  python: "tag-py",
  javascript: "tag-js",
  typescript: "tag-ts",
  java: "tag-java",
  go: "tag-go",
  rust: "tag-rust",
  c: "tag-c",
  cpp: "tag-cpp",
};

function $(selector) {
  return document.querySelector(selector);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function normalize(value) {
  return String(value ?? "").toLowerCase();
}

function uniqueSorted(values) {
  return Array.from(new Set(values.filter(Boolean))).sort((left, right) => left.localeCompare(right));
}

function languageForFile(filePath) {
  return fileLanguageMap[filePath] || "";
}

function filesFromChain(chain) {
  return uniqueSorted((chain.nodes || []).map((node) => node.file_path));
}

function matchesRecord(values, files, fallbackLanguages) {
  const query = state.query;
  const safeFiles = Array.isArray(files) ? uniqueSorted(files) : uniqueSorted([files]);
  const languages = uniqueSorted([
    ...safeFiles.map((filePath) => languageForFile(filePath)),
    ...(fallbackLanguages || []),
  ]);

  if (query && !(values || []).some((value) => normalize(value).includes(query))) {
    return false;
  }
  if (state.file !== "all" && !safeFiles.includes(state.file)) {
    return false;
  }
  if (state.language !== "all" && !languages.includes(state.language)) {
    return false;
  }
  return true;
}

function emptyState(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function note(message) {
  return `<div class="note">${escapeHtml(message)}</div>`;
}

function setCount(id, shown, total) {
  const node = document.getElementById(id);
  if (!node) {
    return;
  }
  node.textContent = total === undefined ? String(shown) : `${shown}/${total}`;
}

function setPanelOpen(panel, open) {
  panel.classList.toggle("is-open", open);
  const button = panel.querySelector(".section-toggle");
  const body = panel.querySelector(".panel-body");
  if (button) {
    button.setAttribute("aria-expanded", open ? "true" : "false");
  }
  if (body) {
    body.hidden = !open;
  }
}

function renderSummaryCards() {
  const cards = [
    ["Files", D.summary.total_files],
    ["Functions", D.summary.total_functions],
    ["Classes", D.summary.total_classes],
    ["Call Edges", D.summary.total_edges],
    ["Call Chains", D.summary.total_chains],
  ];
  $("#summary-cards").innerHTML = cards.map(([label, value]) => `
    <article class="card stat-card">
      <span class="stat-number">${Number(value).toLocaleString()}</span>
      <span class="stat-label">${escapeHtml(label)}</span>
    </article>
  `).join("");
}

function renderLanguages() {
  const languages = D.languages || [];
  setCount("count-languages", languages.length);
  $("#languages-body").innerHTML = languages.length
    ? `<div class="tag-row">${languages.map((language) => `
        <span class="tag ${tagClassMap[language] || ""}">${escapeHtml(language)}</span>
      `).join("")}</div>`
    : emptyState("No languages detected.");
}

function renderWarnings() {
  const items = D.parse_errors || [];
  const filtered = items.filter((item) => matchesRecord(
    [item.file, item.phase, item.error],
    [item.file],
  ));
  setCount("count-warnings", filtered.length, items.length);
  if (!filtered.length) {
    $("#warnings-body").innerHTML = items.length
      ? emptyState("No parse warnings match the active filters.")
      : emptyState("No parse warnings were recorded.");
    return;
  }

  $("#warnings-body").innerHTML = `<div class="cycle-list">${filtered.slice(0, 40).map((item) => `
    <article class="warning-item">
      <div class="warning-meta">${escapeHtml(item.phase)} in <code>${escapeHtml(item.file)}</code></div>
      <div><code>${escapeHtml(item.error)}</code></div>
    </article>
  `).join("")}</div>${filtered.length > 40 ? note(`Showing first 40 of ${filtered.length} parse warnings.`) : ""}`;
}

function renderComplexity() {
  const complexity = D.analysis.complexity_distribution || {};
  const entries = Object.entries(complexity);
  const maxValue = Math.max(1, ...entries.map((entry) => entry[1]));
  const classMap = {
    "low (1-5)": "bar-low",
    "medium (6-10)": "bar-med",
    "high (11-20)": "bar-high",
    "very_high (21+)": "bar-vhigh",
  };

  setCount("count-complexity", entries.reduce((sum, entry) => sum + entry[1], 0));
  $("#complexity-body").innerHTML = entries.length
    ? `<div class="bars">${entries.map(([label, value]) => `
        <div class="bar-row">
          <span class="bar-label">${escapeHtml(label)}</span>
          <div class="bar-track"><div class="bar-fill ${classMap[label] || "bar-low"}" style="width: ${Math.max(6, (value / maxValue) * 100)}%"></div></div>
          <span class="bar-value">${value}</span>
        </div>
      `).join("")}</div>`
    : emptyState("No complexity data is available.");
}

function renderHotspots() {
  const items = D.analysis.hotspot_functions || [];
  const filtered = items.filter((item) => matchesRecord(
    [item.function, item.file, String(item.call_count)],
    [item.file],
  ));
  setCount("count-hotspots", filtered.length, items.length);

  if (!filtered.length) {
    $("#hotspots-body").innerHTML = items.length
      ? emptyState("No hotspot functions match the active filters.")
      : emptyState("No hotspot functions were detected.");
    return;
  }

  const rows = filtered.slice(0, 50).map((item) => `
    <tr>
      <td><code>${escapeHtml(item.function)}</code></td>
      <td><code>${escapeHtml(item.file)}</code></td>
      <td class="right">${item.call_count}</td>
    </tr>
  `).join("");
  $("#hotspots-body").innerHTML = `
    <table>
      <thead><tr><th>Function</th><th>File</th><th class="right">Calls</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${filtered.length > 50 ? note(`Showing first 50 of ${filtered.length} hotspot functions.`) : ""}
  `;
}

function renderCoupling() {
  const items = Object.entries(D.analysis.module_coupling || {})
    .sort((left, right) => right[1].instability - left[1].instability);
  const filtered = items.filter(([modulePath]) => matchesRecord([modulePath], [modulePath]));
  setCount("count-coupling", filtered.length, items.length);

  if (!filtered.length) {
    $("#coupling-body").innerHTML = items.length
      ? emptyState("No coupling rows match the active filters.")
      : emptyState("No module coupling data is available.");
    return;
  }

  const rows = filtered.slice(0, 40).map(([modulePath, metrics]) => `
    <tr>
      <td><code>${escapeHtml(modulePath)}</code></td>
      <td class="right">${metrics.fan_in}</td>
      <td class="right">${metrics.fan_out}</td>
      <td class="right">${Number(metrics.instability).toFixed(3)}</td>
    </tr>
  `).join("");
  $("#coupling-body").innerHTML = `
    <table>
      <thead><tr><th>Module</th><th class="right">Fan-In</th><th class="right">Fan-Out</th><th class="right">Instability</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${filtered.length > 40 ? note(`Showing first 40 of ${filtered.length} coupling rows.`) : ""}
  `;
}

function renderCycles() {
  const items = D.analysis.circular_dependencies || [];
  const filtered = items.filter((cycle) => matchesRecord(cycle, cycle));
  setCount("count-cycles", filtered.length, items.length);

  if (!filtered.length) {
    $("#cycles-body").innerHTML = items.length
      ? emptyState("No cycles match the active filters.")
      : emptyState("No circular dependencies detected.");
    return;
  }

  $("#cycles-body").innerHTML = `<div class="cycle-list">${filtered.slice(0, 30).map((cycle) => `
    <article class="cycle-item"><code>${cycle.map((step) => escapeHtml(step)).join(" &rarr; ")}</code></article>
  `).join("")}</div>${filtered.length > 30 ? note(`Showing first 30 of ${filtered.length} dependency cycles.`) : ""}`;
}

function renderUnusedImports() {
  const items = D.analysis.unused_imports || [];
  const filtered = items.filter((item) => matchesRecord(
    [item.module, ...(item.names || []), item.file],
    [item.file],
  ));
  setCount("count-imports", filtered.length, items.length);

  if (!filtered.length) {
    $("#imports-body").innerHTML = items.length
      ? emptyState("No unused imports match the active filters.")
      : emptyState("No unused imports detected.");
    return;
  }

  const rows = filtered.slice(0, 60).map((item) => `
    <tr>
      <td><code>${escapeHtml(item.file)}</code></td>
      <td><code>${escapeHtml(item.module)}</code></td>
      <td>${escapeHtml((item.names || []).join(", ") || item.module)}</td>
      <td class="right">${item.line}</td>
    </tr>
  `).join("");
  $("#imports-body").innerHTML = `
    <table>
      <thead><tr><th>File</th><th>Module</th><th>Unused Names</th><th class="right">Line</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${filtered.length > 60 ? note(`Showing first 60 of ${filtered.length} unused import records.`) : ""}
  `;
}

function renderHierarchy() {
  const items = Object.entries(D.analysis.class_hierarchy || {}).filter((entry) => entry[1].length > 0);
  const filtered = items.filter(([baseClass, childClasses]) => {
    const files = [
      classIndex[baseClass] ? classIndex[baseClass].file : "",
      ...childClasses.map((childClass) => classIndex[childClass] ? classIndex[childClass].file : ""),
    ];
    const languages = [
      classIndex[baseClass] ? classIndex[baseClass].language : "",
      ...childClasses.map((childClass) => classIndex[childClass] ? classIndex[childClass].language : ""),
    ];
    return matchesRecord([baseClass, ...childClasses], files, languages);
  });
  setCount("count-hierarchy", filtered.length, items.length);

  if (!filtered.length) {
    $("#hierarchy-body").innerHTML = items.length
      ? emptyState("No inheritance entries match the active filters.")
      : emptyState("No inheritance relationships detected.");
    return;
  }

  $("#hierarchy-body").innerHTML = `<div class="hierarchy-list">${filtered.slice(0, 40).map(([baseClass, childClasses]) => `
    <article class="hierarchy-item">
      <div class="hierarchy-base">Base class: <code>${escapeHtml(baseClass)}</code></div>
      <div class="hierarchy-children">${childClasses.map((childClass) => `
        <span class="chain-node"><code>${escapeHtml(childClass)}</code></span>
      `).join("")}</div>
    </article>
  `).join("")}</div>${filtered.length > 40 ? note(`Showing first 40 of ${filtered.length} inheritance entries.`) : ""}`;
}

function renderDeadFunctions() {
  const items = D.analysis.dead_functions || [];
  const filtered = items.filter((item) => matchesRecord(
    [item.function, item.file, String(item.line)],
    [item.file],
  ));
  setCount("count-dead", filtered.length, items.length);

  if (!filtered.length) {
    $("#dead-body").innerHTML = items.length
      ? emptyState("No dead functions match the active filters.")
      : emptyState("No dead functions detected.");
    return;
  }

  const rows = filtered.slice(0, 80).map((item) => `
    <tr>
      <td><code>${escapeHtml(item.function)}</code></td>
      <td><code>${escapeHtml(item.file)}</code></td>
      <td class="right">${item.line}</td>
    </tr>
  `).join("");
  $("#dead-body").innerHTML = `
    <table>
      <thead><tr><th>Function</th><th>File</th><th class="right">Line</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${filtered.length > 80 ? note(`Showing first 80 of ${filtered.length} dead functions.`) : ""}
  `;
}

function renderChains() {
  const items = D.chains || [];
  const filtered = items.filter((chain) => {
    const files = filesFromChain(chain);
    const languages = uniqueSorted((chain.nodes || []).map((node) => node.language));
    const values = [
      String(chain.length),
      String(chain.cross_file_transitions),
      ...files,
      ...(chain.nodes || []).map((node) => node.qualified_name),
    ];
    return matchesRecord(values, files, languages);
  });
  setCount("count-chains", filtered.length, items.length);

  if (!filtered.length) {
    $("#chains-body").innerHTML = items.length
      ? emptyState("No call chains match the active filters.")
      : emptyState("No call chains were generated.");
    return;
  }

  $("#chains-body").innerHTML = `<div class="chain-list">${filtered.slice(0, 50).map((chain, index) => `
    <article class="chain-item">
      <div class="chain-meta">Chain ${index + 1} of ${filtered.length} | length ${chain.length} | cross-file ${chain.cross_file_transitions}</div>
      <div class="chain-flow">${(chain.nodes || []).map((node, nodeIndex, allNodes) => {
        const isCrossFile = nodeIndex > 0 && node.file_path !== allNodes[nodeIndex - 1].file_path;
        const arrow = nodeIndex === 0 ? "" : `<span class="chain-arrow ${isCrossFile ? "chain-cross" : ""}">${isCrossFile ? "=>": "->"}</span>`;
        return `${arrow}<span class="chain-node" title="${escapeHtml(`${node.file_path}:${node.line}`)}"><code>${escapeHtml(node.qualified_name)}</code></span>`;
      }).join("")}</div>
    </article>
  `).join("")}</div>${filtered.length > 50 ? note(`Showing first 50 of ${filtered.length} call chains.`) : ""}`;
}

function updateFilterSummary() {
  const parts = [];
  if (state.query) {
    parts.push(`search: "${state.query}"`);
  }
  if (state.language !== "all") {
    parts.push(`language: ${state.language}`);
  }
  if (state.file !== "all") {
    parts.push(`file: ${state.file}`);
  }
  $("#filter-summary").textContent = parts.length
    ? `Active filters: ${parts.join(" | ")}`
    : "No filters active. The report is showing complete analysis output.";
}

function populateFilters() {
  const languageSelect = $("#language-filter");
  const fileSelect = $("#file-filter");

  const languages = uniqueSorted(D.languages || []);
  const files = uniqueSorted([
    ...(D.modules || []).map((module) => module.file_path),
    ...((D.analysis.dead_functions || []).map((item) => item.file)),
    ...((D.analysis.hotspot_functions || []).map((item) => item.file)),
    ...((D.analysis.unused_imports || []).map((item) => item.file)),
    ...((D.parse_errors || []).map((item) => item.file)),
    ...((D.chains || []).flatMap((chain) => filesFromChain(chain))),
  ]);

  languageSelect.innerHTML = `<option value="all">All languages</option>${languages.map((language) => `
    <option value="${escapeHtml(language)}">${escapeHtml(language)}</option>
  `).join("")}`;
  fileSelect.innerHTML = `<option value="all">All files</option>${files.map((filePath) => `
    <option value="${escapeHtml(filePath)}">${escapeHtml(filePath)}</option>
  `).join("")}`;
}

function renderAll() {
  renderSummaryCards();
  renderLanguages();
  renderWarnings();
  renderComplexity();
  renderHotspots();
  renderCoupling();
  renderCycles();
  renderUnusedImports();
  renderHierarchy();
  renderDeadFunctions();
  renderChains();
  updateFilterSummary();
}

document.getElementById("project-path").textContent = D.project_path;
populateFilters();
renderAll();

document.querySelectorAll(".section-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    const panel = button.closest(".panel");
    if (!panel) {
      return;
    }
    setPanelOpen(panel, !panel.classList.contains("is-open"));
  });
});

$("#global-search").addEventListener("input", (event) => {
  state.query = normalize(event.target.value.trim());
  renderAll();
});

$("#language-filter").addEventListener("change", (event) => {
  state.language = event.target.value;
  renderAll();
});

$("#file-filter").addEventListener("change", (event) => {
  state.file = event.target.value;
  renderAll();
});

$("#clear-filters").addEventListener("click", () => {
  state.query = "";
  state.language = "all";
  state.file = "all";
  $("#global-search").value = "";
  $("#language-filter").value = "all";
  $("#file-filter").value = "all";
  renderAll();
});

$("#expand-all").addEventListener("click", () => {
  document.querySelectorAll(".panel").forEach((panel) => setPanelOpen(panel, true));
});

$("#collapse-all").addEventListener("click", () => {
  document.querySelectorAll(".panel").forEach((panel) => setPanelOpen(panel, false));
});
</script>
</body>
</html>
"""
