from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from zddv.config import ProjectConfig
from zddv.storage import list_runs


def _testcase_name(row: dict) -> str:
    name = row["test_name"] or "unnamed"
    if row["seed"] is not None:
        return f"{name}[seed={row['seed']}]"
    return name


def export_runs(
    project: ProjectConfig,
    output: str | Path,
    *,
    format: str,
    limit: int = 1000,
    status: str | None = None,
) -> Path:
    rows = list_runs(project, limit=limit, status=status)
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if format == "json":
        payload = {
            "project": project.name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runs": rows,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    if format != "junit":
        raise ValueError(f"Unsupported export format: {format}")

    total_time_s = sum((row["duration_ms"] or 0.0) for row in rows) / 1000.0
    failures = sum(row["status"] != "PASS" for row in rows)
    suite = ET.Element(
        "testsuite",
        {
            "name": f"ZDDV:{project.name}",
            "tests": str(len(rows)),
            "failures": str(failures),
            "errors": "0",
            "skipped": "0",
            "time": f"{total_time_s:.6f}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    for row in reversed(rows):
        duration_s = (row["duration_ms"] or 0.0) / 1000.0
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"zddv.{project.name}",
                "name": _testcase_name(row),
                "time": f"{duration_s:.6f}",
            },
        )
        if row["status"] != "PASS":
            failure_type = (
                "timeout" if row["status"] == "TIMEOUT" else "simulation_failure"
            )
            failure = ET.SubElement(
                case,
                "failure",
                {
                    "type": failure_type,
                    "message": (
                        f"{row['status']} (return code {row['returncode']})"
                    ),
                },
            )
            failure.text = (
                f"run_id={row['run_id']}\n"
                f"run_dir={row['run_dir']}"
            )

        out = ET.SubElement(case, "system-out")
        out.text = (
            f"run_id={row['run_id']}\n"
            f"simulator={row['simulator']}\n"
            f"status={row['status']}\n"
            f"run_dir={row['run_dir']}"
        )

    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path
