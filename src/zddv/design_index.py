from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig


_UNIT_START = re.compile(
    r"(?m)^\s*(?P<kind>module|interface|program|package)\s+"
    r"(?:(?:automatic|static)\s+)?(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b"
)
_END_KEYWORD = {
    "module": "endmodule",
    "interface": "endinterface",
    "program": "endprogram",
    "package": "endpackage",
}
_INSTANTIABLE_KINDS = {"module", "interface", "program"}
_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


@dataclass(frozen=True)
class _UnitRegion:
    kind: str
    name: str
    start: int
    body_start: int
    end: int
    line: int
    end_line: int


def _mask_non_code(text: str) -> str:
    """Mask comments and string literals while preserving offsets and newlines."""
    chars = list(text)
    i = 0
    state = "code"
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""

        if state == "code":
            if ch == "/" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if ch == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if ch == '"':
                chars[i] = " "
                i += 1
                state = "string"
                continue
            i += 1
            continue

        if state == "line_comment":
            if ch == "\n":
                state = "code"
            else:
                chars[i] = " "
            i += 1
            continue

        if state == "block_comment":
            if ch == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "code"
                continue
            if ch != "\n":
                chars[i] = " "
            i += 1
            continue

        if state == "string":
            if ch == "\\" and i + 1 < len(chars):
                chars[i] = " "
                if chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if ch == '"':
                chars[i] = " "
                i += 1
                state = "code"
                continue
            if ch != "\n":
                chars[i] = " "
            i += 1

    return "".join(chars)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _find_unit_regions(masked: str) -> list[_UnitRegion]:
    regions: list[_UnitRegion] = []
    cursor = 0
    while True:
        match = _UNIT_START.search(masked, cursor)
        if match is None:
            break

        kind = match.group("kind")
        end_re = re.compile(rf"(?m)^\s*{_END_KEYWORD[kind]}\b")
        end_match = end_re.search(masked, match.end())
        if end_match is None:
            end_offset = len(masked)
            cursor = len(masked)
        else:
            end_offset = end_match.end()
            cursor = end_offset

        regions.append(
            _UnitRegion(
                kind=kind,
                name=match.group("name"),
                start=match.start(),
                body_start=match.end(),
                end=end_offset,
                line=_line_number(masked, match.start()),
                end_line=_line_number(masked, end_offset),
            )
        )
    return regions


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _skip_balanced(text: str, pos: int, opening: str, closing: str) -> int | None:
    if pos >= len(text) or text[pos] != opening:
        return None
    depth = 0
    i = pos
    while i < len(text):
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _scan_instances(
    masked: str,
    region: _UnitRegion,
    instantiable_names: set[str],
) -> list[dict[str, Any]]:
    if not instantiable_names:
        return []

    body = masked[region.body_start : region.end]
    names = sorted(instantiable_names, key=len, reverse=True)
    type_re = re.compile(
        r"(?<![A-Za-z0-9_$])(?P<type>"
        + "|".join(re.escape(name) for name in names)
        + r")(?![A-Za-z0-9_$])"
    )

    instances: list[dict[str, Any]] = []
    for match in type_re.finditer(body):
        pos = _skip_space(body, match.end())

        if pos < len(body) and body[pos] == "#":
            pos = _skip_space(body, pos + 1)
            after_params = _skip_balanced(body, pos, "(", ")")
            if after_params is None:
                continue
            pos = _skip_space(body, after_params)

        name_match = _IDENTIFIER.match(body, pos)
        if name_match is None:
            continue
        instance_name = name_match.group(0)
        pos = _skip_space(body, name_match.end())

        while pos < len(body) and body[pos] == "[":
            after_dim = _skip_balanced(body, pos, "[", "]")
            if after_dim is None:
                break
            pos = _skip_space(body, after_dim)

        if pos >= len(body) or body[pos] != "(":
            continue

        absolute = region.body_start + match.start()
        instances.append(
            {
                "name": instance_name,
                "type": match.group("type"),
                "line": _line_number(masked, absolute),
            }
        )

    return instances


def _relative_path(project: ProjectConfig, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.root.resolve()))
    except ValueError:
        return str(path.resolve())


def _hierarchy_node(
    unit_name: str,
    *,
    instance_name: str,
    path: str,
    units_by_name: dict[str, dict[str, Any]],
    stack: tuple[str, ...],
) -> dict[str, Any]:
    unit = units_by_name.get(unit_name)
    if unit is None:
        return {
            "instance": instance_name,
            "type": unit_name,
            "path": path,
            "resolved": False,
            "children": [],
        }

    recursive = unit_name in stack
    node = {
        "instance": instance_name,
        "type": unit_name,
        "kind": unit["kind"],
        "path": path,
        "resolved": True,
        "recursive": recursive,
        "file": unit["file"],
        "line": unit["line"],
        "children": [],
    }
    if recursive:
        return node

    next_stack = (*stack, unit_name)
    for child in unit["instances"]:
        child_path = f"{path}.{child['name']}"
        node["children"].append(
            _hierarchy_node(
                child["type"],
                instance_name=child["name"],
                path=child_path,
                units_by_name=units_by_name,
                stack=next_stack,
            )
        )
    return node


def build_design_index(project: ProjectConfig) -> dict[str, Any]:
    """Build a deterministic source index and source-level hierarchy."""
    sources = project.source_files()
    files: list[dict[str, Any]] = []
    parsed: list[tuple[Path, str, list[_UnitRegion]]] = []

    for path in sources:
        raw = path.read_text(encoding="utf-8", errors="replace")
        masked = _mask_non_code(raw)
        regions = _find_unit_regions(masked)
        parsed.append((path, masked, regions))
        files.append(
            {
                "path": _relative_path(project, path),
                "bytes": path.stat().st_size,
                "lines": raw.count("\n") + (0 if raw.endswith("\n") else 1),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    instantiable_names = {
        region.name
        for _, _, regions in parsed
        for region in regions
        if region.kind in _INSTANTIABLE_KINDS
    }

    units: list[dict[str, Any]] = []
    for path, masked, regions in parsed:
        for region in regions:
            instances = (
                _scan_instances(masked, region, instantiable_names)
                if region.kind in _INSTANTIABLE_KINDS
                else []
            )
            units.append(
                {
                    "kind": region.kind,
                    "name": region.name,
                    "file": _relative_path(project, path),
                    "line": region.line,
                    "end_line": region.end_line,
                    "instances": instances,
                }
            )

    units.sort(key=lambda item: (item["file"], item["line"], item["name"]))
    files.sort(key=lambda item: item["path"])

    duplicates: dict[str, list[str]] = {}
    units_by_name: dict[str, dict[str, Any]] = {}
    for unit in units:
        if unit["name"] in units_by_name:
            duplicates.setdefault(unit["name"], [units_by_name[unit["name"]]["file"]]).append(
                unit["file"]
            )
            continue
        units_by_name[unit["name"]] = unit

    hierarchy = _hierarchy_node(
        project.top,
        instance_name=project.top,
        path=project.top,
        units_by_name=units_by_name,
        stack=(),
    )

    all_instances = [
        {
            **instance,
            "parent": unit["name"],
            "file": unit["file"],
        }
        for unit in units
        for instance in unit["instances"]
    ]

    return {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "files": files,
        "units": units,
        "instances": all_instances,
        "duplicates": duplicates,
        "hierarchy": hierarchy,
        "summary": {
            "files": len(files),
            "units": len(units),
            "instances": len(all_instances),
            "duplicate_unit_names": len(duplicates),
        },
    }


def write_design_index(project: ProjectConfig) -> dict[str, Any]:
    index = build_design_index(project)
    out_dir = (project.root / ".zddv" / "design").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.json"
    path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return {**index, "path": str(path)}


def hierarchy_lines(node: dict[str, Any]) -> list[str]:
    """Render a normalized hierarchy tree for CLI/debug use."""
    lines: list[str] = []

    def visit(item: dict[str, Any], depth: int) -> None:
        prefix = "  " * depth
        marker = ""
        if not item.get("resolved", True):
            marker = " [UNRESOLVED]"
        elif item.get("recursive"):
            marker = " [RECURSIVE]"
        if depth == 0:
            label = f"{item['instance']}: {item['type']}"
        else:
            label = f"{item['instance']}: {item['type']}"
        lines.append(prefix + label + marker)
        for child in item.get("children", []):
            visit(child, depth + 1)

    visit(node, 0)
    return lines
