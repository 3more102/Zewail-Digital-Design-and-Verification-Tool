from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from zddv.config import ProjectConfig
from zddv.design_index import write_design_index
from zddv.design_revision import design_revision_fingerprint
from zddv.simulator import VerilatorBackend


SCHEMA_VERSION = 1
_JSON_LOC = re.compile(r"([^,]+),(\d+):(\d+),(\d+):(\d+)")


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _relative_path(project_root: Path, value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("<") and value.endswith(">"):
        return value

    path = Path(value)
    try:
        resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
        return str(resolved.relative_to(project_root.resolve()))
    except (OSError, ValueError):
        return value


def _decode_location(
    loc: str | None,
    files: dict[str, dict[str, Any]],
    project_root: Path,
) -> dict[str, Any] | None:
    if not loc:
        return None

    match = _JSON_LOC.fullmatch(loc)
    if match:
        file_id, first_line, first_col, last_line, last_col = match.groups()
    else:
        parts = loc.split(",")
        if len(parts) != 5 or not all(part.isdigit() for part in parts[1:]):
            return {"raw": loc}
        file_id, first_line, last_line, first_col, last_col = parts

    info = files.get(str(file_id), {})
    filename = info.get("realpath") or info.get("filename") or info.get("name")
    return {
        "file_id": str(file_id),
        "path": _relative_path(project_root, filename),
        "line": int(first_line),
        "column": int(first_col),
        "end_line": int(last_line),
        "end_column": int(last_col),
    }


def _deduplicate(
    modules: list[dict[str, Any]],
    instances: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    module_map: dict[tuple[str, str | None, int | None], dict[str, Any]] = {}
    for module in modules:
        location = module.get("location") or {}
        key = (module["name"], location.get("path"), location.get("line"))
        module_map[key] = module

    instance_map: dict[str, dict[str, Any]] = {}
    for instance in instances:
        path = instance["path"]
        current = instance_map.get(path)
        if current is None or (current.get("module") is None and instance.get("module")):
            instance_map[path] = instance

    return (
        sorted(module_map.values(), key=lambda item: (not item["top"], item["name"])),
        sorted(
            instance_map.values(),
            key=lambda item: (item["path"].count("."), item["path"]),
        ),
    )


def _json_node_name(node: dict[str, Any]) -> str | None:
    for key in ("origName", "verilogName", "name"):
        value = node.get(key)
        if value:
            return str(value).strip()
    return None


def _json_module_aliases(node: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for key in ("name", "origName", "verilogName"):
        value = node.get(key)
        if value:
            aliases.add(str(value).strip())
    return aliases


def _json_module_ports(
    module_node: dict[str, Any],
    *,
    files: dict[str, dict[str, Any]],
    project_root: Path,
) -> list[dict[str, Any]]:
    """Normalize direct module VAR nodes only from documented ioDirection evidence."""
    module_name = (
        module_node.get("origName")
        or module_node.get("verilogName")
        or module_node.get("name")
    )
    if not module_name:
        return []

    candidates: list[dict[str, Any]] = []
    for child in module_node.values():
        if isinstance(child, dict):
            if str(child.get("type", "")).upper() == "VAR":
                candidates.append(child)
        elif isinstance(child, list):
            candidates.extend(
                item
                for item in child
                if isinstance(item, dict)
                and str(item.get("type", "")).upper() == "VAR"
            )

    ports: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None, int | None, int | None]] = set()
    for node in candidates:
        direction_raw = str(node.get("ioDirection") or "").strip()
        if not direction_raw or direction_raw.upper() == "NONE":
            continue

        name = node.get("verilogName") or node.get("name") or node.get("origName")
        if not name:
            continue

        location = _decode_location(node.get("loc"), files, project_root)
        location_key = location or {}
        key = (
            str(name),
            direction_raw.upper(),
            location_key.get("path"),
            location_key.get("line"),
            location_key.get("column"),
        )
        if key in seen:
            continue
        seen.add(key)

        ports.append(
            {
                "module": str(module_name),
                "module_elaborated_name": module_node.get("name"),
                "name": str(name),
                "elaborated_name": node.get("name"),
                "verilog_name": node.get("verilogName"),
                "original_name": node.get("origName"),
                "direction": direction_raw.lower(),
                "direction_raw": direction_raw,
                "direction_field": "ioDirection",
                "var_type": node.get("varType"),
                "location": location,
            }
        )

    return sorted(
        ports,
        key=lambda item: (
            item["module"],
            str(item.get("module_elaborated_name") or ""),
            item["name"],
            item["direction"],
        ),
    )


def _json_direct_cells(
    value: Any,
    *,
    files: dict[str, dict[str, Any]],
    project_root: Path,
    generate_scopes: tuple[str, ...] = (),
    root: bool = True,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            cells.extend(
                _json_direct_cells(
                    item,
                    files=files,
                    project_root=project_root,
                    generate_scopes=generate_scopes,
                    root=False,
                )
            )
        return cells
    if not isinstance(value, dict):
        return cells

    node_type = str(value.get("type", "")).upper()
    if not root and node_type in {"MODULE", "PACKAGE"}:
        return cells

    next_scopes = generate_scopes
    if node_type == "GENBLOCK":
        scope_name = _json_node_name(value)
        if scope_name:
            next_scopes = (*generate_scopes, scope_name)

    if node_type == "CELL":
        name = _json_node_name(value)
        if not name:
            return cells
        module_pointer = value.get("modp")
        cells.append(
            {
                "name": name,
                "elaborated_name": value.get("name"),
                "declared_module": value.get("modName") or value.get("submodname"),
                "module_pointer": (
                    str(module_pointer) if module_pointer is not None else None
                ),
                "generate_scopes": list(generate_scopes),
                "hier": value.get("hier"),
                "location": _decode_location(
                    value.get("loc"),
                    files,
                    project_root,
                ),
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
                _json_direct_cells(
                    child,
                    files=files,
                    project_root=project_root,
                    generate_scopes=next_scopes,
                    root=False,
                )
            )
    return cells


def parse_verilator_json(
    ast_path: str | Path,
    meta_path: str | Path,
    *,
    project_root: str | Path,
    top: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ast = json.loads(Path(ast_path).read_text(encoding="utf-8"))
    metadata = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    files = {str(key): value for key, value in metadata.get("files", {}).items()}
    nodes = list(_walk(ast))

    modules: list[dict[str, Any]] = []
    module_by_addr: dict[str, str] = {}
    module_node_by_addr: dict[str, dict[str, Any]] = {}
    module_nodes: list[dict[str, Any]] = []
    ports: list[dict[str, Any]] = []
    for node in nodes:
        if str(node.get("type", "")).upper() != "MODULE":
            continue
        name = node.get("origName") or node.get("verilogName") or node.get("name")
        if not name:
            continue
        name = str(name)
        address = node.get("addr")
        if address is not None:
            module_by_addr[str(address)] = name
            module_node_by_addr[str(address)] = node
        module_nodes.append(node)
        ports.extend(
            _json_module_ports(
                node,
                files=files,
                project_root=root,
            )
        )
        modules.append(
            {
                "name": name,
                "elaborated_name": node.get("name"),
                "top": bool(node.get("topModule")) or name == top,
                "location": _decode_location(node.get("loc"), files, root),
            }
        )

    top_module = next((module for module in modules if module["top"]), None)
    instances: list[dict[str, Any]] = [
        {
            "path": top,
            "name": top,
            "module": top_module["name"] if top_module else top,
            "top": True,
            "location": top_module["location"] if top_module else None,
            "generate_scopes": [],
        }
    ]

    structured_modules = [
        node
        for node in ast.get("modulesp", [])
        if isinstance(node, dict)
        and str(node.get("type", "")).upper() == "MODULE"
    ]
    aliases: dict[str, list[dict[str, Any]]] = {}
    for node in module_nodes:
        for alias in _json_module_aliases(node):
            aliases.setdefault(alias, []).append(node)

    def resolve_module_node(cell: dict[str, Any]) -> dict[str, Any] | None:
        pointer = cell.get("module_pointer")
        if pointer is not None and pointer in module_node_by_addr:
            return module_node_by_addr[pointer]
        declared = cell.get("declared_module")
        if declared:
            matches = aliases.get(str(declared).strip(), [])
            if len(matches) == 1:
                return matches[0]
        return None

    def select_top_node() -> dict[str, Any] | None:
        candidates = [
            node
            for node in structured_modules
            if bool(node.get("topModule")) or top in _json_module_aliases(node)
        ]
        explicit = [node for node in candidates if bool(node.get("topModule"))]
        if len(explicit) == 1:
            return explicit[0]
        level_one = [node for node in candidates if node.get("level") == 1]
        if len(level_one) == 1:
            return level_one[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    top_node = select_top_node()
    if top_node is not None:
        def visit(
            module_node: dict[str, Any],
            parent_path: str,
            stack: tuple[str, ...],
        ) -> None:
            module_key = str(
                module_node.get("addr")
                or module_node.get("name")
                or module_node.get("origName")
                or ""
            )
            if module_key in stack:
                return
            next_stack = (*stack, module_key)

            for cell in _json_direct_cells(
                module_node,
                files=files,
                project_root=root,
            ):
                hierarchy = cell.get("hier")
                if (
                    isinstance(hierarchy, str)
                    and (hierarchy == top or hierarchy.startswith(top + "."))
                ):
                    path = hierarchy
                else:
                    path = ".".join(
                        [
                            parent_path,
                            *cell.get("generate_scopes", []),
                            str(cell["name"]),
                        ]
                    )

                child_node = resolve_module_node(cell)
                if child_node is not None:
                    module_name = (
                        child_node.get("origName")
                        or child_node.get("verilogName")
                        or child_node.get("name")
                    )
                else:
                    module_name = cell.get("declared_module")
                instances.append(
                    {
                        "path": path,
                        "name": str(cell["name"]),
                        "module": str(module_name) if module_name else None,
                        "top": False,
                        "location": cell.get("location"),
                        "generate_scopes": list(cell.get("generate_scopes", [])),
                    }
                )
                if child_node is not None:
                    visit(child_node, path, next_stack)

        visit(top_node, top, ())
    else:
        # Compatibility path for older/fixture JSON layouts where CELL records
        # are already emitted with full hierarchy strings outside modulesp.
        for node in nodes:
            if str(node.get("type", "")).upper() != "CELL":
                continue
            hierarchy = node.get("hier") or node.get("name")
            if not hierarchy:
                continue
            hierarchy = str(hierarchy)
            instance_name = node.get("origName") or hierarchy.rsplit(".", 1)[-1]

            module_name = None
            module_pointer = node.get("modp")
            if module_pointer is not None:
                module_name = module_by_addr.get(str(module_pointer))
            module_name = module_name or node.get("modName") or node.get("submodname")

            instances.append(
                {
                    "path": hierarchy,
                    "name": str(instance_name),
                    "module": str(module_name) if module_name else None,
                    "top": hierarchy == top,
                    "location": _decode_location(node.get("loc"), files, root),
                    "generate_scopes": [],
                }
            )

    modules, instances = _deduplicate(modules, instances)
    ports = sorted(
        ports,
        key=lambda item: (
            item["module"],
            str(item.get("module_elaborated_name") or ""),
            item["name"],
            item["direction"],
        ),
    )
    return {
        "modules": modules,
        "instances": instances,
        "ports": ports,
        "port_evidence": {
            "status": "NORMALIZED",
            "source_format": "json",
            "contract": "verilator_module_var_io_direction",
        },
    }


def parse_verilator_xml(
    xml_path: str | Path,
    *,
    project_root: str | Path,
    top: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    document = ET.parse(xml_path).getroot()

    files: dict[str, dict[str, Any]] = {}
    for item in document.findall("./files/file"):
        file_id = item.get("id")
        if file_id:
            filename = item.get("filename") or item.get("name")
            files[str(file_id)] = {"filename": filename, "realpath": filename}

    modules: list[dict[str, Any]] = []
    for node in document.findall(".//netlist/module"):
        name = node.get("origName") or node.get("name")
        if not name:
            continue
        modules.append(
            {
                "name": name,
                "elaborated_name": node.get("name"),
                "top": node.get("topModule") in {"1", "true", "True"} or name == top,
                "location": _decode_location(node.get("loc"), files, root),
            }
        )

    top_module = next((module for module in modules if module["top"]), None)
    instances: list[dict[str, Any]] = [
        {
            "path": top,
            "name": top,
            "module": top_module["name"] if top_module else top,
            "top": True,
            "location": top_module["location"] if top_module else None,
        }
    ]

    for node in document.findall(".//cell"):
        hierarchy = node.get("hier") or node.get("name")
        if not hierarchy:
            continue
        hierarchy = str(hierarchy)
        instance_name = node.get("name") or hierarchy.rsplit(".", 1)[-1]
        instances.append(
            {
                "path": hierarchy,
                "name": instance_name,
                "module": node.get("submodname"),
                "top": hierarchy == top,
                "location": _decode_location(node.get("loc"), files, root),
            }
        )

    modules, instances = _deduplicate(modules, instances)
    return {
        "modules": modules,
        "instances": instances,
        "ports": [],
        "port_evidence": {
            "status": "UNAVAILABLE",
            "source_format": "xml",
            "reason": "legacy_xml_port_schema_not_normalized",
        },
    }


def hierarchy_lines(index: dict[str, Any]) -> list[str]:
    top = str(index.get("top") or "")
    lines: list[str] = []
    for instance in index.get("instances", []):
        path = str(instance.get("path") or "")
        if not path:
            continue

        if path == top:
            depth = 0
        elif top and path.startswith(top + "."):
            depth = path[len(top) + 1 :].count(".") + 1
        else:
            depth = path.count(".")

        name = instance.get("name") or path.rsplit(".", 1)[-1]
        module = instance.get("module") or "?"
        lines.append(f"{'  ' * depth}{name}: {module}")
    return lines


def write_elaborated_index(
    project: ProjectConfig,
    backend: VerilatorBackend | None = None,
) -> dict[str, Any]:
    if project.simulator != "verilator":
        raise RuntimeError(
            "Elaborated hierarchy is currently implemented with Verilator only."
        )

    backend = backend or VerilatorBackend()
    source_index = write_design_index(project)
    out_dir = (project.root / ".zddv" / "design").resolve()
    export = backend.export_design_tree(project, out_dir)

    if export["format"] == "json":
        parsed = parse_verilator_json(
            export["ast"],
            export["meta"],
            project_root=project.root,
            top=project.top,
        )
    elif export["format"] == "xml":
        parsed = parse_verilator_xml(
            export["ast"],
            project_root=project.root,
            top=project.top,
        )
    else:
        raise RuntimeError(f"Unsupported design-tree format: {export['format']}")

    result = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "top": project.top,
        "simulator": project.simulator,
        "simulator_version": export["simulator_version"],
        "source_format": export["format"],
        "source_index": source_index["path"],
        "design_fingerprint": design_revision_fingerprint(project),
        "modules": parsed["modules"],
        "instances": parsed["instances"],
        "ports": parsed["ports"],
        "port_evidence": parsed["port_evidence"],
        "summary": {
            "modules": len(parsed["modules"]),
            "instances": len(parsed["instances"]),
            "ports": len(parsed["ports"]),
        },
        "tool_log": export["log"],
    }

    path = out_dir / "elaborated.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    hierarchy_path = out_dir / "elaborated-hierarchy.txt"
    hierarchy_path.write_text("\n".join(hierarchy_lines(result)) + "\n", encoding="utf-8")
    return {
        **result,
        "path": str(path),
        "hierarchy_path": str(hierarchy_path),
    }
