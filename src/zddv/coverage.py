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
import xml.etree.ElementTree as ET

from zddv.config import ProjectConfig
from zddv.functional_coverage import ingest_functional_coverage
from zddv.storage import record_coverage_snapshot


_COVERAGE_RECORD = re.compile(r"^C\s+'(?P<name>.*)'\s+(?P<count>-?\d+)\s*$")
_POINT_TYPE = re.compile(r"pagev_(?P<kind>[A-Za-z0-9_]+)")

_QUESTA_SUMMARY_ROW = re.compile(
    r"^\s*(?P<kind>[A-Za-z][A-Za-z0-9 _/-]*?)\s+"
    r"(?P<bins>\d[\d,]*)\s+(?P<hits>\d[\d,]*)\s+(?P<misses>\d[\d,]*)\s+"
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
}

_QUESTA_CVG_SCOPE = re.compile(
    r"^\s*(?!Coverpoint\b|Cross\b|bin\b|ignore_bins?\b|illegal_bins?\b)"
    r"(?P<name>\S.*?)\s+(?P<metric>\d+(?:\.\d+)?)%\s+"
    r"(?P<goal>\d+(?:\.\d+)?)%?\s+"
    r"(?:\S+\s+)?(?P<status>\S+)\s*$",
    re.IGNORECASE,
)
_QUESTA_CVG_POINT = re.compile(
    r"^\s*(?P<kind>Coverpoint|Cross)\s+(?P<name>.+?)\s+"
    r"(?P<metric>\d+(?:\.\d+)?)%\s+"
    r"(?P<goal>\d+(?:\.\d+)?)%?\s+"
    r"(?:\S+\s+)?(?P<status>\S+)\s*$",
    re.IGNORECASE,
)
_QUESTA_CVG_BIN = re.compile(
    r"^\s*bin\s+(?P<name>.+?)\s+"
    r"(?P<hits>\d[\d,]*)\s+(?P<goal>\d[\d,]*)\s+"
    r"(?P<status>\S+)\s*$",
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
                **{
                    key: point[key]
                    for key in ("scope", "file", "line", "statement")
                    if key in point and point[key] is not None
                },
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
        total = int(match.group("bins").replace(",", ""))
        hit = int(match.group("hits").replace(",", ""))
        misses = int(match.group("misses").replace(",", ""))
        if hit + misses != total:
            continue
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


def _xml_local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def parse_questa_statement_coverage_xml(path: str | Path) -> list[dict]:
    """Parse documented Questa XML statement records into normalized points."""
    source = Path(path)
    root = ET.parse(source).getroot()
    points: list[dict] = []

    instances = [
        element
        for element in root.iter()
        if _xml_local_tag(element) == "instancedata"
    ]
    for instance_index, instance in enumerate(instances):
        scope = str(
            instance.attrib.get("path")
            or instance.attrib.get("du")
            or f"instance-{instance_index}"
        )
        files: dict[str, str] = {}
        for element in instance.iter():
            if _xml_local_tag(element) != "filemap":
                continue
            file_id = element.attrib.get("fn")
            file_path = element.attrib.get("path")
            if file_id is not None and file_path:
                files[str(file_id)] = str(file_path)

        for statement_index, element in enumerate(instance.iter()):
            if _xml_local_tag(element) != "stmt":
                continue
            raw_hits = element.attrib.get("hits")
            if raw_hits is None:
                continue
            try:
                hits = int(str(raw_hits).replace(",", ""))
            except ValueError:
                continue

            file_id = element.attrib.get("fn")
            file_path = files.get(str(file_id)) if file_id is not None else None
            raw_line = element.attrib.get("ln")
            raw_statement = element.attrib.get("st")
            try:
                line_number = int(raw_line) if raw_line is not None else None
            except ValueError:
                line_number = None
            try:
                statement_number = (
                    int(raw_statement) if raw_statement is not None else None
                )
            except ValueError:
                statement_number = None

            location = file_path or f"fn={file_id or '?'}"
            line_label = str(line_number) if line_number is not None else "?"
            statement_label = (
                str(statement_number) if statement_number is not None else str(statement_index)
            )
            points.append(
                {
                    "name": (
                        f"{scope}:{location}:{line_label}:statement:{statement_label}"
                    ),
                    "count": hits,
                    "hit": hits > 0,
                    "type": "statement",
                    "metadata": {
                        "scope": scope,
                        "file": file_path,
                        "file_id": str(file_id) if file_id is not None else None,
                        "line": line_number,
                        "statement": statement_number,
                    },
                }
            )

    return points


def _write_normalized_coverage_points(
    project: ProjectConfig,
    points: list[dict],
) -> Path:
    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / "points.json"
    destination.write_text(json.dumps(points, indent=2), encoding="utf-8")
    return destination


def load_normalized_coverage_points(project: ProjectConfig) -> list[dict]:
    """Load point-level coverage evidence produced by the active backend."""
    out_dir = (project.root / ".zddv" / "coverage").resolve()
    points_path = out_dir / "points.json"
    if points_path.exists():
        payload = json.loads(points_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise RuntimeError(
                f"Normalized coverage points are invalid: {points_path}"
            )
        return payload

    if project.simulator.strip().lower() == "verilator":
        legacy_path = out_dir / "coverage.dat"
        if legacy_path.exists():
            return parse_verilator_coverage(legacy_path)

    raise RuntimeError(
        f"Normalized coverage points not found under {out_dir}. "
        "Run 'zddv coverage' first."
    )


def parse_questa_functional_coverage_report(text: str) -> dict:
    """Normalize ordinary covergroup bins from a detailed vcover text report.

    Ignore and illegal bins have different verification semantics, so this
    adapter deliberately does not rewrite them as ordinary coverage goals.
    """
    bins: list[dict] = []
    scope = ""
    coverpoint = ""
    coverage_kind = ""

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip().lower()
        if stripped.startswith(
            ("covered/total bins:", "missing/total bins:", "% hit:")
        ):
            continue

        point = _QUESTA_CVG_POINT.match(line)
        if point is not None:
            coverpoint = point.group("name").strip()
            coverage_kind = point.group("kind").lower()
            continue

        item = _QUESTA_CVG_BIN.match(line)
        if item is not None and coverpoint:
            hits = int(item.group("hits").replace(",", ""))
            goal = int(item.group("goal").replace(",", ""))
            if goal < 1:
                continue
            bins.append(
                {
                    "scope": scope,
                    "coverpoint": coverpoint,
                    "bin": item.group("name").strip(),
                    "hits": hits,
                    "goal": goal,
                    "metadata": {
                        "questa_status": item.group("status"),
                        "coverage_kind": coverage_kind,
                    },
                }
            )
            continue

        group = _QUESTA_CVG_SCOPE.match(line)
        if group is not None:
            name = group.group("name").strip()
            for prefix in ("TYPE ", "INSTANCE ", "Covergroup "):
                if name.upper().startswith(prefix.upper()):
                    name = name[len(prefix):].strip()
                    break
            scope = name
            coverpoint = ""
            coverage_kind = ""

    return {"source": "questa-vcover", "bins": bins}



def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_questa_statement_coverage_xml(path: str | Path) -> list[dict]:
    """Normalize statement items from Questa vcover XML coverage output."""
    source = Path(path)
    root = ET.parse(source).getroot()

    file_map: dict[str, str] = {}
    for element in root.iter():
        if _xml_local_name(element.tag) not in {"file", "fileMap"}:
            continue
        file_number = element.attrib.get("fn")
        file_path = element.attrib.get("path")
        if file_number is not None and file_path:
            file_map[str(file_number)] = str(file_path)

    parents = {
        child: parent
        for parent in root.iter()
        for child in parent
    }

    points: list[dict] = []
    for element in root.iter():
        if _xml_local_name(element.tag) != "stmt":
            continue

        try:
            hits = int(element.attrib["hits"])
        except (KeyError, TypeError, ValueError):
            continue
        if hits < 0:
            continue

        line: int | None = None
        statement: int | None = None
        try:
            if "ln" in element.attrib:
                line = int(element.attrib["ln"])
        except ValueError:
            line = None
        try:
            if "st" in element.attrib:
                statement = int(element.attrib["st"])
        except ValueError:
            statement = None

        file_number = element.attrib.get("fn")
        source_path = file_map.get(str(file_number)) if file_number is not None else None

        scope = ""
        parent = parents.get(element)
        while parent is not None:
            if _xml_local_name(parent.tag) in {"instance", "instanceData"}:
                scope = str(parent.attrib.get("path") or "").strip()
                if scope:
                    break
            parent = parents.get(parent)

        location = source_path or (
            f"file#{file_number}" if file_number is not None else "<unknown-source>"
        )
        if line is not None:
            location += f":{line}"
        if statement is not None:
            location += f":stmt{statement}"
        name = f"{scope}|{location}" if scope else location

        points.append(
            {
                "type": "statement",
                "name": name,
                "count": hits,
                "hit": hits > 0,
                "scope": scope,
                "file": source_path,
                "line": line,
                "statement": statement,
            }
        )

    points.sort(
        key=lambda point: (
            str(point.get("scope") or ""),
            str(point.get("file") or ""),
            -1 if point.get("line") is None else int(point["line"]),
            -1 if point.get("statement") is None else int(point["statement"]),
        )
    )
    return points


def write_questa_statement_hole_report(
    project: ProjectConfig,
    output: str | Path,
    *,
    limit: int | None = None,
) -> dict:
    """Export statement details from merged UCDB and write normalized zero-hit holes."""
    tool = shutil.which("vcover")
    if tool is None:
        raise RuntimeError(
            "Questa vcover was not found in PATH. Install/configure Questa and retry."
        )

    merged_path = (project.root / ".zddv" / "coverage" / "coverage.ucdb").resolve()
    if not merged_path.exists():
        raise RuntimeError(
            f"Merged Questa coverage not found at {merged_path}. "
            "Run 'zddv coverage' first."
        )

    out_dir = (project.root / ".zddv" / "coverage" / "questa").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path = out_dir / "statement-details.xml"
    if xml_path.exists():
        xml_path.unlink()

    command = [
        tool,
        "report",
        "-xml",
        "-notimestamps",
        "-code",
        "s",
        "-output",
        str(xml_path),
        str(merged_path),
    ]
    completed = _run(command, project.root)
    if completed.returncode != 0 or not xml_path.exists():
        raise RuntimeError(
            "Questa statement coverage XML export failed:\n"
            + "$ "
            + " ".join(command)
            + "\n"
            + (completed.stdout or "").strip()
        )

    try:
        points = parse_questa_statement_coverage_xml(xml_path)
    except ET.ParseError as exc:
        raise RuntimeError(
            f"Questa statement coverage XML is malformed: {xml_path}"
        ) from exc

    if not points:
        raise RuntimeError(
            f"No statement coverage items could be normalized from {xml_path}."
        )

    report = build_coverage_hole_report(
        points,
        point_type="statement",
        limit=limit,
    )

    destination = Path(output)
    if not destination.is_absolute():
        destination = (project.root / destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        **report,
        "source": "questa-vcover-xml",
        "merged": str(merged_path),
        "xml": str(xml_path),
        "command": command,
    }
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**payload, "path": str(destination)}


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
    metrics_path = out_dir / "metrics.json"
    functional_report_path = out_dir / "functional.txt"
    functional_json_path = out_dir / "functional.json"
    statement_report_path = out_dir / "statements.xml"
    points_path = out_dir / "points.json"
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

    functional_cmd = [tool, "report", "-cvg", "-details", str(merged_path)]
    functional_report = _run(functional_cmd, project.root)
    functional_report_path.write_text(
        functional_report.stdout or "",
        encoding="utf-8",
    )
    functional_snapshot_id = None
    functional_bins = 0
    if functional_report.returncode == 0:
        functional_payload = parse_questa_functional_coverage_report(
            functional_report.stdout or ""
        )
        functional_bins = len(functional_payload["bins"])
        if functional_bins:
            functional_json_path.write_text(
                json.dumps(functional_payload, indent=2),
                encoding="utf-8",
            )
            functional_record = ingest_functional_coverage(
                project,
                functional_json_path,
                source="questa-vcover",
            )
            functional_snapshot_id = functional_record["snapshot_id"]

    if points_path.exists():
        points_path.unlink()
    if statement_report_path.exists():
        statement_report_path.unlink()

    statement_command = [
        tool,
        "report",
        "-setdefault",
        "byinstance",
        "-xml",
        "-code",
        "s",
        "-output",
        str(statement_report_path),
        str(merged_path),
    ]
    statement_report = _run(statement_command, project.root)
    statement_points: list[dict] = []
    statement_status = "unavailable"
    statement_error = ""
    if statement_report.returncode == 0 and statement_report_path.exists():
        try:
            statement_points = parse_questa_statement_coverage_xml(
                statement_report_path
            )
        except ET.ParseError as exc:
            statement_status = "invalid-xml"
            statement_error = str(exc)
        else:
            statement_status = "normalized" if statement_points else "empty"
            if statement_points:
                _write_normalized_coverage_points(project, statement_points)
    else:
        statement_error = (statement_report.stdout or "").strip()

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
        "functional_report": str(functional_report_path),
        "functional_snapshot_id": functional_snapshot_id,
        "functional_bins": functional_bins,
        "statement_report": (
            str(statement_report_path) if statement_report_path.exists() else None
        ),
        "statement_points": len(statement_points),
        "statement_points_path": (
            str(points_path) if points_path.exists() else None
        ),
        "statement_capture": statement_status,
        "statement_command": statement_command,
    }
    if statement_error:
        payload["statement_error"] = statement_error
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
        "functional_report": str(functional_report_path),
        "functional_snapshot_id": functional_snapshot_id,
        "functional_bins": functional_bins,
        "statement_report": (
            str(statement_report_path) if statement_report_path.exists() else None
        ),
        "statement_points": len(statement_points),
        "statement_points_path": (
            str(points_path) if points_path.exists() else None
        ),
        "statement_capture": statement_status,
        "statement_error": statement_error or None,
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
