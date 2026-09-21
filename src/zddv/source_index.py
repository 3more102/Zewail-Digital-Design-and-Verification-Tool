from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig


_MODULE_START = re.compile(r"\bmodule\s+(?P<name>[A-Za-z_][A-Za-z0-9_$]*)\b")
_ENDMODULE = re.compile(r"\bendmodule\b")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*|[#();,.]")


@dataclass(frozen=True)
class _Token:
    value: str
    offset: int


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments while preserving newlines and offsets."""
    out = list(text)
    index = 0
    state = "code"
    while index < len(text):
        if state == "code":
            if text.startswith("//", index):
                out[index] = " "
                out[index + 1] = " "
                index += 2
                state = "line"
                continue
            if text.startswith("/*", index):
                out[index] = " "
                out[index + 1] = " "
                index += 2
                state = "block"
                continue
            if text[index] == '"':
                index += 1
                state = "string"
                continue
            index += 1
            continue

        if state == "line":
            if text[index] == "\n":
                state = "code"
            else:
                out[index] = " "
            index += 1
            continue

        if state == "block":
            if text.startswith("*/", index):
                out[index] = " "
                out[index + 1] = " "
                index += 2
                state = "code"
                continue
            if text[index] != "\n":
                out[index] = " "
            index += 1
            continue

        if state == "string":
            if text[index] == "\\" and index + 1 < len(text):
                index += 2
                continue
            if text[index] == '"':
                index += 1
                state = "code"
                continue
            index += 1

    return "".join(out)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _module_regions(text: str) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    cursor = 0
    while True:
        match = _MODULE_START.search(text, cursor)
        if match is None:
            break
        end_match = _ENDMODULE.search(text, match.end())
        if end_match is None:
            raise ValueError(
                f"Module {match.group('name')} has no matching endmodule."
            )
        regions.append(
            {
                "name": match.group("name"),
                "start": match.start(),
                "body_start": match.end(),
                "end": end_match.end(),
            }
        )
        cursor = end_match.end()
    return regions


def _tokens(text: str, start: int, end: int) -> list[_Token]:
    return [
        _Token(match.group(0), match.start())
        for match in _TOKEN.finditer(text, start, end)
    ]


def _skip_balanced(tokens: list[_Token], index: int) -> int | None:
    if index >= len(tokens) or tokens[index].value != "(":
        return None
    depth = 0
    for current in range(index, len(tokens)):
        value = tokens[current].value
        if value == "(":
            depth += 1
        elif value == ")":
            depth -= 1
            if depth == 0:
                return current + 1
    return None


def _find_instances(
    text: str,
    *,
    start: int,
    end: int,
    known_modules: set[str],
) -> list[dict[str, Any]]:
    tokens = _tokens(text, start, end)
    instances: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        module_type = tokens[index].value
        if module_type not in known_modules:
            index += 1
            continue

        cursor = index + 1
        if cursor < len(tokens) and tokens[cursor].value == "#":
            cursor += 1
            skipped = _skip_balanced(tokens, cursor)
            if skipped is None:
                index += 1
                continue
            cursor = skipped

        if cursor + 1 >= len(tokens):
            index += 1
            continue

        instance_name = tokens[cursor].value
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", instance_name):
            index += 1
            continue
        if tokens[cursor + 1].value != "(":
            index += 1
            continue

        instances.append(
            {
                "module": module_type,
                "instance": instance_name,
                "line": _line_number(text, tokens[index].offset),
            }
        )
        index = cursor + 2

    return instances


def build_source_index(project: ProjectConfig) -> dict[str, Any]:
    source_files = project.source_files()
    parsed_files: list[tuple[Path, str, list[dict[str, Any]]]] = []
    modules: list[dict[str, Any]] = []

    for path in source_files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = _strip_comments(raw)
        regions = _module_regions(text)
        parsed_files.append((path, text, regions))
        for region in regions:
            modules.append(
                {
                    "name": region["name"],
                    "file": str(path),
                    "line": _line_number(text, region["start"]),
                    "end_line": _line_number(text, region["end"]),
                    "instances": [],
                }
            )

    names = [module["name"] for module in modules]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(
            "Duplicate module definitions: " + ", ".join(duplicates)
        )

    known_modules = set(names)
    module_lookup = {module["name"]: module for module in modules}
    edges: list[dict[str, Any]] = []

    for path, text, regions in parsed_files:
        for region in regions:
            parent = module_lookup[region["name"]]
            instances = _find_instances(
                text,
                start=region["body_start"],
                end=region["end"],
                known_modules=known_modules,
            )
            parent["instances"] = instances
            for instance in instances:
                edges.append(
                    {
                        "parent": parent["name"],
                        "child": instance["module"],
                        "instance": instance["instance"],
                        "file": str(path),
                        "line": instance["line"],
                    }
                )

    instantiated = {edge["child"] for edge in edges}
    roots = sorted(name for name in known_modules if name not in instantiated)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "configured_top": project.top,
        "source_count": len(source_files),
        "module_count": len(modules),
        "edge_count": len(edges),
        "roots": roots,
        "modules": sorted(modules, key=lambda item: item["name"]),
        "edges": sorted(
            edges,
            key=lambda item: (
                item["parent"],
                item["instance"],
                item["child"],
            ),
        ),
    }


def write_source_index(project: ProjectConfig) -> dict[str, Any]:
    result = build_source_index(project)
    out_dir = (project.root / ".zddv" / "source").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / "index.json"
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"path": str(output), "index": result}


def hierarchy_lines(index: dict[str, Any], top: str | None = None) -> list[str]:
    modules = {item["name"]: item for item in index["modules"]}
    root = top or index.get("configured_top")
    if not root or root not in modules:
        available = ", ".join(sorted(modules)) or "(none)"
        raise ValueError(
            f"Hierarchy top '{root}' was not found. Available modules: {available}"
        )

    lines: list[str] = [root]

    def visit(module_name: str, prefix: str, stack: tuple[str, ...]) -> None:
        module = modules[module_name]
        instances = module.get("instances", [])
        for position, instance in enumerate(instances):
            last = position == len(instances) - 1
            connector = "└── " if last else "├── "
            child = instance["module"]
            lines.append(
                f"{prefix}{connector}{instance['instance']}: {child}"
            )
            if child in stack:
                cycle_prefix = prefix + ("    " if last else "│   ")
                lines.append(f"{cycle_prefix}└── <cycle>")
                continue
            child_prefix = prefix + ("    " if last else "│   ")
            visit(child, child_prefix, (*stack, child))

    visit(root, "", (root,))
    return lines
