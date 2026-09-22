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
from zddv.storage import record_coverage_snapshot


_COVERAGE_RECORD = re.compile(r"^C\s+'(?P<name>.*)'\s+(?P<count>-?\d+)\s*$")
_POINT_TYPE = re.compile(r"pagev_(?P<kind>[A-Za-z0-9_]+)")

_QUESTA_SUMMARY_ROW = re.compile(
    r"^\s*(?P<kind>[A-Za-z][A-Za-z0-9 _/-]*?)\s+"
    r"(?P<bins>\d+)\s+(?P<hits>\d+)\s+(?P<misses>\d+)\s+"
    r"(?P<weight>\d+(?:\.\d+)?)\s+(?P<coverage>\d+(?:\.\d+)?)%\s*$"
)
_QUESTA_TOTAL_COVERAGE = re.compile(
    r"Total coverage \(filtered view\):\s*(?P<coverage>\d+(?:\.\d+)?)%"
)
_QUESTA_KIND_ALIASES = {
    "branches": "branch",
    "branch": "branch",
    "conditions": "condition",
    "condition": "condition",
    "expressions": "expression",
    "expression": "expression",
    "statements": "statement",
    "statement": "statement",
    "toggles": "toggle",
    "toggle": "toggle",
    "fsms": "fsm",
    "fsm": "fsm",
    "assertions": "assertion",
    "assertion": "assertion",
    "covergroups": "covergroup",
    "covergroup": "covergroup",
    "directives": "directive",
    "directive": "directive",
    "coverpoint": "covergroup",
    "coverpoint_bin": "covergroup",
    "cross": "covergroup",
    "cross_bin": "covergroup",
    "bin": "covergroup",
}
_QUESTA_POINT_KINDS = {
    "branch",
    "condition",
    "expression",
    "statement",
    "toggle",
    "fsm",
    "assertion",
    "directive",
    "covergroup",
}


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
    holes.sort(key=lambda point: (str(point.get("type", "unknown")), str(point.get("name", ""))))

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



def _normalize_questa_coverage_kind(label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return _QUESTA_KIND_ALIASES.get(normalized, normalized or "unknown")


def parse_questa_coverage_summary(text: str) -> dict:
    """Parse the numeric table emitted by vcover report -summary."""
    by_type: dict[str, dict[str, int | float]] = {}
    total_points = 0
    hit_points = 0
    unhit_points = 0

    for raw_line in text.splitlines():
        match = _QUESTA_SUMMARY_ROW.match(raw_line)
        if match is None:
            continue
        kind = _normalize_questa_coverage_kind(match.group("kind"))
        total = int(match.group("bins"))
        hit = int(match.group("hits"))
        misses = int(match.group("misses"))
        reported_rate = float(match.group("coverage"))
        by_type[kind] = {
            "total": total,
            "hit": hit,
            "hit_rate": reported_rate,
        }
        total_points += total
        hit_points += hit
        unhit_points += misses

    tool_total_match = _QUESTA_TOTAL_COVERAGE.search(text)
    tool_total_coverage = (
        float(tool_total_match.group("coverage"))
        if tool_total_match is not None
        else None
    )

    return {
        "total_points": total_points,
        "hit_points": hit_points,
        "unhit_points": unhit_points,
        "hit_rate": (
            100.0 * hit_points / total_points
            if total_points
            else 0.0
        ),
        "by_type": dict(sorted(by_type.items())),
        "tool_total_coverage": tool_total_coverage,
    }


def _xml_local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].strip().lower().replace("-", "_")


def _xml_attributes(element: ET.Element) -> dict[str, str]:
    return {
        _xml_local_name(str(key)): str(value).strip()
        for key, value in element.attrib.items()
    }


def _parse_xml_int(value: str | None) -> int | None:
    if value is None:
        return None
    match = re.search(r"-?\d+", value)
    return int(match.group(0)) if match is not None else None


def _questa_kind_from_xml(
    tag: str,
    attrs: dict[str, str],
    inherited_kind: str | None,
) -> str:
    raw = (
        attrs.get("coverage_type")
        or attrs.get("coveragetype")
        or attrs.get("metric")
        or attrs.get("kind")
        or attrs.get("type")
        or tag
    )
    normalized = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")

    if normalized in {"bin", "coverpoint_bin", "cross_bin"}:
        return "covergroup"
    if normalized in _QUESTA_KIND_ALIASES:
        return _QUESTA_KIND_ALIASES[normalized]
    if "statement" in normalized or normalized in {"stmt", "line"}:
        return "statement"
    if "branch" in normalized:
        return "branch"
    if "condition" in normalized:
        return "condition"
    if "expression" in normalized or normalized == "expr":
        return "expression"
    if "toggle" in normalized:
        return "toggle"
    if "fsm" in normalized or "state" in normalized or "transition" in normalized:
        return "fsm"
    if "assert" in normalized:
        return "assertion"
    if "directive" in normalized or normalized == "cover":
        return "directive"
    if normalized in {"covergroup", "coverpoint", "cross"}:
        return "covergroup"
    return inherited_kind or "unknown"


def _questa_xml_hit_count(attrs: dict[str, str]) -> int | None:
    for key in (
        "hits",
        "hit_count",
        "hitcount",
        "count",
        "cover_count",
        "covercount",
        "covered_count",
        "coveredcount",
    ):
        value = _parse_xml_int(attrs.get(key))
        if value is not None:
            return value

    status = (attrs.get("status") or attrs.get("state") or "").strip().lower()
    if status in {"zero", "uncovered", "missed", "unhit", "not_covered"}:
        return 0
    if status in {"covered", "hit", "passed"}:
        return 1
    return None


def parse_questa_coverage_xml(text: str) -> list[dict]:
    """Normalize item-level coverage evidence from vcover XML output."""
    root = ET.fromstring(text)
    points: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def walk(
        element: ET.Element,
        path_parts: list[str],
        inherited_kind: str | None,
    ) -> None:
        tag = _xml_local_name(str(element.tag))
        attrs = _xml_attributes(element)
        kind = _questa_kind_from_xml(tag, attrs, inherited_kind)

        label = (
            attrs.get("path")
            or attrs.get("fullname")
            or attrs.get("full_name")
            or attrs.get("name")
            or attrs.get("id")
        )
        next_parts = list(path_parts)
        if label and (not next_parts or next_parts[-1] != label):
            next_parts.append(label)

        children = list(element)
        count = _questa_xml_hit_count(attrs)
        container_tag = tag in {
            "coverage",
            "coveragereport",
            "report",
            "instance",
            "scope",
            "designunit",
            "du",
            "covergroup",
            "coverpoint",
            "cross",
        }
        if (
            kind in _QUESTA_POINT_KINDS
            and count is not None
            and not (container_tag and children)
        ):
            name = attrs.get("path") or "/".join(next_parts)
            source = (
                attrs.get("source")
                or attrs.get("file")
                or attrs.get("filename")
                or attrs.get("srcfile")
            )
            line = attrs.get("line") or attrs.get("lineno") or attrs.get("line_number")
            if source and line:
                location = f"{source}:{line}"
                name = f"{name}@{location}" if name else location
            elif source and not name:
                name = source

            if not name:
                name = f"{kind}:{len(points)}"

            key = (kind, name)
            if key not in seen:
                seen.add(key)
                points.append(
                    {
                        "name": name,
                        "count": max(0, int(count)),
                        "hit": int(count) > 0,
                        "type": kind,
                    }
                )

        child_kind = kind if kind in _QUESTA_POINT_KINDS else inherited_kind
        for child in children:
            walk(child, next_parts, child_kind)

    walk(root, [], None)
    return points


def load_normalized_coverage_points(project: ProjectConfig) -> list[dict]:
    simulator = project.simulator.strip().lower()
    out_dir = (project.root / ".zddv" / "coverage").resolve()
    if simulator == "verilator":
        merged_path = out_dir / "coverage.dat"
        if not merged_path.exists():
            raise RuntimeError(
                f"Merged coverage not found at {merged_path}. Run 'zddv coverage' first."
            )
        return parse_verilator_coverage(merged_path)

    if simulator in {"questa", "questasim"}:
        points_path = out_dir / "points.json"
        if not points_path.exists():
            raise RuntimeError(
                f"Normalized Questa coverage points not found at {points_path}. "
                "Run 'zddv coverage' first."
            )
        payload = json.loads(points_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError(f"Invalid normalized coverage point file: {points_path}")
        return [dict(item) for item in payload]

    raise RuntimeError(
        f"Coverage point loading is not implemented for simulator: {project.simulator}"
    )


def merge_questa_coverage(project: ProjectConfig) -> dict:
    tool = shutil.which("vcover")
    if tool is None:
        raise RuntimeError(
            "Questa vcover was not found in PATH. Install/configure Questa and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    coverage_files = sorted(run_root.glob("*/coverage.ucdb"))
    if not coverage_files:
        raise RuntimeError(
            f"No coverage.ucdb files found under {run_root}. "
            "Run coverage-enabled Questa simulations first."
        )

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_path = out_dir / "coverage.ucdb"
    summary_path = out_dir / "summary.txt"
    details_path = out_dir / "details.xml"
    points_path = out_dir / "points.json"
    metrics_path = out_dir / "metrics.json"
    inputs = [str(path) for path in coverage_files]

    if merged_path.exists():
        merged_path.unlink()

    merge_cmd = [tool, "merge", "-out", str(merged_path), *inputs]
    merge = _run(merge_cmd, project.root)
    if merge.returncode != 0 or not merged_path.exists():
        raise RuntimeError(
            "Questa UCDB merge failed:\n"
            + "$ "
            + " ".join(merge_cmd)
            + "\n"
            + (merge.stdout or "").strip()
        )

    report_cmd = [tool, "report", "-summary", str(merged_path)]
    report = _run(report_cmd, project.root)
    summary_path.write_text(report.stdout, encoding="utf-8")
    if report.returncode != 0:
        raise RuntimeError(f"Questa coverage report failed. See {summary_path}")

    metrics = parse_questa_coverage_summary(report.stdout)
    if not metrics["by_type"]:
        raise RuntimeError(
            f"No numeric coverage summary rows could be parsed from {summary_path}."
        )

    details_cmd = [
        tool,
        "report",
        "-xml",
        "-output",
        str(details_path),
        str(merged_path),
    ]
    details = _run(details_cmd, project.root)
    if details.returncode != 0 or not details_path.exists():
        raise RuntimeError(
            "Questa item-level coverage report failed:\n"
            + "$ "
            + " ".join(details_cmd)
            + "\n"
            + (details.stdout or "").strip()
        )
    try:
        points = parse_questa_coverage_xml(
            details_path.read_text(encoding="utf-8", errors="replace")
        )
    except ET.ParseError as exc:
        raise RuntimeError(
            f"Questa XML coverage report could not be parsed: {details_path}: {exc}"
        ) from exc
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
        "details": str(details_path),
        "points": str(points_path),
        "item_points": len(points),
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
        "details_path": str(details_path),
        "points_path": str(points_path),
        "metrics_path": str(metrics_path),
        "report": report.stdout,
        "metrics": metrics,
        "points": points,
        "snapshot_id": snapshot_id,
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

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_path = out_dir / "coverage.dat"
    summary_path = out_dir / "summary.txt"
    metrics_path = out_dir / "metrics.json"
    inputs = [str(path) for path in coverage_files]

    # Verilator 5.020 uses the legacy -write/-read form. New releases accept
    # --write with positional inputs. Try legacy first for distro compatibility,
    # then fall back to the current syntax.
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
    metrics = summarize_coverage_points(points)
    if metrics["total_points"] == 0:
        raise RuntimeError(
            f"No coverage points could be parsed from {merged_path}."
        )

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
        "metrics_path": str(metrics_path),
        "report": report.stdout,
        "metrics": metrics,
        "snapshot_id": snapshot_id,
    }


def merge_coverage(project: ProjectConfig) -> dict:
    simulator = project.simulator.strip().lower()
    if simulator == "verilator":
        return merge_verilator_coverage(project)
    if simulator in {"questa", "questasim"}:
        return merge_questa_coverage(project)
    raise RuntimeError(
        f"Coverage merge/report is not implemented for simulator: {project.simulator}"
    )
