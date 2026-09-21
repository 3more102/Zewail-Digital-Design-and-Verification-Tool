from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Iterable

from zddv.config import ProjectConfig


_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|::|#|\(|\)|;|,")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class _Token:
    value: str
    line: int


@dataclass
class _Module:
    name: str
    source: Path
    line: int
    body: list[_Token]
    instances: list[dict]


def _mask_non_code(text: str) -> str:
    """Mask comments and strings while preserving newlines and character offsets."""
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


def _tokens(text: str) -> list[_Token]:
    masked = _mask_non_code(text)
    result: list[_Token] = []
    line = 1
    cursor = 0
    for match in _TOKEN_RE.finditer(masked):
        line += masked.count("\n", cursor, match.start())
        cursor = match.start()
        result.append(_Token(match.group(0), line))
    return result


def _is_identifier(value: str) -> bool:
    return bool(_IDENTIFIER_RE.match(value))


def _skip_balanced(tokens: list[_Token], start: int) -> int | None:
    if start >= len(tokens) or tokens[start].value != "(":
        return None
    depth = 0
    for index in range(start, len(tokens)):
        value = tokens[index].value
        if value == "(":
            depth += 1
        elif value == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _parse_modules(path: Path) -> list[_Module]:
    text = path.read_text(encoding="utf-8", errors="replace")
    tokens = _tokens(text)
    modules: list[_Module] = []
    i = 0

    while i < len(tokens):
        if tokens[i].value != "module":
            i += 1
            continue

        j = i + 1
        if j < len(tokens) and tokens[j].value in {"automatic", "static"}:
            j += 1
        if j >= len(tokens) or not _is_identifier(tokens[j].value):
            i += 1
            continue

        name = tokens[j].value
        end = j + 1
        while end < len(tokens) and tokens[end].value != "endmodule":
            end += 1
        if end >= len(tokens):
            raise ValueError(f"Unterminated module '{name}' in {path}")

        modules.append(
            _Module(
                name=name,
                source=path,
                line=tokens[j].line,
                body=tokens[j + 1 : end],
                instances=[],
            )
        )
        i = end + 1

    return modules


def _find_instances(module: _Module, known_modules: set[str]) -> list[dict]:
    tokens = module.body
    found: list[dict] = []
    i = 0

    while i < len(tokens):
        module_type = tokens[i].value
        if module_type not in known_modules:
            i += 1
            continue

        j = i + 1
        if j < len(tokens) and tokens[j].value == "#":
            j += 1
            after_params = _skip_balanced(tokens, j)
            if after_params is None:
                i += 1
                continue
            j = after_params

        parsed_any = False
        while j + 1 < len(tokens):
            if not _is_identifier(tokens[j].value) or tokens[j + 1].value != "(":
                break

            instance_name = tokens[j].value
            after_ports = _skip_balanced(tokens, j + 1)
            if after_ports is None:
                break

            found.append(
                {
                    "name": instance_name,
                    "module": module_type,
                    "source": str(module.source),
                    "line": tokens[j].line,
                }
            )
            parsed_any = True
            j = after_ports
            if j < len(tokens) and tokens[j].value == ",":
                j += 1
                continue
            break

        i = j if parsed_any else i + 1

    return found


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _hierarchy_node(
    modules: dict[str, _Module],
    module_name: str,
    *,
    instance_name: str,
    path: str,
    source: str,
    line: int,
    stack: tuple[str, ...],
) -> dict:
    cycle = module_name in stack
    node = {
        "instance": instance_name,
        "module": module_name,
        "path": path,
        "source": source,
        "line": line,
        "cycle": cycle,
        "children": [],
    }
    if cycle:
        return node

    module = modules[module_name]
    for instance in module.instances:
        child_path = f"{path}.{instance['name']}"
        node["children"].append(
            _hierarchy_node(
                modules,
                instance["module"],
                instance_name=instance["name"],
                path=child_path,
                source=instance["source"],
                line=instance["line"],
                stack=(*stack, module_name),
            )
        )
    return node


def _walk_hierarchy(node: dict) -> Iterable[dict]:
    yield node
    for child in node.get("children", []):
        yield from _walk_hierarchy(child)


def build_design_index(project: ProjectConfig) -> dict:
    source_files = project.source_files()
    if not source_files:
        raise RuntimeError("No RTL/testbench sources matched the project configuration.")

    modules_list: list[_Module] = []
    for path in source_files:
        modules_list.extend(_parse_modules(path))

    modules: dict[str, _Module] = {}
    for module in modules_list:
        if module.name in modules:
            first = modules[module.name]
            raise ValueError(
                f"Duplicate module '{module.name}' in {first.source} and {module.source}"
            )
        modules[module.name] = module

    known = set(modules)
    for module in modules.values():
        module.instances = _find_instances(module, known)

    if project.top not in modules:
        raise ValueError(
            f"Top module '{project.top}' was not found in the configured sources."
        )

    serialized_modules: list[dict] = []
    for name in sorted(modules):
        module = modules[name]
        serialized_modules.append(
            {
                "name": module.name,
                "source": _relative(module.source, project.root),
                "line": module.line,
                "instances": [
                    {
                        **instance,
                        "source": _relative(Path(instance["source"]), project.root),
                    }
                    for instance in module.instances
                ],
            }
        )

    top_module = modules[project.top]
    hierarchy = _hierarchy_node(
        modules,
        project.top,
        instance_name=project.top,
        path=project.top,
        source=_relative(top_module.source, project.root),
        line=top_module.line,
        stack=(),
    )

    for node in _walk_hierarchy(hierarchy):
        node["source"] = _relative(Path(node["source"]), project.root)

    hierarchy_nodes = list(_walk_hierarchy(hierarchy))
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project.name,
        "top": project.top,
        "sources": [_relative(path, project.root) for path in source_files],
        "modules": serialized_modules,
        "hierarchy": hierarchy,
        "stats": {
            "source_files": len(source_files),
            "modules": len(modules),
            "module_instances": sum(len(module.instances) for module in modules.values()),
            "hierarchy_nodes": len(hierarchy_nodes),
        },
    }


def write_design_index(project: ProjectConfig, index: dict | None = None) -> Path:
    payload = index or build_design_index(project)
    output = (project.root / ".zddv" / "index" / "design.json").resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output


def format_hierarchy(index: dict) -> str:
    root = index.get("hierarchy")
    if not isinstance(root, dict):
        return ""

    lines: list[str] = []

    def visit(node: dict, depth: int) -> None:
        suffix = " [cycle]" if node.get("cycle") else ""
        lines.append(
            f"{'  ' * depth}{node.get('instance')}: {node.get('module')}{suffix}"
        )
        for child in node.get("children", []):
            visit(child, depth + 1)

    visit(root, 0)
    return "\n".join(lines)
