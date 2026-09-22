from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET

from zddv.config import ProjectConfig
from zddv.functional_coverage import ingest_functional_coverage
from zddv.storage import record_coverage_score_snapshot, record_coverage_snapshot


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
_QUESTA_CODE_DETAIL_HEADER = re.compile(
    r"^\s*(?P<kind>Statement|Branch|Condition|Expression)\s+Coverage\s+for\s+file\s+"
    r"(?P<file>.+?)\s*--\s*$",
    re.IGNORECASE,
)
_QUESTA_CODE_DETAIL_ROW = re.compile(
    r"^\s*(?P<line>\d+)\s+(?P<item>\d+)\s+"
    r"(?P<hits>(?:\*{3})?\d[\d,]*(?:\*{3})?)"
    r"(?:\s+(?P<detail>.*?))?\s*$"
)
_QUESTA_FEC_ITEM = re.compile(
    r"^\s*Line\s+(?P<line>\d+)\s+Item\s+(?P<item>\d+)"
    r"(?:\s+(?P<detail>.*?))?\s*$",
    re.IGNORECASE,
)
_QUESTA_FEC_ROW = re.compile(
    r"^\s*Row\s+(?P<row>\d+):\s+"
    r"(?P<hits>(?:\*{3})?\d[\d,]*(?:\*{3})?)\s+"
    r"(?P<target>\S+)"
    r"(?:\s+(?P<detail>.*?))?\s*$",
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
                    for key in (
                        "scope",
                        "file",
                        "statement",
                        "condition",
                        "expression",
                        "fec_context",
                        "fec_target",
                        "evidence",
                    )
                    if key in point and point[key] is not None
                },
                **(
                    {"source_file": str(point["source_file"])}
                    if point.get("source_file") is not None
                    else {}
                ),
                **(
                    {"line": int(point["line"])}
                    if point.get("line") is not None
                    else {}
                ),
                **(
                    {"item": int(point["item"])}
                    if point.get("item") is not None
                    else {}
                ),
                **(
                    {"row": int(point["row"])}
                    if point.get("row") is not None
                    else {}
                ),
                **(
                    {"detail": str(point["detail"])}
                    if point.get("detail") is not None
                    else {}
                ),
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


def parse_questa_code_coverage_report(text: str) -> list[dict]:
    """Normalize statement/branch plus scalar condition/expression FEC rows.

    FEC rows are accepted only after the explicit Rows/FEC Target table
    header. This avoids treating truth-table or other diagnostic rows as
    normalized coverage points. Multibit FEC layouts are intentionally left
    unnormalized until their exact row semantics are verified.
    """
    points: list[dict] = []
    kind = ""
    source_file = ""
    fec_line: int | None = None
    fec_item: int | None = None
    fec_context = ""
    in_fec_rows = False

    for raw_line in text.splitlines():
        header = _QUESTA_CODE_DETAIL_HEADER.match(raw_line)
        if header is not None:
            kind = header.group("kind").strip().lower()
            source_file = header.group("file").strip()
            fec_line = None
            fec_item = None
            fec_context = ""
            in_fec_rows = False
            continue

        if not source_file:
            continue

        if kind in {"statement", "branch"}:
            item = _QUESTA_CODE_DETAIL_ROW.match(raw_line)
            if item is None:
                continue

            hits = int(item.group("hits").replace("*", "").replace(",", ""))
            line_number = int(item.group("line"))
            item_number = int(item.group("item"))
            detail = (item.group("detail") or "").strip()

            name = f"{source_file}:{line_number}:{item_number}"
            if detail:
                name += f" {detail}"

            points.append(
                {
                    "name": name,
                    "count": hits,
                    "hit": hits > 0,
                    "type": kind,
                    "source_file": source_file,
                    "line": line_number,
                    "item": item_number,
                    "detail": detail,
                }
            )
            continue

        if kind not in {"condition", "expression"}:
            continue

        item = _QUESTA_FEC_ITEM.match(raw_line)
        if item is not None:
            fec_line = int(item.group("line"))
            fec_item = int(item.group("item"))
            fec_context = (item.group("detail") or "").strip()
            in_fec_rows = False
            continue

        normalized_line = raw_line.strip().lower()
        if normalized_line.startswith("rows:") and "fec target" in normalized_line:
            in_fec_rows = True
            continue

        row = _QUESTA_FEC_ROW.match(raw_line)
        if (
            not in_fec_rows
            or row is None
            or fec_line is None
            or fec_item is None
        ):
            continue

        hits = int(row.group("hits").replace("*", "").replace(",", ""))
        row_number = int(row.group("row"))
        target = row.group("target").strip()
        evidence = (row.group("detail") or "").strip()
        name = f"{source_file}:{fec_line}:{fec_item}:row{row_number} {target}"
        if evidence:
            name += f" {evidence}"

        point = {
            "name": name,
            "count": hits,
            "hit": hits > 0,
            "type": kind,
            "source_file": source_file,
            "line": fec_line,
            "item": fec_item,
            "row": row_number,
            "fec_context": fec_context,
            "fec_target": target,
            "evidence": evidence,
            "detail": evidence,
        }
        point[kind] = fec_context
        points.append(point)

    return points


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_ancestor(
    element: ET.Element,
    parents: dict[ET.Element, ET.Element],
    names: set[str],
) -> ET.Element | None:
    parent = parents.get(element)
    while parent is not None:
        if _xml_local_name(parent.tag) in names:
            return parent
        parent = parents.get(parent)
    return None


def parse_questa_statement_coverage_xml(path: str | Path) -> list[dict]:
    """Normalize documented by-instance statement items from vcover XML."""
    root = ET.parse(Path(path)).getroot()
    parents = {child: parent for parent in root.iter() for child in parent}

    global_files: dict[str, str] = {}
    instance_files: dict[ET.Element, dict[str, str]] = {}
    for element in root.iter():
        if _xml_local_name(element.tag) not in {"file", "fileMap"}:
            continue
        file_number = element.attrib.get("fn")
        file_path = element.attrib.get("path")
        if file_number is None or not file_path:
            continue
        instance = _xml_ancestor(
            element,
            parents,
            {"instance", "instanceData"},
        )
        if instance is None:
            global_files[str(file_number)] = str(file_path)
        else:
            instance_files.setdefault(instance, {})[str(file_number)] = str(file_path)

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
            pass
        try:
            if "st" in element.attrib:
                statement = int(element.attrib["st"])
        except ValueError:
            pass

        instance = _xml_ancestor(
            element,
            parents,
            {"instance", "instanceData"},
        )
        scope = (
            str(instance.attrib.get("path") or "").strip()
            if instance is not None
            else ""
        )

        file_number = element.attrib.get("fn")
        source_path = None
        if file_number is not None:
            key = str(file_number)
            if instance is not None:
                source_path = instance_files.get(instance, {}).get(key)
            if source_path is None:
                source_path = global_files.get(key)

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
    """Generate by-instance statement XML and write normalized zero-hit holes."""
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
    xml_path = out_dir / "statement-by-instance.xml"
    if xml_path.exists():
        xml_path.unlink()

    command = [
        tool,
        "report",
        "-xml",
        "-notimestamps",
        "-setdefault",
        "byinstance",
        "-code",
        "s",
        "-output",
        str(xml_path),
        str(merged_path),
    ]
    completed = _run(command, project.root)
    if completed.returncode != 0 or not xml_path.exists():
        raise RuntimeError(
            "Questa by-instance statement coverage export failed:\n"
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
        "source": "questa-vcover-xml-byinstance",
        "merged": str(merged_path),
        "xml": str(xml_path),
        "command": command,
    }
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {**payload, "path": str(destination)}


def _capture_questa_report_file(
    command: list[str],
    *,
    cwd: Path,
    output: Path,
) -> dict:
    """Capture an optional vcover report artifact without using stale output."""
    if output.exists():
        output.unlink()
    result = _run(command, cwd)
    if result.returncode != 0:
        status = "failed"
    elif not output.exists():
        status = "missing"
    else:
        status = "captured"
    return {
        "status": status,
        "path": str(output),
        "returncode": int(result.returncode),
        "command": command,
        "diagnostic": (
            (result.stdout or "").strip()
            if status != "captured"
            else None
        ),
    }


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
    code_report_path = out_dir / "code-details.txt"
    functional_report_path = out_dir / "functional.txt"
    functional_json_path = out_dir / "functional.json"
    details_xml_path = out_dir / "details.xml"
    zero_detail_path = out_dir / "zeros.txt"
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

    code_cmd = [
        tool,
        "report",
        "-details",
        "-dumptables",
        "-code",
        "sbce",
        str(merged_path),
    ]
    code_report = _run(code_cmd, project.root)
    code_report_path.write_text(
        code_report.stdout or "",
        encoding="utf-8",
    )
    code_points = (
        parse_questa_code_coverage_report(code_report.stdout or "")
        if code_report.returncode == 0
        else []
    )
    code_detail_status = (
        "ok"
        if code_report.returncode == 0 and code_points
        else "empty"
        if code_report.returncode == 0
        else "tool-error"
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

    details_cmd = [
        tool,
        "report",
        "-xml",
        "-codeAll",
        "-output",
        str(details_xml_path),
        str(merged_path),
    ]
    zero_detail_cmd = [
        tool,
        "report",
        "-zeros",
        "-details",
        "-codeAll",
        "-output",
        str(zero_detail_path),
        str(merged_path),
    ]
    detailed_code_coverage_evidence = {
        "xml": _capture_questa_report_file(
            details_cmd,
            cwd=project.root,
            output=details_xml_path,
        ),
        "zero_detail": _capture_questa_report_file(
            zero_detail_cmd,
            cwd=project.root,
            output=zero_detail_path,
        ),
    }

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
        "code_report": str(code_report_path),
        "code_detail_status": code_detail_status,
        "code_detail_returncode": int(code_report.returncode),
        "code_detail_points": len(code_points),
        "code_detail_holes": sum(not point["hit"] for point in code_points),
        "functional_report": str(functional_report_path),
        "functional_snapshot_id": functional_snapshot_id,
        "functional_bins": functional_bins,
        "detailed_code_coverage_evidence": detailed_code_coverage_evidence,
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
        "code_report": str(code_report_path),
        "code_detail_status": code_detail_status,
        "code_detail_returncode": int(code_report.returncode),
        "code_detail_points": len(code_points),
        "code_detail_holes": sum(not point["hit"] for point in code_points),
        "functional_report": str(functional_report_path),
        "functional_snapshot_id": functional_snapshot_id,
        "functional_bins": functional_bins,
        "detailed_code_coverage_evidence": detailed_code_coverage_evidence,
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


class _UrgHtmlTableRows(HTMLParser):
    """Collect text cells from one URG HTML table without external dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None:
            text = " ".join("".join(self._cell_parts or []).split())
            self._row.append(text)
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell_parts = None


_URG_INSTANCE_COVERAGE_SECTION = re.compile(
    r"(?P<kind>Line|Cond|Toggle|FSM|Branch)\s+Coverage\s+for\s+Instance\b",
    re.IGNORECASE,
)
_URG_ANY_COVERAGE_SECTION = re.compile(
    r"(?:Line|Cond|Toggle|FSM|Branch)\s+Coverage\s+for\s+(?:Instance|Module)\b",
    re.IGNORECASE,
)
_URG_HTML_TABLE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
_URG_INSTANCE_COUNT_ROWS = {
    "line": {"total": "line"},
    "cond": {"conditions": "condition"},
    "toggle": {"total bits": "toggle"},
    "branch": {"branches": "branch"},
    "fsm": {
        "states": "fsm_state",
        "transitions": "fsm_transition",
        "sequences": "fsm_sequence",
    },
}


def _urg_visible_text(fragment: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(unescape(without_tags).split())


def _urg_instance_identity(header_fragment: str, *, fallback: str) -> str:
    header_text = _urg_visible_text(header_fragment)
    match = re.search(
        r"Coverage\s+for\s+Instance\s*:\s*(?P<instance>.+?)(?:\s+Summary\s+for\s+FSM\s*::|$)",
        header_text,
        re.IGNORECASE,
    )
    if match is None:
        return fallback
    identity = match.group("instance").strip()
    return identity or fallback


def _parse_urg_instance_section_counts(
    html_text: str,
) -> tuple[list[tuple[str, str, int, int]], int]:
    records: list[tuple[str, str, int, int]] = []
    matched_sections = 0

    for section_match in _URG_INSTANCE_COVERAGE_SECTION.finditer(html_text):
        kind = section_match.group("kind").lower()
        targets = _URG_INSTANCE_COUNT_ROWS[kind]
        next_section = _URG_ANY_COVERAGE_SECTION.search(
            html_text, section_match.end()
        )
        section_end = next_section.start() if next_section is not None else len(html_text)
        section = html_text[section_match.end() : section_end]
        table_matches = list(_URG_HTML_TABLE.finditer(section))
        if not table_matches:
            continue

        first_table_start = section_match.end() + table_matches[0].start()
        instance = _urg_instance_identity(
            html_text[section_match.start() : first_table_start],
            fallback=f"offset-{section_match.start()}",
        )
        section_matched = False

        for table_index, table_match in enumerate(table_matches):
            parser = _UrgHtmlTableRows()
            parser.feed(table_match.group(0))
            parser.close()
            rows = parser.rows
            has_count_header = any(
                "total" in {cell.strip().casefold() for cell in row}
                and "covered" in {cell.strip().casefold() for cell in row}
                for row in rows
            )
            if not has_count_header:
                continue

            fsm_name = ""
            if kind == "fsm":
                context = _urg_visible_text(section[: table_match.start()])
                names = re.findall(
                    r"Summary\s+for\s+FSM\s*::\s*([^\s]+)",
                    context,
                    re.IGNORECASE,
                )
                fsm_name = names[-1] if names else f"table-{table_index}"

            for row in rows:
                if not row:
                    continue
                metric = targets.get(row[0].strip().casefold())
                if metric is None:
                    continue
                values: list[int] = []
                for cell in row[1:]:
                    token = cell.strip().replace(",", "")
                    if re.fullmatch(r"\d+", token):
                        values.append(int(token))
                        if len(values) == 2:
                            break
                if len(values) != 2:
                    continue
                total, covered = values
                if covered > total:
                    raise ValueError(
                        f"URG instance detail has invalid {metric} count: "
                        f"{covered}/{total}"
                    )
                evidence_key = f"{instance}|{fsm_name}|{metric}"
                records.append((evidence_key, metric, covered, total))
                section_matched = True

        if section_matched:
            matched_sections += 1

    return records, matched_sections


def parse_vcs_urg_instance_counts(report_dir: str | Path) -> dict:
    """Aggregate explicitly reported URG instance-detail code-metric counts."""
    source = Path(report_dir)
    html_files = sorted(source.glob("mod*.html"))
    aggregate: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    seen: dict[str, tuple[str, int, int]] = {}
    matched_sections = 0
    duplicate_records = 0

    for html_path in html_files:
        html_text = html_path.read_text(encoding="utf-8", errors="replace")
        records, section_count = _parse_urg_instance_section_counts(html_text)
        matched_sections += section_count
        for evidence_key, metric, covered, total in records:
            previous = seen.get(evidence_key)
            current = (metric, covered, total)
            if previous is not None:
                if previous != current:
                    raise ValueError(
                        "Conflicting URG instance-detail counts for "
                        f"{evidence_key}: {previous[1]}/{previous[2]} vs "
                        f"{covered}/{total}"
                    )
                duplicate_records += 1
                continue
            seen[evidence_key] = current
            aggregate[metric][0] += covered
            aggregate[metric][1] += total

    normalized: dict[str, dict[str, int | float | None]] = {}
    for metric, (covered, total) in sorted(aggregate.items()):
        normalized[metric] = {
            "covered": covered,
            "total": total,
            "hit_rate": (100.0 * covered / total) if total else None,
        }

    return {
        "by_metric_counts": normalized,
        "files_scanned": len(html_files),
        "instance_sections": matched_sections,
        "unique_records": len(seen),
        "duplicate_records": duplicate_records,
        "source": "urg-instance-detail-html",
    }


_URG_DASHBOARD_METRICS = {
    "SCORE": "score",
    "LINE": "line",
    "COND": "condition",
    "TOGGLE": "toggle",
    "FSM": "fsm",
    "BRANCH": "branch",
    "ASSERT": "assertion",
    "GROUP": "group",
}
_URG_MISSING_SCORE = {"", "-", "--", "N/A", "NA"}


def _parse_urg_score_token(token: str) -> float | None:
    value = token.strip().rstrip("%")
    if value.upper() in _URG_MISSING_SCORE:
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise ValueError(f"Unsupported URG score token: {token!r}")
    parsed = float(value)
    if not 0.0 <= parsed <= 100.0:
        raise ValueError(f"URG coverage score out of range: {parsed}")
    return parsed


def _urg_score_cells(
    header_line: str,
    value_line: str,
    header_tokens: list[str],
) -> list[str]:
    """Return score cells while preserving blank URG metric columns."""
    if "|" in header_line or "|" in value_line:
        headers = [
            cell.strip().upper()
            for cell in header_line.strip().strip("|").split("|")
        ]
        values = [
            cell.strip()
            for cell in value_line.strip().strip("|").split("|")
        ]
        if headers == header_tokens and len(values) == len(header_tokens):
            return values

    tokens = value_line.split()
    if len(tokens) == len(header_tokens):
        return tokens

    matches = list(
        re.finditer(
            r"\b(?:SCORE|LINE|COND|TOGGLE|FSM|BRANCH|ASSERT|GROUP)\b",
            header_line.upper(),
        )
    )
    if [match.group() for match in matches] != header_tokens:
        return []

    cells: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value_line)
        cells.append(value_line[start:end].strip())
    return cells


def _parse_vcs_urg_group_summary(lines: list[str]) -> tuple[dict[str, dict[str, int | float]], str, str | None]:
    """Parse documented global covergroup type/instance counts from dashboard.txt."""
    section_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "Total Groups Coverage Summary" in line
        ),
        None,
    )
    if section_index is None:
        return {}, "group-summary-missing", None

    for header_index in range(section_index + 1, min(len(lines), section_index + 12)):
        header = lines[header_index].upper()
        if not (
            "COVERED" in header
            and "EXPECTED" in header
            and "SCORE" in header
        ):
            continue

        for value_index in range(header_index + 1, min(len(lines), header_index + 8)):
            raw = lines[value_index].strip()
            if not raw or set(raw) <= {"-", "=", "+", "|", " "}:
                continue

            tokens = re.findall(r"\d[\d,]*(?:\.\d+)?%?", raw)
            if len(tokens) < 3:
                continue

            try:
                covered = int(tokens[0].replace(",", "").rstrip("%"))
                total = int(tokens[1].replace(",", "").rstrip("%"))
                score = _parse_urg_score_token(tokens[2])
            except ValueError:
                continue
            if score is None:
                continue
            if covered < 0 or total < 0 or covered > total:
                return {}, "group-summary-unparsed", (
                    "URG Total Groups Coverage Summary has invalid type counts"
                )

            counts: dict[str, dict[str, int | float]] = {
                "group": {
                    "covered": covered,
                    "total": total,
                    "hit_rate": score,
                }
            }

            # Current URG dashboards can also report instance counts in the
            # same row: COVERED EXPECTED INST SCORE. Keep these separate from
            # type counts instead of folding them together.
            if len(tokens) >= 6:
                try:
                    instance_covered = int(
                        tokens[3].replace(",", "").rstrip("%")
                    )
                    instance_total = int(
                        tokens[4].replace(",", "").rstrip("%")
                    )
                    instance_score = _parse_urg_score_token(tokens[5])
                except ValueError:
                    return {}, "group-summary-unparsed", (
                        "URG Total Groups Coverage Summary has invalid instance counts"
                    )
                if (
                    instance_score is None
                    or instance_covered < 0
                    or instance_total < 0
                    or instance_covered > instance_total
                ):
                    return {}, "group-summary-unparsed", (
                        "URG Total Groups Coverage Summary has invalid instance counts"
                    )
                counts["group_instance"] = {
                    "covered": instance_covered,
                    "total": instance_total,
                    "hit_rate": instance_score,
                }

            return counts, "normalized", None

    return {}, "group-summary-unparsed", (
        "URG Total Groups Coverage Summary count row could not be parsed"
    )


_VCS_URG_MODULE_HEADER = re.compile(
    r"^\s*(?P<kind>Line|Branch|Cond(?:ition)?|Toggle|FSM)\s+Coverage\s+"
    r"for\s+Module\s*:\s*(?P<module>.+?)\s*$",
    re.IGNORECASE,
)
_VCS_URG_ANY_COVERAGE_SECTION = re.compile(
    r"^\s*.+?\s+Coverage\s+for\s+(?:Module|Instance)\s*:\s*.+?\s*$",
    re.IGNORECASE,
)
_VCS_URG_MODULE_TOTAL_ROWS = {
    "line": re.compile(
        r"^\s*TOTAL\s+(?P<total>\d[\d,]*)\s+"
        r"(?P<covered>\d[\d,]*)\s+"
        r"(?P<score>\d+(?:\.\d+)?)%?\s*$",
        re.IGNORECASE,
    ),
    "condition": re.compile(
        r"^\s*Conditions\s+(?P<total>\d[\d,]*)\s+"
        r"(?P<covered>\d[\d,]*)\s+"
        r"(?P<score>\d+(?:\.\d+)?)%?\s*$",
        re.IGNORECASE,
    ),
    "toggle": re.compile(
        r"^\s*Total\s+Bits\s+(?P<total>\d[\d,]*)\s+"
        r"(?P<covered>\d[\d,]*)\s+"
        r"(?P<score>\d+(?:\.\d+)?)%?\s*$",
        re.IGNORECASE,
    ),
    "fsm": re.compile(
        r"^\s*Transitions\s+(?P<total>\d[\d,]*)\s+"
        r"(?P<covered>\d[\d,]*)\s+"
        r"(?P<score>\d+(?:\.\d+)?)%?\s*$",
        re.IGNORECASE,
    ),
    "branch": re.compile(
        r"^\s*Branches\s+(?P<total>\d[\d,]*)\s+"
        r"(?P<covered>\d[\d,]*)\s+"
        r"(?P<score>\d+(?:\.\d+)?)%?\s*$",
        re.IGNORECASE,
    ),
}


def parse_vcs_urg_module_counts(path: str | Path) -> dict:
    """Parse documented module-level code-metric totals from URG modinfo.txt.

    Count names are intentionally module-scoped. Condition uses the Conditions
    row, toggle uses Total Bits, and FSM uses scored Transitions (states are not
    part of the URG FSM score).
    """
    source = Path(path)
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    records: dict[tuple[str, str], dict[str, int | float | str]] = {}

    for index, raw_line in enumerate(lines):
        header = _VCS_URG_MODULE_HEADER.match(raw_line)
        if header is None:
            continue

        kind = header.group("kind").lower()
        if kind in {"cond", "condition"}:
            kind = "condition"
        module = header.group("module").strip()
        row_pattern = _VCS_URG_MODULE_TOTAL_ROWS[kind]

        end = len(lines)
        for candidate in range(index + 1, len(lines)):
            if _VCS_URG_ANY_COVERAGE_SECTION.match(lines[candidate]):
                end = candidate
                break

        parsed_rows = [
            row_pattern.match(candidate)
            for candidate in lines[index + 1 : end]
        ]
        parsed_rows = [row for row in parsed_rows if row is not None]
        if not parsed_rows:
            continue

        parsed_counts: list[tuple[int, int, float]] = []
        for parsed_row in parsed_rows:
            total = int(parsed_row.group("total").replace(",", ""))
            covered = int(parsed_row.group("covered").replace(",", ""))
            score = float(parsed_row.group("score"))
            if (
                total < 0
                or covered < 0
                or covered > total
                or not 0.0 <= score <= 100.0
            ):
                raise ValueError(
                    f"Invalid URG {kind} module total for {module}: "
                    f"{covered}/{total} ({score}%)"
                )
            parsed_counts.append((total, covered, score))

        if kind == "fsm":
            total = sum(item[0] for item in parsed_counts)
            covered = sum(item[1] for item in parsed_counts)
            score = 100.0 * covered / total if total else 0.0
        else:
            total, covered, score = parsed_counts[0]

        key = (kind, module)
        record = {
            "metric": kind,
            "module": module,
            "covered": covered,
            "total": total,
            "hit_rate": score,
        }
        previous = records.get(key)
        if previous is not None and previous != record:
            raise ValueError(
                f"Conflicting URG {kind} module totals for {module}"
            )
        records[key] = record

    aggregates: dict[str, dict[str, int | float]] = {}
    for kind in ("line", "condition", "toggle", "fsm", "branch"):
        selected = [
            record
            for (record_kind, _), record in records.items()
            if record_kind == kind
        ]
        if not selected:
            continue
        total = sum(int(record["total"]) for record in selected)
        covered = sum(int(record["covered"]) for record in selected)
        aggregates[f"module_{kind}"] = {
            "covered": covered,
            "total": total,
            "hit_rate": 100.0 * covered / total if total else 0.0,
        }

    return {
        "source": "urg-modinfo",
        "path": str(source.resolve()),
        "by_metric_counts": aggregates,
        "modules": [
            records[key]
            for key in sorted(records, key=lambda item: (item[0], item[1]))
        ],
    }


def parse_vcs_urg_dashboard(path: str | Path) -> dict:
    """Parse documented URG dashboard coverage scores and global group counts."""
    source = Path(path)
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()

    section_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "Total Coverage Summary" in line
        ),
        None,
    )
    if section_index is None:
        raise ValueError("URG dashboard has no Total Coverage Summary section")

    known_headers = set(_URG_DASHBOARD_METRICS)
    for header_index in range(section_index + 1, min(len(lines), section_index + 16)):
        header_line = lines[header_index]
        header_tokens = [
            token
            for token in re.findall(r"[A-Za-z]+", header_line.upper())
            if token in known_headers
        ]
        if not header_tokens or header_tokens[0] != "SCORE":
            continue

        for value_index in range(header_index + 1, min(len(lines), header_index + 8)):
            raw_line = lines[value_index]
            raw = raw_line.strip()
            if not raw or set(raw) <= {"-", "=", "+", "|", " "}:
                continue

            score_tokens = _urg_score_cells(
                header_line,
                raw_line,
                header_tokens,
            )
            if len(score_tokens) != len(header_tokens):
                continue

            try:
                parsed = {
                    _URG_DASHBOARD_METRICS[header]: _parse_urg_score_token(token)
                    for header, token in zip(
                        header_tokens,
                        score_tokens,
                        strict=True,
                    )
                }
            except ValueError:
                continue

            score = parsed.pop("score", None)
            if score is None:
                raise ValueError("URG Total Coverage Summary has no SCORE value")

            group_counts, count_status, count_error = _parse_vcs_urg_group_summary(lines)
            result = {
                "tool_total_coverage": score,
                "by_metric": {
                    name: value
                    for name, value in parsed.items()
                    if value is not None
                },
                "by_metric_counts": group_counts,
                "count_status": count_status,
                "source": "urg-dashboard",
                "dashboard": str(source.resolve()),
            }
            if count_error is not None:
                result["count_error"] = count_error
            return result

    raise ValueError("URG Total Coverage Summary score row could not be parsed")


def merge_vcs_coverage(project: ProjectConfig) -> dict:
    """Merge VCS coverage and retain documented URG summary/brief evidence."""
    tool = shutil.which("urg")
    if tool is None:
        raise RuntimeError(
            "Synopsys URG was not found in PATH. Install/configure VCS/URG and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    coverage_dirs = sorted(run_root.glob("*/coverage.vdb"))
    if not coverage_dirs:
        raise RuntimeError(
            f"No coverage.vdb directories found under {run_root}. "
            "Run coverage-enabled VCS simulations first."
        )

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    merged_path = out_dir / "coverage.vdb"
    report_dir = out_dir / "urg-report"
    brief_report_dir = out_dir / "urg-brief"
    manifest_path = out_dir / "vcs-coverage.json"

    if merged_path.exists():
        if merged_path.is_dir():
            shutil.rmtree(merged_path)
        else:
            merged_path.unlink()
    if report_dir.exists():
        if report_dir.is_dir():
            shutil.rmtree(report_dir)
        else:
            report_dir.unlink()
    if brief_report_dir.exists():
        if brief_report_dir.is_dir():
            shutil.rmtree(brief_report_dir)
        else:
            brief_report_dir.unlink()

    inputs = [str(path) for path in coverage_dirs]
    command = [
        tool,
        "-dir",
        *inputs,
        "-dbname",
        merged_path.name,
        "-report",
        report_dir.name,
        "-format",
        "both",
    ]
    result = _run(command, out_dir)
    if (
        result.returncode != 0
        or not merged_path.exists()
        or not report_dir.exists()
    ):
        raise RuntimeError(
            "VCS coverage merge/report failed:\n"
            + "$ "
            + " ".join(command)
            + "\n"
            + (result.stdout or "").strip()
        )

    brief_command = [
        tool,
        "-dir",
        merged_path.name,
        "-report",
        brief_report_dir.name,
        "-format",
        "text",
        "-show",
        "brief",
        "-metric",
        "line+cond+fsm+tgl+branch",
    ]
    brief_result = _run(brief_command, out_dir)
    brief_status = (
        "captured"
        if brief_result.returncode == 0 and brief_report_dir.exists()
        else "failed"
    )
    brief_error: str | None = None
    if brief_status != "captured":
        brief_error = (brief_result.stdout or "").strip() or (
            f"URG brief report exited with status {brief_result.returncode}"
        )

    created_at = datetime.now(timezone.utc).isoformat()
    dashboard_path = report_dir / "dashboard.txt"
    metrics: dict | None = None
    metrics_status = "dashboard-missing"
    metrics_error: str | None = None
    module_counts_status = "modinfo-missing"
    module_counts_error: str | None = None
    module_report: dict | None = None
    snapshot_id: str | None = None

    if dashboard_path.exists():
        try:
            metrics = parse_vcs_urg_dashboard(dashboard_path)
            metrics_status = "normalized"
            try:
                code_counts = parse_vcs_urg_instance_counts(report_dir)
                reported_counts = metrics.setdefault("by_metric_counts", {})
                reported_counts.update(code_counts["by_metric_counts"])
                metrics["code_count_files"] = code_counts["files_scanned"]
                metrics["code_count_sections"] = code_counts["instance_sections"]
                metrics["code_count_unique_records"] = code_counts["unique_records"]
                metrics["code_count_duplicate_records"] = code_counts["duplicate_records"]
                metrics["code_count_source"] = code_counts["source"]
                if code_counts["by_metric_counts"]:
                    metrics["code_count_status"] = "normalized"
                elif code_counts["files_scanned"]:
                    metrics["code_count_status"] = "detail-unparsed"
                else:
                    metrics["code_count_status"] = "detail-missing"
            except (OSError, ValueError) as exc:
                metrics["code_count_status"] = "detail-unparsed"
                metrics["code_count_error"] = str(exc)
        except (OSError, ValueError) as exc:
            metrics_status = "dashboard-unparsed"
            metrics_error = str(exc)

    modinfo_path = report_dir / "modinfo.txt"
    if modinfo_path.exists():
        try:
            module_report = parse_vcs_urg_module_counts(modinfo_path)
            module_counts = module_report["by_metric_counts"]
            if module_counts:
                module_counts_status = "normalized"
                if metrics is not None:
                    metrics.setdefault("by_metric_counts", {}).update(module_counts)
                    metrics["module_report"] = module_report
            else:
                module_counts_status = "modinfo-unparsed"
                module_counts_error = (
                    "No documented module code-metric total rows were found"
                )
        except (OSError, ValueError) as exc:
            module_counts_status = "modinfo-unparsed"
            module_counts_error = str(exc)

    if metrics is not None:
        snapshot_id = (
            datetime.now(timezone.utc).strftime("cov-score-%Y%m%dT%H%M%S")
            + "-"
            + uuid.uuid4().hex[:8]
        )

    payload = {
        "created_at": created_at,
        "project": project.name,
        "simulator": project.simulator,
        "status": "merged-report-captured",
        "metrics_status": metrics_status,
        "input_count": len(inputs),
        "inputs": inputs,
        "merged": str(merged_path),
        "report_dir": str(report_dir),
        "dashboard": str(dashboard_path) if dashboard_path.exists() else None,
        "modinfo": str(modinfo_path) if modinfo_path.exists() else None,
        "module_counts_status": module_counts_status,
        "command": command,
        "brief_status": brief_status,
        "brief_report_dir": (
            str(brief_report_dir) if brief_report_dir.exists() else None
        ),
        "brief_command": brief_command,
        "metrics": metrics,
        "snapshot_id": snapshot_id,
    }
    if metrics_error is not None:
        payload["metrics_error"] = metrics_error
    if module_counts_error is not None:
        payload["module_counts_error"] = module_counts_error
    if brief_error is not None:
        payload["brief_error"] = brief_error
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    summary_path = dashboard_path if dashboard_path.exists() else report_dir
    if metrics is not None and snapshot_id is not None:
        record_coverage_score_snapshot(
            project,
            {
                "snapshot_id": snapshot_id,
                "created_at": created_at,
                "project": project.name,
                "simulator": project.simulator,
                "input_count": len(inputs),
                "score": metrics["tool_total_coverage"],
                "by_metric": metrics["by_metric"],
                "by_metric_counts": metrics.get("by_metric_counts", {}),
                "merged": str(merged_path),
                "summary": str(summary_path),
                "metrics_path": str(manifest_path),
            },
        )

    return {
        "inputs": inputs,
        "merged": str(merged_path),
        "summary": str(summary_path),
        "metrics_path": str(manifest_path),
        "report": result.stdout or "",
        "metrics": metrics,
        "snapshot_id": snapshot_id,
        "report_dir": str(report_dir),
        "dashboard": str(dashboard_path) if dashboard_path.exists() else None,
        "modinfo": str(modinfo_path) if modinfo_path.exists() else None,
        "metrics_status": metrics_status,
        "metrics_error": metrics_error,
        "module_counts_status": module_counts_status,
        "module_counts_error": module_counts_error,
        "brief_status": brief_status,
        "brief_report_dir": (
            str(brief_report_dir) if brief_report_dir.exists() else None
        ),
        "brief_command": brief_command,
        "brief_error": brief_error,
    }




def _imc_quote_path(path: Path) -> str:
    """Quote a path for an IMC command file using Tcl-compatible double quotes."""
    value = path.resolve().as_posix()
    value = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    return f'"{value}"'


_XCELIUM_IMC_SUMMARY_HEADER = re.compile(
    r"^\\s*name\\s+Overall\\*?(?:\\s|$)",
    re.IGNORECASE,
)
_XCELIUM_IMC_SUMMARY_ROW = re.compile(
    r"^\\s*(?P<scope>\\S+)\\s+(?P<overall>\\d+(?:\\.\\d+)?)%(?:\\s|$)"
)


def parse_xcelium_imc_summary(text: str) -> dict:
    """Normalize only the explicitly headed first Overall percentage from IMC."""
    lines = text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if _XCELIUM_IMC_SUMMARY_HEADER.match(line)
        ),
        None,
    )
    if header_index is None:
        raise ValueError("IMC summary header with Overall coverage was not found")

    for raw_line in lines[header_index + 1 :]:
        match = _XCELIUM_IMC_SUMMARY_ROW.match(raw_line)
        if match is None:
            continue
        score = float(match.group("overall"))
        if not 0.0 <= score <= 100.0:
            raise ValueError("IMC overall coverage score is outside 0..100")
        return {
            "tool_total_coverage": score,
            "by_metric": {},
            "by_metric_counts": {},
            "scope": match.group("scope"),
            "source": "imc-summary",
            "metric_semantics": "first-overall-column",
        }

    raise ValueError("IMC summary data row could not be parsed")


def merge_xcelium_coverage(project: ProjectConfig) -> dict:
    """Merge captured Xcelium run databases and normalize verified IMC evidence."""
    tool = shutil.which("imc")
    if tool is None:
        raise RuntimeError(
            "Cadence IMC was not found in PATH. Configure Xcelium/IMC and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    candidate_dirs = sorted(run_root.glob("*/coverage/*"))
    coverage_dirs = [
        path
        for path in candidate_dirs
        if path.is_dir() and any(path.glob("*.ucd"))
    ]
    if not coverage_dirs:
        raise RuntimeError(
            f"No Xcelium .ucd run databases found under {run_root}. "
            "Run coverage-enabled Xcelium simulations first."
        )

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    merged_path = out_dir / "xcelium-imc-merged"
    report_dir = out_dir / "xcelium-imc-report"
    summary_path = report_dir / "summary.txt"
    script_path = out_dir / "xcelium-imc-merge.tcl"
    manifest_path = out_dir / "xcelium-coverage.json"

    for stale in (merged_path, report_dir):
        if stale.exists():
            if stale.is_dir():
                shutil.rmtree(stale)
            else:
                stale.unlink()
    report_dir.mkdir(parents=True, exist_ok=True)

    input_args = " ".join(_imc_quote_path(path) for path in coverage_dirs)
    script_path.write_text(
        "\n".join(
            [
                (
                    f"merge -out {_imc_quote_path(merged_path)} -overwrite "
                    f"{input_args}"
                ),
                f"load -run {_imc_quote_path(merged_path)}",
                (
                    "report -summary -cumulative on -inst -local off "
                    f"-out {_imc_quote_path(summary_path)}"
                ),
                "exit",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    command = [tool, "-batch", "-exec", str(script_path)]
    result = _run(command, out_dir)
    merged_ucd = (
        list(merged_path.rglob("*.ucd"))
        if merged_path.exists() and merged_path.is_dir()
        else []
    )
    if result.returncode != 0 or not merged_ucd:
        raise RuntimeError(
            "Xcelium IMC coverage merge/report failed:\n"
            + "$ "
            + " ".join(command)
            + "\n"
            + (result.stdout or "").strip()
        )

    created_at = datetime.now(timezone.utc).isoformat()
    summary_exists = summary_path.is_file()
    metrics: dict | None = None
    metrics_status = "summary-missing" if not summary_exists else "summary-unparsed"
    metrics_error: str | None = None
    snapshot_id: str | None = None

    if summary_exists:
        try:
            metrics = parse_xcelium_imc_summary(
                summary_path.read_text(encoding="utf-8", errors="replace")
            )
            metrics_status = "normalized"
        except (OSError, ValueError) as exc:
            metrics_error = str(exc)

    if metrics is not None:
        snapshot_id = (
            datetime.now(timezone.utc).strftime("cov-score-%Y%m%dT%H%M%S")
            + "-"
            + uuid.uuid4().hex[:8]
        )

    payload = {
        "created_at": created_at,
        "project": project.name,
        "simulator": project.simulator,
        "status": (
            "merged-report-captured"
            if summary_exists
            else "merged-evidence-captured"
        ),
        "metrics_status": metrics_status,
        "input_count": len(coverage_dirs),
        "inputs": [str(path) for path in coverage_dirs],
        "merged": str(merged_path),
        "merged_ucd_files": [str(path) for path in sorted(merged_ucd)],
        "report_dir": str(report_dir),
        "summary": str(summary_path) if summary_exists else None,
        "script": str(script_path),
        "command": command,
        "metrics": metrics,
        "snapshot_id": snapshot_id,
    }
    if metrics_error is not None:
        payload["metrics_error"] = metrics_error
    manifest_path.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    if metrics is not None and snapshot_id is not None:
        record_coverage_score_snapshot(
            project,
            {
                "snapshot_id": snapshot_id,
                "created_at": created_at,
                "project": project.name,
                "simulator": project.simulator,
                "input_count": len(coverage_dirs),
                "score": metrics["tool_total_coverage"],
                "by_metric": metrics.get("by_metric", {}),
                "by_metric_counts": metrics.get("by_metric_counts", {}),
                "merged": str(merged_path),
                "summary": str(summary_path),
                "metrics_path": str(manifest_path),
            },
        )

    return {
        "inputs": payload["inputs"],
        "merged": str(merged_path),
        "summary": str(summary_path) if summary_exists else str(report_dir),
        "metrics_path": str(manifest_path),
        "report": result.stdout or "",
        "metrics": metrics,
        "snapshot_id": snapshot_id,
        "report_dir": str(report_dir),
        "script": str(script_path),
        "metrics_status": metrics_status,
        "metrics_error": metrics_error,
    }



def merge_coverage(project: ProjectConfig) -> dict:
    simulator = project.simulator.strip().lower()
    if simulator == "verilator":
        return merge_verilator_coverage(project)
    if simulator in {"questa", "questasim"}:
        return merge_questa_coverage(project)
    if simulator == "vcs":
        return merge_vcs_coverage(project)
    if simulator in {"xcelium", "xrun"}:
        return merge_xcelium_coverage(project)
    raise RuntimeError(
        f"Coverage merge/report is not implemented for simulator: {project.simulator}"
    )
