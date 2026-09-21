from __future__ import annotations

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
        signature = failure_signature(row.get("log_path", ""), status)
        if signature:
            ET.SubElement(
                properties,
                "property",
                {"name": "failure_signature", "value": signature},
            )

        if status == "FAIL":
            failure = ET.SubElement(
                case,
                "failure",
                {
                    "message": signature or f"simulation returned {row['returncode']}",
                    "type": "SimulationFailure",
                },
            )
            failure.text = f"Log: {row.get('log_path', '')}"
        elif status == "TIMEOUT":
            error = ET.SubElement(
                case,
                "error",
                {
                    "message": signature or "simulation timed out",
                    "type": "SimulationTimeout",
                },
            )
            error.text = f"Log: {row.get('log_path', '')}"

    tree = ET.ElementTree(suite)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
