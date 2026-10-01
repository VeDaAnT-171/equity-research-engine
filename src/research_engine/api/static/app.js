/* Equity Research — company pages built from SEC filings.
 *
 * This page reads what the analysis wrote and never calculates a financial figure of its own:
 * a number shown here that is not in the company's output would have no filing behind it.
 * Formatting (millions, percentages, parentheses for negatives) is presentation only.
 *
 * Interaction rules:
 *   - every control is a real button or link, operable from the keyboard;
 *   - nothing is available only on hover; marks are explained in a visible legend;
 *   - every chart has its numbers in a table beside it;
 *   - loading reserves the space the content will take, so nothing jumps;
 *   - async changes are announced once, through a single polite live region.
 */

const state = {
  companies: [],
  companyId: null,
  view: 'summary',
  scenario: null,
  statement: null,
  allYears: false,
  cache: new Map(),
  requestId: 0,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const panel = $('#panel');
const live = $('#live');
const announce = (msg) => { live.textContent = msg; };

/* ---------- data access ---------- */

class NotReady extends Error {
  constructor(detail) { super(detail.error || 'not ready'); this.stage = detail.stage; }
}

/* A published snapshot has no server: each API answer is a file beside the page, so
   `/api/x` becomes `api/x.json`, relative, which also works under a project path. */
const SNAPSHOT = document.querySelector('meta[name="research-engine-snapshot"]');
const resolve = (path) => {
  if (!SNAPSHOT) return path;
  const rel = path.replace(/^\//, '');
  return /\.(svg|png)$/.test(rel) ? rel : `${rel}.json`;
};

async function api(path, { fresh = false } = {}) {
  if (!fresh && state.cache.has(path)) return state.cache.get(path);
  const res = await fetch(resolve(path));
  if (res.status === 409) throw new NotReady((await res.json()).detail || {});
  if (SNAPSHOT && res.status === 404) throw new Error('This detail is not included in the published snapshot.');
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not json */ }
    throw new Error(typeof detail === 'string' ? detail : 'The request could not be completed.');
  }
  const data = await res.json();
  if (SNAPSHOT && data && data.__status === 409) throw new NotReady(data.detail || {});
  state.cache.set(path, data);
  return data;
}

const forCompany = (suffix) => `/api/companies/${encodeURIComponent(state.companyId)}${suffix}`;
const optional = (p) => p.catch((err) => { if (err instanceof NotReady) return null; throw err; });

async function glossary() {
  try { return await api(forCompany('/glossary')); } catch { return { labels: {}, units: {}, statements: {} }; }
}

/* ---------- formatting ---------- */

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const symbolFor = (cur) => (!cur || cur === 'USD' ? '$' : `${cur} `);

function money(v, cur, digits = 1) {
  if (v === null || v === undefined) return '–';
  const s = symbolFor(cur);
  const sign = v < 0 ? '-' : '';
  const a = Math.abs(v);
  if (a >= 1e12) return `${sign}${s}${(a / 1e12).toFixed(2)}tn`;
  if (a >= 1e9) return `${sign}${s}${(a / 1e9).toFixed(digits)}bn`;
  if (a >= 1e6) return `${sign}${s}${(a / 1e6).toFixed(digits)}m`;
  return `${sign}${s}${a.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

const pct = (v, digits = 1) => `${(v * 100).toFixed(digits)}%`;
const signedPct = (v) => `${v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`;

/* Headline format: tiles, ratios, estimates and the source panel. */
function fmt(v, unit, cur) {
  if (v === null || v === undefined) return '–';
  if (unit === 'ratio') return pct(v);
  if (unit === 'multiple') return `${v.toFixed(2)}x`;
  if (unit === 'currency') return money(v, cur);
  if (unit === 'currency_per_share') return `${symbolFor(cur)}${v.toFixed(2)}`;
  if (unit === 'count') return Math.abs(v) >= 1e6 ? `${(v / 1e6).toLocaleString('en-US', { maximumFractionDigits: 1 })}m` : v.toLocaleString('en-US');
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/* Statement format: millions, negatives in parentheses, as financial statements are printed. */
function fmtStatement(v, unit) {
  if (v === null || v === undefined) return '–';
  let text;
  if (unit === 'currency') text = Math.abs(v / 1e6).toLocaleString('en-US', { maximumFractionDigits: 0 });
  else if (unit === 'currency_per_share') text = Math.abs(v).toFixed(2);
  else if (unit === 'count') text = Math.abs(v / 1e6).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  else if (unit === 'ratio') text = pct(Math.abs(v));
  else text = Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
  return v < 0 ? `(${text})` : text;
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'];

function dateText(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()].slice(0, 3)} ${d.getUTCFullYear()}`;
}

const lower = (s) => (s && /^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s);

/* ---------- fragments ---------- */

const traceable = (id, text, label, cls = '', shown = text) =>
  id ? `<button type="button" class="trace ${cls}" data-node="${esc(id)}" data-label="${esc(label || '')}"
        data-shown="${esc(shown)}" aria-label="${esc(text)}, ${esc(label || '')}: show source">${esc(text)}</button>`
    : esc(text);

const th = (label, cls = '') => `<th scope="col" class="${cls}">${label}</th>`;

function change(v) {
  if (v === null || v === undefined) return '';
  const dir = v > 0 ? 'up' : v < 0 ? 'down' : '';
  return `<span class="chg ${dir}"><span class="sr-only">${v > 0 ? 'up' : v < 0 ? 'down' : 'unchanged'} </span>${esc(signedPct(v).replace(/^[+-]/, ''))}</span>`;
}

function chartCard(c, scope) {
  const src = scope === 'forecast'
    ? forCompany(`/forecast/charts/${encodeURIComponent(c.chart_id)}.svg`)
    : forCompany(`/charts/${encodeURIComponent(c.chart_id)}.svg`);
  const years = c.years || [];
  const span = years.length ? `FY${years[0]}–FY${years[years.length - 1]}` : '';
  const notes = (c.notes || []).length
    ? `<p class="chartnote"><strong>Note:</strong> ${c.notes.map(esc).join(' ')}</p>` : '';
  const fb = (c.fallback_for || []).length
    ? `<p class="chartnote"><span class="mark fb" aria-hidden="true">‡</span> Uses a trend estimate for
       ${c.fallback_for.map((m) => esc(lower(label(m)))).join(', ')}.</p>` : '';
  // The image carries its own title, so the card does not print it a second time.
  return `<section class="card" aria-label="${esc(c.title)}">
    <h3 class="sr-only">${esc(c.title)}, ${esc(span)}</h3>
    <div class="chart"><img src="${resolve(src)}" loading="lazy" width="720" height="380"
      alt="${esc(c.title)}, ${esc(span)}. The figures are in the table below the chart."></div>
    ${notes}${fb}
    <details class="data-table" data-chart="${esc(c.chart_id)}" data-scope="${esc(scope)}">
      <summary>Show the figures</summary><div class="tablewrap" data-slot tabindex="0" role="region" aria-label="Chart figures, scrollable"><p class="empty">Loading…</p></div>
    </details>
  </section>`;
}

/* Labels come from the glossary written with the figures, so a name cannot describe a
   different definition than the number beside it. */
let LABELS = {};
let UNITS = {};
const label = (id) => LABELS[id] || String(id ?? '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

function yearsShown(all) {
  if (state.allYears || all.length <= 10) return all;
  return all.slice(-10);
}

function yearToggle(all) {
  if (all.length <= 10) return '';
  return `<button type="button" class="linkbtn" data-action="years">${state.allYears
    ? 'Show last 10 years' : `Show all ${all.length} years`}</button>`;
}

/* ---------- source panel ---------- */

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
  const focusable = Array.from(drawer.querySelectorAll('a[href], button')).filter((el) => el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});

/* `deposits[t-1] * (1 + growth.deposits)` -> `Deposits (prior year) × (1 + deposit growth)`. */
function formulaText(formula) {
  if (!formula) return '';
  return formula
    .replace(/average\(([a-z_][a-z0-9_.]*)\)|([a-z_][a-z0-9_]*(?:\.[a-z0-9_]+)?)(\[t-1\])?/g,
      (m, avg, id, prior) => {
        if (avg) return `average ${lower(label(avg))} (start and end of year)`;
        if (/^\d/.test(id)) return m;
        return `${label(id)}${prior ? ' (prior year)' : ''}`;
      })
    .replace(/ \/ /g, ' ÷ ').replace(/ \* /g, ' × ').replace(/ - /g, ' − ')
    .replace(/^\((.*)\)( ÷ .*)$/, '$1$2');
}

const METHOD = {
  actual: 'Reported', driver_formula: 'Model', derivation: 'Calculated', growth: 'Trend', level: 'Held at recent level',
};
const SOURCE = {
  historical: 'Company history', analyst_assumption: 'Analyst', management_guidance: 'Company guidance',
  consensus: 'Consensus', scenario: 'Scenario', derived: 'Calculated',
};

function filingLink(accession, cik) {
  if (!accession || !cik) return '';
  return `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}/${accession}-index.htm`;
}

async function openSource(nodeId, title, shown) {
  lastFocus = document.activeElement;
  drawer.hidden = false;
  document.body.style.overflow = 'hidden';
  $('#drawer-title').textContent = title || 'Figure';
  const body = $('#drawer-body');
  body.innerHTML = '<p class="empty">Loading…</p>';
  $('#drawer-close').focus();
  try {
    const [t, assumptions, library] = await Promise.all([
      api(forCompany(`/lineage/${encodeURIComponent(nodeId)}`)),
      optional(api(forCompany('/assumptions'))).catch(() => null),
      api(forCompany('/library')).catch(() => null),
    ]);
    // A figure read from a library document names that document, not the SEC's structured data.
    const docTitle = new Map((library?.documents || []).filter((d) => d.document_id).map((d) => [d.document_id, d]));
    const parentDoc = (id) => (t.edges || []).filter((e) => e.child === id).map((e) => docTitle.get(e.parent)).find(Boolean);
    const cur = (state.companies.find((c) => c.company_id === state.companyId) || {}).currency;
    const sourceUrl = (t.source_urls || [])[0] || '';
    const cik = (sourceUrl.match(/CIK0*(\d+)/) || [])[1];
    const root = t.nodes.find((n) => n.id === t.node_id) || t.nodes[0];
    const attr = root.attributes || {};
    const unitOf = (id) => UNITS[id] || (UNITS[String(id).split(':').pop()] ?? null);

    let html = shown ? `<p class="bigvalue">${esc(shown)}</p>` : '';
    if (root.kind === 'model_output' && attr.formula) {
      const method = attr.method ? `<p class="muted">${esc(METHOD[attr.method] || attr.method)}${
        attr.method === 'growth' ? ': last year’s figure grown at the rate below' : ''}</p>` : '';
      html += `<h3>How it’s calculated</h3><p class="formula">${esc(formulaText(attr.formula))}</p>${method}`;
    } else if (root.kind === 'fact' && attr.formula) {
      html += `<h3>How it’s calculated</h3><p class="formula">${esc(formulaText(attr.formula))}</p>
        <p class="muted">Not reported directly; calculated from the reported lines below.</p>`;
    }

    const rest = t.nodes.filter((n) => n !== root);
    const assumptionRows = rest.filter((n) => n.kind === 'assumption').map((n) => {
      const a = (assumptions?.assumptions || []).find((x) => x.assumption_id === n.label && x.type === n.attributes?.type)
        || (assumptions?.assumptions || []).find((x) => x.assumption_id === n.label);
      const value = a && a.value !== null && a.value !== undefined ? (a.unit === 'ratio' ? pct(a.value, 2) : a.value) : '';
      return `<li><div class="row"><span class="name">${esc(a?.label || label(n.label))}</span>
        <span class="val">${esc(value)}</span></div>
        <p class="how">${esc(SOURCE[n.attributes?.type] || '')}${a?.basis_text ? ` · ${esc(a.basis_text)}` : ''}</p></li>`;
    }).join('');
    if (assumptionRows) html += `<h3>Assumptions</h3><ul class="sources">${assumptionRows}</ul>`;

    const outputs = rest.filter((n) => n.kind === 'model_output').map((n) => {
      const a = n.attributes || {};
      const id = n.label.includes(':') ? n.label.split(':').pop() : n.label;
      return `<li><div class="row"><span class="name">${esc(label(id))} · FY${esc(a.fiscal_year)}</span>
        <span class="val">${esc(fmt(Number(a.value), unitOf(id), cur))}</span></div>
        ${a.formula ? `<p class="how">${esc(formulaText(a.formula))}</p>` : ''}</li>`;
    }).join('');
    if (outputs) html += `<h3>Built on</h3><ul class="sources">${outputs}</ul>`;

    const facts = rest.filter((n) => n.kind === 'fact')
      .sort((a, b) => label(a.label).localeCompare(label(b.label)) || String(b.attributes?.period).localeCompare(String(a.attributes?.period)));
    const factRows = facts.map((n) => {
      const a = n.attributes || {};
      const link = filingLink(a.filing_accession, cik);
      const doc = parentDoc(n.id);
      const how = doc
        ? `${esc(doc.title)}${a.table ? ` · ${esc(a.table)}` : ''}${a.page ? ` · page ${esc(a.page)}` : ''}`
        : a.filing_form
          ? `${esc(a.filing_form)} filed ${esc(dateText(a.filed_date))}${link
            ? ` · <a href="${esc(link)}" target="_blank" rel="noopener noreferrer">View filing</a>` : ''}`
          : (a.formula ? `Calculated: ${esc(formulaText(a.formula))}` : '');
      return `<li><div class="row"><span class="name">${esc(label(n.label))} · ${esc(a.period || '')}</span>
        <span class="val">${esc(fmt(Number(a.value), unitOf(n.label), cur))}</span></div>
        <p class="how">${how}</p>
        ${a.xbrl_concept ? `<p class="how"><span class="tagname">${esc(a.xbrl_concept)}</span></p>` : ''}</li>`;
    }).join('');
    const rootDoc = root.kind === 'fact' ? parentDoc(root.id) : null;
    if (rootDoc) {
      html += `<h3>Read from</h3><ul class="sources"><li>
        <div class="row"><span class="name">${esc(rootDoc.title)}</span><span class="val">${esc(rootDoc.kind_label)}</span></div>
        <p class="how">${esc([attr.table, attr.page ? `page ${attr.page}` : '', attr.period].filter(Boolean).join(' · '))}</p>
        ${attr.source_text ? `<p class="how quote">“${esc(attr.source_text)}”</p>` : ''}
        ${attr.xbrl_concept ? `<p class="how"><span class="tagname">${esc(attr.xbrl_concept)}</span></p>` : ''}
        <p class="how">${esc(rootDoc.reason)}</p>
      </li></ul>`;
    } else if (root.kind === 'fact' && !attr.formula) {
      const link = filingLink(attr.filing_accession, cik);
      html += `<h3>Reported in</h3><ul class="sources"><li>
        <div class="row"><span class="name">${esc(attr.filing_form || 'Filing')}</span>
        <span class="val">${esc(dateText(attr.filed_date))}</span></div>
        <p class="how">Period: ${esc(attr.period || '')}${link
          ? ` · <a href="${esc(link)}" target="_blank" rel="noopener noreferrer">View filing</a>` : ''}</p>
        ${attr.xbrl_concept ? `<p class="how"><span class="tagname">${esc(attr.xbrl_concept)}</span></p>` : ''}
      </li></ul>`;
    }
    if (factRows) html += `<h3>Reported figures used</h3><ul class="sources">${factRows}</ul>`;
    if (cik) {
      html += `<h3>Source</h3><p class="muted">U.S. SEC, EDGAR ·
        <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=${esc(cik)}&amp;type=10-K" target="_blank" rel="noopener noreferrer">Company filings</a>
        · <a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">Structured data</a></p>`;
    }
    body.innerHTML = html;
    announce(`Source for ${title}.`);
  } catch (err) {
    body.innerHTML = `<p class="err">${esc(err.message)}</p>`;
  }
}

/* ---------- interactions ---------- */

panel.addEventListener('click', (e) => {
  const trace = e.target.closest('.trace');
  if (trace) { openSource(trace.dataset.node, trace.dataset.label, trace.dataset.shown); return; }
  const act = e.target.closest('[data-action]');
  if (!act) return;
  const { action, value } = act.dataset;
  if (action === 'years') { state.allYears = !state.allYears; rerender(); }
  if (action === 'statement') { state.statement = value; rerender(); }
  if (action === 'scenario') { state.scenario = value; rerender(); }
  if (action === 'tab') selectTab(tabs().find((t) => t.dataset.view === value), { focus: true });
});

panel.addEventListener('toggle', async (e) => {
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
    const cols = d.columns.filter((c) => c !== 'method');
    const head = cols.map((c) => th(c === 'series' ? '' : esc(c), c === 'series' ? '' : 'num')).join('');
    const rows = d.rows.map((r) => `<tr>${cols.map((c) => (c === 'series'
      ? `<th scope="row">${esc(label(r[c]) || r[c])}</th>`
      : `<td class="num">${typeof r[c] === 'number' ? esc(fmt(r[c], d.unit_kind, d.currency)) : '–'}</td>`)).join('')}</tr>`).join('');
    slot.innerHTML = `<table class="sticky"><caption class="sr-only">${esc(d.title)}</caption>
      <thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  } catch (err) {
    slot.innerHTML = `<p class="err">${esc(err.message)}</p>`;
  }
}, true);

/* ---------- page frame ---------- */

function notReadyCard() {
  const how = SNAPSHOT ? 'It is not part of this published snapshot.'
    : HOSTED.on ? 'This company is still being prepared. It will appear here when ready.'
      : 'Run the analysis for this company, then reload the page.';
  return `<div class="card"><div class="body"><h2 class="section">Not available yet</h2>
    <p class="muted">${how}</p></div></div>`;
}

const SKELETON = `<div class="skeleton" aria-hidden="true">
  <div class="sk-row"><div class="sk sk-kpi"></div><div class="sk sk-kpi"></div><div class="sk sk-kpi"></div><div class="sk sk-kpi"></div></div>
  <div class="sk sk-table"></div></div>`;

async function render(fn) {
  const id = ++state.requestId;
  panel.setAttribute('aria-busy', 'true');
  panel.innerHTML = SKELETON;
  let html;
  try {
    html = await fn();
  } catch (err) {
    html = err instanceof NotReady ? notReadyCard()
      : `<div class="card"><div class="body"><h2 class="section">This section could not be loaded</h2>
         <p class="err">${esc(err.message)}</p><p class="muted">Please reload the page.</p></div></div>`;
  }
  if (id !== state.requestId) return;
  panel.innerHTML = html;
  panel.setAttribute('aria-busy', 'false');
  announce(`${$(`#tab-${state.view}`)?.textContent || ''} loaded.`);
}

/* Re-render the current view, keeping the reader where they were on the page. */
function rerender() {
  const y = window.scrollY;
  render(VIEWS[state.view]).then(() => window.scrollTo(0, y));
}

async function companyBand() {
  const band = $('#company-band');
  const [o, g, fin] = await Promise.all([
    api(forCompany('')), glossary(), optional(api(forCompany('/financials'))).catch(() => null),
  ]);
  const years = fin?.fiscal_years || [];
  const fye = MONTHS[(o.fiscal_calendar?.fiscal_year_end_month || 0) - 1];
  const asOf = SNAPSHOT ? SNAPSHOT.getAttribute('content') : o.generated_at;
  band.innerHTML = `<div class="band">
    <div><h1>${esc(o.name)}</h1>
      <p class="meta"><span class="ticker">${esc(o.exchange)}: ${esc(o.ticker)}</span>
        ${o.sector ? `<span>${esc(o.sector)}</span>` : ''}
        ${g.model_name ? `<span>${esc(g.model_name)}</span>` : ''}</p></div>
    <dl>
      ${fye ? `<div><dt>Fiscal year ends</dt><dd>${esc(fye)}</dd></div>` : ''}
      ${years.length ? `<div><dt>History</dt><dd>FY${esc(years[0])}–FY${esc(years[years.length - 1])}</dd></div>` : ''}
      <div><dt>Currency</dt><dd>${esc(o.reporting_currency || '–')}</dd></div>
      <div><dt>Data as of</dt><dd>${esc(dateText(asOf))}</dd></div>
    </dl></div>`;
  band.hidden = false;
  const c = state.companies.find((x) => x.company_id === state.companyId);
  if (c) c.currency = o.reporting_currency;
  document.title = `${o.name} (${o.ticker}) · Equity Research`;
}

/* ---------- summary ---------- */

function latest(series) {
  const pts = [...(series?.points || [])].sort((a, b) => a.fiscal_year - b.fiscal_year);
  return pts[pts.length - 1];
}

async function viewSummary() {
  const [g, fin, a, charts, f, o, lib] = await Promise.all([
    glossary(), api(forCompany('/financials')), api(forCompany('/analytics')),
    api(forCompany('/charts')).catch(() => []), optional(api(forCompany('/forecast'))).catch(() => null),
    api(forCompany('')), api(forCompany('/library')).catch(() => null),
  ]);
  const inUse = (lib?.documents || []).filter((d) => d.status === 'verified').length;
  const cur = fin.currency;
  const rows = Object.fromEntries(fin.statements.flatMap((s) => s.rows).map((r) => [r.metric_id, r]));
  const series = Object.fromEntries(a.series.map((s) => [s.analytic_id, s]));
  const lastYear = fin.fiscal_years[fin.fiscal_years.length - 1];

  const tile = (metricId, growthId) => {
    const row = rows[metricId];
    const cell = row?.values?.[String(lastYear)];
    if (!cell) return '';
    const gp = growthId && series[growthId]?.points.find((p) => p.fiscal_year === lastYear);
    return `<div class="card kpi"><p class="k">${esc(row.label)} <span class="fy">FY${esc(lastYear)}</span></p>
      <p class="v">${traceable(cell.fact_id, fmt(cell.value, row.unit_kind, cur), `${row.label}, FY${lastYear}`)}</p>
      <p class="d">${gp ? `${change(gp.value)} <span class="muted">vs FY${lastYear - 1}</span>` : '&nbsp;'}</p></div>`;
  };
  const ratioTile = (id) => {
    const p = latest(series[id]);
    if (!p) return '';
    return `<div class="card kpi"><p class="k">${esc(label(id))} <span class="fy">FY${esc(p.fiscal_year)}</span></p>
      <p class="v">${traceable(p.value_id, fmt(p.value, series[id].unit_kind, cur), `${label(id)}, FY${p.fiscal_year}`)}</p>
      <p class="d">&nbsp;</p></div>`;
  };
  const tiles = [
    tile('revenue', 'revenue_growth'), tile('net_income', 'net_income_growth'),
    tile('diluted_eps', 'diluted_eps_growth'),
    ratioTile(['return_on_average_equity', 'roic', 'net_margin'].find((id) => series[id]) || ''),
    tile('total_assets'), tile('total_equity'),
  ].join('');

  // Estimates at a glance: the headline lines from the first scenario, last actual and two years out.
  let estimates = '';
  if (f && (f.scenarios || []).length) {
    const scn = f.scenarios[0];
    const cols = [f.base_year, ...(f.forecast_years || []).slice(0, 3)];
    const pick = ['revenue', 'net_income', 'diluted_eps'].map((id) => scn.metrics.find((m) => m.metric_id === id)).filter(Boolean);
    const body = pick.map((m) => {
      const by = new Map(m.points.map((p) => [p.fiscal_year, p]));
      return `<tr><th scope="row">${esc(m.label || label(m.metric_id))}</th>${cols.map((y, i) => {
        const p = by.get(y);
        return `<td class="num ${i ? 'est' : ''}">${p ? traceable(p.value_id, fmt(p.value, m.unit_kind, m.currency || cur),
          `${m.label || label(m.metric_id)}, FY${y}${i ? ' estimate' : ''}`) + ((p.fallback_for || []).length ? '<span class="mark fb" aria-hidden="true">‡</span>' : '') : '–'}</td>`;
      }).join('')}</tr>`;
    }).join('');
    if (body) {
      estimates = `<section class="card"><div class="card-head"><h2>Estimates</h2>
          <span class="sub">${esc(scn.name)}</span></div>
        <div class="body flush"><div class="tablewrap" tabindex="0" role="region" aria-label="Table, scrollable"><table>
          <thead><tr>${th('')}${cols.map((y, i) => th(`FY${y}${i ? 'E' : 'A'}`, `num ${i ? 'est' : ''}`)).join('')}</tr></thead>
          <tbody>${body}</tbody></table></div>
        <p class="legend"><span>A = reported, E = estimate. Estimates are projections from reported history, not company guidance.</span>
          ${(f.fallback_notes || []).length ? `<span><span class="mark fb" aria-hidden="true">‡</span> Uses a trend estimate for
            ${f.fallback_notes.map((n) => esc(lower(n.label))).join(', ')}, which the full model can’t calculate from the filings.</span>` : ''}
          <button type="button" class="linkbtn" data-action="tab" data-value="forecast">All estimates</button></p></div></section>`;
    }
  }

  const reviewCount = o.counts?.issues?.warning ?? 0;
  const about = `<section class="card"><div class="card-head"><h2>About this data</h2></div>
    <div class="body"><dl class="kv">
      <dt>Source</dt><dd>SEC structured financial data from 10-K and 10-Q filings${inUse ? ', plus the documents in the library' : ''}</dd>
      <dt>Coverage</dt><dd>FY${esc(fin.fiscal_years[0])}–FY${esc(lastYear)}, full fiscal years</dd>
      ${o.framework?.evidence ? `<dt>Industry</dt><dd>${esc(g.model_name || g.sector || '')} <span class="muted">· ${esc(o.framework.evidence.replace(/ \((.*)\)$/, ', $1'))}</span></dd>` : ''}
      <dt>Documents</dt><dd>${inUse ? `${esc(inUse)} in use` : 'None yet'}
        · <button type="button" class="linkbtn" data-action="tab" data-value="sources">${HOSTED.on && !inUse ? 'Add one' : 'Sources'}</button></dd>
      <dt>Data checks</dt><dd>${reviewCount ? `${esc(reviewCount)} item${reviewCount === 1 ? '' : 's'} to review` : 'No items to review'}
        · <button type="button" class="linkbtn" data-action="tab" data-value="checks">Details</button></dd>
      <dt>Updated</dt><dd>${esc(dateText(SNAPSHOT ? SNAPSHOT.getAttribute('content') : o.generated_at))}</dd>
    </dl></div></section>`;

  return `<h2 class="sr-only">Summary</h2>
    <div class="grid kpis">${tiles}</div>
    <div class="grid cols-2" style="margin-top:16px">${charts.slice(0, 2).map((c) => chartCard(c, 'analysis')).join('')}</div>
    <div class="grid cols-2" style="margin-top:16px">${estimates}${about}</div>`;
}

/* ---------- financial statements ---------- */

async function viewFinancials() {
  const fin = await api(forCompany('/financials'));
  const statements = fin.statements || [];
  if (!statements.length) return '<p class="empty">No annual statements are available for this company.</p>';
  if (!statements.some((s) => s.id === state.statement)) state.statement = statements[0].id;
  const s = statements.find((x) => x.id === state.statement);
  const years = yearsShown(fin.fiscal_years);
  const hasShares = s.rows.some((r) => r.unit_kind === 'count');
  const hasPerShare = s.rows.some((r) => r.unit_kind === 'currency_per_share');
  const hasRatio = s.rows.some((r) => r.unit_kind === 'ratio');
  const unitNote = `${esc(fin.currency || '')} millions${hasPerShare ? ', except per-share amounts' : ''}${hasShares ? '; shares in millions' : ''}${hasRatio ? '; ratios in %' : ''}`;

  let anyDoc = false;
  const body = s.rows.map((r) => `<tr><th scope="row">${esc(r.label)}</th>${years.map((y) => {
    const c = r.values[String(y)];
    if (!c) return '<td class="num muted">–</td>';
    const text = fmtStatement(c.value, r.unit_kind);
    const fromDoc = c.document ? `<span class="mark doc" aria-hidden="true">d</span><span class="sr-only"> (from ${esc(c.document)})</span>` : '';
    if (c.document) anyDoc = true;
    return `<td class="num${c.value < 0 ? ' neg' : ''}">${traceable(c.fact_id, text, `${r.label}, FY${y}`, c.derived ? 'calc' : '', fmt(c.value, r.unit_kind, fin.currency))}${
      c.derived ? '<span class="sr-only"> (calculated)</span>' : ''}${fromDoc}</td>`;
  }).join('')}</tr>`).join('');

  return `<div class="toolbar">
      <div class="segmented" role="group" aria-label="Statement">${statements.map((x) =>
        `<button type="button" data-action="statement" data-value="${esc(x.id)}" aria-pressed="${x.id === s.id}">${esc(x.label)}</button>`).join('')}</div>
      <span class="units">${unitNote} · ${yearToggle(fin.fiscal_years)}</span>
    </div>
    <section class="card" aria-label="${esc(s.label)}"><div class="body flush">
      <div class="tablewrap" tabindex="0" role="region" aria-label="${esc(s.label)}, scrollable"><table class="sticky">
        <caption class="sr-only">${esc(s.label)}, fiscal years, ${unitNote}</caption>
        <thead><tr>${th('')}${years.map((y) => th(`FY${y}`, 'num')).join('')}</tr></thead>
        <tbody>${body}</tbody></table></div>
      <p class="legend"><span><em>Italic</em>: not reported directly; calculated from other reported lines.</span>
        ${anyDoc ? '<span><span class="mark doc" aria-hidden="true">d</span> Read from a document in the library (see Sources).</span>' : ''}
        <span>Select any figure to see the filing it came from.</span></p>
    </div></section>`;
}

/* ---------- ratios ---------- */

async function viewRatios() {
  const [a, charts, g] = await Promise.all([
    api(forCompany('/analytics')), api(forCompany('/charts')).catch(() => []), glossary(),
  ]);
  const all = a.fiscal_years || [];
  const years = yearsShown(all);
  const catLabel = g.category_labels || {};
  const CAT_ORDER = ['growth', 'profitability', 'returns', 'per_share', 'efficiency', 'credit', 'capital',
    'leverage', 'liquidity', 'capital_intensity', 'operating'];
  const rankOf = (c) => { const i = CAT_ORDER.indexOf(c); return i < 0 ? 99 : i; };
  const ordered = [...a.series].sort((x, y) => rankOf(x.category) - rankOf(y.category));
  let body = '';
  let category = null;
  for (const s of ordered) {
    if (s.category !== category) {
      category = s.category;
      body += `<tr class="group"><th scope="colgroup" colspan="${years.length + 2}">${esc(catLabel[category] || label(category))}</th></tr>`;
    }
    const by = new Map(s.points.map((p) => [p.fiscal_year, p]));
    body += `<tr><th scope="row">${esc(s.label || label(s.analytic_id))}</th>${years.map((y) => {
      const p = by.get(y);
      if (!p) return '<td class="num muted">–</td>';
      const flag = p.quality_flags.length ? '<span class="mark flag" aria-hidden="true">†</span><span class="sr-only"> (input flagged by a data check)</span>' : '';
      return `<td class="num">${traceable(p.value_id, fmt(p.value, s.unit_kind, s.currency), `${s.label || label(s.analytic_id)}, FY${y}`,
        p.uses_derived_facts ? 'calc' : '')}${flag}</td>`;
    }).join('')}<td class="num muted">${s.summary?.median != null ? esc(fmt(s.summary.median, s.unit_kind, s.currency)) : '–'}</td></tr>`;
  }

  // "No prior year before the first year on record" is true of every series and tells a reader nothing.
  const shownGaps = (a.gaps || []).filter((x) => !/:start_of_history/.test(x.key));
  const gaps = shownGaps.length ? `<h2 class="section">Not available <span class="hint">figures that could not be calculated, and why</span></h2>
    <section class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region" aria-label="Table, scrollable"><table>
      <thead><tr>${th('Ratio')}${th('Reason')}${th('Years', 'num')}</tr></thead>
      <tbody>${shownGaps.map((x) => `<tr><th scope="row">${esc(x.label)}</th><td class="wrap">${esc(x.reason)}</td>
        <td class="num">${esc(x.count)}</td></tr>`).join('')}</tbody></table></div></div></section>` : '';

  return `<div class="toolbar"><span class="units">Fiscal years · ${yearToggle(all)}</span></div>
    <section class="card" aria-label="Ratios"><div class="body flush">
      <div class="tablewrap" tabindex="0" role="region" aria-label="Ratios, scrollable"><table class="sticky">
        <caption class="sr-only">Ratios by fiscal year</caption>
        <thead><tr>${th('')}${years.map((y) => th(`FY${y}`, 'num')).join('')}${th(`Median, FY${all[0]}–FY${all[all.length - 1]}`, 'num')}</tr></thead>
        <tbody>${body}</tbody></table></div>
      <p class="legend"><span><em>Italic</em>: uses a figure calculated from other reported lines.</span>
        <span><span class="mark flag" aria-hidden="true">†</span> An input was flagged by a data check.</span>
        <span>Select any figure to see how it was calculated.</span></p>
    </div></section>
    ${charts.length ? `<h2 class="section">Charts</h2><div class="charts">${charts.map((c) => chartCard(c, 'analysis')).join('')}</div>` : ''}
    ${gaps}`;
}

/* ---------- estimates ---------- */

async function viewForecast() {
  const [f, charts, assumptions, g] = await Promise.all([
    api(forCompany('/forecast')), api(forCompany('/forecast/charts')).catch(() => []),
    api(forCompany('/assumptions')).catch(() => null), glossary(),
  ]);
  const scenarios = f.scenarios || [];
  const rank = new Map((g.metric_order || []).map((id, i) => [id, i]));
  charts.sort((x, y) => (rank.get(x.metric_id) ?? 999) - (rank.get(y.metric_id) ?? 999));
  if (!scenarios.some((s) => s.id === state.scenario)) state.scenario = scenarios[0]?.id ?? null;
  const active = scenarios.find((s) => s.id === state.scenario) || { metrics: [] };
  const years = f.forecast_years || [];
  const cols = [f.base_year, ...years];
  const cur = (state.companies.find((c) => c.company_id === state.companyId) || {}).currency;
  const order = new Map((g.metric_order || []).map((id, i) => [id, i]));
  const metrics = [...active.metrics].sort((x, y) => (order.get(x.metric_id) ?? 999) - (order.get(y.metric_id) ?? 999));

  const body = metrics.map((m) => {
    const by = new Map(m.points.map((p) => [p.fiscal_year, p]));
    const projected = years.map((y) => by.get(y)).find(Boolean);
    const name = m.label || label(m.metric_id);
    return `<tr><th scope="row">${esc(name)}</th>${cols.map((y, i) => {
      const p = by.get(y);
      if (!p) return `<td class="num muted ${i ? 'est' : ''}">–</td>`;
      const fb = (p.fallback_for || []).length ? '<span class="mark fb" aria-hidden="true">‡</span><span class="sr-only"> (uses a trend estimate)</span>' : '';
      return `<td class="num ${i ? 'est' : ''}">${traceable(p.value_id, fmt(p.value, m.unit_kind, m.currency || cur), `${name}, FY${y}${i ? ' estimate' : ''}`)}${fb}</td>`;
    }).join('')}<td>${projected ? `<span class="tag ${projected.method === 'growth' ? 'trend' : ''}">${esc(METHOD[projected.method] || projected.method)}</span>` : '<span class="muted">Not estimated</span>'}</td></tr>`;
  }).join('');

  const fallback = (f.fallback_notes || []).length ? `<div class="note warn" role="note">
      ${f.fallback_notes.map((n) => `<p><span class="mark fb" aria-hidden="true">‡</span> ${esc(n.text)}
        Figures that depend on it are marked ‡.</p>`).join('')}</div>` : '';

  const switcher = scenarios.length > 1 ? `<div class="segmented scenarios" role="group" aria-label="Scenario">
    ${scenarios.map((s) => `<button type="button" data-action="scenario" data-value="${esc(s.id)}" aria-pressed="${s.id === state.scenario}">${esc(s.name)}</button>`).join('')}
    </div>` : '';

  const aRows = (assumptions?.assumptions || [])
    .filter((x) => !x.scenario || x.scenario === state.scenario)
    .sort((x, y) => String(x.label).localeCompare(String(y.label)))
    .map((x) => `<tr><th scope="row">${esc(x.label || label(x.assumption_id))}</th>
      <td class="num">${x.value === null || x.value === undefined ? '<span class="muted">Not set</span>'
        : esc(x.unit === 'ratio' ? pct(x.value, 2) : Number(x.value).toLocaleString('en-US', { maximumSignificantDigits: 6 }))}</td>
      <td>${esc(x.source_text || SOURCE[x.type] || '')}</td>
      <td class="wrap">${esc(x.basis_text || x.rationale || '')}</td>
      <td>${esc(x.period ? `FY${String(x.period).replace(/^FY/, '')}` : 'All years')}</td></tr>`).join('');

  const missing = [...(f.refusals || []).map((r) => ({ label: r.label, reason: r.reason })),
    ...(f.unseeded_list || []).filter((u) => !(f.refusals || []).some((r) => r.reason === u.text))
      .map((u) => ({ label: u.label, reason: u.text }))];

  return `<div class="note" role="note"><p><strong>Estimates are projections, not company guidance.</strong>
      Each line is projected from ${esc(f.base_year ? `FY${f.base_year}` : 'the latest reported year')} using the
      company’s own recent history${(assumptions?.assumptions || []).some((x) => x.type !== 'historical') ? ' and the assumptions listed below' : ''}.
      Select a figure to see how it was built.</p></div>
    ${fallback}${switcher}
    <section class="card" aria-label="Estimates"><div class="body flush">
      <div class="tablewrap" tabindex="0" role="region" aria-label="Estimates, scrollable"><table class="sticky">
        <caption class="sr-only">Estimates, ${esc(active.name || '')}</caption>
        <thead><tr>${th('')}${cols.map((y, i) => th(`FY${y}${i ? 'E' : 'A'}`, `num ${i ? 'est' : ''}`)).join('')}${th('Method')}</tr></thead>
        <tbody>${body || `<tr><td colspan="${cols.length + 2}" class="muted">No estimates in this scenario.</td></tr>`}</tbody>
      </table></div>
      <p class="legend"><span>A = reported, E = estimate.</span><span>Trend: grown at its recent median rate. Calculated: from other estimated lines. Model: from the industry’s driver relationships.</span>
        ${(f.fallback_notes || []).length ? '<span><span class="mark fb" aria-hidden="true">‡</span> Uses a trend estimate in place of the full model.</span>' : ''}</p>
    </div></section>
    ${charts.length ? `<h2 class="section">Charts <span class="hint">solid: reported · dashed: estimated</span></h2>
      <div class="charts">${charts.slice(0, 4).map((c) => chartCard(c, 'forecast')).join('')}</div>
      ${charts.length > 4 ? `<section class="card" style="margin-top:16px"><details class="more">
        <summary>More charts (${esc(charts.length - 4)})</summary>
        <div class="charts" style="padding:0 16px 16px">${charts.slice(4).map((c) => chartCard(c, 'forecast')).join('')}</div>
      </details></section>` : ''}` : ''}
    ${aRows ? `<h2 class="section">Assumptions</h2>
      <section class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region" aria-label="Table, scrollable"><table>
        <thead><tr>${th('Assumption')}${th('Value', 'num')}${th('Source')}${th('Basis')}${th('Applies to')}</tr></thead>
        <tbody>${aRows}</tbody></table></div></div></section>` : ''}
    ${missing.length ? `<h2 class="section">Not estimated <span class="hint">and why</span></h2>
      <section class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region" aria-label="Table, scrollable"><table>
        <thead><tr>${th('Line')}${th('Reason')}</tr></thead>
        <tbody>${missing.map((x) => `<tr><th scope="row">${esc(x.label)}</th><td class="wrap">${esc(x.reason)}</td></tr>`).join('')}</tbody>
      </table></div></div></section>` : ''}`;
}

/* ---------- data checks ---------- */

async function viewChecks() {
  const q = await api(forCompany('/checks'));
  const n = q.counts || {};
  const tile = (k, v, d) => `<div class="card kpi"><p class="k">${esc(k)}</p><p class="v">${esc(v)}</p><p class="d">${esc(d)}</p></div>`;

  const checks = (q.checks || []).map((c) => {
    const total = c.passed + c.failed + c.not_evaluable;
    const w = (x) => (total ? (x / total) * 100 : 0);
    return `<tr><th scope="row">${esc(c.label)}</th><td class="wrap muted">${esc(c.description)}</td>
      <td class="num">${esc(c.passed)}</td><td class="num${c.failed ? ' neg' : ''}">${esc(c.failed)}</td>
      <td class="num muted">${esc(c.not_evaluable)}</td>
      <td><span class="bar" role="img" aria-label="${esc(c.passed)} passed, ${esc(c.failed)} flagged, ${esc(c.not_evaluable)} not testable">
        <span style="width:${w(c.passed)}%;background:var(--pos)"></span><span style="width:${w(c.failed)}%;background:#d98c00"></span>
        <span style="width:${w(c.not_evaluable)}%;background:#c6ccd6"></span></span></td></tr>`;
  }).join('');

  const table = (rows) => `<div class="tablewrap" tabindex="0" role="region" aria-label="Table, scrollable"><table>
    <thead><tr>${th('')}${th('Check')}${th('Line item')}${th('Period')}${th('Finding')}</tr></thead>
    <tbody>${rows.map((i) => `<tr><td><span class="tag ${i.severity === 'error' ? 'excluded' : i.severity === 'warning' ? 'review' : ''}">${esc(i.severity_label)}</span></td>
      <td>${esc(i.check_label)}</td><td>${esc(i.item || '–')}</td><td>${esc(i.period || '–')}</td>
      <td class="wrap">${esc(i.text)}</td></tr>`).join('')}</tbody></table></div>`;
  const important = (q.issues || []).filter((i) => i.severity !== 'info');
  const notes = (q.issues || []).filter((i) => i.severity === 'info');

  return `<div class="note" role="note"><p>Every reported figure is tested before it is used: statements must add up,
      years must be complete and moves must be plausible. A figure that fails a critical check is excluded;
      anything else worth a second look is listed here and marked wherever it is used.</p></div>
    <div class="grid kpis">
      ${tile('Items to review', n.review ?? 0, 'marked † wherever used')}
      ${tile('Excluded figures', n.excluded ?? 0, 'failed a critical check')}
      ${tile('Notes', n.notes ?? 0, 'revisions and unusual moves')}
    </div>
    <h2 class="section">Checks</h2>
    <section class="card"><div class="body flush"><div class="tablewrap" tabindex="0" role="region" aria-label="Checks, scrollable"><table>
      <thead><tr>${th('Check')}${th('What it tests')}${th('Passed', 'num')}${th('Flagged', 'num')}${th('Not testable', 'num')}${th('')}</tr></thead>
      <tbody>${checks}</tbody></table></div></div></section>
    <h2 class="section">Items to review <span class="hint">${esc(important.length)}</span></h2>
    <section class="card"><div class="body flush">${important.length ? table(important) : '<p class="empty">Nothing to review.</p>'}</div></section>
    ${notes.length ? `<section class="card" style="margin-top:16px"><details class="more">
      <summary>Notes (${esc(notes.length)}): revisions in later filings and unusual year-on-year moves</summary>
      ${table(notes)}</details></section>` : ''}`;
}

/* ---------- sources ---------- */

const STATUS_TAG = { verified: 'ok', unverified: '', rejected: 'excluded', pending: 'review' };

function periodsText(periods) {
  const years = periods.map((p) => Number(String(p).replace(/^FY/, ''))).filter(Boolean).sort();
  if (!years.length) return periods.join(', ');
  const runs = [];
  for (const y of years) {
    const last = runs[runs.length - 1];
    if (last && y === last[1] + 1) last[1] = y; else runs.push([y, y]);
  }
  return runs.map(([a, b]) => (a === b ? `FY${a}` : `FY${a}–FY${b}`)).join(', ');
}

function uploadCard() {
  const info = HOSTED.info || {};
  if (!HOSTED.on || !info.documents) return '';
  return `<section class="card" aria-labelledby="add-doc-title" style="margin-top:16px">
    <div class="card-head"><h2 id="add-doc-title">Add a document</h2>
      <span class="sub">HTML or PDF, up to ${esc(info.max_document_mb || 25)} MB</span></div>
    <div class="body">
      <p class="muted" style="margin-top:0">Annual reports, quarterly reports, earnings releases and investor presentations
        help most. The document is read, checked against the SEC figures it shares with them, and used only to fill
        figures the SEC data doesn't have. ${info.documents_saved
          ? 'Documents that pass their checks are proposed for the permanent library, so they stay for everyone.'
          : 'Documents added here are kept on this server until it restarts.'}</p>
      <form id="doc-form" class="docform">
        <label class="fld"><span>File</span>
          <input type="file" name="file" accept=".htm,.html,.xhtml,.pdf,text/html,application/pdf" required></label>
        <label class="fld"><span>Type</span>
          <select name="kind">
            <option value="annual_report">Annual report (10-K)</option>
            <option value="quarterly_report">Quarterly report (10-Q)</option>
            <option value="earnings_release">Earnings release</option>
            <option value="investor_presentation">Investor presentation</option>
            <option value="other">Other</option>
          </select></label>
        <label class="fld"><span>Title <span class="muted">(optional)</span></span>
          <input type="text" name="title" maxlength="120" placeholder="e.g. Annual report 2025"></label>
        <button type="submit" class="btn">Add document</button>
      </form>
      <details class="more-inline"><summary>Or add an SEC filing by its EDGAR address</summary>
        <form id="sec-form" class="docform">
          <label class="fld wide"><span>EDGAR document address</span>
            <input type="url" name="url" required placeholder="https://www.sec.gov/Archives/edgar/data/…/….htm"></label>
          <label class="fld"><span>Type</span>
            <select name="kind"><option value="annual_report">Annual report (10-K)</option>
              <option value="quarterly_report">Quarterly report (10-Q)</option><option value="other">Other</option></select></label>
          <button type="submit" class="btn">Add filing</button>
        </form></details>
      <div id="doc-run" aria-live="polite"></div>
    </div></section>`;
}

async function viewSources() {
  const lib = await api(forCompany('/library'));
  const docs = lib.documents || [];
  const rows = docs.map((d) => {
    const added = d.contributed.length
      ? d.contributed.map((c) => `${esc(c.label)} <span class="muted">(${esc(periodsText(c.periods))})</span>`).join('<br>')
      : '<span class="muted">–</span>';
    const origin = d.url
      ? `<a href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">SEC filing</a>`
      : d.has_file && !SNAPSHOT
        ? `<a href="${resolve(forCompany(`/library/${encodeURIComponent(d.id)}/file`))}" target="_blank" rel="noopener">${d.media_type === 'pdf' ? 'PDF' : 'Download'}</a>` : '';
    const who = d.added_by === 'visitor' ? 'Added by a visitor' : d.added_by === 'config' ? 'Configured source' : 'Added by the owner';
    return `<tr>
      <th scope="row"><span class="doctitle">${esc(d.title)}</span>
        <span class="muted small">${esc(d.kind_label)}${d.form ? ` · ${esc(d.form)}` : ''}${d.filed ? ` · filed ${esc(dateText(d.filed))}` : ''}</span>
        <span class="muted small">${esc(who)}${d.added_at ? `, ${esc(dateText(d.added_at))}` : ''}${origin ? ` · ${origin}` : ''}</span></th>
      <td><span class="tag ${STATUS_TAG[d.status] || ''}">${esc(d.status_label)}</span></td>
      <td>${added}</td>
      <td class="wrap muted">${esc(d.reason)}</td></tr>`;
  }).join('');
  const src = lib.structured_source || {};
  return `<div class="note" role="note"><p><strong>Where these figures come from.</strong> Reported statements come from
      the SEC's structured financial data. Documents in the library fill what it lacks — ratios, averages and
      capital figures that are printed in the reports but not tagged. A document's numbers are used only after it
      agrees with the SEC on the figures both contain, and never replace an SEC figure.</p></div>
    <section class="card"><div class="card-head"><h2>Primary source</h2></div>
      <div class="body"><p style="margin:0">${esc(src.title || 'SEC structured financial data')}${src.url
        ? ` · <a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">Company filings on EDGAR</a>` : ''}</p></div></section>
    <h2 class="section">Document library <span class="hint">${esc(docs.length)} document${docs.length === 1 ? '' : 's'}</span></h2>
    <section class="card"><div class="body flush">${docs.length ? `<div class="tablewrap" tabindex="0" role="region" aria-label="Document library, scrollable"><table>
      <thead><tr>${th('Document')}${th('Status')}${th('What it added')}${th('Check')}</tr></thead>
      <tbody>${rows}</tbody></table></div>`
      : '<p class="empty">No documents yet.</p>'}</div></section>
    ${uploadCard()}`;
}

async function afterDocumentAdded(body) {
  const slot = $('#doc-run');
  if (!body.run) {
    slot.innerHTML = `<p class="muted">This document is already in the library.</p>`;
    return;
  }
  let job = body.run;
  const step = async () => {
    slot.innerHTML = runCard({ ...job, name: body.document.title, ticker: '' });
    if (job.state === 'done' || job.state === 'failed') {
      state.cache.clear();
      if (job.state === 'done') {
        announce('Document analysed.');
        await companyBand().catch(() => {});
        rerender();
      }
      return;
    }
    setTimeout(async () => {
      try { job = await api(`/api/runs/${encodeURIComponent(job.job_id)}`, { fresh: true }); } catch { /* keep polling */ }
      step();
    }, 1500);
  };
  step();
}

panel.addEventListener('submit', async (e) => {
  const form = e.target.closest('#doc-form, #sec-form');
  if (!form) return;
  e.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  const slot = $('#doc-run');
  const data = new FormData(form);
  button.disabled = true;
  slot.innerHTML = '<p class="muted">Uploading…</p>';
  try {
    let res;
    if (form.id === 'doc-form') {
      const file = data.get('file');
      const limit = (HOSTED.info.max_document_mb || 25) * 1024 * 1024;
      if (!file || !file.size) throw new Error('Choose a file first.');
      if (file.size > limit) throw new Error(`The file is larger than ${HOSTED.info.max_document_mb || 25} MB.`);
      const params = new URLSearchParams({ filename: file.name, kind: data.get('kind') });
      if (data.get('title')) params.set('title', data.get('title'));
      res = await fetch(`${forCompany('/library')}?${params}`, {
        method: 'PUT', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' } });
    } else {
      res = await fetch(forCompany('/library'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: data.get('url'), kind: data.get('kind') }) });
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'The document could not be added.');
    form.reset();
    afterDocumentAdded(body);
  } catch (err) {
    slot.innerHTML = `<p class="err">${esc(err.message)}</p>`;
    announce(err.message);
  } finally {
    button.disabled = false;
  }
});

/* ---------- navigation ---------- */

function rememberLocation() {
  try { history.replaceState(null, '', `#${state.companyId}/${state.view}`); } catch { /* sandboxed */ }
}

const VIEWS = {
  summary: viewSummary, financials: viewFinancials, ratios: viewRatios, forecast: viewForecast, checks: viewChecks,
  sources: viewSources,
};
const tabs = () => $$('#tabs .tab');

function selectTab(tab, { focus = false } = {}) {
  if (!tab) return;
  tabs().forEach((t) => { const on = t === tab; t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1; });
  if (focus) tab.focus();
  state.view = tab.dataset.view;
  panel.setAttribute('aria-labelledby', tab.id);
  rememberLocation();
  render(VIEWS[state.view]);
}

$('#tabs').addEventListener('click', (e) => { const t = e.target.closest('.tab'); if (t) selectTab(t); });
$('#tabs').addEventListener('keydown', (e) => {
  const list = tabs();
  const i = list.indexOf(document.activeElement);
  if (i < 0) return;
  const move = { ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity }[e.key];
  if (move === undefined) return;
  e.preventDefault();
  const next = move === -Infinity ? 0 : move === Infinity ? list.length - 1 : (i + move + list.length) % list.length;
  selectTab(list[next], { focus: true });
});

async function showCompany(companyId) {
  state.companyId = companyId;
  state.scenario = null;
  state.statement = null;
  state.cache.clear();
  const g = await glossary();
  LABELS = g.labels || {};
  UNITS = g.units || {};
  await companyBand().catch(() => { $('#company-band').hidden = true; });
  rememberLocation();
  render(VIEWS[state.view]);
}

function paintSwitcher() {
  const sel = $('#company');
  sel.innerHTML = state.companies.map((c) =>
    `<option value="${esc(c.company_id)}" ${c.company_id === state.companyId ? 'selected' : ''}>${esc(c.name)} (${esc(c.ticker)})</option>`).join('');
  $('#switcher').hidden = state.companies.length < 2;
}

$('#company').addEventListener('change', (e) => {
  const c = state.companies.find((x) => x.company_id === e.target.value);
  announce(`Showing ${c?.name ?? ''}.`);
  showCompany(e.target.value);
});

/* ---------- company search (hosted) ---------- */

const HOSTED = { on: false, poll: null, info: {} };

async function detectHosted() {
  if (SNAPSHOT) return false;
  try {
    const res = await fetch('/api/app');
    if (!res.ok) return false;
    HOSTED.info = await res.json();
    return HOSTED.info.hosted === true;
  } catch { return false; }
}

/* A company is listed once it is ready; one listed mid-preparation would open onto empty pages. */
const listable = (companies) => (HOSTED.on ? companies.filter((c) => c.stages?.forecast) : companies);

async function refreshCompanies() {
  state.companies = listable(await api('/api/companies', { fresh: true }));
  paintSwitcher();
}

async function openCompany(companyId) {
  await refreshCompanies();
  $('#company').value = companyId;
  $('#finder-results').innerHTML = '';
  $('#finder').hidden = true;
  await showCompany(companyId);
}

const STEP = { ingest: 'Fetching filings from SEC EDGAR', quality: 'Checking the reported figures',
  analyze: 'Calculating ratios and trends', forecast: 'Building estimates' };

function runCard(job) {
  const done = new Set(job.stages_done || []);
  const steps = (job.stages || []).map((s) => {
    const cls = done.has(s) ? 'done' : (s === job.stage && job.state === 'running' ? 'current' : '');
    const text = job.reason && s === 'ingest' ? 'Reading the new document' : (STEP[s] || s);
    return `<li class="${cls}">${esc(text)}</li>`;
  }).join('');
  let status;
  if (job.state === 'queued') {
    status = job.queue_position > 1 ? `In line — ${esc(job.queue_position - 1)} ahead.` : 'Starting…';
  } else if (job.state === 'running') {
    status = `Preparing — ${esc(Math.round(job.elapsed_seconds))}s`;
  } else if (job.state === 'failed') {
    status = `<span class="err">${esc(job.error || 'This company could not be prepared.')}</span>`;
  } else {
    status = 'Ready.';
  }
  return `<div class="run" role="status"><p><strong>${esc(job.name)}</strong> <span class="muted">${esc(job.ticker)}</span></p>
    <p>${status}</p>${job.state === 'failed' ? '' : `<ol aria-label="Progress">${steps}</ol>`}</div>`;
}

function followRun(job) {
  clearTimeout(HOSTED.poll);
  $('#finder').hidden = false;
  const slot = $('#finder-run');
  let last = '';
  const tick = async () => {
    slot.innerHTML = runCard(job);
    const phase = `${job.state}:${job.stage}`;
    if (phase !== last) {
      last = phase;
      if (job.state === 'running') announce(`${job.name}: ${STEP[job.stage] || ''}.`);
      if (job.state === 'failed') announce(job.error || `${job.name} could not be prepared.`);
    }
    if (job.state === 'done') { announce(`${job.name} is ready.`); slot.innerHTML = ''; await openCompany(job.company_id); return; }
    if (job.state === 'failed') return;
    HOSTED.poll = setTimeout(async () => {
      try { job = await api(`/api/runs/${encodeURIComponent(job.job_id)}`, { fresh: true }); } catch { /* keep polling */ }
      tick();
    }, 1500);
  };
  tick();
}

async function startRun(entry) {
  if (entry.analysed) { await openCompany(entry.company_id); return; }
  const res = await fetch('/api/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cik: entry.cik }) });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = typeof body.detail === 'string' ? body.detail : 'This company could not be added right now.';
    $('#finder-run').innerHTML = `<div class="run"><p class="err">${esc(msg)}</p></div>`;
    announce(msg);
    return;
  }
  $('#finder-results').innerHTML = '';
  followRun(body);
}

function initFinder() {
  $('#finder-form').hidden = false;
  let results = [];
  $('#finder-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const q = $('#finder-q').value.trim();
    if (!q) return;
    $('#finder').hidden = false;
    const out = $('#finder-results');
    out.innerHTML = '<p class="muted">Searching…</p>';
    try { results = await api(`/api/search?q=${encodeURIComponent(q)}`, { fresh: true }); } catch (err) {
      out.innerHTML = `<p class="err">${esc(err.message)}</p>`; return;
    }
    if (!results.length) {
      out.innerHTML = `<p class="muted">No company matches “${esc(q)}”. Coverage is limited to companies that file with the U.S. SEC.</p>`;
      announce('No matches.');
      return;
    }
    out.innerHTML = `<ul class="results" aria-label="Matching companies">${results.map((r, i) => `
      <li><button type="button" class="result" data-i="${i}"><span>${esc(r.name)}
        <span class="meta">${esc(r.ticker)}${r.exchange ? ` · ${esc(r.exchange)}` : ''}</span></span>
        <span class="go">${r.analysed ? 'Open' : 'Add'}</span></button></li>`).join('')}</ul>`;
    announce(`${results.length} match${results.length === 1 ? '' : 'es'}.`);
    out.querySelector('.result')?.focus();
  });
  $('#finder-results').addEventListener('click', (e) => {
    const b = e.target.closest('.result');
    if (b) startRun(results[Number(b.dataset.i)]);
  });
}

/* ---------- start ---------- */

function welcome(title, text) {
  panel.setAttribute('aria-busy', 'false');
  panel.innerHTML = `<div class="card welcome"><h2>${title}</h2><p class="lead">${text}</p></div>`;
}

async function boot() {
  try {
    HOSTED.on = await detectHosted();
    if (HOSTED.on) initFinder();
    state.companies = listable(await api('/api/companies'));

    if (!state.companies.length && HOSTED.on) {
      const active = await api('/api/runs', { fresh: true }).catch(() => []);
      welcome(active.length ? 'Getting ready' : 'Search for a company',
        active.length ? 'The library is loading its first company; it opens here when ready. You can search for another company meanwhile.'
          : 'Type a company name or ticker above to see its financial statements, ratios and estimates.');
      if (active.length) followRun(active[0]);
      $('#finder-q').focus();
      return;
    }
    if (!state.companies.length) {
      welcome('No companies yet', 'No company has been analysed in this workspace.');
      return;
    }
    const [hashCompany, hashView] = location.hash.slice(1).split('/');
    state.companyId = state.companies.some((c) => c.company_id === hashCompany) ? hashCompany : state.companies[0].company_id;
    if (VIEWS[hashView]) state.view = hashView;
    paintSwitcher();
    const tab = tabs().find((t) => t.dataset.view === state.view) || tabs()[0];
    tabs().forEach((t) => { const on = t === tab; t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1; });
    panel.setAttribute('aria-labelledby', tab.id);
    await showCompany(state.companyId);
  } catch (err) {
    const network = err instanceof TypeError || /fetch|network|load failed/i.test(err.message);
    welcome(network ? 'Could not connect' : 'Something went wrong',
      network ? 'The data could not be loaded. Check your connection and reload the page.' : esc(err.message));
  }
}

boot();
