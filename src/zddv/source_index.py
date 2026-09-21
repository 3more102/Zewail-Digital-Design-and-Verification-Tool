from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from zddv.config import ProjectConfig
from zddv.storage import record_design_index_snapshot


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_MODULE_START = re.compile(
    r"\bmodule\s+(?:(?:automatic|static)\s+)?([A-Za-z_][A-Za-z0-9_$]*)\b"
)
_ENDMODULE = re.compile(r"\bendmodule\b")


def _mask_noncode(text: str) -> str:
    """Blank comments and string contents while preserving offsets/newlines."""
    out = list(text)
    i = 0
    n = len(out)
    while i < n:
        if text.startswith("//", i):
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue

        if text.startswith("/*", i):
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not text.startswith("*/", i):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = " "
                if i + 1 < n:
                    out[i + 1] = " "
                i += 2
            continue

        if text[i] == '"':
            out[i] = " "
            i += 1
            escaped = False
            while i < n:
                ch = text[i]
                if ch == "\n":
                    escaped = False
                    i += 1
                    continue
                out[i] = " "
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    i += 1
                    break
                i += 1
            continue

        i += 1
    return "".join(out)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _skip_ws(text: str, offset: int) -> int:
    while offset < len(text) and text[offset].isspace():
        offset += 1
    return offset


def _skip_balanced(
    text: str,
    offset: int,
    opener: str,
    closer: str,
) -> int | None:
    if offset >= len(text) or text[offset] != opener:
        return None
    depth = 0
    i = offset
    while i < len(text):
        ch = text[i]
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _parse_instances_in_module(
    body: str,
    *,
    body_offset: int,
    source_text: str,
    source_path: str,
    parent_module: str,
    known_modules: set[str],
) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []

    for child_module in sorted(known_modules, key=len, reverse=True):
        pattern = re.compile(rf"\b{re.escape(child_module)}\b")
        for match in pattern.finditer(body):
            i = _skip_ws(body, match.end())

            # Reject package/class-style references such as name::member.
            if i < len(body) and body[i : i + 2] == "::":
                continue

            # Optional parameter override: child #(...) u_child (...);
            if i < len(body) and body[i] == "#":
                i = _skip_ws(body, i + 1)
                end = _skip_balanced(body, i, "(", ")")
                if end is None:
                    continue
                i = _skip_ws(body, end)

            parsed_any = False
            while i < len(body):
                name_match = _IDENT.match(body, i)
                if not name_match:
                    break

                instance_name = name_match.group(0)
                name_pos = i
                i = _skip_ws(body, name_match.end())

                # Unpacked instance arrays are indexed as one declaration for now.
                while i < len(body) and body[i] == "[":
                    end = _skip_balanced(body, i, "[", "]")
                    if end is None:
                        break
                    i = _skip_ws(body, end)

                if i >= len(body) or body[i] != "(":
                    break

                end = _skip_balanced(body, i, "(", ")")
                if end is None:
                    break

                instances.append(
                    {
                        "parent_module": parent_module,
                        "module_name": child_module,
                        "instance_name": instance_name,
                        "source_path": source_path,
                        "source_line": _line_number(
                            source_text,
                            body_offset + name_pos,
                        ),
                    }
                )
                parsed_any = True
                i = _skip_ws(body, end)

                # SystemVerilog permits multiple instances in one declaration.
                if i < len(body) and body[i] == ",":
                    i = _skip_ws(body, i + 1)
                    continue
                break

            if not parsed_any:
                continue

    return instances


def analyze_source_files(paths: list[Path], top: str) -> dict[str, Any]:
    """Index project-local modules and elaborate project-local instance paths."""
    source_units: list[dict[str, Any]] = []
    module_regions: list[dict[str, Any]] = []

    for path in paths:
        text = path.read_text(encoding="utf-8")
        masked = _mask_noncode(text)
        pos = 0

        while True:
            start = _MODULE_START.search(masked, pos)
            if start is None:
                break

            end = _ENDMODULE.search(masked, start.end())
            if end is None:
                raise ValueError(
                    f"Unterminated module {start.group(1)} in {path}"
                )

            module_name = start.group(1)
            source_units.append(
                {
                    "module_name": module_name,
                    "source_path": str(path.resolve()),
                    "start_line": _line_number(text, start.start()),
                    "end_line": _line_number(text, end.end()),
                }
            )
            module_regions.append(
                {
                    "module_name": module_name,
                    "source_path": str(path.resolve()),
                    "text": text,
                    "masked": masked,
                    "body_start": start.end(),
                    "body_end": end.start(),
                }
            )
            pos = end.end()

    if not source_units:
        raise ValueError("No SystemVerilog/Verilog module declarations found.")

    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in source_units:
        by_name.setdefault(item["module_name"], []).append(item)
    duplicates = {name: rows for name, rows in by_name.items() if len(rows) > 1}
    if duplicates:
        names = ", ".join(sorted(duplicates))
        raise ValueError(
            "Duplicate project-local module declarations are not yet supported: "
            f"{names}"
        )

    module_names = set(by_name)
    if top not in module_names:
        raise ValueError(
            f"Configured top module '{top}' was not found in project sources."
        )

    definitions: list[dict[str, Any]] = []
    for region in module_regions:
        body_start = int(region["body_start"])
        body_end = int(region["body_end"])
        body = str(region["masked"])[body_start:body_end]
        definitions.extend(
            _parse_instances_in_module(
                body,
                body_offset=body_start,
                source_text=str(region["text"]),
                source_path=str(region["source_path"]),
                parent_module=str(region["module_name"]),
                known_modules=module_names,
            )
        )

    children: dict[str, list[dict[str, Any]]] = {}
    for item in definitions:
        children.setdefault(item["parent_module"], []).append(item)
    for rows in children.values():
        rows.sort(key=lambda item: (item["source_line"], item["instance_name"]))

    hierarchy: list[dict[str, Any]] = []

    def walk(
        module_name: str,
        parent_path: str,
        depth: int,
        stack: tuple[str, ...],
    ) -> None:
        for item in children.get(module_name, []):
            child = item["module_name"]
            instance_path = f"{parent_path}.{item['instance_name']}"
            recursive = child in stack
            hierarchy.append(
                {
                    **item,
                    "instance_path": instance_path,
                    "parent_path": parent_path,
                    "depth": depth,
                    "recursive": recursive,
                }
            )
            if not recursive:
                walk(child, instance_path, depth + 1, (*stack, child))

    walk(top, top, 1, (top,))

    return {
        "top": top,
        "sources": [str(path.resolve()) for path in paths],
        "modules": sorted(
            source_units,
            key=lambda item: (item["module_name"], item["source_path"]),
        ),
        "instance_definitions": definitions,
        "instances": hierarchy,
    }


def build_design_index(project: ProjectConfig) -> dict[str, Any]:
    sources = project.source_files()
    if not sources:
        raise ValueError("No RTL/testbench source files match the project configuration.")

    analysis = analyze_source_files(sources, project.top)
    created_at = datetime.now(timezone.utc).isoformat()
    snapshot_id = (
        "design-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid4().hex[:8]
    )

    output_dir = project.root / ".zddv" / "index"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "design-index.json"

    record = {
        "snapshot_id": snapshot_id,
        "created_at": created_at,
        "project": project.name,
        "top": project.top,
        "source_count": len(analysis["sources"]),
        "module_count": len(analysis["modules"]),
        "instance_count": len(analysis["instances"]),
        "index_path": str(output.resolve()),
        "sources": analysis["sources"],
        "modules": analysis["modules"],
        "instances": analysis["instances"],
    }
    output.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record_design_index_snapshot(project, record)
    return record
