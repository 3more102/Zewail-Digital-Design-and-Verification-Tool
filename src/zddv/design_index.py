from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig


_UNIT_RE = re.compile(
    r"\b(?P<kind>module|interface|program|package)\s+"
    r"(?:(?:automatic|static)\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_$]*)",
    re.MULTILINE,
)

_END_KEYWORD = {
    "module": "endmodule",
    "interface": "endinterface",
    "program": "endprogram",
    "package": "endpackage",
}


def _mask_comments_and_strings(text: str) -> str:
    """Mask comments and string contents while preserving character offsets/newlines."""
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


def _relative_path(project: ProjectConfig, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _find_units(masked: str, source_path: str) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for match in _UNIT_RE.finditer(masked):
        kind = match.group("kind")
        name = match.group("name")
        end_re = re.compile(rf"\b{_END_KEYWORD[kind]}\b")
        end_match = end_re.search(masked, match.end())
        if end_match is None:
            continue

        units.append(
            {
                "kind": kind,
                "name": name,
                "source": source_path,
                "line": _line_number(masked, match.start()),
                "end_line": _line_number(masked, end_match.end()),
                "_body_start": match.end(),
                "_body_end": end_match.start(),
                "_instances": [],
            }
        )
    return units


def _find_instances(
    masked: str,
    units: list[dict[str, Any]],
    known_unit_names: set[str],
) -> None:
    instantiable = sorted(known_unit_names, key=lambda value: (-len(value), value))

    for unit in units:
        if unit["kind"] == "package":
            continue
        body_start = int(unit["_body_start"])
        body_end = int(unit["_body_end"])
        body = masked[body_start:body_end]

        instances: list[dict[str, Any]] = []
        for child_name in instantiable:
            if child_name == unit["name"]:
                # Recursive instantiation is legal in generated/elaboration contexts;
                # do not suppress it. The hierarchy builder handles recursion safely.
                pass

            pattern = re.compile(
                rf"(?<![A-Za-z0-9_$]){re.escape(child_name)}\s*"
                rf"(?:#\s*\([^;]*?\)\s*)?"
                rf"(?P<instance>[A-Za-z_][A-Za-z0-9_$]*)\s*\(",
                re.DOTALL,
            )
            for match in pattern.finditer(body):
                absolute = body_start + match.start()
                instances.append(
                    {
                        "unit": child_name,
                        "instance": match.group("instance"),
                        "line": _line_number(masked, absolute),
                    }
                )

        instances.sort(key=lambda item: (item["line"], item["instance"], item["unit"]))
        unit["_instances"] = instances


def _public_unit(unit: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": unit["kind"],
        "name": unit["name"],
        "source": unit["source"],
        "line": unit["line"],
        "end_line": unit["end_line"],
        "instances": list(unit["_instances"]),
    }


def _build_hierarchy(
    top: str,
    definitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    def expand(
        unit_name: str,
        instance_name: str,
        path: str,
        stack: tuple[str, ...],
    ) -> dict[str, Any]:
        definition = definitions[unit_name]
        node: dict[str, Any] = {
            "instance": instance_name,
            "unit": unit_name,
            "kind": definition["kind"],
            "path": path,
            "source": definition["source"],
            "line": definition["line"],
            "children": [],
        }

        for child in definition["_instances"]:
            child_unit = child["unit"]
            child_instance = child["instance"]
            child_path = f"{path}.{child_instance}"
            if child_unit in stack:
                node["children"].append(
                    {
                        "instance": child_instance,
                        "unit": child_unit,
                        "kind": definitions[child_unit]["kind"],
                        "path": child_path,
                        "source": definitions[child_unit]["source"],
                        "line": child["line"],
                        "recursive": True,
                        "children": [],
                    }
                )
                continue
            node["children"].append(
                expand(
                    child_unit,
                    child_instance,
                    child_path,
                    (*stack, child_unit),
                )
            )
        return node

    return expand(top, top, top, (top,))


def _count_hierarchy_nodes(node: dict[str, Any]) -> int:
    return 1 + sum(_count_hierarchy_nodes(child) for child in node["children"])


def format_hierarchy_tree(node: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    def visit(current: dict[str, Any], prefix: str, is_last: bool, root: bool = False) -> None:
        marker = "" if root else ("└─ " if is_last else "├─ ")
        recursive = " [recursive]" if current.get("recursive") else ""
        lines.append(
            f"{prefix}{marker}{current['instance']} : {current['unit']}{recursive}"
        )
        children = current.get("children", [])
        for idx, child in enumerate(children):
            child_prefix = prefix if root else prefix + ("   " if is_last else "│  ")
            visit(child, child_prefix, idx == len(children) - 1)

    visit(node, "", True, root=True)
    return lines


def index_project(
    project: ProjectConfig,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    sources = project.source_files()
    if not sources:
        raise RuntimeError("No RTL/testbench sources matched the project configuration.")

    parsed_files: list[dict[str, Any]] = []
    all_units: list[dict[str, Any]] = []

    for path in sources:
        raw = path.read_text(encoding="utf-8", errors="replace")
        masked = _mask_comments_and_strings(raw)
        rel = _relative_path(project, path)
        units = _find_units(masked, rel)
        all_units.extend(units)

        parsed_files.append(
            {
                "path": rel,
                "language": "systemverilog" if path.suffix.lower() == ".sv" else "verilog",
                "bytes": path.stat().st_size,
                "lines": raw.count("\n") + (0 if raw.endswith("\n") else 1),
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "units": [unit["name"] for unit in units],
            }
        )

    known_names = {unit["name"] for unit in all_units}
    by_source: dict[str, list[dict[str, Any]]] = {}
    for unit in all_units:
        by_source.setdefault(unit["source"], []).append(unit)
    for source, units in by_source.items():
        masked = _mask_comments_and_strings(
            (project.root / source).read_text(encoding="utf-8", errors="replace")
            if not Path(source).is_absolute()
            else Path(source).read_text(encoding="utf-8", errors="replace")
        )
        _find_instances(masked, units, known_names)

    definitions: dict[str, dict[str, Any]] = {}
    duplicates: dict[str, list[str]] = {}
    for unit in all_units:
        name = unit["name"]
        if name in definitions:
            duplicates.setdefault(name, [definitions[name]["source"]]).append(unit["source"])
            continue
        definitions[name] = unit

    if project.top not in definitions:
        available = ", ".join(sorted(definitions)) or "(none)"
        raise RuntimeError(
            f"Configured top '{project.top}' was not found in indexed sources. "
            f"Available design units: {available}"
        )

    hierarchy = _build_hierarchy(project.top, definitions)
    instance_count = sum(len(unit["_instances"]) for unit in all_units)
    reachable_nodes = _count_hierarchy_nodes(hierarchy)

    output_root = Path(output_dir) if output_dir is not None else project.root / ".zddv" / "index"
    if not output_root.is_absolute():
        output_root = project.root / output_root
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    diagnostics = []
    for name, paths in sorted(duplicates.items()):
        diagnostics.append(
            {
                "severity": "warning",
                "code": "DUPLICATE_UNIT",
                "message": f"Design unit '{name}' is defined multiple times.",
                "sources": paths,
            }
        )

    source_index = {
        "schema_version": "1.0",
        "project": project.name,
        "top": project.top,
        "files": parsed_files,
        "units": [_public_unit(unit) for unit in all_units],
        "diagnostics": diagnostics,
        "stats": {
            "files": len(parsed_files),
            "units": len(all_units),
            "instances": instance_count,
        },
    }
    hierarchy_index = {
        "schema_version": "1.0",
        "project": project.name,
        "top": project.top,
        "root": hierarchy,
        "stats": {
            "reachable_instances": max(0, reachable_nodes - 1),
            "reachable_nodes": reachable_nodes,
        },
    }

    source_path = output_root / "source-index.json"
    hierarchy_path = output_root / "hierarchy.json"
    source_path.write_text(json.dumps(source_index, indent=2) + "\n", encoding="utf-8")
    hierarchy_path.write_text(json.dumps(hierarchy_index, indent=2) + "\n", encoding="utf-8")

    return {
        "source_index": source_index,
        "hierarchy": hierarchy_index,
        "source_index_path": str(source_path),
        "hierarchy_path": str(hierarchy_path),
    }
