from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from zddv.config import ProjectConfig
from zddv.simulator import VerilatorBackend


SCHEMA_VERSION = 1
_JSON_LOC = re.compile(r"([^,]+),(\\d+):(\\d+),(\\d+):(\\d+)")


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
        }
    ]

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
            }
        )

    modules, instances = _deduplicate(modules, instances)
    return {"modules": modules, "instances": instances}


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

    for node in document.findall(".//cells/cell"):
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
    return {"modules": modules, "instances": instances}


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
        "source_index": str((out_dir / "index.json").resolve()),
        "modules": parsed["modules"],
        "instances": parsed["instances"],
        "summary": {
            "modules": len(parsed["modules"]),
            "instances": len(parsed["instances"]),
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
