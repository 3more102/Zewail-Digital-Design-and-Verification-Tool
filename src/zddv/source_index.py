from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig


_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_DESIGN_UNIT_RE = re.compile(
    rf"\b(module|interface|package|program)\s+(?:(?:automatic|static)\s+)?({_IDENTIFIER})\b"
)
_MODULE_RE = re.compile(
    rf"\bmodule\s+(?:(?:automatic|static)\s+)?(?P<name>{_IDENTIFIER})\b"
    r"(?P<body>.*?)\bendmodule\b",
    re.DOTALL,
)


def _mask_comments_and_strings(text: str) -> str:
    """Mask comments and strings while preserving offsets and newlines."""
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

        if ch == "\\" and i + 1 < len(chars):
            if chars[i] != "\n":
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
        return path.resolve().as_posix()


def _instance_pattern(module_name: str) -> re.Pattern[str]:
    parameter_block = r"(?:#\s*\((?:[^()]|\([^()]*\))*\)\s*)?"
    return re.compile(
        rf"\b{re.escape(module_name)}\s*"
        + parameter_block
        + rf"(?P<instance>{_IDENTIFIER})\s*"
        + r"(?:\[[^\]]+\]\s*)?\(",
        re.DOTALL,
    )


def _find_instances(
    body: str,
    module_names: list[str],
    body_offset: int,
    whole_text: str,
) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []
    for module_name in module_names:
        for match in _instance_pattern(module_name).finditer(body):
            instances.append(
                {
                    "module": module_name,
                    "instance": match.group("instance"),
                    "line": _line_number(whole_text, body_offset + match.start()),
                    "resolved": True,
                }
            )
    instances.sort(key=lambda item: (item["line"], item["instance"], item["module"]))
    return instances


def scan_project_sources(project: ProjectConfig) -> dict[str, Any]:
    file_records: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    parsed: list[tuple[Path, str, str]] = []

    for path in project.source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        masked = _mask_comments_and_strings(text)
        rel = _relative_path(project, path)
        parsed.append((path, text, masked))

        line_count = text.count("\n")
        if text and not text.endswith("\n"):
            line_count += 1
        file_records.append({"path": rel, "lines": line_count})

        for match in _DESIGN_UNIT_RE.finditer(masked):
            symbols.append(
                {
                    "kind": match.group(1),
                    "name": match.group(2),
                    "file": rel,
                    "line": _line_number(masked, match.start()),
                }
            )

    module_names = sorted(
        {item["name"] for item in symbols if item["kind"] == "module"}
    )
    modules: list[dict[str, Any]] = []
    for path, _text, masked in parsed:
        rel = _relative_path(project, path)
        for match in _MODULE_RE.finditer(masked):
            modules.append(
                {
                    "name": match.group("name"),
                    "file": rel,
                    "line": _line_number(masked, match.start()),
                    "instances": _find_instances(
                        match.group("body"),
                        module_names,
                        match.start("body"),
                        masked,
                    ),
                }
            )

    symbols.sort(
        key=lambda item: (item["file"], item["line"], item["kind"], item["name"])
    )
    modules.sort(key=lambda item: (item["name"], item["file"], item["line"]))
    return {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "files": file_records,
        "symbols": symbols,
        "modules": modules,
    }


def _module_map(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for item in index.get("modules", []):
        name = item["name"]
        if name in modules:
            duplicates.add(name)
        else:
            modules[name] = item
    if duplicates:
        names = ", ".join(sorted(duplicates))
        raise ValueError(
            f"Duplicate module definitions prevent hierarchy resolution: {names}"
        )
    return modules


def build_hierarchy(
    index: dict[str, Any],
    top: str | None = None,
    *,
    max_depth: int = 64,
) -> dict[str, Any]:
    top_name = top or index.get("top")
    if not top_name:
        raise ValueError("No top module specified.")
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")

    modules = _module_map(index)
    if top_name not in modules:
        known = ", ".join(sorted(modules)) or "none"
        raise ValueError(f"Top module '{top_name}' was not found. Known modules: {known}")

    def expand(
        module_name: str,
        instance_name: str,
        path: str,
        depth: int,
        ancestry: tuple[str, ...],
        instantiated_at: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        module = modules[module_name]
        node: dict[str, Any] = {
            "module": module_name,
            "instance": instance_name,
            "path": path,
            "definition": {"file": module["file"], "line": module["line"]},
            "children": [],
        }
        if instantiated_at is not None:
            node["instantiated_at"] = instantiated_at
        if depth >= max_depth:
            node["truncated"] = True
            return node

        for child in module.get("instances", []):
            child_path = f"{path}.{child['instance']}"
            location = {"file": module["file"], "line": child["line"]}
            if child["module"] in ancestry:
                target = modules[child["module"]]
                node["children"].append(
                    {
                        "module": child["module"],
                        "instance": child["instance"],
                        "path": child_path,
                        "definition": {
                            "file": target["file"],
                            "line": target["line"],
                        },
                        "instantiated_at": location,
                        "children": [],
                        "recursive": True,
                    }
                )
            else:
                node["children"].append(
                    expand(
                        child["module"],
                        child["instance"],
                        child_path,
                        depth + 1,
                        (*ancestry, child["module"]),
                        location,
                    )
                )
        return node

    return expand(top_name, top_name, top_name, 0, (top_name,))


def hierarchy_lines(hierarchy: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    def walk(
        node: dict[str, Any],
        prefix: str,
        is_last: bool,
        *,
        root: bool = False,
    ) -> None:
        label = f"{node['instance']} : {node['module']}"
        if node.get("recursive"):
            label += " [recursive]"
        if node.get("truncated"):
            label += " [max-depth]"
        lines.append(
            label if root else f"{prefix}{'└─ ' if is_last else '├─ '}{label}"
        )

        children = node.get("children", [])
        child_prefix = prefix if root else prefix + ("   " if is_last else "│  ")
        for idx, child in enumerate(children):
            walk(child, child_prefix, idx == len(children) - 1)

    walk(hierarchy, "", True, root=True)
    return lines


def write_source_index(
    project: ProjectConfig,
    *,
    max_depth: int = 64,
) -> dict[str, Any]:
    index = scan_project_sources(project)
    hierarchy = build_hierarchy(index, max_depth=max_depth)

    output_dir = project.root / ".zddv" / "index"
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "source_index.json"
    hierarchy_path = output_dir / "hierarchy.json"

    generated_at = datetime.now(timezone.utc).isoformat()
    index_payload = {**index, "generated_at": generated_at}
    hierarchy_payload = {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "generated_at": generated_at,
        "hierarchy": hierarchy,
    }
    index_path.write_text(
        json.dumps(index_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    hierarchy_path.write_text(
        json.dumps(hierarchy_payload, indent=2) + "\n",
        encoding="utf-8",
    )

    return {
        "index": index_payload,
        "hierarchy": hierarchy,
        "index_path": index_path,
        "hierarchy_path": hierarchy_path,
        "file_count": len(index["files"]),
        "symbol_count": len(index["symbols"]),
        "module_count": len(index["modules"]),
        "instance_count": sum(
            len(item.get("instances", [])) for item in index["modules"]
        ),
    }
