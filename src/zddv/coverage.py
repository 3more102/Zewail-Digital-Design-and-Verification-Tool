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


_QUESTA_SUMMARY_ROW = re.compile(
    r"^\s*(?P<label>[A-Za-z][A-Za-z0-9 _/-]*?)\s+"
    r"(?P<bins>\d[\d,]*)\s+(?P<hits>\d[\d,]*)\s+"
    r"(?P<misses>\d[\d,]*)\s+(?P<weight>-?\d+)\s+"
    r"(?P<coverage>\d+(?:\.\d+)?)%\s*$"
)

_QUESTA_CODE_TYPES = {
    "branches": "branch",
    "conditions": "condition",
    "expressions": "expression",
    "statements": "statement",
    "toggles": "toggle",
    "fsm states": "fsm_state",
    "fsm transitions": "fsm_transition",
    "fsms": "fsm",
}

_QUESTA_CVG_SCOPE = re.compile(
    r"^\s*(?!Coverpoint\b|Cross\b|bin\b)(?P<name>\S.*?)\s+"
    r"(?P<metric>\d+(?:\.\d+)?)%\s+"
    r"(?P<goal>\d+(?:\.\d+)?)%\s+\S+\s*$",
    re.IGNORECASE,
)

_QUESTA_CVG_POINT = re.compile(
    r"^\s*(?P<kind>Coverpoint|Cross)\s+(?P<name>.+?)\s+"
    r"(?P<metric>\d+(?:\.\d+)?)%\s+"
    r"(?P<goal>\d+(?:\.\d+)?)%\s+\S+\s*$",
    re.IGNORECASE,
)

_QUESTA_CVG_BIN = re.compile(
    r"^\s*bin\s+(?P<name>.+?)\s+"
    r"(?P<hits>\d[\d,]*)\s+(?P<goal>\d[\d,]*)\s+"
    r"(?P<status>\S+)\s*$",
    re.IGNORECASE,
)


def parse_questa_coverage_summary(text: str) -> dict:
    """Normalize stable code-coverage rows from vcover report -summary."""
    by_type: dict[str, dict[str, int | float]] = {}
    total_points = 0
    hit_points = 0

    for raw_line in text.splitlines():
        match = _QUESTA_SUMMARY_ROW.match(raw_line)
        if not match:
            continue

        label = " ".join(match.group("label").lower().split())
        kind = _QUESTA_CODE_TYPES.get(label)
        if kind is None:
            continue

        total = int(match.group("bins").replace(",", ""))
        hit = int(match.group("hits").replace(",", ""))
        misses = int(match.group("misses").replace(",", ""))
        if total < 0 or hit < 0 or misses < 0 or hit + misses != total:
            continue

        by_type[kind] = {
            "total": total,
            "hit": hit,
            "hit_rate": 100.0 * hit / total if total else 0.0,
        }
        total_points += total
        hit_points += hit

    return {
        "total_points": total_points,
        "hit_points": hit_points,
        "unhit_points": total_points - hit_points,
        "hit_rate": 100.0 * hit_points / total_points if total_points else 0.0,
        "by_type": dict(sorted(by_type.items())),
    }


def parse_questa_functional_coverage_report(text: str) -> dict:
    """Convert detailed Questa covergroup text into ZDDV functional bins.

    The parser intentionally consumes only ordinary bin rows beneath a
    reported Coverpoint or Cross. Ignore/illegal-bin rows are not rewritten as
    normal coverage goals.
    """
    bins: list[dict] = []
    scope = ""
    coverpoint = ""
    point_kind = ""

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        point = _QUESTA_CVG_POINT.match(line)
        if point:
            coverpoint = point.group("name").strip()
            point_kind = point.group("kind").lower()
            continue

        item = _QUESTA_CVG_BIN.match(line)
        if item and coverpoint:
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
                        "coverage_kind": point_kind,
                    },
                }
            )
            continue

        group = _QUESTA_CVG_SCOPE.match(line)
        if group:
            name = group.group("name").strip()
            for prefix in ("TYPE ", "INSTANCE "):
                if name.upper().startswith(prefix):
                    name = name[len(prefix):].strip()
                    break
            scope = name
            coverpoint = ""
            point_kind = ""

    return {
        "source": "questa-vcover",
        "bins": bins,
    }


def merge_questa_coverage(project: ProjectConfig) -> dict:
    """Merge per-run Questa UCDBs and normalize code/functional coverage."""
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
    merge_log_path = out_dir / "merge.log"
    summary_path = out_dir / "summary.txt"
    metrics_path = out_dir / "metrics.json"
    functional_report_path = out_dir / "functional.txt"
    functional_json_path = out_dir / "functional.json"

    inputs = [str(path) for path in coverage_files]
    merge_cmd = [tool, "merge", "-out", str(merged_path), *inputs]
    merge = _run(merge_cmd, project.root)
    merge_log_path.write_text(merge.stdout or "", encoding="utf-8")
    if merge.returncode != 0 or not merged_path.exists():
        raise RuntimeError(f"Questa coverage merge failed. See {merge_log_path}")

    summary_cmd = [tool, "report", "-summary", str(merged_path)]
    summary = _run(summary_cmd, project.root)
    summary_path.write_text(summary.stdout or "", encoding="utf-8")
    if summary.returncode != 0:
        raise RuntimeError(f"Questa coverage summary failed. See {summary_path}")

    metrics = parse_questa_coverage_summary(summary.stdout or "")
    if metrics["total_points"] == 0:
        raise RuntimeError(
            f"No normalized Questa code-coverage rows found in {summary_path}."
        )

    functional_cmd = [tool, "report", "-cvg", "-details", str(merged_path)]
    functional = _run(functional_cmd, project.root)
    functional_report_path.write_text(functional.stdout or "", encoding="utf-8")
    functional_snapshot_id = None
    functional_bins = 0
    if functional.returncode == 0:
        payload = parse_questa_functional_coverage_report(functional.stdout or "")
        functional_bins = len(payload["bins"])
        if functional_bins:
            functional_json_path.write_text(
                json.dumps(payload, indent=2),
                encoding="utf-8",
            )
            functional_record = ingest_functional_coverage(
                project,
                functional_json_path,
                source="questa-vcover",
            )
            functional_snapshot_id = functional_record["snapshot_id"]

    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        datetime.now(timezone.utc).strftime("cov-%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    record = {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "project": project.name,
        "simulator": "questa",
        "input_count": len(inputs),
        **metrics,
        "merged": str(merged_path),
        "summary": str(summary_path),
        "metrics_path": str(metrics_path),
        "merge_log": str(merge_log_path),
        "functional_report": str(functional_report_path),
        "functional_snapshot_id": functional_snapshot_id,
        "functional_bins": functional_bins,
    }
    metrics_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    record_coverage_snapshot(project, record)

    return {
        "inputs": inputs,
        "merged": str(merged_path),
        "summary": str(summary_path),
        "metrics_path": str(metrics_path),
        "report": summary.stdout or "",
        "metrics": metrics,
        "snapshot_id": snapshot_id,
        "merge_log": str(merge_log_path),
        "functional_report": str(functional_report_path),
        "functional_snapshot_id": functional_snapshot_id,
        "functional_bins": functional_bins,
    }
