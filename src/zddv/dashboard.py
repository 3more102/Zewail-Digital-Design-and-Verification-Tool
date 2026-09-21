from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from zddv.config import ProjectConfig
from zddv.diagnostics import failure_signature
from zddv.storage import list_run_records, run_statistics


def _failure_groups(records: list[dict]) -> list[tuple[str, int]]:
    signatures: Counter[str] = Counter()
    for row in records:
        if row["status"] == "PASS":
            continue
        log_path = row.get("log_path")
        if not log_path:
            signatures["Unknown failure"] += 1
            continue
        path = Path(log_path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            signatures["Missing log artifact"] += 1
            continue
        signatures[failure_signature(text)] += 1
    return signatures.most_common()


def generate_html_report(project: ProjectConfig, *, limit: int = 100) -> dict:
    records = list_run_records(project, limit=limit)
    stats = run_statistics(project)
    failures = _failure_groups(records)

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

    failure_rows = []
    for signature, count in failures:
        failure_rows.append(
            "<tr>"
            f"<td>{count}</td>"
            f"<td><code>{escape(signature)}</code></td>"
            "</tr>"
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
</div>

<section>
<h2>Per-test summary</h2>
<table>
<thead><tr><th>Test</th><th>Total</th><th>Pass</th><th>Fail</th><th>Timeout</th><th>Pass rate</th></tr></thead>
<tbody>{''.join(test_rows) or '<tr><td colspan="6">No runs recorded.</td></tr>'}</tbody>
</table>
</section>

<section>
<h2>Failure signatures</h2>
<table>
<thead><tr><th>Count</th><th>Signature</th></tr></thead>
<tbody>{''.join(failure_rows) or '<tr><td colspan="2">No failures in the selected recent runs.</td></tr>'}</tbody>
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
        "failure_groups": [
            {"signature": signature, "count": count}
            for signature, count in failures
        ],
        "shown_runs": len(records),
    }
