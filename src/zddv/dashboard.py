from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from zddv.config import ProjectConfig
from zddv.storage import (
    assertion_statistics,
    list_assertion_events,
    list_coverage_snapshots,
    list_run_records,
    run_statistics,
)
from zddv.triage import group_failure_records


def generate_html_report(project: ProjectConfig, *, limit: int = 100) -> dict:
    records = list_run_records(project, limit=limit)
    stats = run_statistics(project)
    groups = group_failure_records(records)
    assertion_stats = assertion_statistics(project)
    assertion_events = list_assertion_events(project, limit=min(limit, 50))
    coverage_rows = list_coverage_snapshots(project, limit=1)
    latest_coverage = coverage_rows[0] if coverage_rows else None

    out_dir = (project.root / ".zddv" / "reports").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "latest.html"

    recent_rows = []
    for row in records:
        test = escape(str(row["test_name"] or "(default)"))
        seed = "-" if row["seed"] is None else escape(str(row["seed"]))
        duration = "-"
        if row["duration_ms"] is not None:
            duration = f"{row['duration_ms']:.1f}"
        recent_rows.append(
            "<tr>"
            f"<td class='status {escape(row['status'].lower())}'>{escape(row['status'])}</td>"
            f"<td>{test}</td>"
            f"<td>{seed}</td>"
            f"<td>{escape(duration)}</td>"
            f"<td><code>{escape(row['run_id'])}</code></td>"
            "</tr>"
        )

    test_rows = []
    for item in stats["tests"]:
        test_rows.append(
            "<tr>"
            f"<td>{escape(item['test_name'])}</td>"
            f"<td>{item['total']}</td>"
            f"<td>{item['passed']}</td>"
            f"<td>{item['failed']}</td>"
            f"<td>{item['timed_out']}</td>"
            f"<td>{item['pass_rate']:.1f}%</td>"
            "</tr>"
        )

    group_rows = []
    for group in groups:
        tests = ", ".join(group["tests"]) or "-"
        statuses = ", ".join(group["statuses"]) or "-"
        group_rows.append(
            "<tr>"
            f"<td>{group['count']}</td>"
            f"<td>{escape(statuses)}</td>"
            f"<td>{escape(tests)}</td>"
            f"<td><code>{escape(group['signature'])}</code></td>"
            "</tr>"
        )

    assertion_rows = []
    for event in assertion_events:
        location = f"{event['source_path']}:{event['source_line']}"
        if event["source_column"] is not None:
            location += f":{event['source_column']}"
        assertion_rows.append(
            "<tr>"
            f"<td>{escape(event['severity'])}</td>"
            f"<td>{escape(str(event['sim_time'] or '-'))}</td>"
            f"<td>{escape(str(event['assertion_name'] or event['scope'] or '(unnamed)'))}</td>"
            f"<td>{escape(location)}</td>"
            f"<td>{escape(event['message'])}</td>"
            f"<td><code>{escape(event['run_id'])}</code></td>"
            "</tr>"
        )

    coverage_card = ""
    coverage_section = ""
    if latest_coverage is not None:
        coverage_card = (
            "<div class=\"card\"><div>Coverage hit rate</div>"
            f"<div class=\"metric\">{latest_coverage['hit_rate']:.1f}%</div>"
            f"<small>{latest_coverage['hit_points']}/{latest_coverage['total_points']} points</small>"
            "</div>"
        )
        coverage_type_rows = []
        for kind, values in latest_coverage["by_type"].items():
            coverage_type_rows.append(
                "<tr>"
                f"<td>{escape(kind)}</td>"
                f"<td>{values['total']}</td>"
                f"<td>{values['hit']}</td>"
                f"<td>{values['hit_rate']:.1f}%</td>"
                "</tr>"
            )
        coverage_section = (
            "<section><h2>Latest coverage snapshot</h2>"
            f"<small>{escape(latest_coverage['snapshot_id'])}</small>"
            "<table><thead><tr><th>Type</th><th>Total</th><th>Hit</th>"
            "<th>Hit rate</th></tr></thead><tbody>"
            + ("".join(coverage_type_rows) or '<tr><td colspan="4">No typed coverage points.</td></tr>')
            + "</tbody></table></section>"
        )

    generated = datetime.now(timezone.utc).isoformat()
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZDDV Verification Report — {escape(project.name)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 0; background: #111827; color: #e5e7eb; }}
main {{ max-width: 1180px; margin: auto; padding: 28px; }}
h1, h2 {{ color: #f9fafb; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap: 12px; }}
.card {{ background: #1f2937; border: 1px solid #374151; border-radius: 12px; padding: 16px; }}
.metric {{ font-size: 2rem; font-weight: 700; }}
table {{ width: 100%; border-collapse: collapse; background: #1f2937; }}
th, td {{ padding: 10px 12px; border-bottom: 1px solid #374151; text-align: left; }}
th {{ color: #9ca3af; font-size: .85rem; }}
.status.pass {{ color: #86efac; font-weight: 700; }}
.status.fail, .status.timeout {{ color: #fca5a5; font-weight: 700; }}
code {{ color: #bfdbfe; }}
section {{ margin-top: 28px; overflow-x: auto; }}
small {{ color: #9ca3af; }}
</style>
</head>
<body>
<main>
<h1>ZDDV Verification Report</h1>
<p>{escape(project.name)} · generated {escape(generated)}</p>
<div class="grid">
  <div class="card"><div>Total runs</div><div class="metric">{stats['total']}</div></div>
  <div class="card"><div>Passed</div><div class="metric">{stats['passed']}</div></div>
  <div class="card"><div>Failed</div><div class="metric">{stats['failed']}</div></div>
  <div class="card"><div>Timeouts</div><div class="metric">{stats['timed_out']}</div></div>
  <div class="card"><div>Pass rate</div><div class="metric">{stats['pass_rate']:.1f}%</div></div>
  <div class="card"><div>Assertion events</div><div class="metric">{assertion_stats['total_events']}</div><small>{assertion_stats['unique_assertions']} unique · {assertion_stats['affected_runs']} run(s)</small></div>
  {coverage_card}
</div>

{coverage_section}

<section>
<h2>Assertion events</h2>
<small>Showing up to {min(limit, 50)} captured assertion events.</small>
<table>
<thead><tr><th>Severity</th><th>Sim time</th><th>Assertion</th><th>Location</th><th>Message</th><th>Run ID</th></tr></thead>
<tbody>{''.join(assertion_rows) or '<tr><td colspan="6">No assertion failures captured.</td></tr>'}</tbody>
</table>
</section>

<section>
<h2>Per-test summary</h2>
<table>
<thead><tr><th>Test</th><th>Total</th><th>Pass</th><th>Fail</th><th>Timeout</th><th>Pass rate</th></tr></thead>
<tbody>{''.join(test_rows) or '<tr><td colspan="6">No runs recorded.</td></tr>'}</tbody>
</table>
</section>

<section>
<h2>Failure groups</h2>
<table>
<thead><tr><th>Count</th><th>Status</th><th>Tests</th><th>Signature</th></tr></thead>
<tbody>{''.join(group_rows) or '<tr><td colspan="4">No failures in the selected recent runs.</td></tr>'}</tbody>
</table>
</section>

<section>
<h2>Recent runs</h2>
<small>Showing up to {limit} runs.</small>
<table>
<thead><tr><th>Status</th><th>Test</th><th>Seed</th><th>Time (ms)</th><th>Run ID</th></tr></thead>
<tbody>{''.join(recent_rows) or '<tr><td colspan="5">No runs recorded.</td></tr>'}</tbody>
</table>
</section>
</main>
</body>
</html>
"""
    report_path.write_text(html, encoding="utf-8")
    return {
        "path": str(report_path),
        "stats": stats,
        "failure_groups": groups,
        "assertion_stats": assertion_stats,
        "assertion_events": assertion_events,
        "latest_coverage": latest_coverage,
        "shown_runs": len(records),
    }
