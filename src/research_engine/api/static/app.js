/* Dashboard for the equity research engine.
 *
 * Reads only what the pipeline wrote. It formats and arranges; it never computes a financial
 * figure, because a number shown here that does not exist in `output/` would be unauditable.
 *
 * Interaction rules this file is written against:
 *   - every control is a real button or link, reachable and operable from the keyboard;
 *   - no information is available only on hover, so provenance marks and long formulas open
 *     visible detail rather than a tooltip;
 *   - every chart has a data table beside it, because a picture of a line tells a screen
 *     reader nothing;
 *   - loading reserves the space the content will occupy, so nothing jumps when it arrives;
 *   - async state changes announce themselves once, through a single polite live region.
 */

const state = {
  companies: [],
  companyId: null,
  view: 'overview',
  scenario: null,
  sort: {},
  cache: new Map(),
  requestId: 0,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const main = $('#main');
const panel = $('#panel');
const live = $('#live');

const announce = (msg) => { live.textContent = msg; };

/* ---------- api ---------- */

class StageMissing extends Error {
  constructor(detail) {
    super(detail.error || 'stage not run');
    this.stage = detail.stage;
  }
}

async function api(path, { fresh = false } = {}) {
  if (!fresh && state.cache.has(path)) return state.cache.get(path);
  const res = await fetch(path);
  if (res.status === 409) throw new StageMissing((await res.json()).detail || {});
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  const data = await res.json();
  state.cache.set(path, data);
  return data;
}

const forCompany = (suffix) => `/api/companies/${encodeURIComponent(state.companyId)}${suffix}`;

/* ---------- formatting ---------- */

function fmtValue(v, unitKind, currency) {
  if (v === null || v === undefined) return '–';
  if (unitKind === 'ratio') return `${(v * 100).toFixed(1)}%`;
  if (unitKind === 'multiple') return `${v.toFixed(2)}x`;
  if (unitKind === 'currency' || unitKind === 'currency_per_share') {
    const prefix = currency ? `${currency} ` : '';
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${prefix}${(v / 1e9).toFixed(1)}bn`;
    if (abs >= 1e6) return `${prefix}${(v / 1e6).toFixed(1)}m`;
    return `${prefix}${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  }
  if (unitKind === 'count') {
    const abs = Math.abs(v);
    if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}bn`;
    if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}m`;
  }
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const titleize = (s) => String(s ?? '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

const shortType = (t) => String(t ?? '').replace('_assumption', '').replace('management_', '');

const badge = (kind, text) => `<span class="badge b-${esc(kind)}">${esc(text ?? kind)}</span>`;

/* Decimal arithmetic leaves ratios with 28 significant digits. Shortening one for display is
 * presentation only: the full value stays in the parquet datasets and in lineage.json. */
function tidyAttr(v) {
  const s = String(v ?? '');
  if (!/^-?\d+\.\d{8,}$/.test(s)) return s;
  const n = Number(s);
  return Number.isFinite(n) ? `${n.toPrecision(10).replace(/\.?0+$/, '')}…` : s;
}

/* ---------- reusable fragments ---------- */

const traceable = (id, text, label) =>
  id ? `<button type="button" class="trace" data-node="${esc(id)}" data-label="${esc(label || '')}"
        aria-label="${esc(text)} — trace ${esc(label || '')} to its source">${esc(text)}</button>`
    : esc(text);

/* Provenance marks are buttons that reveal visible text, never hover-only tooltips. */
const provenanceMark = (kind, symbol, detail) =>
  `<button type="button" class="mark-btn ${kind}" data-detail="${esc(detail)}"
     aria-label="${esc(detail)}">${symbol}</button>`;

function detailStrip(id) {
  return `<p class="chartmeta" id="${esc(id)}" role="status" aria-live="polite">
    <span class="faint">Select a marker (* or †) to see why a value is marked.</span></p>`;
}

const sortableTh = (key, label, cls = '') =>
  `<th class="${cls}" aria-sort="none" data-key="${esc(key)}">
     <button type="button" class="sort">${esc(label)}</button></th>`;

const plainTh = (label, cls = '') => `<th class="${cls}"><span class="th-inner">${esc(label)}</span></th>`;

/* A chart that stops early looks finished. The engine already recorded why each series ended;
   this puts that record beside the picture, because the picture is what gets read. Marked as a
   note rather than an error: an incomplete series is a limit on the chart, not a failed render. */
function chartNotes(c) {
  const notes = c.notes || [];
  if (!notes.length) return '';
  const incomplete = Object.keys(c.truncated || {}).length > 0;
  return `<p class="chartnote${incomplete ? ' incomplete' : ''}" role="note">
    <strong>${incomplete ? 'Incomplete over the period shown.' : 'Note.'}</strong>
    ${notes.map((n) => esc(n)).join(' ')}</p>`;
}

function chartDataTable(scope, chartId) {
  return `<details class="data-table" data-chart="${esc(chartId)}" data-scope="${esc(scope)}">
    <summary>Show the numbers behind this chart</summary>
    <div class="tablewrap" data-slot>
      <p class="empty">Loading…</p>
    </div>
  </details>`;
}

/* ---------- lineage drawer ---------- */

const drawer = $('#drawer');
let lastFocus = null;

function closeDrawer() {
  drawer.hidden = true;
  document.body.style.overflow = '';
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
}

$('#drawer-close').addEventListener('click', closeDrawer);
drawer.addEventListener('click', (e) => { if (e.target === drawer) closeDrawer(); });

document.addEventListener('keydown', (e) => {
  if (drawer.hidden) return;
  if (e.key === 'Escape') { closeDrawer(); return; }
  if (e.key !== 'Tab') return;
  // Keep focus inside the dialog while it is open, and never trap it once closed.
  const focusable = Array.from(drawer.querySelectorAll('a[href], button, [tabindex]:not([tabindex="-1"])'))
    .filter((el) => el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

const KIND_ORDER = { chart: -1, model_output: 0, assumption: 1, fact: 2, document: 3, source: 4 };

async function openLineage(nodeId, label) {
  lastFocus = document.activeElement;
  drawer.hidden = false;
  document.body.style.overflow = 'hidden';
  $('#drawer-title').textContent = label || nodeId;
  const body = $('#drawer-body');
  body.innerHTML = '<p class="empty">Tracing…</p>';
  $('#drawer-close').focus();

  try {
    const t = await api(forCompany(`/lineage/${encodeURIComponent(nodeId)}`));
    const nodes = [...t.nodes].sort((a, b) =>
      (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) || a.label.localeCompare(b.label));
    const chain = nodes.map((n) => {
      const attrs = Object.entries(n.attributes || {})
        .map(([k, v]) => `${esc(titleize(k))}: ${esc(tidyAttr(v))}`).join(' &middot; ');
      const lbl = n.kind === 'source'
        ? `<a href="${esc(n.label)}" target="_blank" rel="noopener noreferrer">${esc(n.label)}</a>`
        : esc(n.label);
      return `<li class="link k-${esc(n.kind)}">
        <p class="kind">${esc(titleize(n.kind))}</p>
        <p class="lbl">${lbl}</p>
        ${attrs ? `<p class="attrs">${attrs}</p>` : ''}
      </li>`;
    }).join('');

    body.innerHTML = `
      <div class="note">
        <p>This value resolves through <strong>${t.nodes.length}</strong> lineage
        ${t.nodes.length === 1 ? 'node' : 'nodes'} and <strong>${t.depth}</strong>
        ${t.depth === 1 ? 'level' : 'levels'} to <strong>${t.source_urls.length}</strong> source
        ${t.source_urls.length === 1 ? 'document' : 'documents'}.${t.truncated
          ? ' The chain was truncated for display.' : ''}</p>
      </div>
      <ol class="chain">${chain}</ol>`;
    announce(`Lineage for ${label || nodeId}: ${t.nodes.length} nodes to ${t.source_urls.length} source documents.`);
  } catch (err) {
    body.innerHTML = `<p class="empty err">${esc(err.message)}</p>`;
  }
}

/* ---------- delegated interactions ---------- */

main.addEventListener('click', async (e) => {
  const trace = e.target.closest('.trace');
  if (trace) { openLineage(trace.dataset.node, trace.dataset.label); return; }

  const mark = e.target.closest('.mark-btn');
  if (mark) {
    const strip = mark.closest('section, .card')?.querySelector('[role="status"]');
    if (strip) strip.innerHTML = `<span>${esc(mark.dataset.detail)}</span>`;
    return;
  }

  const scn = e.target.closest('.scn');
  if (scn) {
    state.scenario = scn.dataset.scenario;
    render(viewForecast);
    return;
  }

  const sort = e.target.closest('th button.sort');
  if (sort) { applySort(sort.closest('table'), sort.closest('th')); return; }
});

main.addEventListener('toggle', async (e) => {
  const details = e.target.closest('details.data-table');
  if (!details || !details.open || details.dataset.loaded) return;
  details.dataset.loaded = '1';
  const slot = details.querySelector('[data-slot]');
  const { chart, scope } = details.dataset;
  const path = scope === 'forecast'
    ? forCompany(`/forecast/charts/${encodeURIComponent(chart)}/data`)
    : forCompany(`/charts/${encodeURIComponent(chart)}/data`);
  try {
    const d = await api(path);
    const head = d.columns.map((c) => plainTh(c === 'series' ? 'Series' : c,
      c.startsWith('FY') ? 'num' : '')).join('');
    const rows = d.rows.map((r) => `<tr>${d.columns.map((c) => {
      const v = r[c];
      if (c === 'series' || c === 'method') return `<td class="metric">${esc(v ?? '–')}</td>`;
      return `<td class="num">${typeof v === 'number' ? esc(fmtValue(v, d.unit_kind, d.currency)) : '–'}</td>`;
    }).join('')}</tr>`).join('');
    // A trailing dash in this table could mean "not reported" or "we lost the input". The chart's
    // notes say which, so the accessible fallback carries them rather than leaving the reader to
    // infer it from a row of blanks.
    const caption = [`${d.title} — the values plotted above.`, ...(d.notes || [])].join(' ');
    slot.innerHTML = `<table><caption>${esc(caption)}</caption>
      <thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  } catch (err) {
    slot.innerHTML = `<p class="empty err">${esc(err.message)}</p>`;
  }
}, true);

function applySort(table, th) {
  const headers = Array.from(table.tHead.rows[0].cells);
  const index = headers.indexOf(th);
  const current = th.getAttribute('aria-sort');
  const next = current === 'ascending' ? 'descending' : 'ascending';
  headers.forEach((h) => h.setAttribute('aria-sort', h === th ? next : 'none'));

  const body = table.tBodies[0];
  const rows = Array.from(body.rows);
  const value = (row) => {
    const cell = row.cells[index];
    const raw = cell?.dataset.sort ?? cell?.textContent.trim() ?? '';
    const num = Number(raw.replace(/[^0-9.\-]/g, ''));
    return { num: Number.isFinite(num) && raw !== '' && /\d/.test(raw) ? num : null, text: raw.toLowerCase() };
  };
  rows.sort((a, b) => {
    const x = value(a);
    const y = value(b);
    const cmp = (x.num !== null && y.num !== null) ? x.num - y.num : x.text.localeCompare(y.text);
    return next === 'ascending' ? cmp : -cmp;
  });
  rows.forEach((r) => body.appendChild(r));
  announce(`Sorted by ${th.textContent.trim()}, ${next}.`);
}

/* ---------- shells ---------- */

function stageMissingCard(stage) {
  const cmd = { ingest: 'make ingest', quality: 'make quality', analyze: 'make analyze', forecast: 'make forecast' }[stage];
  return `<div class="card"><div class="body">
    <h2 class="section">Nothing to show yet</h2>
    <p class="muted">The <code>${esc(stage)}</code> stage has not been run for this company, so
    there is no data behind this view. Run
    <code>${esc(cmd)} CONFIG=companies/${esc(state.companyId)}/config.yaml</code> and reload.</p>
    <p class="faint">An empty table here would be indistinguishable from a company with no issues,
    which is why the dashboard says nothing rather than showing zeros.</p>
  </div></div>`;
}

const SKELETON = `<div class="skeleton" aria-hidden="true">
  <div class="sk sk-note"></div>
  <div class="sk-stats"><div class="sk sk-stat"></div><div class="sk sk-stat"></div>
    <div class="sk sk-stat"></div><div class="sk sk-stat"></div></div>
  <div class="sk sk-table"></div>
</div>`;

async function render(fn) {
  const id = ++state.requestId;
  panel.setAttribute('aria-busy', 'true');
  panel.innerHTML = SKELETON;   // reserves roughly the space the content will occupy
  announce('Loading…');
  let html;
  try {
    html = await fn();
  } catch (err) {
    html = err instanceof StageMissing
      ? stageMissingCard(err.stage)
      : `<div class="card"><div class="body"><h2 class="section">Could not load this view</h2>
         <p class="err">${esc(err.message)}</p>
         <p class="muted">Reload the page, or check that the server still has access to the
         company's <code>output/</code> directory.</p></div></div>`;
  }
  if (id !== state.requestId) return;   // a newer request won; drop this one
  panel.innerHTML = html;
  panel.setAttribute('aria-busy', 'false');
  announce(`${titleize(state.view)} ready.`);
}

/* ---------- overview ---------- */

async function viewOverview() {
  const o = await api(forCompany(''));
  const c = o.counts || {};
  const issues = c.issues || {};
  const stat = (k, v, n) => `<div class="card stat"><p class="k">${esc(k)}</p>
    <p class="v${String(v).length > 9 ? ' sm' : ''}">${esc(v)}</p>
    ${n ? `<p class="n">${esc(n)}</p>` : ''}</div>`;

  let documents = '';
  if (o.stages.ingest) {
    const docs = await api(forCompany('/documents'));
    documents = `
      <h2 class="section">Source documents
        <span class="hint">content-addressed; a changed source becomes a new version, never an overwrite</span></h2>
      <div class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region"
        aria-label="Source documents"><table>
        <thead><tr>${plainTh('Document')}${plainTh('Status')}${plainTh('SHA-256')}${plainTh('Source')}</tr></thead>
        <tbody>${docs.map((d) => `<tr>
          <td class="metric">${esc(d.document_id)}</td>
          <td>${esc(d.status)}</td>
          <td class="mono muted">${esc((d.file_hash || '').slice(0, 16))}</td>
          <td class="wrap mono"><a href="${esc(d.source)}" target="_blank" rel="noopener noreferrer">${esc(d.source)}</a></td>
        </tr>`).join('')}</tbody></table></div></div></div>`;
  }

  const warnings = (o.warnings || []).length
    ? `<h2 class="section">Warnings from ingestion</h2>
       <div class="card"><div class="body">${o.warnings.map((w) =>
         `<div class="note warn"><p>${esc(w)}</p></div>`).join('')}</div></div>`
    : '';

  const horizon = o.forecast_horizon
    ? `<dt>Forecast</dt><dd>FY${esc(o.forecast_horizon.base_year)} base →
        FY${esc((o.forecast_horizon.years || []).slice(-1)[0] ?? '–')},
        ${esc((o.forecast_horizon.scenarios || []).length)} scenario(s)</dd>`
    : '';

  return `
    <h2 class="sr-only">Overview of ${esc(o.name)}</h2>
    <div class="grid cols-4">
      ${stat('Facts', c.facts_current ?? '–', `${c.facts ?? 0} versions retained`)}
      ${stat('Derived facts', c.facts_derived ?? '–', 'computed, with formulas')}
      ${stat('Analytic values', c.analytic_values ?? '–', `${c.charts ?? 0} charts rendered`)}
      ${stat('Forecast values', c.forecast_values ?? '–', o.forecast_horizon
        ? `${(o.forecast_horizon.scenarios || []).length} scenario(s)` : 'not run')}
    </div>

    <h2 class="section">Entity</h2>
    <div class="grid cols-2">
      <div class="card"><div class="body"><dl class="kv">
        <dt>Company</dt><dd>${esc(o.name)} <span class="muted">(${esc(o.ticker)}, ${esc(o.exchange)})</span></dd>
        <dt>Country</dt><dd>${esc(o.country)}</dd>
        <dt>Currency</dt><dd>${esc(o.reporting_currency || '–')}</dd>
        <dt>Framework</dt><dd>${o.framework?.name
          ? `<strong>${esc(o.framework.name)}</strong> <span class="muted">via ${esc(o.framework.method)}
             — ${esc(o.framework.evidence)}</span>`
          : '<span class="muted">not classified yet</span>'}</dd>
        <dt>Fiscal year</dt><dd>${o.fiscal_calendar
          ? `ends month ${esc(o.fiscal_calendar.fiscal_year_end_month)}
             <span class="muted">(${esc(o.fiscal_calendar.convention)})</span>` : '–'}</dd>
        ${horizon}
      </dl></div></div>

      <div class="card"><div class="body"><dl class="kv">
        <dt>Data quality</dt><dd>
          <span class="sev sev-error">${esc(issues.error ?? 0)} errors</span>
          <span class="sev sev-warning">${esc(issues.warning ?? 0)} warnings</span>
          <span class="sev sev-info">${esc(issues.info ?? 0)} info</span></dd>
        <dt>Documents</dt><dd>${esc(c.documents ?? 0)} registered</dd>
        <dt>Engine</dt><dd class="mono">${esc(o.versions?.engine_version || '–')}
          <span class="muted">schema ${esc(o.versions?.schema_version || '–')},
          parser ${esc(o.versions?.parser_version || '–')}</span></dd>
        <dt>Last ingest</dt><dd class="mono muted">${esc((o.generated_at || '–').slice(0, 19).replace('T', ' '))}</dd>
      </dl></div></div>
    </div>
    ${warnings}
    ${documents}`;
}

/* ---------- historical ---------- */

async function viewHistorical() {
  const [a, charts] = await Promise.all([
    api(forCompany('/analytics')),
    api(forCompany('/charts')).catch(() => []),
  ]);
  const years = a.fiscal_years || [];

  const chartCards = charts.length ? `
    <h2 class="section">Charts
      <span class="hint">framework-defined; hollow markers and hatched bars are derived values,
      † flags a warning on an input</span></h2>
    <div class="charts">${charts.map((c) => `
      <section class="card" aria-label="${esc(c.title)}">
        <h3>${esc(c.title)}</h3>
        <div class="body"><div class="chart">
          <img src="${forCompany(`/charts/${encodeURIComponent(c.chart_id)}.svg`)}"
               alt="${esc(c.title)}: FY${esc(c.years[0])} to FY${esc(c.years[c.years.length - 1])}.
                    The same values are in the table below." loading="lazy" width="720" height="380">
        </div></div>
        <p class="chartmeta">
          <span>FY${esc(c.years[0])}–FY${esc(c.years[c.years.length - 1])}</span>
          ${c.derived_points ? `<span>${esc(c.derived_points)} derived point(s)</span>` : ''}
          ${c.flagged_points ? `<span>† ${esc(c.flagged_points)} flagged input(s)</span>` : ''}
        </p>
        ${chartNotes(c)}
        ${chartDataTable('analysis', c.chart_id)}
      </section>`).join('')}</div>` : '';

  let rows = '';
  let category = null;
  for (const s of a.series) {
    if (s.category !== category) {
      category = s.category;
      rows += `<tr class="rowgroup"><th colspan="${years.length + 3}" scope="colgroup">${esc(titleize(category))}</th></tr>`;
    }
    const byYear = new Map(s.points.map((p) => [p.fiscal_year, p]));
    const cells = years.map((y) => {
      const p = byYear.get(y);
      if (!p) return '<td class="num muted">–</td>';
      const marks =
        (p.uses_derived_facts
          ? provenanceMark('derived', '*', `${s.analytic_id} FY${y} uses facts the engine derived rather than ones the filer reported.`)
          : '')
        + (p.quality_flags.length
          ? provenanceMark('flagged', '†', `${s.analytic_id} FY${y}: an input was flagged by ${p.quality_flags.join(', ')}.`)
          : '');
      return `<td class="num" data-sort="${esc(p.value)}">${traceable(p.value_id,
        fmtValue(p.value, s.unit_kind, s.currency), `${s.analytic_id} FY${y}`)}${marks}</td>`;
    }).join('');
    const sum = s.summary || {};
    rows += `<tr>
      <th scope="row" class="metric" style="font-weight:400;background:none;text-transform:none;letter-spacing:0">${esc(s.analytic_id)}</th>
      ${cells}
      <td class="num muted">${sum.median != null ? esc(fmtValue(sum.median, s.unit_kind, s.currency)) : '–'}</td>
      <td class="muted">${esc(s.points[0]?.basis || '')}</td>
    </tr>`;
  }

  const notComputed = Object.entries(a.not_computed || {});
  const gaps = notComputed.length ? `
    <h2 class="section">Not computed
      <span class="hint">every analytic-year the engine declined to produce, with its reason</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr>${sortableTh('reason', 'Analytic and reason')}${sortableTh('years', 'Years', 'num')}</tr></thead>
      <tbody>${notComputed.sort((x, y) => y[1] - x[1]).map(([k, v]) =>
        `<tr><td class="metric">${esc(k)}</td><td class="num">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div></div></div>` : '';

  return `
    <div class="note"><p><strong>Classification: model output.</strong> Every figure below is
    computed by the engine from reported and derived facts. Select any value to trace it to the
    filing it came from; select a <code>*</code> or <code>†</code> marker to read what it means.</p></div>

    <section aria-label="Analytics">
      <h2 class="section">Analytics <span class="hint">${esc(a.series.length)} series ·
        FY${esc(years[0] ?? '–')}–FY${esc(years[years.length - 1] ?? '–')}</span></h2>
      <div class="card"><div class="body flush">
        <div class="tablewrap" tabindex="0" role="region" aria-label="Historical analytics, scrollable"><table>
          <caption class="sr-only">Historical analytics by fiscal year</caption>
          <thead><tr>${plainTh('Analytic')}${years.map((y) => plainTh(`FY${y}`, 'num')).join('')}
            ${plainTh('Median', 'num')}${plainTh('Basis')}</tr></thead>
          <tbody>${rows}</tbody></table></div>
        ${detailStrip('hist-detail')}
      </div></div>
    </section>
    ${chartCards}
    ${gaps}`;
}

/* ---------- forecast ---------- */

async function viewForecast() {
  const [f, charts] = await Promise.all([
    api(forCompany('/forecast')),
    api(forCompany('/forecast/charts')).catch(() => []),
  ]);
  const scenarios = f.scenarios || [];
  if (!state.scenario || !scenarios.some((s) => s.id === state.scenario)) {
    state.scenario = scenarios[0]?.id ?? null;
  }
  const active = scenarios.find((s) => s.id === state.scenario) || { metrics: [] };
  const years = f.forecast_years || [];
  const cols = [f.base_year, ...years];

  const switcher = scenarios.length ? `
    <div class="scenarios" role="group" aria-label="Scenario">
      ${scenarios.map((s) => `<button type="button" class="scn" data-scenario="${esc(s.id)}"
        aria-pressed="${s.id === state.scenario}">${esc(s.name)}</button>`).join('')}
    </div>` : '';

  const plan = new Map((f.projection_plan || []).map((p) => [p.metric_id, p]));

  const rows = active.metrics.map((m) => {
    const byYear = new Map(m.points.map((p) => [p.fiscal_year, p]));
    const cells = cols.map((y) => {
      const p = byYear.get(y);
      if (!p) return '<td class="num muted">–</td>';
      return `<td class="num">${traceable(p.value_id, fmtValue(p.value, m.unit_kind, m.currency),
        `${m.metric_id} FY${y}`)}</td>`;
    }).join('');
    const projected = years.map((y) => byYear.get(y)).find(Boolean);
    const src = projected?.assumption_types?.[0];
    // No projected point means the chain stopped upstream; saying "actual" would imply the
    // engine chose to carry the last reported year forward, which is not what happened.
    const method = projected
      ? badge(projected.method, projected.method === 'driver_formula' ? 'driver' : projected.method)
      : '<span class="muted">not projected</span>';
    const formula = plan.get(m.metric_id)?.formula || '';
    return `<tr>
      <th scope="row" class="metric" style="font-weight:400;background:none;text-transform:none;letter-spacing:0">${esc(m.metric_id)}</th>
      ${cells}
      <td>${method}</td>
      <td>${src ? badge(src, shortType(src)) : '<span class="muted">–</span>'}</td>
      <td class="wrap mono muted">${esc(formula)}</td>
    </tr>`;
  }).join('');

  const chartCards = charts.length ? `
    <h2 class="section">Scenario charts
      <span class="hint">solid to the last reported year, dashed after it; scenarios are
      distinguished by line style and an end label, not by colour</span></h2>
    <div class="charts">${charts.map((c) => `
      <section class="card" aria-label="${esc(c.title)} forecast">
        <h3>${esc(c.title)}</h3>
        <div class="body"><div class="chart">
          <img src="${forCompany(`/forecast/charts/${encodeURIComponent(c.chart_id)}.svg`)}"
               alt="${esc(c.title)} projected FY${esc(c.base_year)} onward across
                    ${esc((c.scenarios || []).length)} scenarios. The same values are in the table below."
               loading="lazy" width="720" height="380">
        </div></div>
        <p class="chartmeta"><span>${esc((c.scenarios || []).join(', '))}</span></p>
        ${chartDataTable('forecast', c.chart_id)}
      </section>`).join('')}</div>` : '';

  const demoted = Object.entries(f.demoted_derivations || {});
  const demotedCard = demoted.length ? `
    <h2 class="section">Derivations demoted to assumptions
      <span class="hint">a historical derivation that would be circular in a forecast</span></h2>
    <div class="card"><div class="body">${demoted.map(([k, v]) =>
      `<div class="note warn"><p><strong class="mono">${esc(k)}</strong></p><p>${esc(v)}</p></div>`).join('')}
    </div></div>` : '';

  const unseeded = Object.entries(f.unseeded || {});
  const unseededCard = unseeded.length ? `
    <h2 class="section">Assumptions history could not seed
      <span class="hint">metrics depending on these are not projected until an analyst supplies a value</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr>${sortableTh('a', 'Assumption')}${sortableTh('r', 'Reason')}</tr></thead>
      <tbody>${unseeded.map(([k, v]) =>
        `<tr><td class="metric">${esc(k)}</td><td class="wrap muted">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div></div></div>` : '';

  const notProjected = Object.entries(f.not_projected || {});
  const refusals = notProjected.length ? `
    <h2 class="section">Not projected
      <span class="hint">refused rather than extrapolated, with the reason and the years affected</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr>${sortableTh('m', 'Metric and reason')}${sortableTh('y', 'Years', 'num')}</tr></thead>
      <tbody>${notProjected.sort((x, y) => y[1] - x[1]).map(([k, v]) =>
        `<tr><td class="metric">${esc(k)}</td><td class="num">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div></div></div>` : '';

  const noFile = !f.assumptions_file ? `<div class="note warn"><p>
    No <code>assumptions.yaml</code> for this company, so the forecast runs on history the engine
    seeded itself — no management guidance, no consensus, no analyst judgement and no scenarios
    beyond the base case.</p></div>` : '';

  return `
    ${noFile}
    <div class="note"><p><strong>Classification: model output.</strong> Nothing below is a fact.
    Each figure is produced by the framework's driver graph from the assumptions listed under
    Assumptions, starting from the last reported year.</p></div>
    ${switcher}
    ${active.description ? `<div class="note"><p>${esc(active.description)}</p></div>` : ''}
    <section aria-label="Projected metrics">
      <div class="card"><div class="body flush">
        <div class="tablewrap" tabindex="0" role="region" aria-label="Forecast by metric, scrollable"><table>
        <caption class="sr-only">Projected values for the ${esc(active.name || state.scenario)} scenario</caption>
        <thead><tr>${plainTh('Metric')}
          <th class="num"><span class="th-inner">FY${esc(f.base_year)}<br><span class="muted">actual</span></span></th>
          ${years.map((y) => plainTh(`FY${y}`, 'num')).join('')}
          ${plainTh('Method')}${plainTh('Source')}${plainTh('Formula')}</tr></thead>
        <tbody>${rows || `<tr><td colspan="${cols.length + 4}" class="muted">Nothing projected in this scenario.</td></tr>`}</tbody>
      </table></div></div></div>
    </section>
    ${chartCards}
    ${demotedCard}
    ${unseededCard}
    ${refusals}`;
}

/* ---------- assumptions ---------- */

const RUNG = ['scenario', 'analyst_assumption', 'management_guidance', 'consensus', 'historical', 'derived'];

async function viewAssumptions() {
  const a = await api(forCompany('/assumptions'));
  const order = a.resolution_order || RUNG;

  const sorted = [...(a.assumptions || [])].sort((x, y) =>
    x.assumption_id.localeCompare(y.assumption_id) || RUNG.indexOf(x.type) - RUNG.indexOf(y.type));

  const rows = sorted.map((s) => {
    const value = s.value === null || s.value === undefined
      ? '<span class="muted">not set</span>'
      : (s.unit === 'ratio' ? `${(Number(s.value) * 100).toFixed(2)}%`
        : Number(s.value).toLocaleString(undefined, { maximumSignificantDigits: 6 }));
    const cited = s.source_document_id
      ? `<span class="mono">${esc(s.source_document_id)}</span>`
      : (s.source_fact_ids || []).length
        ? `${esc((s.source_fact_ids || []).length)} fact(s)`
        : '<span class="muted">–</span>';
    return `<tr>
      <th scope="row" class="metric" style="font-weight:400;background:none;text-transform:none;letter-spacing:0">${esc(s.assumption_id)}</th>
      <td class="num" data-sort="${esc(s.value ?? '')}">${value}</td>
      <td data-sort="${esc(RUNG.indexOf(s.type))}">${badge(s.type, shortType(s.type))}</td>
      <td class="muted">${esc(s.period || 'all years')}</td>
      <td class="muted">${esc(s.scenario || '–')}</td>
      <td class="wrap muted">${esc(s.rationale || s.description || '')}</td>
      <td class="mono muted">${cited}</td>
    </tr>`;
  }).join('');

  return `
    <div class="note">
      <p><strong>Resolution order.</strong>
      ${order.map((r, i) => `${i ? ' → ' : ''}${badge(r, shortType(r))}`).join('')}</p>
      <p>The analyst outranks management deliberately: deciding whether to believe guidance is the
      job. Within one rung, an assumption pinned to a fiscal year beats one applying to every year.
      Guidance and consensus cannot exist without a cited document, and an analyst cannot declare a
      <code>historical</code> assumption — only the engine can, because only it can cite the fact
      ids that make one checkable.</p>
    </div>

    <h2 class="section">Assumptions in force
      <span class="hint">${esc(sorted.length)} registered · base FY${esc(a.base_year ?? '–')}</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region"
      aria-label="Assumptions, scrollable"><table>
      <caption class="sr-only">Every assumption available to the forecast</caption>
      <thead><tr>${sortableTh('id', 'Assumption')}${sortableTh('value', 'Value', 'num')}
        ${sortableTh('type', 'Source')}${sortableTh('period', 'Period')}${sortableTh('scenario', 'Scenario')}
        ${plainTh('Basis / rationale')}${plainTh('Cited')}</tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="muted">No assumptions registered.</td></tr>'}</tbody>
    </table></div></div></div>`;
}

/* ---------- quality ---------- */

async function viewQuality() {
  const q = await api(forCompany('/quality'));
  const counts = q.counts || {};

  const checks = Object.values(q.checks || {}).sort((a, b) => a.check.localeCompare(b.check));
  const checkRows = checks.map((c) => {
    const total = c.passed + c.failed + c.not_evaluable;
    const pct = (n) => (total ? (n / total) * 100 : 0);
    return `<tr>
      <th scope="row" class="metric" style="font-weight:400;background:none;text-transform:none;letter-spacing:0">${esc(c.check)}</th>
      <td class="wrap muted">${esc(c.description || '')}</td>
      <td class="num" style="color:var(--ok)">${esc(c.passed)}</td>
      <td class="num" style="color:${c.failed ? 'var(--err)' : 'var(--muted)'}">${esc(c.failed)}</td>
      <td class="num muted">${esc(c.not_evaluable)}</td>
      <td><span class="bar" role="img"
        aria-label="${esc(c.passed)} passed, ${esc(c.failed)} failed, ${esc(c.not_evaluable)} not evaluable">
        <span style="width:${pct(c.passed)}%;background:var(--ok)"></span>
        <span style="width:${pct(c.failed)}%;background:var(--err)"></span>
        <span style="width:${pct(c.not_evaluable)}%;background:var(--muted)"></span>
      </span></td>
    </tr>`;
  }).join('');

  const issues = (q.issues || []).map((i) => `<tr>
    <td data-sort="${esc({ error: 0, warning: 1, info: 2 }[i.severity] ?? 9)}">
      <span class="sev sev-${esc(i.severity)}">${esc(i.severity)}</span></td>
    <td class="metric">${esc(i.check)}</td>
    <td class="metric muted">${esc(i.metric_id || '')} ${esc(i.period || '')}</td>
    <td class="wrap muted">${esc(i.message)}</td>
  </tr>`).join('');

  return `
    <div class="note"><p>A check with no data is reported as <strong>not evaluable</strong>, never
    as a pass. Error-severity issues block their facts from the analysis; warnings travel as flags
    to every value and chart point built on them.</p></div>

    <div class="grid cols-4">
      <div class="card stat"><p class="k">Errors</p>
        <p class="v" style="color:${counts.error ? 'var(--err)' : 'var(--ok)'}">${esc(counts.error ?? 0)}</p>
        <p class="n">block their facts</p></div>
      <div class="card stat"><p class="k">Warnings</p>
        <p class="v" style="color:${counts.warning ? 'var(--warn)' : 'var(--muted)'}">${esc(counts.warning ?? 0)}</p>
        <p class="n">propagate as flags</p></div>
      <div class="card stat"><p class="k">Info</p>
        <p class="v">${esc(counts.info ?? 0)}</p><p class="n">recorded, not acted on</p></div>
      <div class="card stat"><p class="k">Framework</p>
        <p class="v sm">${esc(q.framework || '–')}</p>
        <p class="n">${esc(q.historical_years ?? '–')} years requested</p></div>
    </div>

    <h2 class="section">Checks</h2>
    <div class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region"
      aria-label="Data quality checks, scrollable"><table>
      <thead><tr>${sortableTh('check', 'Check')}${plainTh('Description')}
        ${sortableTh('passed', 'Passed', 'num')}${sortableTh('failed', 'Failed', 'num')}
        ${sortableTh('ne', 'Not evaluable', 'num')}${plainTh('Distribution')}</tr></thead>
      <tbody>${checkRows}</tbody></table></div></div></div>

    <h2 class="section">Issues <span class="hint">${esc((q.issues || []).length)} raised</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region"
      aria-label="Data quality issues, scrollable"><table>
      <thead><tr>${sortableTh('sev', 'Severity')}${sortableTh('check', 'Check')}
        ${plainTh('Where')}${plainTh('Message')}</tr></thead>
      <tbody>${issues || '<tr><td colspan="4" class="muted">No issues raised.</td></tr>'}</tbody>
    </table></div></div></div>`;
}

/* ---------- shell wiring ---------- */

/* Deep linking is a convenience, not a feature the dashboard depends on. In a sandboxed or
 * srcdoc iframe the document's origin does not match the URL being written, and replaceState
 * throws a SecurityError; the same happens under some file:// policies. Losing the address bar
 * there is acceptable, taking the whole page down with it is not. */
function rememberLocation() {
  try {
    history.replaceState(null, '', `#${state.companyId}/${state.view}`);
  } catch {
    /* embedded or sandboxed: the view still works, the URL just does not follow it */
  }
}

const VIEWS = {
  overview: viewOverview,
  historical: viewHistorical,
  forecast: viewForecast,
  assumptions: viewAssumptions,
  quality: viewQuality,
};

const tabs = () => $$('#tabs .tab');

function selectTab(tab, { focus = false } = {}) {
  tabs().forEach((t) => {
    const on = t === tab;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
  });
  if (focus) tab.focus();
  state.view = tab.dataset.view;
  panel.setAttribute('aria-labelledby', tab.id);
  rememberLocation();
  render(VIEWS[state.view]);
}

$('#tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (tab) selectTab(tab);
});

// Arrow-key navigation, as a tablist is expected to behave.
$('#tabs').addEventListener('keydown', (e) => {
  const list = tabs();
  const i = list.indexOf(document.activeElement);
  if (i < 0) return;
  const move = { ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity }[e.key];
  if (move === undefined) return;
  e.preventDefault();
  const next = move === -Infinity ? 0
    : move === Infinity ? list.length - 1
      : (i + move + list.length) % list.length;
  selectTab(list[next], { focus: true });
});

function paintStages(stages) {
  const entries = Object.entries(stages || {});
  $('#stages').innerHTML = entries
    .map(([s, on]) => `<li class="stage ${on ? 'on' : ''}">${esc(s)}<span class="sr-only">: ${on ? 'completed' : 'not run'}</span></li>`)
    .join('');
  const done = entries.filter(([, on]) => on).map(([s]) => s);
  $('#stage-summary').textContent = done.length
    ? `Stages completed: ${done.join(', ')}.` : 'No pipeline stages have been run.';
}

$('#company').addEventListener('change', (e) => {
  state.companyId = e.target.value;
  state.scenario = null;
  state.cache.clear();
  const c = state.companies.find((x) => x.company_id === state.companyId);
  paintStages(c?.stages);
  rememberLocation();
  announce(`Switched to ${c?.name ?? state.companyId}.`);
  render(VIEWS[state.view]);
});

async function boot() {
  try {
    const health = await api('/api/health');
    $('#engine-version').textContent = `engine ${health.engine_version} · schema ${health.schema_version}`;

    state.companies = await api('/api/companies');
    if (!state.companies.length) {
      panel.setAttribute('aria-busy', 'false');
      panel.innerHTML = `<div class="card"><div class="body">
        <h2 class="section">No companies in this workspace</h2>
        <p class="muted">The server found no <code>*/config.yaml</code> under its companies
        directory. Point it at one with
        <code>research-engine serve --companies &lt;dir&gt;</code>.</p>
      </div></div>`;
      return;
    }

    const [hashCompany, hashView] = location.hash.slice(1).split('/');
    state.companyId = state.companies.some((c) => c.company_id === hashCompany)
      ? hashCompany : state.companies[0].company_id;

    $('#company').innerHTML = state.companies.map((c) =>
      `<option value="${esc(c.company_id)}" ${c.company_id === state.companyId ? 'selected' : ''}>
        ${esc(c.name)} — ${esc(c.ticker)}</option>`).join('');
    paintStages(state.companies.find((c) => c.company_id === state.companyId)?.stages);

    const tab = tabs().find((t) => t.dataset.view === hashView) || tabs()[0];
    selectTab(tab);
  } catch (err) {
    // Only a fetch failure means the server is unreachable. Saying so for every error sends
    // the reader to restart a server that was never the problem.
    const networkFailure = err instanceof TypeError || /fetch|network|load failed/i.test(err.message);
    panel.setAttribute('aria-busy', 'false');
    panel.innerHTML = `<div class="card"><div class="body">
      <h2 class="section">${networkFailure ? 'Could not reach the server' : 'The dashboard failed to start'}</h2>
      <p class="err">${esc(err.message)}</p>
      <p class="muted">${networkFailure
        ? 'Check that <code>research-engine serve</code> is still running, then reload.'
        : 'This is a fault in the dashboard itself rather than in the data behind it. Reload the page; if it persists, the message above is the detail to report.'}</p>
    </div></div>`;
  }
}

boot();
