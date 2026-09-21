from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from zddv.config import ProjectConfig


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_$]*"
_UNIT_RE = re.compile(
    rf"\b(?P<kind>module|interface)\s+(?:automatic\s+|static\s+)?"
    rf"(?P<name>{_IDENTIFIER})\b(?P<header>.*?)"
    rf"\b(?P<end>endmodule|endinterface)\b",
    re.DOTALL,
)
_INSTANCE_RE = re.compile(
    rf"(?<![\w$])(?P<type>{_IDENTIFIER})(?![A-Za-z0-9_$])"
    rf"(?:\s*#\s*\((?:[^()]|\([^()]*\))*\)\s*|\s+)"
    rf"(?P<name>{_IDENTIFIER})(?![A-Za-z0-9_$])\s*\(",
    re.DOTALL,
)

_SKIP_INSTANCE_TYPES = {
    "always",
    "always_comb",
    "always_ff",
    "always_latch",
    "assert",
    "assign",
    "automatic",
    "begin",
    "bit",
    "byte",
    "case",
    "class",
    "cover",
    "else",
    "end",
    "for",
    "foreach",
    "forever",
    "fork",
    "function",
    "generate",
    "if",
    "initial",
    "int",
    "integer",
    "interface",
    "logic",
    "longint",
    "module",
    "property",
    "real",
    "reg",
    "repeat",
    "sequence",
    "shortint",
    "signed",
    "static",
    "string",
    "struct",
    "task",
    "time",
    "typedef",
    "union",
    "unsigned",
    "wait",
    "while",
    "wire",
}


@dataclass(frozen=True)
class DesignUnit:
    kind: str
    name: str
    source: str
    line: int
    end_line: int


@dataclass(frozen=True)
class Instance:
    parent: str
    child_type: str
    name: str
    source: str
    line: int
    resolved: bool


def _mask_comments_and_strings(text: str) -> str:
    """Mask comments and string bodies while preserving offsets and line numbers."""
    out = list(text)
    i = 0
    n = len(out)

    while i < n:
        if i + 1 < n and out[i] == "/" and out[i + 1] == "/":
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and out[i] != "\n":
                out[i] = " "
                i += 1
            continue

        if i + 1 < n and out[i] == "/" and out[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < n:
                if i + 1 < n and out[i] == "*" and out[i + 1] == "/":
                    out[i] = out[i + 1] = " "
                    i += 2
                    break
                if out[i] != "\n":
                    out[i] = " "
                i += 1
            continue

        if out[i] == '"':
            out[i] = " "
            i += 1
            escaped = False
            while i < n:
                ch = out[i]
                if ch == "\n":
                    escaped = False
                    i += 1
                    continue
                out[i] = " "
                if ch == '"' and not escaped:
                    i += 1
                    break
                if ch == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                i += 1
            continue

        i += 1

    return "".join(out)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def parse_systemverilog(
    path: Path,
    *,
    root: Path | None = None,
) -> tuple[list[DesignUnit], list[Instance]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    masked = _mask_comments_and_strings(text)
    source = str(path.resolve())

    if root is not None:
        try:
            source = str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            pass

    units: list[DesignUnit] = []
    pending: list[tuple[str, str, str, int]] = []

    for match in _UNIT_RE.finditer(masked):
        kind = match.group("kind")
        name = match.group("name")
        unit = DesignUnit(
            kind=kind,
            name=name,
            source=source,
            line=_line_number(masked, match.start()),
            end_line=_line_number(masked, match.end()),
        )
        units.append(unit)

        body_start = match.start("header")
        body = masked[body_start : match.start("end")]
        for inst in _INSTANCE_RE.finditer(body):
            child_type = inst.group("type")
            instance_name = inst.group("name")
            if child_type in _SKIP_INSTANCE_TYPES or child_type == name:
                continue

            pending.append(
                (
                    name,
                    child_type,
                    instance_name,
                    _line_number(masked, body_start + inst.start()),
                )
            )

    known = {unit.name for unit in units}
    instances = [
        Instance(
            parent=parent,
            child_type=child_type,
            name=instance_name,
            source=source,
            line=line,
            resolved=child_type in known,
        )
        for parent, child_type, instance_name, line in pending
    ]
    return units, instances


def _hierarchy(
    top: str,
    units: list[DesignUnit],
    instances: list[Instance],
) -> dict[str, Any]:
    unit_names = {unit.name for unit in units}
    children: dict[str, list[Instance]] = {}
    for instance in instances:
        children.setdefault(instance.parent, []).append(instance)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    if top not in unit_names:
        return {
            "top": top,
            "top_found": False,
            "nodes": nodes,
            "edges": edges,
        }

    stack: list[tuple[str, str, tuple[str, ...]]] = [(top, top, (top,))]
    while stack:
        unit_name, path, ancestry = stack.pop()
        nodes.append({"path": path, "unit": unit_name})

        for inst in reversed(children.get(unit_name, [])):
            child_path = f"{path}.{inst.name}"
            cycle = inst.child_type in ancestry
            edges.append(
                {
                    "parent_path": path,
                    "instance": inst.name,
                    "child_type": inst.child_type,
                    "child_path": child_path,
                    "resolved": inst.resolved,
                    "cycle": cycle,
                    "source": inst.source,
                    "line": inst.line,
                }
            )
            if inst.resolved and not cycle:
                stack.append(
                    (
                        inst.child_type,
                        child_path,
                        (*ancestry, inst.child_type),
                    )
                )

    nodes.sort(key=lambda item: item["path"])
    edges.sort(key=lambda item: (item["parent_path"], item["instance"]))
    return {
        "top": top,
        "top_found": True,
        "nodes": nodes,
        "edges": edges,
    }


def _debug_db_path(project: ProjectConfig) -> Path:
    return (project.root / ".zddv" / "debug" / "design.db").resolve()


def _persist_index(project: ProjectConfig, payload: dict[str, Any]) -> Path:
    path = _debug_db_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_files (
                path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                line_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS design_units (
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                PRIMARY KEY (name, source, line)
            );
            CREATE TABLE IF NOT EXISTS instances (
                parent TEXT NOT NULL,
                child_type TEXT NOT NULL,
                name TEXT NOT NULL,
                source TEXT NOT NULL,
                line INTEGER NOT NULL,
                resolved INTEGER NOT NULL,
                PRIMARY KEY (parent, name, source, line)
            );
            CREATE TABLE IF NOT EXISTS hierarchy_edges (
                parent_path TEXT NOT NULL,
                instance TEXT NOT NULL,
                child_type TEXT NOT NULL,
                child_path TEXT NOT NULL,
                resolved INTEGER NOT NULL,
                cycle INTEGER NOT NULL,
                source TEXT NOT NULL,
                line INTEGER NOT NULL,
                PRIMARY KEY (parent_path, instance, child_path)
            );
            """
        )

        for table in (
            "metadata",
            "source_files",
            "design_units",
            "instances",
            "hierarchy_edges",
        ):
            db.execute(f"DELETE FROM {table}")

        db.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [
                ("created_at", payload["created_at"]),
                ("project", payload["project"]),
                ("top", payload["hierarchy"]["top"]),
                (
                    "top_found",
                    json.dumps(payload["hierarchy"]["top_found"]),
                ),
            ],
        )
        db.executemany(
            "INSERT INTO source_files(path, sha256, line_count) VALUES (?, ?, ?)",
            [
                (item["path"], item["sha256"], item["line_count"])
                for item in payload["files"]
            ],
        )
        db.executemany(
            """
            INSERT INTO design_units(kind, name, source, line, end_line)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item["kind"],
                    item["name"],
                    item["source"],
                    item["line"],
                    item["end_line"],
                )
                for item in payload["units"]
            ],
        )
        db.executemany(
            """
            INSERT INTO instances(
                parent, child_type, name, source, line, resolved
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["parent"],
                    item["child_type"],
                    item["name"],
                    item["source"],
                    item["line"],
                    int(item["resolved"]),
                )
                for item in payload["instances"]
            ],
        )
        db.executemany(
            """
            INSERT INTO hierarchy_edges(
                parent_path, instance, child_type, child_path,
                resolved, cycle, source, line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["parent_path"],
                    item["instance"],
                    item["child_type"],
                    item["child_path"],
                    int(item["resolved"]),
                    int(item["cycle"]),
                    item["source"],
                    item["line"],
                )
                for item in payload["hierarchy"]["edges"]
            ],
        )

    return path


def index_project(project: ProjectConfig) -> dict[str, Any]:
    units: list[DesignUnit] = []
    raw_instances: list[Instance] = []
    file_records: list[dict[str, Any]] = []

    for path in project.source_files():
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        try:
            rel = str(path.resolve().relative_to(project.root.resolve()))
        except ValueError:
            rel = str(path.resolve())

        file_records.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "line_count": text.count("\n")
                + (0 if not text or text.endswith("\n") else 1),
            }
        )

        file_units, file_instances = parse_systemverilog(
            path,
            root=project.root,
        )
        units.extend(file_units)
        raw_instances.extend(file_instances)

    known = {unit.name for unit in units}
    instances = [
        Instance(
            parent=item.parent,
            child_type=item.child_type,
            name=item.name,
            source=item.source,
            line=item.line,
            resolved=item.child_type in known,
        )
        for item in raw_instances
    ]

    duplicate_names = sorted(
        name
        for name in known
        if sum(1 for unit in units if unit.name == name) > 1
    )
    hierarchy = _hierarchy(project.top, units, instances)

    payload: dict[str, Any] = {
        "schema": "zddv.design-index.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "files": file_records,
        "units": [asdict(unit) for unit in units],
        "instances": [asdict(item) for item in instances],
        "hierarchy": hierarchy,
        "duplicate_unit_names": duplicate_names,
        "unresolved_instances": [
            asdict(item) for item in instances if not item.resolved
        ],
    }

    out_dir = project.root / ".zddv" / "debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = (out_dir / "source-index.json").resolve()
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    db_path = _persist_index(project, payload)

    return {
        **payload,
        "json_path": str(json_path),
        "database_path": str(db_path),
    }
