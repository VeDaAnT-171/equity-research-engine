"""Self-contained HTML rendering of the data-quality report."""

from __future__ import annotations

from html import escape

from .checks import MIN_GROWTH_OBSERVATIONS, MODIFIED_Z_CUTOFF, SCALE_BAND_LOG10
from .model import QualityReport
from .tolerance import MAX_PRESENTATION_UNIT

_CSS = """
:root{--bg:#fff;--fg:#1b1f24;--muted:#5b6470;--line:#d9dee4;--err:#b42318;--warn:#b54708;--info:#175cd3;--r:#e7f0fb;--d:#fdf3e7}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--fg:#e6e9ed;--muted:#9aa4af;--line:#2c333b;--err:#f97066;--warn:#fdb022;--info:#84adff;--r:#1c2a3a;--d:#3a2c1c}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 24px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:32px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.muted{color:var(--muted)}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--muted)}td.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:0 8px;border-radius:10px;font-size:12px;font-weight:600;border:1px solid currentColor}
.error{color:var(--err)}.warning{color:var(--warn)}.info{color:var(--info)}
.cards{display:flex;gap:16px;flex-wrap:wrap}.card{border:1px solid var(--line);border-radius:8px;padding:12px 16px;min-width:140px}
.card b{display:block;font-size:22px}
td.R{background:var(--r);text-align:center}td.D{background:var(--d);text-align:center}td.cov{text-align:center}
"""


def render_html(report: QualityReport, *, generated_at: str, versions: dict) -> str:
    counts = report.counts()
    rows = []
    for issue in report.to_dict()["issues"]:
        rows.append(
            f"<tr><td><span class='pill {issue['severity']}'>{issue['severity']}</span></td>"
            f"<td>{escape(issue['check'])}</td><td>{escape(issue['metric_id'] or '')}</td>"
            f"<td>{escape(issue['period'] or '')}</td><td>{escape(issue['message'])}</td></tr>"
        )
    checks = "".join(
        f"<tr><td>{escape(c.check)}</td><td>{escape(c.description)}</td><td class='num'>{c.passed}</td>"
        f"<td class='num'>{c.failed}</td><td class='num'>{c.not_evaluable}</td></tr>"
        for c in sorted(report.checks.values(), key=lambda c: (-c.failed, c.check))
        if c.passed or c.failed or c.not_evaluable
    )
    years = sorted({y for cells in report.coverage.values() for y in cells})
    coverage = "".join(
        f"<tr><td>{escape(m)}</td>" + "".join(
            f"<td class='{cells[y]}'>{cells[y]}</td>" if y in cells else "<td class='cov'>·</td>" for y in years
        ) + "</tr>"
        for m, cells in report.coverage.items()
    )
    derivations = "".join(f"<tr><td>{escape(k)}</td><td class='num'>{v}</td></tr>" for k, v in sorted(report.derivations.items()))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Data quality: {escape(report.company_id)}</title><style>{_CSS}</style></head>
<body><main>
<h1>Data quality report: {escape(report.company_id)}</h1>
<p class="muted">Framework {escape(report.framework)} · generated {escape(generated_at)} · engine {escape(versions.get('engine_version', ''))}
· parser {escape(versions.get('parser_version', ''))}. Reported values are never modified; issues are flags for review.</p>
<div class="cards">
<div class="card error"><b>{counts['error']}</b>errors</div>
<div class="card warning"><b>{counts['warning']}</b>warnings</div>
<div class="card info"><b>{counts['info']}</b>info</div>
<div class="card"><b>{sum(v for k, v in report.derivations.items() if k.startswith(('interim:', 'framework:')))}</b>derived facts</div>
</div>
<h2>Issues</h2>
<div class="scroll"><table><thead><tr><th>Severity</th><th>Check</th><th>Metric</th><th>Period</th><th>Detail</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="5" class="muted">No issues.</td></tr>'}</tbody></table></div>
<h2>Checks</h2>
<p class="muted">Not evaluable means required inputs were missing. Missing data is never counted as a pass.</p>
<div class="scroll"><table><thead><tr><th>Check</th><th>Rule</th><th>Passed</th><th>Failed</th><th>Not evaluable</th></tr></thead>
<tbody>{checks}</tbody></table></div>
<h2>Annual coverage</h2>
<p class="muted">R = reported, D = derived with lineage to reported inputs. Configured history: {report.historical_years} years.</p>
<div class="scroll"><table><thead><tr><th>Metric</th>{''.join(f'<th>{escape(y)}</th>' for y in years)}</tr></thead>
<tbody>{coverage}</tbody></table></div>
<h2>Derivations and gaps</h2>
<div class="scroll"><table><thead><tr><th>Event</th><th>Count</th></tr></thead><tbody>{derivations}</tbody></table></div>
<h2>Methodology</h2>
<ul>
<li><b>Rounding tolerance</b>: n × u / 2 for n terms, where u is the largest power of ten dividing every term, capped at {MAX_PRESENTATION_UNIT:,}.</li>
<li><b>Scale break</b>: year-on-year ratio within {SCALE_BAND_LOG10} (log10) of 10^3, 10^6 or 10^9.</li>
<li><b>Unusual change</b>: modified z-score of log changes above {MODIFIED_Z_CUTOFF} (Iglewicz &amp; Hoaglin), requiring at least {MIN_GROWTH_OBSERVATIONS} observations.</li>
<li><b>Interim derivation</b>: Q2 = H1 − Q1, Q3 = 9M − H1, Q4 = FY − 9M, H2 = FY − H1, else sums of reported quarters; additive metrics and contiguous periods only.</li>
</ul>
</main></body></html>
"""
