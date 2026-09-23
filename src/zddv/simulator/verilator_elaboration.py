from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from zddv.config import ProjectConfig


_LOC_RE = re.compile(
    r"^(?P<file>[^,]+),(?P<first_line>\d+):(?P<first_col>\d+),"
    r"(?P<last_line>\d+):(?P<last_col>\d+)$"
)
_MODULE_NODE_TYPES = {"MODULE"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _meta_files(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = meta.get("files", {})
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(value, dict)
    }


def _location(
    loc: Any,
    files: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(loc, str):
        return None
    match = _LOC_RE.fullmatch(loc)
    if match is None:
        return {"raw": loc}

    file_id = match.group("file")
    entry = files.get(file_id, {})
    result: dict[str, Any] = {
        "file_id": file_id,
        "line": int(match.group("first_line")),
        "column": int(match.group("first_col")),
        "end_line": int(match.group("last_line")),
        "end_column": int(match.group("last_col")),
    }
    if isinstance(entry.get("filename"), str):
        result["file"] = entry["filename"]
    if isinstance(entry.get("language"), str):
        result["language"] = entry["language"]
    return result


def _node_name(node: dict[str, Any]) -> str | None:
    for key in ("verilogName", "name", "origName"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _collect_cells(
    value: Any,
    *,
    files: dict[str, dict[str, Any]],
    generate_scopes: tuple[str, ...] = (),
    root: bool = True,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            cells.extend(
                _collect_cells(
                    item,
                    files=files,
                    generate_scopes=generate_scopes,
                    root=False,
                )
            )
        return cells

    if not isinstance(value, dict):
        return cells

    node_type = value.get("type")
    if not root and node_type in _MODULE_NODE_TYPES | {"PACKAGE"}:
        return cells

    next_scopes = generate_scopes
    if node_type == "GENBLOCK":
        scope_name = _node_name(value)
        if scope_name:
            next_scopes = (*generate_scopes, scope_name)

    if node_type == "CELL":
        instance = _node_name(value)
        module = value.get("modName")
        if isinstance(instance, str) and isinstance(module, str) and module:
            cells.append(
                {
                    "instance": instance,
                    "orig_name": value.get("origName"),
                    "module": module,
                    "generate_scopes": list(generate_scopes),
                    "source": _location(value.get("loc"), files),
                }
            )
        return cells

    for key, child in value.items():
        if key in {
            "type",
            "name",
            "origName",
            "verilogName",
            "addr",
            "loc",
            "modName",
            "modp",
        }:
            continue
        if isinstance(child, (dict, list)):
            cells.extend(
                _collect_cells(
                    child,
                    files=files,
                    generate_scopes=next_scopes,
                    root=False,
                )
            )
    return cells


def _normalized_modules(
    tree: dict[str, Any],
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    files = _meta_files(meta)
    raw_modules = tree.get("modulesp", [])
    if not isinstance(raw_modules, list):
        raise ValueError("Verilator JSON tree has no modulesp list")

    modules: list[dict[str, Any]] = []
    for node in raw_modules:
        if not isinstance(node, dict):
            continue
        node_type = node.get("type")
        if node_type not in _MODULE_NODE_TYPES:
            continue
        name = node.get("name")
        if not isinstance(name, str) or not name:
            continue

        modules.append(
            {
                "name": name,
                "orig_name": node.get("origName"),
                "verilog_name": node.get("verilogName"),
                "node_type": node_type,
                "level": node.get("level"),
                "depth": node.get("depth"),
                "source": _location(node.get("loc"), files),
                "cells": _collect_cells(node, files=files),
            }
        )

    modules.sort(key=lambda item: (str(item["name"]), str(item.get("orig_name") or "")))
    return modules


def _module_aliases(module: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for key in ("name", "orig_name", "verilog_name"):
        value = module.get(key)
        if isinstance(value, str) and value:
            aliases.add(value.strip())
    return aliases


def _resolve_module(
    name: str,
    modules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    exact = [module for module in modules if module.get("name") == name]
    if len(exact) == 1:
        return exact[0]
    aliases = [module for module in modules if name in _module_aliases(module)]
    if len(aliases) == 1:
        return aliases[0]
    return None


def _select_top(
    top: str,
    modules: list[dict[str, Any]],
) -> dict[str, Any]:
    named = [module for module in modules if top in _module_aliases(module)]
    level_one = [module for module in named if module.get("level") == 1]
    if len(level_one) == 1:
        return level_one[0]
    if len(named) == 1:
        return named[0]

    roots = [module for module in modules if module.get("level") == 1]
    if len(roots) == 1:
        return roots[0]

    raise ValueError(
        f"Unable to resolve elaborated top '{top}' uniquely "
        f"from {len(modules)} Verilator module node(s)"
    )


def _hierarchy_node(
    module: dict[str, Any] | None,
    *,
    instance: str,
    declared_type: str,
    path: str,
    source: dict[str, Any] | None,
    generate_scopes: list[str],
    modules: list[dict[str, Any]],
    stack: tuple[str, ...],
) -> dict[str, Any]:
    if module is None:
        return {
            "instance": instance,
            "type": declared_type,
            "path": path,
            "resolved": False,
            "generate_scopes": generate_scopes,
            "source": source,
            "children": [],
        }

    module_name = str(module["name"])
    recursive = module_name in stack
    node = {
        "instance": instance,
        "type": module_name,
        "declared_type": declared_type,
        "path": path,
        "resolved": True,
        "recursive": recursive,
        "generate_scopes": generate_scopes,
        "source": source or module.get("source"),
        "module_source": module.get("source"),
        "children": [],
    }
    if recursive:
        return node

    next_stack = (*stack, module_name)
    for cell in module.get("cells", []):
        child_type = str(cell["module"])
        child = _resolve_module(child_type, modules)
        segments = [*cell.get("generate_scopes", []), str(cell["instance"])]
        child_path = ".".join([path, *segments])
        node["children"].append(
            _hierarchy_node(
                child,
                instance=str(cell["instance"]),
                declared_type=child_type,
                path=child_path,
                source=cell.get("source"),
                generate_scopes=list(cell.get("generate_scopes", [])),
                modules=modules,
                stack=next_stack,
            )
        )
    return node


def _flatten_hierarchy(node: dict[str, Any]) -> list[dict[str, Any]]:
    result = [node]
    for child in node.get("children", []):
        result.extend(_flatten_hierarchy(child))
    return result


def normalize_verilator_elaboration(
    tree: dict[str, Any],
    meta: dict[str, Any],
    *,
    project_name: str,
    top: str,
    simulator_version: str | None = None,
    tree_sha256: str | None = None,
    meta_sha256: str | None = None,
) -> dict[str, Any]:
    if tree.get("type") != "NETLIST":
        raise ValueError("Unsupported Verilator JSON root: expected NETLIST")

    modules = _normalized_modules(tree, meta)
    top_module = _select_top(top, modules)
    hierarchy = _hierarchy_node(
        top_module,
        instance=top,
        declared_type=top,
        path=top,
        source=top_module.get("source"),
        generate_scopes=[],
        modules=modules,
        stack=(),
    )
    hierarchy_nodes = _flatten_hierarchy(hierarchy)
    all_cells = [cell for module in modules for cell in module.get("cells", [])]
    unresolved = [node for node in hierarchy_nodes if not node.get("resolved", False)]

    return {
        "schema_version": 1,
        "project": project_name,
        "top": top,
        "analysis_level": "verilator_elaborated_json",
        "simulator": "verilator",
        "simulator_version": simulator_version,
        "modules": modules,
        "hierarchy": hierarchy,
        "summary": {
            "modules": len(modules),
            "cells": len(all_cells),
            "generated_cells": sum(
                1 for cell in all_cells if cell.get("generate_scopes")
            ),
            "hierarchy_nodes": len(hierarchy_nodes),
            "unresolved_hierarchy_nodes": len(unresolved),
        },
        "provenance": {
            "tree_sha256": tree_sha256,
            "meta_sha256": meta_sha256,
        },
        "limitations": [
            "Normalization uses Verilator's evolving JSON AST contract and rejects unknown roots rather than guessing.",
            "This milestone normalizes elaborated module/cell hierarchy and generate scopes; signal/port connectivity remains source-structural.",
            "Verilator-specific AST evidence is kept separate from the simulator-independent design-index contract.",
        ],
    }


def _inside_project(project: ProjectConfig, path: Path) -> Path:
    resolved = path if path.is_absolute() else project.root / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(project.root.resolve())
    except ValueError as exc:
        raise ValueError("Elaboration output must remain inside the project root") from exc
    return resolved


def _verilator_tool() -> str:
    tool = shutil.which("verilator")
    if tool is None:
        raise RuntimeError("Verilator was not found in PATH.")
    return tool


def _verilator_version(tool: str) -> str:
    completed = subprocess.run(
        [tool, "--version"],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Unable to query Verilator")
    return completed.stdout.strip()


def write_verilator_elaboration(
    project: ProjectConfig,
    *,
    output: str | Path = ".zddv/design/elaboration.json",
    raw_dir: str | Path = ".zddv/design/verilator",
) -> dict[str, Any]:
    sources = project.source_files()
    if not sources:
        raise RuntimeError("No RTL/testbench sources matched the project configuration.")

    destination = _inside_project(project, Path(output))
    raw = _inside_project(project, Path(raw_dir))
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)

    tree_path = raw / "design.tree.json"
    meta_path = raw / "design.tree.meta.json"
    log_path = raw / "elaboration.log"
    tool = _verilator_tool()

    command = [
        tool,
        "--json-only",
        "--no-json-edit-nums",
        "--top-module",
        project.top,
        "--json-only-output",
        str(tree_path),
        "--json-only-meta-output",
        str(meta_path),
        *[str(path) for path in sources],
    ]
    completed = subprocess.run(
        command,
        cwd=project.root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"Verilator JSON elaboration failed with return code "
            f"{completed.returncode}. See {log_path}"
        )
    if not tree_path.exists() or not meta_path.exists():
        raise RuntimeError(
            f"Verilator JSON elaboration did not produce the expected tree/meta "
            f"artifacts. See {log_path}"
        )

    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    report = normalize_verilator_elaboration(
        tree,
        meta,
        project_name=project.name,
        top=project.top,
        simulator_version=_verilator_version(tool),
        tree_sha256=_sha256(tree_path),
        meta_sha256=_sha256(meta_path),
    )
    report["artifacts"] = {
        "tree": str(tree_path),
        "meta": str(meta_path),
        "log": str(log_path),
    }
    report["command"] = command
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {**report, "path": str(destination)}
