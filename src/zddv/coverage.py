from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET

from zddv.config import ProjectConfig
from zddv.functional_coverage import ingest_functional_coverage_payload
from zddv.storage import record_coverage_snapshot


_COVERAGE_RECORD = re.compile(r"^C\s+'(?P<name>.*)'\s+(?P<count>-?\d+)\s*$")
_POINT_TYPE = re.compile(r"pagev_(?P<kind>[A-Za-z0-9_]+)")
_QUESTA_FCOV_SCOPE = re.compile(r"^\s*TYPE\s+(?P<scope>\S+)")
_QUESTA_FCOV_ITEM = re.compile(
    r"^\s*(?P<kind>Coverpoint|Cross)\s+(?P<name>\S+)",
    re.IGNORECASE,
)
_QUESTA_FCOV_BIN = re.compile(
    r"^\s*bin\s+(?P<name>.+?)\s+(?P<hits>\d+)\s+(?P<goal>\d+)\s+"
    r"(?P<status>Covered|Uncovered|ZERO)\s*$",
    re.IGNORECASE,
)


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def parse_verilator_coverage(path: str | Path) -> list[dict]:
    source = Path(path)
    points: list[dict] = []
    for raw_line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _COVERAGE_RECORD.match(raw_line.strip())
        if not match:
            continue
        name = match.group("name")
        count = int(match.group("count"))
        type_match = _POINT_TYPE.search(name)
        points.append(
            {
                "name": name,
                "count": count,
                "hit": count > 0,
                "type": type_match.group("kind") if type_match else "unknown",
            }
        )
    return points


def _xml_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _questa_code_type(tag: str) -> str | None:
    if tag in {"stmt", "statement"}:
        return "statement"
    if tag in {"branch", "br"}:
        return "branch"
    if tag in {"condition", "cond"}:
        return "condition"
    if tag in {"expression", "expr"}:
        return "expression"
    if tag in {"toggle", "tog"}:
        return "toggle"
    if tag in {"fsmstate", "fsm_state", "state"}:
        return "fsm_state"
    if tag in {"fsmtransition", "fsm_transition", "transition"}:
        return "fsm_transition"
    return None


def parse_questa_code_coverage_xml(path: str | Path) -> list[dict]:
    source = Path(path)
    root = ET.parse(source).getroot()

    instances = [element for element in root.iter() if _xml_tag(element) == "instance"]
    scopes = instances or [root]
    points_by_name: dict[str, dict] = {}

    for scope_index, scope in enumerate(scopes):
        scope_path = str(
            scope.attrib.get("path")
            or scope.attrib.get("name")
            or scope.attrib.get("du")
            or f"scope-{scope_index}"
        )
        files: dict[str, str] = {}
        for element in scope.iter():
            if _xml_tag(element) != "file":
                continue
            attrs = {str(key).lower(): str(value) for key, value in element.attrib.items()}
            file_id = attrs.get("fn") or attrs.get("id") or attrs.get("index")
            file_path = attrs.get("path") or attrs.get("name")
            if file_id is not None and file_path:
                files[file_id] = file_path

        for element_index, element in enumerate(scope.iter()):
            kind = _questa_code_type(_xml_tag(element))
            if kind is None:
                continue

            attrs = {str(key).lower(): str(value) for key, value in element.attrib.items()}
            raw_hits = attrs.get("hits") or attrs.get("count")
            if raw_hits is None:
                continue
            try:
                hits = int(raw_hits)
            except ValueError:
                continue

            file_id = attrs.get("fn") or attrs.get("file")
            file_path = files.get(file_id or "", file_id or "")
            line = attrs.get("ln") or attrs.get("line")
            item = (
                attrs.get("name")
                or attrs.get("signal")
                or attrs.get("state")
                or attrs.get("st")
                or attrs.get("id")
                or str(element_index)
            )
            location_parts = [scope_path]
            if file_path:
                location_parts.append(file_path)
            if line:
                location_parts.append(f"line:{line}")
            location_parts.append(f"{kind}:{item}")
            name = "::".join(location_parts)

            point = {
                "name": name,
                "count": hits,
                "hit": hits > 0,
                "type": kind,
                "metadata": {
                    "scope": scope_path,
                    "file": file_path or None,
                    "line": int(line) if line and line.isdigit() else line,
                    "xml_tag": _xml_tag(element),
                },
            }
            previous = points_by_name.get(name)
            if previous is None or hits > int(previous["count"]):
                points_by_name[name] = point

    return list(points_by_name.values())


def parse_questa_functional_coverage_report(path: str | Path) -> list[dict]:
    source = Path(path)
    scope = ""
    coverpoint = ""
    item_kind = "coverpoint"
    bins: list[dict] = []

    for raw_line in source.read_text(encoding="utf-8", errors="replace").splitlines():
        scope_match = _QUESTA_FCOV_SCOPE.match(raw_line)
        if scope_match:
            scope = scope_match.group("scope")
            coverpoint = ""
            continue

        item_match = _QUESTA_FCOV_ITEM.match(raw_line)
        if item_match:
            item_kind = item_match.group("kind").lower()
            coverpoint = item_match.group("name")
            continue

        bin_match = _QUESTA_FCOV_BIN.match(raw_line)
        if bin_match is None or not coverpoint:
            continue

        bins.append(
            {
                "scope": scope,
                "coverpoint": coverpoint,
                "bin": bin_match.group("name").strip(),
                "hits": int(bin_match.group("hits")),
                "goal": int(bin_match.group("goal")),
                "metadata": {
                    "questa_status": bin_match.group("status"),
                    "kind": item_kind,
                },
            }
        )

    return bins


def _functional_bins_to_points(bins: list[dict]) -> list[dict]:
    points: list[dict] = []
    for item in bins:
        hits = int(item["hits"])
        goal = int(item["goal"])
        name = "::".join(
            part
            for part in (
                str(item.get("scope") or ""),
                str(item.get("coverpoint") or ""),
                str(item.get("bin") or ""),
            )
            if part
        )
        points.append(
            {
                "name": name,
                "count": hits,
                "hit": hits >= goal,
                "type": "covergroup_bin",
                "metadata": {
                    "goal": goal,
                    **dict(item.get("metadata") or {}),
                },
            }
        )
    return points


def summarize_coverage_points(points: list[dict]) -> dict:
    total = len(points)
    hit = sum(bool(point["hit"]) for point in points)
    by_type: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {"total": 0, "hit": 0}
    )
    for point in points:
        kind = str(point.get("type") or "unknown")
        by_type[kind]["total"] += 1
        if point["hit"]:
            by_type[kind]["hit"] += 1

    normalized: dict[str, dict[str, int | float]] = {}
    for kind, values in sorted(by_type.items()):
        kind_total = int(values["total"])
        kind_hit = int(values["hit"])
        normalized[kind] = {
            "total": kind_total,
            "hit": kind_hit,
            "hit_rate": 100.0 * kind_hit / kind_total if kind_total else 0.0,
        }

    return {
        "total_points": total,
        "hit_points": hit,
        "unhit_points": total - hit,
        "hit_rate": 100.0 * hit / total if total else 0.0,
        "by_type": normalized,
    }


def build_coverage_hole_report(
    points: list[dict],
    *,
    point_type: str | None = None,
    limit: int | None = None,
) -> dict:
    """Build a deterministic report of unhit normalized coverage points."""
    holes = [
        point
        for point in points
        if not bool(point.get("hit"))
        and (point_type is None or str(point.get("type")) == point_type)
    ]
    holes.sort(
        key=lambda point: (
            str(point.get("type", "unknown")),
            str(point.get("name", "")),
        )
    )

    by_type: dict[str, int] = defaultdict(int)
    for point in holes:
        by_type[str(point.get("type") or "unknown")] += 1

    shown = holes if limit is None else holes[: max(0, limit)]
    return {
        "filter_type": point_type,
        "total_holes": len(holes),
        "reported_holes": len(shown),
        "by_type": dict(sorted(by_type.items())),
        "holes": [
            {
                "type": str(point.get("type") or "unknown"),
                "name": str(point.get("name") or ""),
                "count": int(point.get("count", 0)),
            }
            for point in shown
        ],
    }


def write_coverage_hole_report(
    points: list[dict],
    output: str | Path,
    *,
    point_type: str | None = None,
    limit: int | None = None,
) -> dict:
    report = build_coverage_hole_report(
        points,
        point_type=point_type,
        limit=limit,
    )
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return {**report, "path": str(destination)}


def _coverage_output_paths(project: ProjectConfig) -> tuple[Path, Path, Path, Path]:
    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return (
        out_dir,
        out_dir / "summary.txt",
        out_dir / "metrics.json",
        out_dir / "points.json",
    )


def _record_normalized_coverage(
    project: ProjectConfig,
    *,
    inputs: list[str],
    merged_path: Path,
    summary_path: Path,
    metrics_path: Path,
    points_path: Path,
    points: list[dict],
    report: str,
    extra: dict | None = None,
) -> dict:
    metrics = summarize_coverage_points(points)
    if metrics["total_points"] == 0:
        raise RuntimeError(
            f"No coverage points could be normalized from {merged_path}."
        )

    points_path.write_text(json.dumps(points, indent=2), encoding="utf-8")
    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("cov-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    payload = {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "project": project.name,
        "simulator": project.simulator,
        "input_count": len(inputs),
        **metrics,
        "merged": str(merged_path),
        "summary": str(summary_path),
        "points": str(points_path),
        **(extra or {}),
    }
    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    record_coverage_snapshot(
        project,
        {
            **payload,
            "metrics_path": str(metrics_path),
        },
    )

    return {
        "inputs": inputs,
        "merged": str(merged_path),
        "summary": str(summary_path),
        "points_path": str(points_path),
        "metrics_path": str(metrics_path),
        "report": report,
        "metrics": metrics,
        "snapshot_id": snapshot_id,
        **(extra or {}),
    }


def merge_verilator_coverage(project: ProjectConfig) -> dict:
    tool = shutil.which("verilator_coverage")
    if tool is None:
        raise RuntimeError(
            "verilator_coverage was not found in PATH. Install Verilator and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    coverage_files = sorted(run_root.glob("*/coverage.dat"))
    if not coverage_files:
        raise RuntimeError(
            f"No coverage.dat files found under {run_root}. "
            "Run coverage-enabled simulations first."
        )

    out_dir, summary_path, metrics_path, points_path = _coverage_output_paths(project)
    merged_path = out_dir / "coverage.dat"
    inputs = [str(path) for path in coverage_files]

    merge_commands = [
        [tool, "-write", str(merged_path), "-read", *inputs],
        [tool, "--write", str(merged_path), *inputs],
    ]
    merge = None
    attempts: list[str] = []
    for command in merge_commands:
        merge = _run(command, project.root)
        attempts.append("$ " + " ".join(command) + "\n" + merge.stdout)
        if merge.returncode == 0 and merged_path.exists():
            break

    if merge is None or merge.returncode != 0 or not merged_path.exists():
        raise RuntimeError(
            "Coverage merge failed:\n" + "\n".join(attempts).strip()
        )

    report_cmd = [tool, str(merged_path)]
    report = _run(report_cmd, project.root)
    summary_path.write_text(report.stdout, encoding="utf-8")

    if report.returncode != 0:
        raise RuntimeError(f"Coverage report failed. See {summary_path}")

    points = parse_verilator_coverage(merged_path)
    return _record_normalized_coverage(
        project,
        inputs=inputs,
        merged_path=merged_path,
        summary_path=summary_path,
        metrics_path=metrics_path,
        points_path=points_path,
        points=points,
        report=report.stdout,
    )


def merge_questa_coverage(project: ProjectConfig) -> dict:
    tool = shutil.which("vcover")
    if tool is None:
        raise RuntimeError(
            "vcover was not found in PATH. Install Questa/QuestaSim and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    coverage_files = sorted(run_root.glob("*/coverage.ucdb"))
    if not coverage_files:
        raise RuntimeError(
            f"No coverage.ucdb files found under {run_root}. "
            "Run coverage-enabled Questa simulations first."
        )

    out_dir, summary_path, metrics_path, points_path = _coverage_output_paths(project)
    merged_path = out_dir / "coverage.ucdb"
    code_report_path = out_dir / "questa-code.xml"
    functional_report_path = out_dir / "questa-functional.txt"
    inputs = [str(path) for path in coverage_files]

    merge_command = [tool, "merge", str(merged_path), *inputs]
    merge = _run(merge_command, project.root)
    if merge.returncode != 0 or not merged_path.exists():
        raise RuntimeError(
            "Questa UCDB merge failed:\n"
            + "$ "
            + " ".join(merge_command)
            + "\n"
            + merge.stdout.strip()
        )

    code_command = [
        tool,
        "report",
        "-xml",
        "-details",
        "-codeAll",
        "-output",
        str(code_report_path),
        str(merged_path),
    ]
    code_report = _run(code_command, project.root)
    if code_report.returncode != 0 or not code_report_path.exists():
        raise RuntimeError(
            f"Questa code-coverage report failed. See {summary_path}"
        )

    functional_command = [
        tool,
        "report",
        "-details",
        "-cvg",
        "-output",
        str(functional_report_path),
        str(merged_path),
    ]
    functional_report = _run(functional_command, project.root)
    if functional_report.returncode != 0 or not functional_report_path.exists():
        raise RuntimeError(
            f"Questa functional-coverage report failed. See {summary_path}"
        )

    code_points = parse_questa_code_coverage_xml(code_report_path)
    functional_bins = parse_questa_functional_coverage_report(functional_report_path)
    functional_points = _functional_bins_to_points(functional_bins)
    points = [*code_points, *functional_points]

    functional_snapshot = None
    if functional_bins:
        functional_snapshot = ingest_functional_coverage_payload(
            project,
            {
                "source": "questa-ucdb",
                "bins": functional_bins,
            },
            input_path=merged_path,
            source="questa-ucdb",
        )

    summary_lines = [
        "Questa UCDB normalized coverage",
        f"Inputs: {len(inputs)}",
        f"Code points: {len(code_points)}",
        f"Functional bins: {len(functional_bins)}",
        "",
        "vcover merge:",
        merge.stdout.rstrip(),
        "",
        "vcover code report:",
        code_report.stdout.rstrip(),
        "",
        "vcover functional report:",
        functional_report_path.read_text(encoding="utf-8", errors="replace").rstrip(),
        "",
    ]
    summary_text = "\n".join(summary_lines)
    summary_path.write_text(summary_text, encoding="utf-8")

    extra = {
        "code_report": str(code_report_path),
        "functional_report": str(functional_report_path),
        "functional_snapshot_id": (
            functional_snapshot["snapshot_id"] if functional_snapshot else None
        ),
    }
    return _record_normalized_coverage(
        project,
        inputs=inputs,
        merged_path=merged_path,
        summary_path=summary_path,
        metrics_path=metrics_path,
        points_path=points_path,
        points=points,
        report=summary_text,
        extra=extra,
    )


def merge_coverage(project: ProjectConfig) -> dict:
    simulator = project.simulator.strip().lower()
    if simulator == "verilator":
        return merge_verilator_coverage(project)
    if simulator in {"questa", "questasim"}:
        return merge_questa_coverage(project)
    raise RuntimeError(
        f"Coverage reporting is not implemented for simulator: {project.simulator}"
    )


def load_normalized_coverage_points(project: ProjectConfig) -> list[dict]:
    out_dir = (project.root / ".zddv" / "coverage").resolve()
    points_path = out_dir / "points.json"
    if points_path.exists():
        payload = json.loads(points_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError(f"Normalized coverage points are invalid: {points_path}")
        return payload

    if project.simulator.strip().lower() == "verilator":
        legacy_path = out_dir / "coverage.dat"
        if legacy_path.exists():
            return parse_verilator_coverage(legacy_path)

    raise RuntimeError(
        f"Normalized coverage points not found under {out_dir}. "
        "Run 'zddv coverage' first."
    )
