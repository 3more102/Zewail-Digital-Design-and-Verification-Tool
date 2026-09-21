from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable

from zddv.config import ProjectConfig
from zddv.simulator import VerilatorBackend


SCHEMA_VERSION = 1


def _project_path(path: str | Path | None, root: Path) -> str | None:
    if not path:
        return None
    value = str(path)
    if value.startswith("<") and value.endswith(">"):
        return value
    candidate = Path(value)
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        return str(resolved.relative_to(root.resolve()))
    except (OSError, ValueError):
        return value


def _location(loc: str | None, files: dict[str, dict[str, Any]], root: Path) -> dict[str, Any] | None:
    if not loc:
        return None

    match = re.fullmatch(r"([^,]+),(\\d+):(\\d+),(\\d+):(\\d+)", loc)
    if match:
        file_id, first_line, first_col, last_line, last_col = match.groups()
    else:
        parts = loc.split(",")
        if len(parts) != 5 or not all(part.isdigit() for part in parts[1:]):
            return {"raw": loc}
        file_id, first_line, last_line, first_col, last_col = parts

    file_info = files.get(file_id, {})
    filename = file_info.get("realpath") or file_info.get("filename")
    return {
        "file_id": file_id,
        "path": _project_path(filename, root),
        "line": int(first_line),
        "column": int(first_col),
        "end_line": int(last_line),
        "end_column": int(last_col),
    }


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _normalize_module_name(node: dict[str, Any]) -> str | None:
    return node.get("origName") or node.get("verilogName") or node.get("name")


def parse_verilator_json(
    ast_path: str | Path,
    meta_path: str | Path,
    *,
    project_root: str | Path,
    top: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    ast = json.loads(Path(ast_path).read_text(encoding="utf-8"))
    meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    files = dict(meta.get("files", {}))

    nodes = list(_walk_json(ast))
    module_by_addr: dict[str, str] = {}
    modules: list[dict[str, Any]] = []

    for node in nodes:
        if node.get("type") != "MODULE":
            continue
        name = _normalize_module_name(node)
        if not name:
            continue
        addr = node.get("addr")
        if addr:
            module_by_addr[str(addr)] = str(name)
        modules.append(
            {
                "name": str(name),
                "elaborated_name": node.get("name"),
                "top": bool(node.get("topModule")) or str(name) == top,
                "location": _location(node.get("loc"), files, root),
            }
        )

    instances: list[dict[str, Any]] = []
    top_module = next((item for item in modules if item["top"]), None)
    instances.append(
        {
            "hierarchy": top,
            "name": top,
            "module": top_module["name"] if top_module else top,
            "top": True,
            "location": top_module["location"] if top_module else None,
        }
    )

    for node in nodes:
        if node.get("type") != "CELL":
            continue
        hierarchy = node.get("name") or node.get("hier")
        instance_name = node.get("origName")
        if not hierarchy:
            continue
        hierarchy = str(hierarchy)
        if not instance_name:
            instance_name = hierarchy.rsplit(".", 1)[-1]
        module_name = None
        if node.get("modp") is not None:
            module_name = module_by_addr.get(str(node.get("modp")))
        module_name = module_name or node.get("modName") or node.get("submodname")
        instances.append(
            {
                "hierarchy": hierarchy,
                "name": str(instance_name),
                "module": str(module_name) if module_name else None,
                "top": hierarchy == top,
                "location": _location(node.get("loc"), files, root),
            }
        )

    return _deduplicate_design(modules, instances)


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
            files[file_id] = {"filename": filename, "realpath": filename}

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
                "location": _location(node.get("loc"), files, root),
            }
        )

    top_module = next((item for item in modules if item["top"]), None)
    instances: list[dict[str, Any]] = [
        {
            "hierarchy": top,
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
        name = node.get("name") or hierarchy.rsplit(".", 1)[-1]
        instances.append(
            {
                "hierarchy": hierarchy,
                "name": name,
                "module": node.get("submodname"),
                "top": hierarchy == top,
                "location": _location(node.get("loc"), files, root),
            }
        )

    return _deduplicate_design(modules, instances)


def _deduplicate_design(
    modules: list[dict[str, Any]],
    instances: list[dict[str, Any]],
) -> dict[str, Any]:
    module_map: dict[tuple[str, str | None, int | None], dict[str, Any]] = {}
    for module in modules:
        location = module.get("location") or {}
        key = (module["name"], location.get("path"), location.get("line"))
        module_map[key] = module

    instance_map: dict[str, dict[str, Any]] = {}
    for instance in instances:
        hierarchy = instance["hierarchy"]
        current = instance_map.get(hierarchy)
        if current is None or (current.get("module") is None and instance.get("module") is not None):
            instance_map[hierarchy] = instance

    return {
        "modules": sorted(module_map.values(), key=lambda item: (not item["top"], item["name"])),
        "instances": sorted(
            instance_map.values(),
            key=lambda item: (item["hierarchy"].count("."), item["hierarchy"]),
        ),
    }


def build_design_index(
    project: ProjectConfig,
    backend: VerilatorBackend | None = None,
) -> dict[str, Any]:
    if project.simulator != "verilator":
        raise RuntimeError("Design indexing is currently implemented with Verilator only.")

    backend = backend or VerilatorBackend()
    output_dir = (project.root / ".zddv" / "index").resolve()
    export = backend.export_design_tree(project, output_dir)

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

    sources = []
    for path in project.source_files():
        try:
            display = str(path.resolve().relative_to(project.root.resolve()))
        except ValueError:
            display = str(path)
        sources.append(
            {
                "path": display,
                "realpath": str(path.resolve()),
                "suffix": path.suffix.lower(),
            }
        )

    index = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "top": project.top,
        "simulator": project.simulator,
        "simulator_version": export["simulator_version"],
        "source_format": export["format"],
        "sources": sources,
        "modules": parsed["modules"],
        "instances": parsed["instances"],
        "summary": {
            "source_files": len(sources),
            "modules": len(parsed["modules"]),
            "instances": len(parsed["instances"]),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "design.json"
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    hierarchy_path = output_dir / "hierarchy.txt"
    hierarchy_path.write_text("\n".join(format_hierarchy(index)) + "\n", encoding="utf-8")

    return {
        **index,
        "path": str(index_path),
        "hierarchy_path": str(hierarchy_path),
        "tool_log": export["log"],
    }


def load_design_index(project: ProjectConfig) -> dict[str, Any]:
    path = (project.root / ".zddv" / "index" / "design.json").resolve()
    if not path.exists():
        raise FileNotFoundError(f"Design index not found at {path}. Run 'zddv index' first.")
    return json.loads(path.read_text(encoding="utf-8"))


def format_hierarchy(index: dict[str, Any], *, max_depth: int | None = None) -> list[str]:
    top = str(index.get("top") or "")
    lines: list[str] = []
    for instance in index.get("instances", []):
        hierarchy = str(instance.get("hierarchy") or "")
        if not hierarchy:
            continue
        if hierarchy == top:
            depth = 0
        elif top and hierarchy.startswith(top + "."):
            depth = hierarchy[len(top) + 1 :].count(".") + 1
        else:
            depth = hierarchy.count(".")
        if max_depth is not None and depth > max_depth:
            continue
        module = instance.get("module") or "?"
        name = instance.get("name") or hierarchy.rsplit(".", 1)[-1]
        lines.append(f"{'  ' * depth}{name} ({module})")
    return lines
