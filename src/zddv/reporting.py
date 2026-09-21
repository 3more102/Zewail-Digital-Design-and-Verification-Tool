from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def write_junit_report(
    records: list[dict[str, Any]],
    output: str | Path,
    *,
    suite_name: str = "zddv",
) -> Path:
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    failures = sum(row["status"] == "FAIL" for row in records)
    errors = sum(row["status"] == "TIMEOUT" for row in records)
    total_seconds = sum(
        float(row.get("duration_ms") or 0.0) / 1000.0 for row in records
    )

    suite = ET.Element(
        "testsuite",
        {
            "name": suite_name,
            "tests": str(len(records)),
            "failures": str(failures),
            "errors": str(errors),
            "time": f"{total_seconds:.6f}",
        },
    )

    for row in records:
        duration_s = float(row.get("duration_ms") or 0.0) / 1000.0
        test_name = row.get("test_name") or row["run_id"]
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": str(row.get("project") or suite_name),
                "name": str(test_name),
                "time": f"{duration_s:.6f}",
            },
        )

        properties = ET.SubElement(case, "properties")
        ET.SubElement(
            properties,
            "property",
            {"name": "run_id", "value": str(row["run_id"])},
        )
        ET.SubElement(
            properties,
            "property",
            {"name": "simulator", "value": str(row.get("simulator") or "")},
        )
        if row.get("seed") is not None:
            ET.SubElement(
                properties,
                "property",
                {"name": "seed", "value": str(row["seed"])},
            )

        status = row["status"]
        if status == "FAIL":
            failure = ET.SubElement(
                case,
                "failure",
                {
                    "message": f"simulation returned {row['returncode']}",
                    "type": "SimulationFailure",
                },
            )
            failure.text = f"Log: {row.get('log_path', '')}"
        elif status == "TIMEOUT":
            error = ET.SubElement(
                case,
                "error",
                {
                    "message": "simulation timed out",
                    "type": "SimulationTimeout",
                },
            )
            error.text = f"Log: {row.get('log_path', '')}"

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path



def write_html_report(
    records: list[dict[str, Any]],
    failure_groups: list[dict[str, Any]],
    output: str | Path,
    *,
    suite_name: str = "zddv",
) -> Path:
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    total = len(records)
    passed = sum(row.get("status") == "PASS" for row in records)
    failed = sum(row.get("status") == "FAIL" for row in records)
    timed_out = sum(row.get("status") == "TIMEOUT" for row in records)
    pass_rate = (100.0 * passed / total) if total else 0.0
    total_seconds = (
        sum(float(row.get("duration_ms") or 0.0) for row in records) / 1000.0
    )

    def text(value: Any) -> str:
        return escape("" if value is None else str(value), quote=True)

    run_rows: list[str] = []
    for row in records:
        status = str(row.get("status") or "UNKNOWN")
        duration_ms = row.get("duration_ms")
        duration = "-" if duration_ms is None else f"{float(duration_ms):.1f}"
        seed = "-" if row.get("seed") is None else str(row["seed"])
        run_rows.append(
            "<tr>"
            f'<td><span class="status status-{text(status.lower())}">{text(status)}</span></td>'
            f"<td>{text(row.get('test_name') or '-')}</td>"
            f"<td>{text(seed)}</td>"
            f"<td>{text(duration)}</td>"
            f"<td>{text(row.get('simulator') or '-')}</td>"
            f"<td>{text(row.get('created_at') or '-')}</td>"
            f'<td class="mono">{text(row.get("run_id") or "-")}</td>'
            "</tr>"
        )
    if not run_rows:
        run_rows.append(
            '<tr><td colspan="7" class="empty">No runs selected.</td></tr>'
        )

    group_rows: list[str] = []
    for group in failure_groups:
        statuses = ", ".join(str(item) for item in group.get("statuses", [])) or "-"
        tests = ", ".join(str(item) for item in group.get("tests", [])) or "-"
        seeds = ", ".join(str(item) for item in group.get("seeds", [])) or "-"
        group_rows.append(
            "<tr>"
            f"<td>{text(group.get('count', 0))}</td>"
            f"<td>{text(statuses)}</td>"
            f"<td>{text(tests)}</td>"
            f"<td>{text(seeds)}</td>"
            f'<td class="mono signature">{text(group.get("signature") or "-")}</td>'
            "</tr>"
        )
    if not group_rows:
        group_rows.append(
            '<tr><td colspan="5" class="empty">'
            "No failure groups in the selected runs."
            "</td></tr>"
        )

    generated = datetime.now(timezone.utc).isoformat()
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZDDV Report — {text(suite_name)}</title>
<style>
:root {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; color-scheme: light dark; }}
body {{ max-width: 1400px; margin: 0 auto; padding: 32px; line-height: 1.45; }}
h1 {{ margin-bottom: 4px; }}
.meta {{ opacity: .72; margin-bottom: 24px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 20px 0 30px; }}
.card {{ border: 1px solid #8885; border-radius: 10px; padding: 14px 16px; }}
.card strong {{ display: block; font-size: 1.7rem; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0 32px; }}
th, td {{ text-align: left; border-bottom: 1px solid #8884; padding: 9px 10px; vertical-align: top; }}
th {{ position: sticky; top: 0; backdrop-filter: blur(8px); }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9rem; }}
.signature {{ overflow-wrap: anywhere; max-width: 560px; }}
.status {{ font-weight: 700; }}
.status-pass {{ color: #16803a; }}
.status-fail, .status-timeout {{ color: #b42318; }}
.empty {{ text-align: center; opacity: .7; padding: 24px; }}
</style>
</head>
<body>
<h1>ZDDV Verification Report</h1>
<div class="meta">Project: <strong>{text(suite_name)}</strong> · Generated: {text(generated)}</div>
<section class="grid" aria-label="Verification summary">
  <div class="card"><span>Total runs</span><strong>{total}</strong></div>
  <div class="card"><span>Passed</span><strong>{passed}</strong></div>
  <div class="card"><span>Failed</span><strong>{failed}</strong></div>
  <div class="card"><span>Timed out</span><strong>{timed_out}</strong></div>
  <div class="card"><span>Pass rate</span><strong>{pass_rate:.1f}%</strong></div>
  <div class="card"><span>Total sim time</span><strong>{total_seconds:.3f}s</strong></div>
</section>

<h2>Run History</h2>
<table>
<thead><tr><th>Status</th><th>Test</th><th>Seed</th><th>Time (ms)</th><th>Simulator</th><th>Created</th><th>Run ID</th></tr></thead>
<tbody>{''.join(run_rows)}</tbody>
</table>

<h2>Failure Groups</h2>
<table>
<thead><tr><th>Count</th><th>Status</th><th>Tests</th><th>Seeds</th><th>Signature</th></tr></thead>
<tbody>{''.join(group_rows)}</tbody>
</table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return path
