from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import uuid

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
_QUESTA_CODE_DETAIL_HEADER = re.compile(
    r"^\s*(?P<kind>Statement|Branch)\s+Coverage\s+for\s+file\s+"
    r"(?P<file>.+?)\s*--\s*$",
    re.IGNORECASE,
)
_QUESTA_CODE_DETAIL_ROW = re.compile(
    r"^\s*(?P<line>\d+)\s+(?P<item>\d+)\s+"
    r"(?P<hits>(?:\*{3})?\d[\d,]*(?:\*{3})?)"
    r"(?:\s+(?P<detail>.*?))?\s*$"
)

_VCS_URG_KIND_ALIASES = {
    "line": "line",
    "cond": "condition",
    "toggle": "toggle",
    "fsm": "fsm",
    "branch": "branch",
    "assert": "assertion",
    "group": "covergroup",
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
    """Normalize statement and branch items from documented vcover detail text."""
    points: list[dict] = []
    kind = ""
    source_file = ""

    for raw_line in text.splitlines():
        header = _QUESTA_CODE_DETAIL_HEADER.match(raw_line)
        if header is not None:
            kind = header.group("kind").strip().lower()
            source_file = header.group("file").strip()
            continue

        if kind not in {"statement", "branch"} or not source_file:
            continue

        item = _QUESTA_CODE_DETAIL_ROW.match(raw_line)
        if item is None:
            continue

        hits = int(
            item.group("hits").replace("*", "").replace(",", "")
        )
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

    return points


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


def _parse_vcs_urg_columns(
    header_line: str,
    value_line: str,
) -> dict[str, float | None]:
    """Parse one URG dashboard row without inventing values for blank cells."""
    if "|" in header_line and "|" in value_line:
        labels = [part.strip().upper() for part in header_line.split("|")]
        values = [part.strip() for part in value_line.split("|")]
        parsed: dict[str, float | None] = {}
        for label, raw_value in zip(labels, values):
            if not label:
                continue
            match = re.search(r"\d+(?:\.\d+)?", raw_value)
            parsed[label] = float(match.group(0)) if match else None
        return parsed

    matches = list(re.finditer(r"\S+", header_line))
    if not matches:
        return {}

    parsed = {}
    for index, match in enumerate(matches):
        label = match.group(0).strip().upper()
        start = match.start()
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(value_line)
        )
        cell = value_line[start:end].strip() if start < len(value_line) else ""
        number = re.search(r"\d+(?:\.\d+)?", cell)
        parsed[label] = float(number.group(0)) if number else None

    populated = sum(value is not None for value in parsed.values())
    if populated < 2:
        labels = [match.group(0).strip().upper() for match in matches]
        tokens = re.findall(r"\d+(?:\.\d+)?|--|N/?A", value_line, re.IGNORECASE)
        if len(tokens) == len(labels):
            parsed = {}
            for label, token in zip(labels, tokens):
                number = re.fullmatch(r"\d+(?:\.\d+)?", token)
                parsed[label] = float(token) if number else None
    return parsed


def parse_vcs_urg_dashboard(text: str) -> dict:
    """Normalize the Total Coverage Summary from URG dashboard.txt.

    URG's dashboard reports coverage scores rather than raw coverable/hit point
    counts. ZDDV therefore preserves these as score-only metrics and explicitly
    marks point counts unavailable.
    """
    lines = text.splitlines()
    summary_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip().lower() == "total coverage summary"
        ),
        None,
    )
    if summary_index is None:
        return {
            "total_points": 0,
            "hit_points": 0,
            "unhit_points": 0,
            "hit_rate": 0.0,
            "by_type": {},
            "tool_total_coverage": None,
            "counts_available": False,
            "score_source": "urg-dashboard",
        }

    header_line = ""
    value_line = ""
    for line in lines[summary_index + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if not header_line and "SCORE" in stripped.upper():
            header_line = line
            continue
        if header_line:
            value_line = line
            break

    columns = (
        _parse_vcs_urg_columns(header_line, value_line)
        if header_line and value_line
        else {}
    )
    score = columns.get("SCORE")
    by_type: dict[str, dict[str, float]] = {}
    for label, kind in _VCS_URG_KIND_ALIASES.items():
        value = columns.get(label.upper())
        if value is not None:
            by_type[kind] = {"hit_rate": value}

    return {
        "total_points": 0,
        "hit_points": 0,
        "unhit_points": 0,
        "hit_rate": float(score) if score is not None else 0.0,
        "by_type": dict(sorted(by_type.items())),
        "tool_total_coverage": float(score) if score is not None else None,
        "counts_available": False,
        "score_source": "urg-dashboard",
    }


def merge_vcs_coverage(project: ProjectConfig) -> dict:
    """Merge per-run VCS VDBs with URG and ingest dashboard coverage scores."""
    tool = shutil.which("urg")
    if tool is None:
        raise RuntimeError(
            "Synopsys URG was not found in PATH. Install/configure VCS and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    coverage_dirs = sorted(
        path for path in run_root.glob("*/coverage.vdb") if path.is_dir()
    )
    if not coverage_dirs:
        raise RuntimeError(
            f"No coverage.vdb directories found under {run_root}. "
            "Run coverage-enabled VCS simulations first."
        )

    out_dir = (project.root / ".zddv" / "coverage").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    merged_path = out_dir / "coverage.vdb"
    report_dir = out_dir / "urg-report"
    summary_path = report_dir / "dashboard.txt"
    metrics_path = out_dir / "metrics.json"

    if merged_path.exists():
        shutil.rmtree(merged_path)
    if report_dir.exists():
        shutil.rmtree(report_dir)

    inputs = [str(path) for path in coverage_dirs]
    command = [
        tool,
        "-dir",
        *inputs,
        "-dbname",
        str(merged_path),
        "-report",
        str(report_dir),
        "-format",
        "text",
    ]
    result = _run(command, project.root)
    if result.returncode != 0:
        raise RuntimeError(
            "VCS URG coverage merge/report failed:\n"
            + "$ "
            + " ".join(command)
            + "\n"
            + (result.stdout or "").strip()
        )
    if not merged_path.exists():
        raise RuntimeError(
            f"URG completed without creating merged VDB at {merged_path}."
        )
    if not summary_path.exists():
        raise RuntimeError(
            f"URG completed without creating dashboard report at {summary_path}."
        )

    summary_text = summary_path.read_text(encoding="utf-8", errors="replace")
    metrics = parse_vcs_urg_dashboard(summary_text)
    if metrics["tool_total_coverage"] is None or not metrics["by_type"]:
        raise RuntimeError(
            f"No Total Coverage Summary could be parsed from {summary_path}."
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
        "report_dir": str(report_dir),
        "urg_command": command,
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
        "report": result.stdout or "",
        "metrics": metrics,
        "snapshot_id": snapshot_id,
        "report_dir": str(report_dir),
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
        "-code",
        "sb",
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


def merge_coverage(project: ProjectConfig) -> dict:
    simulator = project.simulator.strip().lower()
    if simulator == "verilator":
        return merge_verilator_coverage(project)
    if simulator in {"questa", "questasim"}:
        return merge_questa_coverage(project)
    if simulator == "vcs":
        return merge_vcs_coverage(project)
    raise RuntimeError(
        f"Coverage merge/report is not implemented for simulator: {project.simulator}"
    )
