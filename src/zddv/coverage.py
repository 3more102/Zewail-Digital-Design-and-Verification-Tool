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
    r"^\s*(?P<kind>Statement|Branch|Condition|Expression|FSM)\s+Coverage\s+for\s+file\s+"
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
_QUESTA_MULTIBIT_FEC_ROW = re.compile(
    r"^\s*Row\s+(?P<row>\d+):\s+(?P<target>\S+)\s+(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
_QUESTA_MULTIBIT_INDEX = re.compile(r"<(?P<index>\d+)>")
_QUESTA_HIT_TOKEN = re.compile(r"^(?:\*{3})?\d[\d,]*(?:\*{3})?$")

_QUESTA_FSM_ID = re.compile(
    r"^\s*FSM_ID\s*:\s*(?P<fsm_id>\S.*?)\s*$",
    re.IGNORECASE,
)
_QUESTA_FSM_SECTION = re.compile(
    r"^\s*(?P<covered>Covered|Uncovered)\s+"
    r"(?P<kind>States|Transitions)\s*:\s*$",
    re.IGNORECASE,
)
_QUESTA_FSM_COVERED_STATE = re.compile(
    r"^\s*(?P<state>\S+)\s+(?P<hits>\d[\d,]*)\s*$"
)
_QUESTA_FSM_UNCOVERED_STATE = re.compile(r"^\s*(?P<state>\S+)\s*$")
_QUESTA_FSM_COVERED_TRANSITION = re.compile(
    r"^\s*(?P<line>\d+)\s+(?P<transition_id>\d+)\s+"
    r"(?P<hits>\d[\d,]*)\s+"
    r"(?P<transition>.+?\s+->\s+.+?)\s*$"
)
_QUESTA_FSM_UNCOVERED_TRANSITION = re.compile(
    r"^\s*(?P<line>\d+)\s+(?P<transition_id>\d+)\s+"
    r"(?P<transition>.+?\s+->\s+.+?)\s*$"
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
                        "expression_index",
                        "truth_row",
                        "imc_type_name",
                        "fec_context",
                        "fec_target",
                        "bit",
                        "multibit",
                        "fec_hits",
                        "fec_conditions",
                        "fsm_id",
                        "fsm_kind",
                        "state",
                        "transition",
                        "transition_id",
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
    """Normalize documented Questa code-detail rows into item-level points.

    Statement/branch rows and scalar condition/expression FEC rows are
    normalized conservatively. FSM state/transition rows follow the documented
    v2024.2 text-report sections and keep covered/uncovered evidence explicit.
    Documented multibit-verbose expression FEC rows are normalized per
    input-term bit; multibit condition FEC layouts remain unnormalized.
    """
    points: list[dict] = []
    kind = ""
    source_file = ""
    fec_line: int | None = None
    fec_item: int | None = None
    fec_context = ""
    in_fec_rows = False
    in_multibit_rows = False
    multibit_indices: list[int] = []
    multibit_rows: list[dict] = []
    fsm_id = ""
    fsm_section = ""

    def flush_multibit_points() -> None:
        nonlocal multibit_indices, multibit_rows
        if (
            kind != "expression"
            or fec_line is None
            or fec_item is None
            or not multibit_indices
            or not multibit_rows
        ):
            multibit_indices = []
            multibit_rows = []
            return

        grouped: dict[str, dict[str, dict]] = {}
        for row in multibit_rows:
            target = str(row["target"])
            match = re.match(r"^(?P<base>.+)\[i\]_(?P<state>[01])$", target)
            if match is None:
                continue
            grouped.setdefault(match.group("base"), {})[match.group("state")] = row

        for base, states in grouped.items():
            if set(states) != {"0", "1"}:
                continue
            zero = states["0"]
            one = states["1"]
            zero_hits = list(zero["hits"])
            one_hits = list(one["hits"])
            if (
                len(zero_hits) != len(multibit_indices)
                or len(one_hits) != len(multibit_indices)
            ):
                continue

            for column, bit_index in enumerate(multibit_indices):
                zero_count = int(zero_hits[column])
                one_count = int(one_hits[column])
                hit = zero_count > 0 and one_count > 0
                target = f"{base}[{bit_index}]"
                evidence = f"{target} _0={zero_count} _1={one_count}"
                points.append(
                    {
                        "name": f"{source_file}:{fec_line}:{fec_item}:{target}",
                        "count": int(hit),
                        "hit": hit,
                        "type": "expression",
                        "source_file": source_file,
                        "line": fec_line,
                        "item": fec_item,
                        "bit": bit_index,
                        "expression": fec_context,
                        "fec_context": fec_context,
                        "fec_target": target,
                        "fec_hits": {"0": zero_count, "1": one_count},
                        "fec_conditions": {
                            "0": str(zero.get("detail") or ""),
                            "1": str(one.get("detail") or ""),
                        },
                        "multibit": True,
                        "evidence": evidence,
                        "detail": evidence,
                    }
                )

        multibit_indices = []
        multibit_rows = []

    for raw_line in text.splitlines():
        header = _QUESTA_CODE_DETAIL_HEADER.match(raw_line)
        if header is not None:
            flush_multibit_points()
            kind = header.group("kind").strip().lower()
            source_file = header.group("file").strip()
            fec_line = None
            fec_item = None
            fec_context = ""
            in_fec_rows = False
            in_multibit_rows = False
            fsm_id = ""
            fsm_section = ""
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

        if kind == "fsm":
            id_match = _QUESTA_FSM_ID.match(raw_line)
            if id_match is not None:
                fsm_id = id_match.group("fsm_id").strip()
                fsm_section = ""
                continue

            section = _QUESTA_FSM_SECTION.match(raw_line)
            if section is not None:
                fsm_section = (
                    f"{section.group('covered').strip().lower()}_"
                    f"{section.group('kind').strip().lower()}"
                )
                continue

            stripped = raw_line.strip()
            if stripped.endswith(":"):
                fsm_section = ""
                continue
            if not fsm_id or not fsm_section:
                continue

            if fsm_section == "covered_states":
                row = _QUESTA_FSM_COVERED_STATE.match(raw_line)
                if row is None:
                    continue
                state = row.group("state").strip()
                hits = int(row.group("hits").replace(",", ""))
                points.append(
                    {
                        "name": f"{source_file}:{fsm_id}:state:{state}",
                        "count": hits,
                        "hit": hits > 0,
                        "type": "fsm",
                        "source_file": source_file,
                        "fsm_id": fsm_id,
                        "fsm_kind": "state",
                        "state": state,
                    }
                )
                continue

            if fsm_section == "uncovered_states":
                row = _QUESTA_FSM_UNCOVERED_STATE.match(raw_line)
                if row is None:
                    continue
                state = row.group("state").strip()
                if state.lower() == "state" or not state.strip("-"):
                    continue
                points.append(
                    {
                        "name": f"{source_file}:{fsm_id}:state:{state}",
                        "count": 0,
                        "hit": False,
                        "type": "fsm",
                        "source_file": source_file,
                        "fsm_id": fsm_id,
                        "fsm_kind": "state",
                        "state": state,
                    }
                )
                continue

            if fsm_section == "covered_transitions":
                row = _QUESTA_FSM_COVERED_TRANSITION.match(raw_line)
                if row is None:
                    continue
                line_number = int(row.group("line"))
                transition_id = int(row.group("transition_id"))
                hits = int(row.group("hits").replace(",", ""))
                transition = row.group("transition").strip()
                points.append(
                    {
                        "name": (
                            f"{source_file}:{line_number}:{fsm_id}:"
                            f"transition:{transition_id} {transition}"
                        ),
                        "count": hits,
                        "hit": hits > 0,
                        "type": "fsm",
                        "source_file": source_file,
                        "line": line_number,
                        "fsm_id": fsm_id,
                        "fsm_kind": "transition",
                        "transition_id": transition_id,
                        "transition": transition,
                    }
                )
                continue

            if fsm_section == "uncovered_transitions":
                row = _QUESTA_FSM_UNCOVERED_TRANSITION.match(raw_line)
                if row is None:
                    continue
                line_number = int(row.group("line"))
                transition_id = int(row.group("transition_id"))
                transition = row.group("transition").strip()
                points.append(
                    {
                        "name": (
                            f"{source_file}:{line_number}:{fsm_id}:"
                            f"transition:{transition_id} {transition}"
                        ),
                        "count": 0,
                        "hit": False,
                        "type": "fsm",
                        "source_file": source_file,
                        "line": line_number,
                        "fsm_id": fsm_id,
                        "fsm_kind": "transition",
                        "transition_id": transition_id,
                        "transition": transition,
                    }
                )
                continue

            continue

        if kind not in {"condition", "expression"}:
            continue

        item = _QUESTA_FEC_ITEM.match(raw_line)
        if item is not None:
            flush_multibit_points()
            fec_line = int(item.group("line"))
            fec_item = int(item.group("item"))
            fec_context = (item.group("detail") or "").strip()
            in_fec_rows = False
            in_multibit_rows = False
            continue

        normalized_line = raw_line.strip().lower()
        if normalized_line.startswith("rows: fec target") and kind == "expression":
            in_fec_rows = False
            in_multibit_rows = True
            multibit_indices = []
            multibit_rows = []
            continue
        if normalized_line.startswith("rows:") and "fec target" in normalized_line:
            in_fec_rows = True
            in_multibit_rows = False
            continue

        if in_multibit_rows:
            indices = [
                int(match.group("index"))
                for match in _QUESTA_MULTIBIT_INDEX.finditer(raw_line)
            ]
            if indices and raw_line.strip().lower().startswith("i"):
                multibit_indices = indices
                continue

            multibit_row = _QUESTA_MULTIBIT_FEC_ROW.match(raw_line)
            if multibit_row is None or not multibit_indices:
                continue
            tokens = multibit_row.group("rest").split()
            width = len(multibit_indices)
            if len(tokens) < width:
                continue
            hit_tokens = tokens[:width]
            if not all(_QUESTA_HIT_TOKEN.match(token) for token in hit_tokens):
                continue
            hits = [
                int(token.replace("*", "").replace(",", ""))
                for token in hit_tokens
            ]
            multibit_rows.append(
                {
                    "row": int(multibit_row.group("row")),
                    "target": multibit_row.group("target").strip(),
                    "hits": hits,
                    "detail": " ".join(tokens[width:]).strip(),
                }
            )
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

    flush_multibit_points()
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
    multibit_expression_path = out_dir / "multibit-expression.txt"
    toggle_detail_path = out_dir / "toggle-details.txt"
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
        "sbcef",
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
        "multibit_expression": _capture_questa_report_file(
            [
                tool,
                "report",
                "-details",
                "-multibitverbose",
                "-code",
                "e",
                "-output",
                str(multibit_expression_path),
                str(merged_path),
            ],
            cwd=project.root,
            output=multibit_expression_path,
        ),
        "toggle_detail": _capture_questa_report_file(
            [
                tool,
                "report",
                "-details",
                "-byinstance",
                "-code",
                "t",
                "-all",
                "-output",
                str(toggle_detail_path),
                str(merged_path),
            ],
            cwd=project.root,
            output=toggle_detail_path,
        ),
    }

    multibit_expression_evidence = detailed_code_coverage_evidence[
        "multibit_expression"
    ]
    multibit_expression_points: list[dict] = []
    if (
        multibit_expression_evidence.get("status") == "captured"
        and multibit_expression_path.exists()
    ):
        multibit_expression_points = [
            point
            for point in parse_questa_code_coverage_report(
                multibit_expression_path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            )
            if point.get("type") == "expression"
            and point.get("multibit") is True
        ]
    multibit_expression_status = (
        "ok"
        if multibit_expression_points
        else "empty"
        if multibit_expression_evidence.get("status") == "captured"
        else str(multibit_expression_evidence.get("status") or "missing")
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
        "code_report": str(code_report_path),
        "code_detail_status": code_detail_status,
        "code_detail_returncode": int(code_report.returncode),
        "code_detail_points": len(code_points),
        "code_detail_holes": sum(not point["hit"] for point in code_points),
        "multibit_expression_report": str(multibit_expression_path),
        "multibit_expression_status": multibit_expression_status,
        "multibit_expression_points": len(multibit_expression_points),
        "multibit_expression_holes": sum(
            not point["hit"] for point in multibit_expression_points
        ),
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
        "multibit_expression_report": str(multibit_expression_path),
        "multibit_expression_status": multibit_expression_status,
        "multibit_expression_points": len(multibit_expression_points),
        "multibit_expression_holes": sum(
            not point["hit"] for point in multibit_expression_points
        ),
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
    r"(?P<kind>Line|Cond(?:ition)?|Toggle|FSM|Branch)\s+Coverage\s+for\s+Instance\b",
    re.IGNORECASE,
)
_URG_ANY_COVERAGE_SECTION = re.compile(
    r"(?:Line|Cond(?:ition)?|Toggle|FSM|Branch)\s+Coverage\s+for\s+(?:Instance|Module)\b",
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
        if kind == "condition":
            kind = "cond"
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


_URG_CONDITION_CONTEXT = re.compile(
    r"\bLINE\s+(?P<line>\d+)\s+"
    r"(?P<label>SUB-EXPRESSION|EXPRESSION)\s+",
    re.IGNORECASE,
)
_URG_CONDITION_GUIDES = re.compile(
    r"(?:\s+-+\d+-+)+\s*$",
)


def _urg_condition_context(fragment: str) -> tuple[int, str, str] | None:
    """Return the latest explicit LINE/expression context before a truth table."""
    visible = _urg_visible_text(fragment)
    matches = list(_URG_CONDITION_CONTEXT.finditer(visible))
    if not matches:
        return None

    match = matches[-1]
    expression = _URG_CONDITION_GUIDES.sub(
        "",
        visible[match.end() :],
    ).strip()
    if not expression:
        return None
    label = match.group("label").lower().replace("-", "_")
    return int(match.group("line")), label, expression


def _parse_vcs_urg_condition_points(
    html_text: str,
) -> list[dict]:
    points: list[dict] = []

    for section_match in _URG_INSTANCE_COVERAGE_SECTION.finditer(html_text):
        raw_kind = section_match.group("kind").lower()
        if raw_kind not in {"cond", "condition"}:
            continue

        next_section = _URG_ANY_COVERAGE_SECTION.search(
            html_text,
            section_match.end(),
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

        for table_match in table_matches:
            parser = _UrgHtmlTableRows()
            parser.feed(table_match.group(0))
            parser.close()

            header_index: int | None = None
            status_index: int | None = None
            for index, row in enumerate(parser.rows):
                normalized = [cell.strip().casefold() for cell in row]
                if "status" in normalized:
                    header_index = index
                    status_index = normalized.index("status")
                    break
            if header_index is None or status_index is None:
                continue

            context = _urg_condition_context(
                section[: table_match.start()]
            )
            if context is None:
                continue
            line, label, expression = context

            for row in parser.rows[header_index + 1 :]:
                if len(row) <= status_index:
                    continue
                status = row[status_index].strip().casefold()
                if status not in {"covered", "not covered"}:
                    continue

                target_cells = [
                    cell.strip()
                    for cell in row[:status_index]
                    if cell.strip()
                ]
                if not target_cells:
                    continue
                target = " | ".join(target_cells)
                hit = status == "covered"
                points.append(
                    {
                        "name": (
                            f"{instance}|line:{line}|{label}:{expression}"
                            f"|target:{target}"
                        ),
                        "count": 1 if hit else 0,
                        "hit": hit,
                        "type": "condition",
                        "scope": instance,
                        "line": line,
                        "condition": expression,
                        "fec_context": expression,
                        "fec_target": target,
                        "evidence": f"URG status: {row[status_index].strip()}",
                        "detail": label,
                    }
                )

    return points


def parse_vcs_urg_condition_coverage_points(
    report_dir: str | Path,
) -> list[dict]:
    """Normalize explicit VCS URG condition truth-table rows.

    Only rows with documented Covered/Not Covered status are emitted. Excluded,
    unreachable, summary percentages, and other non-goal rows are ignored.
    Repeated/paginated report evidence is deduplicated by instance, source line,
    expression context, and truth-table target.
    """
    source = Path(report_dir)
    seen: dict[tuple[str, int, str, str], dict] = {}

    for html_path in sorted(source.glob("mod*.html")):
        html_text = html_path.read_text(encoding="utf-8", errors="replace")
        for point in _parse_vcs_urg_condition_points(html_text):
            key = (
                str(point["scope"]),
                int(point["line"]),
                str(point["condition"]),
                str(point["fec_target"]),
            )
            previous = seen.get(key)
            if previous is not None:
                if bool(previous["hit"]) != bool(point["hit"]):
                    raise ValueError(
                        "Conflicting URG condition status for "
                        f"{point['name']}"
                    )
                continue
            seen[key] = point

    return sorted(
        seen.values(),
        key=lambda point: (
            str(point.get("scope") or ""),
            int(point.get("line") or 0),
            str(point.get("condition") or ""),
            str(point.get("fec_target") or ""),
        ),
    )


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


_IMC_GRADE_TOKEN = r"(?:\d+(?:\.\d+)?%|n/a)"
_IMC_COUNT_TOKEN = r"\d[\d,]*/\d[\d,]*"
_IMC_DETAIL_INSTANCE = re.compile(
    r"^\s*Instance\s+name:\s*(?P<instance>.+?)\s*$",
    re.IGNORECASE,
)
_IMC_DETAIL_TYPE = re.compile(
    r"^\s*Type\s+name:\s*(?P<type_name>.+?)\s*$",
    re.IGNORECASE,
)
_IMC_DETAIL_FILE = re.compile(
    r"^\s*File\s+name:\s*(?P<file>.+?)\s*$",
    re.IGNORECASE,
)
_IMC_DETAIL_EXPRESSION_HEADER = re.compile(
    r"^\s*index\s*\|\s*grade\s*\|\s*line\s*\|\s*expression\s*$",
    re.IGNORECASE,
)
_IMC_DETAIL_EXPRESSION_SUMMARY = re.compile(
    r"^\s*(?P<index>\d+(?:\.\d+)*)\s*\|\s*"
    r"(?P<grade>[^|]+?)\s*\|\s*(?P<line>\d+)\s*\|\s*"
    r"(?P<expression>.+?)\s*$",
)
_IMC_DETAIL_EXPRESSION_CONTEXT = re.compile(
    r"^\s*index:\s*(?P<index>\d+(?:\.\d+)*)\s+"
    r"grade:\s*(?P<grade>.+?)\s+line:\s*(?P<line>\d+)\s+"
    r"source:\s*(?P<source>.*?)\s*$",
    re.IGNORECASE,
)
_IMC_DETAIL_EXPRESSION_ROW_HEADER = re.compile(
    r"^\s*index\s*\|\s*hit\s*\|\s*rval(?:\s*\|.*)?$",
    re.IGNORECASE,
)
_IMC_DETAIL_EXPRESSION_ROW = re.compile(
    r"^\s*(?P<row>\d+(?:\.\d+)+)\s*\|\s*"
    r"(?P<hit>IGN|\d[\d,]*)\s*\|\s*(?P<rval>\S+)"
    r"(?:\s*\|\s*(?P<terms>.*?))?\s*$",
    re.IGNORECASE,
)

_IMC_SUMMARY_ROW = re.compile(
    rf"^\s*(?P<name>\S+)\s+"
    rf"(?P<overall_average>{_IMC_GRADE_TOKEN})\s+"
    rf"(?P<overall_covered>{_IMC_GRADE_TOKEN})"
    rf"(?:\s+\((?P<overall_counts>{_IMC_COUNT_TOKEN})\))?\s+"
    rf"(?P<code_average>{_IMC_GRADE_TOKEN})\s+"
    rf"(?P<code_covered>{_IMC_GRADE_TOKEN})"
    rf"(?:\s+\((?P<code_counts>{_IMC_COUNT_TOKEN})\))?\s+"
    rf"(?P<fsm_average>{_IMC_GRADE_TOKEN})\s+"
    rf"(?P<fsm_covered>{_IMC_GRADE_TOKEN})"
    rf"(?:\s+\((?P<fsm_counts>{_IMC_COUNT_TOKEN})\))?\s+"
    rf"(?P<functional_average>{_IMC_GRADE_TOKEN})\s+"
    rf"(?P<functional_covered>{_IMC_GRADE_TOKEN})"
    rf"(?:\s+\((?P<functional_counts>{_IMC_COUNT_TOKEN})\))?\s*$",
    re.IGNORECASE,
)


def _imc_grade(value: str) -> float | None:
    text = value.strip().lower()
    if text == "n/a":
        return None
    if not text.endswith("%"):
        raise ValueError(f"Invalid IMC coverage grade: {value}")
    grade = float(text[:-1])
    if not 0.0 <= grade <= 100.0:
        raise ValueError(f"IMC coverage grade is outside 0..100: {value}")
    return grade


def _imc_counts(value: str | None, *, grade: float | None) -> dict | None:
    if value is None:
        return None
    covered_text, total_text = value.split("/", 1)
    covered = int(covered_text.replace(",", ""))
    total = int(total_text.replace(",", ""))
    if covered < 0 or total < 0 or covered > total:
        raise ValueError(f"Invalid IMC coverage counts: {value}")
    result: dict[str, int | float] = {
        "covered": covered,
        "total": total,
    }
    if grade is not None:
        result["hit_rate"] = grade
    return result


def parse_xcelium_imc_expression_coverage(text: str) -> list[dict]:
    """Normalize documented IMC expression truth-table rows.

    Cadence IMC detailed expression reports identify an instance, type, file,
    expression index/source line, and truth-table rows with an explicit hit
    column. Only numeric hit rows are coverage goals here. Rows marked IGN
    are intentionally excluded rather than rewritten as covered or uncovered.
    """
    points: list[dict] = []
    instance = ""
    type_name = ""
    source_file = ""
    expression_section = False
    expressions: dict[str, tuple[int, str]] = {}
    context: dict[str, str | int] | None = None
    in_truth_table = False

    for raw_line in text.splitlines():
        instance_match = _IMC_DETAIL_INSTANCE.match(raw_line)
        if instance_match is not None:
            instance = instance_match.group("instance").strip()
            type_name = ""
            source_file = ""
            expression_section = False
            expressions = {}
            context = None
            in_truth_table = False
            continue

        type_match = _IMC_DETAIL_TYPE.match(raw_line)
        if type_match is not None:
            type_name = type_match.group("type_name").strip()
            continue

        file_match = _IMC_DETAIL_FILE.match(raw_line)
        if file_match is not None:
            source_file = file_match.group("file").strip()
            continue

        if _IMC_DETAIL_EXPRESSION_HEADER.match(raw_line) is not None:
            expression_section = True
            context = None
            in_truth_table = False
            continue

        if not expression_section or not source_file:
            continue

        summary_match = _IMC_DETAIL_EXPRESSION_SUMMARY.match(raw_line)
        if summary_match is not None:
            expressions[summary_match.group("index")] = (
                int(summary_match.group("line")),
                summary_match.group("expression").strip(),
            )
            continue

        context_match = _IMC_DETAIL_EXPRESSION_CONTEXT.match(raw_line)
        if context_match is not None:
            expression_index = context_match.group("index")
            summary = expressions.get(expression_index)
            line_number = int(context_match.group("line"))
            expression = summary[1] if summary is not None else ""
            if summary is not None and summary[0] != line_number:
                context = None
                in_truth_table = False
                continue
            context = {
                "index": expression_index,
                "line": line_number,
                "expression": expression,
                "source": context_match.group("source").strip(),
            }
            in_truth_table = False
            continue

        if context is None:
            continue

        if _IMC_DETAIL_EXPRESSION_ROW_HEADER.match(raw_line) is not None:
            in_truth_table = True
            continue

        if not in_truth_table:
            continue

        row_match = _IMC_DETAIL_EXPRESSION_ROW.match(raw_line)
        if row_match is None:
            continue

        hit_token = row_match.group("hit").strip()
        if hit_token.upper() == "IGN":
            continue

        hits = int(hit_token.replace(",", ""))
        truth_row = row_match.group("row")
        result_value = row_match.group("rval").strip()
        terms = (row_match.group("terms") or "").strip()
        expression_index = str(context["index"])
        line_number = int(context["line"])
        expression = str(context["expression"])
        source = str(context["source"])
        target = f"rval={result_value}"
        if terms:
            target += f"; terms={terms}"

        scope = instance
        identity = (
            f"{scope}|{source_file}:{line_number}|"
            f"expression:{expression_index}|row:{truth_row}"
        )
        points.append(
            {
                "name": identity,
                "count": hits,
                "hit": hits > 0,
                "type": "expression",
                "scope": scope,
                "source_file": source_file,
                "line": line_number,
                "expression": expression,
                "expression_index": expression_index,
                "truth_row": truth_row,
                "fec_context": expression,
                "fec_target": target,
                "evidence": f"IMC hit={hits}; {target}",
                "detail": source,
                "imc_type_name": type_name,
            }
        )

    return points


def parse_xcelium_imc_summary(text: str) -> dict:
    """Normalize the cumulative top-level row from an IMC summary report.

    IMC reports Average and Covered as distinct grades. ZDDV retains both and
    uses Overall Covered as the percentage-native history score because that
    grade represents the covered/total-bin ratio rather than the hierarchy
    average grade.
    """
    lines = text.splitlines()
    header_index: int | None = None
    required_header = (
        "name overall average overall covered code average code covered "
        "fsm average fsm covered functional average functional covered"
    )
    for index, raw_line in enumerate(lines):
        normalized = re.sub(
            r"\s+",
            " ",
            raw_line.replace("*", "").strip().lower(),
        )
        if normalized == required_header:
            header_index = index
            break
    if header_index is None:
        raise ValueError(
            "IMC summary header with Overall Average/Covered and "
            "Code/FSM/Functional columns was not found"
        )

    match = None
    for raw_line in lines[header_index + 1 :]:
        candidate = raw_line.strip()
        if not candidate or set(candidate) <= {"-", "=", "+", "|", " "}:
            continue
        match = _IMC_SUMMARY_ROW.match(raw_line)
        if match is not None:
            break
    if match is None:
        raise ValueError("IMC summary coverage data row was not found")

    metric_names = (
        "overall_average",
        "overall_covered",
        "code_average",
        "code_covered",
        "fsm_average",
        "fsm_covered",
        "functional_average",
        "functional_covered",
    )
    by_metric: dict[str, float] = {}
    grades: dict[str, float | None] = {}
    for name in metric_names:
        grade = _imc_grade(match.group(name))
        grades[name] = grade
        if grade is not None:
            by_metric[name] = grade

    score = grades["overall_covered"]
    if score is None:
        raise ValueError("IMC summary has no numeric Overall Covered grade")

    by_metric_counts: dict[str, dict[str, int | float]] = {}
    for prefix in ("overall", "code", "fsm", "functional"):
        values = _imc_counts(
            match.group(f"{prefix}_counts"),
            grade=grades[f"{prefix}_covered"],
        )
        if values is not None:
            by_metric_counts[f"{prefix}_covered"] = values

    return {
        "source": "imc-summary",
        "scope": match.group("name"),
        "tool_total_coverage": score,
        "by_metric": by_metric,
        "by_metric_counts": by_metric_counts,
        "metric_semantics": "imc-summary-cumulative",
    }


def merge_xcelium_coverage(project: ProjectConfig) -> dict:
    """Merge Xcelium coverage with native IMC union semantics."""
    tool = shutil.which("imc")
    if tool is None:
        raise RuntimeError(
            "Cadence IMC was not found in PATH. Configure Xcelium/IMC and retry."
        )

    run_root = (project.root / project.run_dir).resolve()
    coverage_dirs = sorted(
        path.resolve()
        for path in run_root.glob("*/coverage/*")
        if path.is_dir() and any(path.glob("*.ucd"))
    )
    if not coverage_dirs:
        raise RuntimeError(
            f"No Xcelium .ucd run databases found under {run_root}. "
            "Run coverage-enabled Xcelium simulations first."
        )

    out_dir = (project.root / ".zddv" / "coverage" / "xcelium").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_cov_work = out_dir / "cov_work"
    if generated_cov_work.exists():
        shutil.rmtree(generated_cov_work)

    runfile_path = out_dir / "runs.txt"
    merge_log_path = out_dir / "merge.log"
    summary_path = out_dir / "summary.txt"
    detail_path = out_dir / "detail.txt"
    script_path = out_dir / "imc-commands.tcl"
    manifest_path = out_dir / "metrics.json"
    inputs = [str(path) for path in coverage_dirs]
    ucd_inputs = [
        str(ucd.resolve())
        for coverage_dir in coverage_dirs
        for ucd in sorted(coverage_dir.glob("*.ucd"))
    ]
    runfile_path.write_text("\n".join(ucd_inputs) + "\n", encoding="utf-8")

    merge_script = (
        f"merge -overwrite -runfile {{{runfile_path}}} "
        "-out merged -metrics all -initial_model union_all -message 1; exit"
    )
    merged_path = out_dir / "cov_work" / "scope" / "merged"
    report_script = (
        'report -summary -inst "*..." -metrics all '
        "-cumulative on -showempty on -local off; exit"
    )
    detail_script = (
        'report -detail -inst "*..." -metrics all '
        "-all -showempty on -source on; exit"
    )
    script_path.write_text(
        merge_script
        + "\n"
        + f"# load: {merged_path}\n"
        + report_script
        + "\n"
        + detail_script
        + "\n",
        encoding="utf-8",
    )

    merge_command = [tool, "-execcmd", merge_script]
    merge = _run(merge_command, out_dir)
    merge_log_path.write_text(merge.stdout or "", encoding="utf-8")

    merged_ucd = sorted(merged_path.glob("*.ucd")) if merged_path.is_dir() else []
    merged_ucm = sorted(merged_path.glob("*.ucm")) if merged_path.is_dir() else []
    if merge.returncode != 0 or not merged_ucd or not merged_ucm:
        raise RuntimeError(
            "Xcelium IMC coverage merge failed. Ensure IMC is compatible with "
            "the Xcelium coverage database version:\n"
            + "$ "
            + " ".join(merge_command)
            + "\n"
            + (merge.stdout or "").strip()
        )

    report_command = [
        tool,
        "-load",
        str(merged_path),
        "-execcmd",
        report_script,
    ]
    report = _run(report_command, out_dir)
    summary_path.write_text(report.stdout or "", encoding="utf-8")
    if report.returncode != 0:
        raise RuntimeError(
            "Xcelium IMC coverage report failed. Ensure IMC is compatible with "
            f"the generated coverage database. See {summary_path}"
        )

    detail_command = [
        tool,
        "-load",
        str(merged_path),
        "-execcmd",
        detail_script,
    ]
    detail_report = _run(detail_command, out_dir)
    detail_path.write_text(detail_report.stdout or "", encoding="utf-8")
    detail_status = "captured" if detail_report.returncode == 0 else "tool-error"

    metrics: dict | None = None
    metrics_error: str | None = None
    metrics_status = "summary-unparsed"
    snapshot_id: str | None = None
    try:
        metrics = parse_xcelium_imc_summary(report.stdout or "")
        metrics_status = "normalized"
        snapshot_id = (
            datetime.now(timezone.utc).strftime("cov-score-%Y%m%dT%H%M%S")
            + "-"
            + uuid.uuid4().hex[:8]
        )
    except ValueError as exc:
        metrics_error = str(exc)

    created_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "created_at": created_at,
        "project": project.name,
        "simulator": project.simulator,
        "status": "merged-report-captured",
        "metrics_status": metrics_status,
        "input_count": len(inputs),
        "inputs": inputs,
        "ucd_inputs": ucd_inputs,
        "merged": str(merged_path),
        "merged_ucd_files": [str(path) for path in merged_ucd],
        "merged_ucm_files": [str(path) for path in merged_ucm],
        "runfile": str(runfile_path),
        "merge_log": str(merge_log_path),
        "summary": str(summary_path),
        "detail": str(detail_path),
        "detail_status": detail_status,
        "detail_returncode": int(detail_report.returncode),
        "script": str(script_path),
        "merge_model": "union_all",
        "merge_command": merge_command,
        "report_command": report_command,
        "detail_command": detail_command,
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
        "detail": str(detail_path),
        "detail_status": detail_status,
        "detail_returncode": int(detail_report.returncode),
        "detail_command": detail_command,
        "metrics_path": str(manifest_path),
        "report": report.stdout or "",
        "metrics": metrics,
        "snapshot_id": snapshot_id,
        "script": str(script_path),
        "runfile": str(runfile_path),
        "merge_log": str(merge_log_path),
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
