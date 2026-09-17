/* Dashboard for the equity research engine.
 *
 * Reads only what the pipeline wrote. It formats and arranges; it never computes a financial
 * figure, because a number shown here that does not exist in `output/` would be unauditable.
 * Every value carrying a lineage id is clickable and traces back to a filed document.
 */

const state = {
  companies: [],
  companyId: null,
  view: 'overview',
  scenario: null,
  cache: new Map(),
};

const $ = (sel) => document.querySelector(sel);
const main = $('#main');

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
  if (res.status === 409) {
    const body = await res.json();
    throw new StageMissing(body.detail || {});
  }
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

const badge = (kind, text) => `<span class="badge b-${esc(kind)}">${esc(text ?? kind)}</span>`;

/* Decimal arithmetic leaves ratios with 28 significant digits. Shortening one for display is
 * presentation only: the full value stays in the parquet datasets and in lineage.json. */
function tidyAttr(v) {
  const s = String(v ?? '');
  if (!/^-?\d+\.\d{8,}$/.test(s)) return s;
  const n = Number(s);
  return Number.isFinite(n) ? `${n.toPrecision(10).replace(/\.?0+$/, '')}…` : s;
}

/* ---------- lineage drawer ---------- */

const drawer = $('#drawer');

function closeDrawer() {
  drawer.hidden = true;
  document.body.style.overflow = '';
}

$('#drawer-close').addEventListener('click', closeDrawer);
drawer.addEventListener('click', (e) => { if (e.target === drawer) closeDrawer(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !drawer.hidden) closeDrawer(); });

const KIND_ORDER = { model_output: 0, assumption: 1, fact: 2, document: 3, source: 4 };

async function openLineage(nodeId, label) {
  drawer.hidden = false;
  document.body.style.overflow = 'hidden';
  $('#drawer-title').textContent = label || nodeId;
  const body = $('#drawer-body');
  body.innerHTML = '<div class="empty">Tracing…</div>';
  try {
    const t = await api(forCompany(`/lineage/${encodeURIComponent(nodeId)}`));
    const nodes = [...t.nodes].sort((a, b) =>
      (KIND_ORDER[a.kind] ?? 9) - (KIND_ORDER[b.kind] ?? 9) || a.label.localeCompare(b.label));
    const chain = nodes.map((n) => {
      const attrs = Object.entries(n.attributes || {})
        .map(([k, v]) => `${esc(titleize(k))}: ${esc(tidyAttr(v))}`).join(' &middot; ');
      const label = n.kind === 'source'
        ? `<a href="${esc(n.label)}" target="_blank" rel="noopener noreferrer">${esc(n.label)}</a>`
        : esc(n.label);
      return `<li class="link k-${esc(n.kind)}">
        <div class="kind">${esc(titleize(n.kind))}</div>
        <div class="lbl">${label}</div>
        ${attrs ? `<div class="attrs">${attrs}</div>` : ''}
      </li>`;
    }).join('');

    body.innerHTML = `
      <div class="note">
        This value resolves through <strong>${t.nodes.length}</strong> lineage
        ${t.nodes.length === 1 ? 'node' : 'nodes'} and
        <strong>${t.depth}</strong> ${t.depth === 1 ? 'level' : 'levels'} to
        <strong>${t.source_urls.length}</strong> source
        ${t.source_urls.length === 1 ? 'document' : 'documents'}.
        ${t.truncated ? ' The chain was truncated for display.' : ''}
      </div>
      <ul class="chain">${chain}</ul>`;
  } catch (err) {
    body.innerHTML = `<div class="empty err">${esc(err.message)}</div>`;
  }
}

main.addEventListener('click', (e) => {
  const btn = e.target.closest('.trace');
  if (btn) openLineage(btn.dataset.node, btn.dataset.label);
});

/* ---------- shells ---------- */

const traceable = (id, text, label) =>
  id ? `<button class="trace" data-node="${esc(id)}" data-label="${esc(label || '')}"
        title="Trace to source">${esc(text)}</button>` : esc(text);

function stageMissingCard(stage) {
  const cmd = { ingest: 'make ingest', quality: 'make quality', analyze: 'make analyze', forecast: 'make forecast' }[stage];
  return `<div class="card"><div class="body">
    <h2 class="section">Nothing to show yet</h2>
    <p class="muted">The <code class="mono">${esc(stage)}</code> stage has not been run for this
    company, so there is no data behind this view. Run
    <code class="mono">${esc(cmd)} CONFIG=companies/${esc(state.companyId)}/config.yaml</code> and reload.</p>
    <p class="faint">An empty table here would be indistinguishable from a company with no issues,
    which is why the dashboard says nothing rather than showing zeros.</p>
  </div></div>`;
}

async function render(fn) {
  main.innerHTML = '<div class="empty">Loading…</div>';
  try {
    main.innerHTML = await fn();
  } catch (err) {
    main.innerHTML = err instanceof StageMissing
      ? stageMissingCard(err.stage)
      : `<div class="empty err">${esc(err.message)}</div>`;
  }
}

/* ---------- overview ---------- */

async function viewOverview() {
  const o = await api(forCompany(''));
  const c = o.counts || {};
  const issues = c.issues || {};
  const stat = (k, v, n) => `<div class="card stat"><div class="k">${esc(k)}</div>
    <div class="v${String(v).length > 9 ? ' sm' : ''}">${esc(v)}</div>
    ${n ? `<div class="n">${esc(n)}</div>` : ''}</div>`;

  let documents = '';
  if (o.stages.ingest) {
    const docs = await api(forCompany('/documents'));
    documents = `
      <h2 class="section">Source documents
        <span class="hint">content-addressed; a changed source becomes a new version, never an overwrite</span></h2>
      <div class="card"><div class="body flush"><div class="tablewrap"><table>
        <thead><tr><th>Document</th><th>Status</th><th>SHA-256</th><th>Source</th></tr></thead>
        <tbody>${docs.map((d) => `<tr>
          <td class="metric">${esc(d.document_id)}</td>
          <td>${esc(d.status)}</td>
          <td class="mono faint">${esc((d.file_hash || '').slice(0, 16))}</td>
          <td class="wrap mono"><a href="${esc(d.source)}" target="_blank" rel="noopener noreferrer">${esc(d.source)}</a></td>
        </tr>`).join('')}</tbody></table></div></div></div>`;
  }

  const warnings = (o.warnings || []).length
    ? `<h2 class="section">Warnings from ingestion</h2>
       <div class="card"><div class="body">${o.warnings.map((w) =>
         `<div class="note warn">${esc(w)}</div>`).join('')}</div></div>`
    : '';

  const horizon = o.forecast_horizon
    ? `<dt>Forecast</dt><dd>FY${esc(o.forecast_horizon.base_year)} base &rarr;
        FY${esc((o.forecast_horizon.years || []).slice(-1)[0] ?? '–')},
        ${esc((o.forecast_horizon.scenarios || []).length)} scenario(s)</dd>`
    : '';

  return `
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
        <dt>Company</dt><dd>${esc(o.name)} <span class="faint">(${esc(o.ticker)}, ${esc(o.exchange)})</span></dd>
        <dt>Country</dt><dd>${esc(o.country)}</dd>
        <dt>Currency</dt><dd>${esc(o.reporting_currency || '–')}</dd>
        <dt>Framework</dt><dd>${o.framework
          ? `<strong>${esc(o.framework.name)}</strong> <span class="faint">via ${esc(o.framework.method)}
             — ${esc(o.framework.evidence)}</span>`
          : '<span class="faint">not classified yet</span>'}</dd>
        <dt>Fiscal year</dt><dd>${o.fiscal_calendar
          ? `ends month ${esc(o.fiscal_calendar.fiscal_year_end_month)}
             <span class="faint">(${esc(o.fiscal_calendar.convention)})</span>` : '–'}</dd>
        ${horizon}
      </dl></div></div>

      <div class="card"><div class="body"><dl class="kv">
        <dt>Data quality</dt><dd>
          <span class="sev sev-error">${esc(issues.error ?? 0)} errors</span> &middot;
          <span class="sev sev-warning">${esc(issues.warning ?? 0)} warnings</span> &middot;
          <span class="sev sev-info">${esc(issues.info ?? 0)} info</span></dd>
        <dt>Documents</dt><dd>${esc(c.documents ?? 0)} registered</dd>
        <dt>Engine</dt><dd class="mono">${esc(o.versions?.engine_version || '–')}
          <span class="faint">schema ${esc(o.versions?.schema_version || '–')},
          parser ${esc(o.versions?.parser_version || '–')}</span></dd>
        <dt>Last ingest</dt><dd class="mono faint">${esc((o.generated_at || '–').slice(0, 19).replace('T', ' '))}</dd>
        <dt>Stages</dt><dd>${Object.entries(o.stages).map(([s, on]) =>
          `<span class="stage ${on ? 'on' : ''}">${esc(s)}</span>`).join(' ')}</dd>
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
      <span class="hint">framework-defined; hollow markers and hatched bars are derived values, † flags a
      warning on an input</span></h2>
    <div class="charts">${charts.map((c) => `
      <div class="card">
        <h3>${esc(c.title)}</h3>
        <div class="body"><div class="chart">
          <img src="${forCompany(`/charts/${encodeURIComponent(c.chart_id)}.svg`)}" alt="${esc(c.title)}" loading="lazy">
        </div></div>
        <div class="chartmeta">
          <span>FY${esc(c.years[0])}–FY${esc(c.years[c.years.length - 1])}</span>
          ${c.derived_points ? `<span class="derivedmark">${esc(c.derived_points)} derived point(s)</span>` : ''}
          ${c.flagged_points ? `<span class="flag">† ${esc(c.flagged_points)} flagged</span>` : ''}
        </div>
      </div>`).join('')}</div>` : '';

  let rows = '';
  let category = null;
  for (const s of a.series) {
    if (s.category !== category) {
      category = s.category;
      rows += `<tr class="rowgroup"><td colspan="${years.length + 3}">${esc(titleize(category))}</td></tr>`;
    }
    const byYear = new Map(s.points.map((p) => [p.fiscal_year, p]));
    const cells = years.map((y) => {
      const p = byYear.get(y);
      if (!p) return '<td class="num faint">–</td>';
      const marks = (p.uses_derived_facts ? '<span class="derivedmark" title="uses engine-derived facts">*</span>' : '')
        + (p.quality_flags.length ? `<span class="flag" title="${esc(p.quality_flags.join(', '))}">†</span>` : '');
      return `<td class="num">${traceable(p.value_id, fmtValue(p.value, s.unit_kind, s.currency),
        `${s.analytic_id} FY${y}`)}${marks}</td>`;
    }).join('');
    const sum = s.summary || {};
    rows += `<tr>
      <td class="metric">${esc(s.analytic_id)}</td>
      ${cells}
      <td class="num faint">${sum.median != null ? fmtValue(sum.median, s.unit_kind, s.currency) : '–'}</td>
      <td class="faint">${esc(s.points[0]?.basis || '')}</td>
    </tr>`;
  }

  const notComputed = Object.entries(a.not_computed || {});
  const gaps = notComputed.length ? `
    <h2 class="section">Not computed
      <span class="hint">every analytic-year the engine declined to produce, with its reason</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Analytic and reason</th><th class="num">Years</th></tr></thead>
      <tbody>${notComputed.sort((x, y) => y[1] - x[1]).map(([k, v]) =>
        `<tr><td class="metric">${esc(k)}</td><td class="num">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div></div></div>` : '';

  return `
    <div class="note"><strong>Classification: model output.</strong> Every figure below is computed
    by the engine from reported and derived facts. Click any value to trace it to the filing it
    came from. <span class="derivedmark">*</span> uses engine-derived facts;
    <span class="flag">†</span> an input carries a data-quality warning.</div>

    <h2 class="section">Analytics <span class="hint">${esc(a.series.length)} series ·
      FY${esc(years[0] ?? '–')}–FY${esc(years[years.length - 1] ?? '–')}</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Analytic</th>${years.map((y) => `<th class="num">FY${esc(y)}</th>`).join('')}
        <th class="num">Median</th><th>Basis</th></tr></thead>
      <tbody>${rows}</tbody></table></div></div></div>
    ${chartCards}
    ${gaps}`;
}

/* ---------- forecast ---------- */

async function viewForecast() {
  const f = await api(forCompany('/forecast'));
  const scenarios = f.scenarios || [];
  if (!state.scenario || !scenarios.some((s) => s.id === state.scenario)) {
    state.scenario = scenarios[0]?.id ?? null;
  }
  const active = scenarios.find((s) => s.id === state.scenario) || { metrics: [] };
  const years = f.forecast_years || [];
  const cols = [f.base_year, ...years];

  const switcher = `<div class="scenarios">${scenarios.map((s) =>
    `<button class="scn ${s.id === state.scenario ? 'active' : ''}" data-scenario="${esc(s.id)}">
      ${esc(s.name)}</button>`).join('')}</div>`;

  const plan = new Map((f.projection_plan || []).map((p) => [p.metric_id, p]));

  const rows = active.metrics.map((m) => {
    const byYear = new Map(m.points.map((p) => [p.fiscal_year, p]));
    const cells = cols.map((y) => {
      const p = byYear.get(y);
      if (!p) return '<td class="num faint">–</td>';
      return `<td class="num">${traceable(p.value_id, fmtValue(p.value, m.unit_kind, m.currency),
        `${m.metric_id} FY${y}`)}</td>`;
    }).join('');
    const projected = years.map((y) => byYear.get(y)).find(Boolean);
    const src = projected?.assumption_types?.[0];
    // No projected point means the chain stopped upstream; saying "actual" would imply the
    // engine chose to carry the last reported year forward, which is not what happened.
    const method = projected
      ? badge(projected.method, projected.method === 'driver_formula' ? 'driver' : projected.method)
      : '<span class="faint">not projected</span>';
    return `<tr>
      <td class="metric">${esc(m.metric_id)}</td>
      ${cells}
      <td>${method}</td>
      <td>${src ? badge(src, src.replace('_assumption', '').replace('management_', '')) : '<span class="faint">–</span>'}</td>
      <td class="wrap mono faint">${esc(plan.get(m.metric_id)?.formula || '')}</td>
    </tr>`;
  }).join('');

  const demoted = Object.entries(f.demoted_derivations || {});
  const demotedCard = demoted.length ? `
    <h2 class="section">Derivations demoted to assumptions
      <span class="hint">a historical derivation that would be circular in a forecast</span></h2>
    <div class="card"><div class="body">${demoted.map(([k, v]) =>
      `<div class="note warn"><strong class="mono">${esc(k)}</strong><br>${esc(v)}</div>`).join('')}
    </div></div>` : '';

  const unseeded = Object.entries(f.unseeded || {});
  const unseededCard = unseeded.length ? `
    <h2 class="section">Assumptions history could not seed
      <span class="hint">metrics depending on these are not projected until an analyst supplies a value</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Assumption</th><th>Reason</th></tr></thead>
      <tbody>${unseeded.map(([k, v]) =>
        `<tr><td class="metric">${esc(k)}</td><td class="wrap muted">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div></div></div>` : '';

  const notProjected = Object.entries(f.not_projected || {});
  const refusals = notProjected.length ? `
    <h2 class="section">Not projected
      <span class="hint">refused rather than extrapolated, with the reason and the years affected</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Metric and reason</th><th class="num">Years</th></tr></thead>
      <tbody>${notProjected.sort((x, y) => y[1] - x[1]).map(([k, v]) =>
        `<tr><td class="metric">${esc(k)}</td><td class="num">${esc(v)}</td></tr>`).join('')}
      </tbody></table></div></div></div>` : '';

  const noFile = !f.assumptions_file ? `<div class="note warn">
    No <code class="mono">assumptions.yaml</code> for this company, so the forecast runs on history
    the engine seeded itself — no management guidance, no consensus, no analyst judgement and no
    scenarios beyond the base case.</div>` : '';

  return `
    ${noFile}
    <div class="note"><strong>Classification: model output.</strong> Nothing below is a fact. Each
    figure is produced by the framework's driver graph from the assumptions listed under
    Assumptions, starting from the last reported year. Click any value to trace it.</div>
    ${switcher}
    ${active.description ? `<div class="note">${esc(active.description)}</div>` : ''}
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Metric</th>
        <th class="num">FY${esc(f.base_year)}<br><span class="faint">actual</span></th>
        ${years.map((y) => `<th class="num">FY${esc(y)}</th>`).join('')}
        <th>Method</th><th>Source</th><th>Formula</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="${cols.length + 3}" class="muted">Nothing projected in this scenario.</td></tr>`}</tbody>
    </table></div></div></div>
    ${demotedCard}
    ${unseededCard}
    ${refusals}`;
}

main.addEventListener('click', (e) => {
  const btn = e.target.closest('.scn');
  if (!btn) return;
  state.scenario = btn.dataset.scenario;
  render(viewForecast);
});

/* ---------- assumptions ---------- */

const RUNG = ['scenario', 'analyst_assumption', 'management_guidance', 'consensus', 'historical', 'derived'];

async function viewAssumptions() {
  const a = await api(forCompany('/assumptions'));
  const order = a.resolution_order || RUNG;

  const sorted = [...(a.assumptions || [])].sort((x, y) =>
    x.assumption_id.localeCompare(y.assumption_id)
    || RUNG.indexOf(x.type) - RUNG.indexOf(y.type));

  const rows = sorted.map((s) => {
    const value = s.value === null || s.value === undefined
      ? '<span class="faint">not set</span>'
      : (s.unit === 'ratio' ? `${(Number(s.value) * 100).toFixed(2)}%`
        : Number(s.value).toLocaleString(undefined, { maximumSignificantDigits: 6 }));
    return `<tr>
      <td class="metric">${esc(s.assumption_id)}</td>
      <td class="num">${value}</td>
      <td>${badge(s.type, s.type.replace('_assumption', '').replace('management_', ''))}</td>
      <td class="faint">${esc(s.period || 'all years')}</td>
      <td class="faint">${esc(s.scenario || '–')}</td>
      <td class="wrap muted">${esc(s.rationale || s.description || '')}</td>
      <td class="mono faint">${s.source_document_id ? esc(s.source_document_id)
        : (s.source_fact_ids || []).length
          ? `<span title="${esc((s.source_fact_ids || []).join(', '))}">${esc((s.source_fact_ids || []).length)} facts</span>`
          : '–'}</td>
    </tr>`;
  }).join('');

  return `
    <div class="note"><strong>Resolution order.</strong>
      ${order.map((r, i) => `${i ? ' &rarr; ' : ''}${badge(r, r.replace('_assumption', '').replace('management_', ''))}`).join('')}
      <br>The analyst outranks management deliberately: deciding whether to believe guidance is the
      job. Within one rung, an assumption pinned to a fiscal year beats one applying to every year.
      Guidance and consensus cannot exist without a cited document, and an analyst cannot declare a
      <span class="mono">historical</span> assumption — only the engine can, because only it can cite
      the fact ids that make one checkable.</div>

    <h2 class="section">Assumptions in force
      <span class="hint">${esc(sorted.length)} registered · base FY${esc(a.base_year ?? '–')}</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Assumption</th><th class="num">Value</th><th>Source</th><th>Period</th>
        <th>Scenario</th><th>Basis / rationale</th><th>Cited</th></tr></thead>
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
      <td class="metric">${esc(c.check)}</td>
      <td class="wrap muted">${esc(c.description || '')}</td>
      <td class="num" style="color:var(--ok)">${esc(c.passed)}</td>
      <td class="num" style="color:${c.failed ? 'var(--err)' : 'var(--faint)'}">${esc(c.failed)}</td>
      <td class="num faint">${esc(c.not_evaluable)}</td>
      <td style="min-width:120px"><div class="bar">
        <span style="width:${pct(c.passed)}%;background:var(--ok)"></span>
        <span style="width:${pct(c.failed)}%;background:var(--err)"></span>
        <span style="width:${pct(c.not_evaluable)}%;background:var(--faint)"></span>
      </div></td>
    </tr>`;
  }).join('');

  const issues = (q.issues || []).map((i) => `<tr>
    <td><span class="sev sev-${esc(i.severity)}">${esc(i.severity)}</span></td>
    <td class="metric">${esc(i.check)}</td>
    <td class="metric faint">${esc(i.metric_id || '')} ${esc(i.period || '')}</td>
    <td class="wrap muted">${esc(i.message)}</td>
  </tr>`).join('');

  return `
    <div class="note">A check with no data is reported as <strong>not evaluable</strong>, never as a
    pass. Error-severity issues block their facts from the analysis; warnings travel as flags to
    every value and chart point built on them.</div>

    <div class="grid cols-4">
      <div class="card stat"><div class="k">Errors</div>
        <div class="v" style="color:${counts.error ? 'var(--err)' : 'var(--ok)'}">${esc(counts.error ?? 0)}</div>
        <div class="n">block their facts</div></div>
      <div class="card stat"><div class="k">Warnings</div>
        <div class="v" style="color:${counts.warning ? 'var(--warn)' : 'var(--faint)'}">${esc(counts.warning ?? 0)}</div>
        <div class="n">propagate as flags</div></div>
      <div class="card stat"><div class="k">Info</div>
        <div class="v">${esc(counts.info ?? 0)}</div><div class="n">recorded, not acted on</div></div>
      <div class="card stat"><div class="k">Framework</div>
        <div class="v sm">${esc(q.framework || '–')}</div>
        <div class="n">${esc(q.historical_years ?? '–')} years requested</div></div>
    </div>

    <h2 class="section">Checks</h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Check</th><th>Description</th><th class="num">Passed</th><th class="num">Failed</th>
        <th class="num">Not evaluable</th><th></th></tr></thead>
      <tbody>${checkRows}</tbody></table></div></div></div>

    <h2 class="section">Issues <span class="hint">${esc((q.issues || []).length)} raised</span></h2>
    <div class="card"><div class="body flush"><div class="tablewrap"><table>
      <thead><tr><th>Severity</th><th>Check</th><th>Where</th><th>Message</th></tr></thead>
      <tbody>${issues || '<tr><td colspan="4" class="muted">No issues raised.</td></tr>'}</tbody>
    </table></div></div></div>`;
}

/* ---------- shell wiring ---------- */

const VIEWS = {
  overview: viewOverview,
  historical: viewHistorical,
  forecast: viewForecast,
  assumptions: viewAssumptions,
  quality: viewQuality,
};

$('#tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === tab));
  state.view = tab.dataset.view;
  history.replaceState(null, '', `#${state.companyId}/${state.view}`);
  render(VIEWS[state.view]);
});

function paintStages(stages) {
  $('#stages').innerHTML = Object.entries(stages || {})
    .map(([s, on]) => `<span class="stage ${on ? 'on' : ''}" title="${on ? 'completed' : 'not run'}">${esc(s)}</span>`)
    .join('');
}

$('#company').addEventListener('change', (e) => {
  state.companyId = e.target.value;
  state.scenario = null;
  state.cache.clear();
  const c = state.companies.find((x) => x.company_id === state.companyId);
  paintStages(c?.stages);
  history.replaceState(null, '', `#${state.companyId}/${state.view}`);
  render(VIEWS[state.view]);
});

async function boot() {
  try {
    const health = await api('/api/health');
    $('#engine-version').textContent = `engine ${health.engine_version} · schema ${health.schema_version}`;

    state.companies = await api('/api/companies');
    if (!state.companies.length) {
      main.innerHTML = `<div class="card"><div class="body">
        <h2 class="section">No companies in this workspace</h2>
        <p class="muted">The server found no <code class="mono">*/config.yaml</code> under its
        companies directory. Point it at one with
        <code class="mono">research-engine serve --companies &lt;dir&gt;</code>.</p>
      </div></div>`;
      return;
    }

    const [hashCompany, hashView] = (location.hash.slice(1).split('/'));
    state.companyId = state.companies.some((c) => c.company_id === hashCompany)
      ? hashCompany : state.companies[0].company_id;
    if (hashView && VIEWS[hashView]) {
      state.view = hashView;
      document.querySelectorAll('.tab').forEach((t) =>
        t.classList.toggle('active', t.dataset.view === hashView));
    }

    $('#company').innerHTML = state.companies.map((c) =>
      `<option value="${esc(c.company_id)}" ${c.company_id === state.companyId ? 'selected' : ''}>
        ${esc(c.name)} — ${esc(c.ticker)}</option>`).join('');
    paintStages(state.companies.find((c) => c.company_id === state.companyId)?.stages);

    render(VIEWS[state.view]);
  } catch (err) {
    main.innerHTML = `<div class="empty err">${esc(err.message)}</div>`;
  }
}

boot();
