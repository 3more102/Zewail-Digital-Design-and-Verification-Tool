from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.design_index import (
    _find_unit_regions,
    _line_number,
    _mask_non_code,
    _skip_balanced,
    _skip_space,
)


_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_PORT_DIR_RE = re.compile(
    r"\\b(?P<direction>input|output|inout)\\b"
    r"(?P<body>.*?)"
    r"(?=(?:\\binput\\b|\\boutput\\b|\\binout\\b)|[);])",
    re.DOTALL,
)
_ASSIGN_RE = re.compile(
    r"\\bassign\\s+"
    r"(?P<lhs>[A-Za-z_$][A-Za-z0-9_$]*(?:\\s*\\[[^\\]]+\\])?)"
    r"\\s*=\\s*(?P<rhs>[^;]+);",
    re.DOTALL,
)
_PROC_ASSIGN_RE = re.compile(
    r"(?P<lhs>[A-Za-z_$][A-Za-z0-9_$]*(?:\\s*\\[[^\\]]+\\])?)"
    r"\\s*(?P<op><=|=(?!=))\\s*(?P<rhs>[^;]+);",
    re.DOTALL,
)
_NUMBER_RE = re.compile(
    r"\\b\\d+(?:'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+)?\\b"
)
_KEYWORDS = {
    "always", "always_comb", "always_ff", "always_latch", "and", "assign",
    "automatic", "begin", "bit", "case", "casex", "casez", "else", "end",
    "endcase", "endfunction", "endgenerate", "endmodule", "endtask", "for",
    "foreach", "function", "generate", "if", "inout", "input", "int",
    "integer", "logic", "module", "or", "output", "parameter", "reg",
    "repeat", "signed", "static", "task", "time", "unsigned", "wire", "while",
}


def _relative_path(project: ProjectConfig, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.root.resolve()))
    except ValueError:
        return str(path.resolve())


def _base_signal(text: str) -> str | None:
    match = _IDENTIFIER_RE.match(text.strip())
    return match.group(0) if match else None


def _expression_identifiers(expression: str) -> list[str]:
    """Return conservative local identifiers referenced by an expression."""
    cleaned = _NUMBER_RE.sub(" ", expression)
    found: list[str] = []
    seen: set[str] = set()

    for match in _IDENTIFIER_RE.finditer(cleaned):
        token = match.group(0)
        if token in _KEYWORDS or token.startswith("$"):
            continue
        if cleaned[match.end():].lstrip()[:1] == "(":
            continue
        if token not in seen:
            seen.add(token)
            found.append(token)
    return found


def _port_names(body: str) -> list[str]:
    names: list[str] = []
    ignored = {
        "wire", "logic", "reg", "bit", "signed", "unsigned",
        "var", "integer", "int",
    }
    for chunk in body.split(","):
        identifiers = [item for item in _IDENTIFIER_RE.findall(chunk) if item not in ignored]
        if identifiers:
            names.append(identifiers[-1])
    return names


def _scan_ports(
    masked: str,
    *,
    region_start: int,
    region_end: int,
) -> list[dict[str, Any]]:
    text = masked[region_start:region_end]
    ports: list[dict[str, Any]] = []
    seen: set[str] = set()

    for match in _PORT_DIR_RE.finditer(text):
        direction = match.group("direction")
        for name in _port_names(match.group("body")):
            if name in seen:
                continue
            seen.add(name)
            ports.append(
                {
                    "name": name,
                    "direction": direction,
                    "line": _line_number(masked, region_start + match.start()),
                }
            )
    return ports


def _add_ref(
    nets: dict[tuple[str, str], dict[str, Any]],
    *,
    unit: str,
    signal: str,
    role: str,
    kind: str,
    file: str,
    line: int,
    detail: str,
) -> None:
    key = (unit, signal)
    entry = nets.setdefault(
        key,
        {"unit": unit, "signal": signal, "drivers": [], "loads": []},
    )
    ref = {"kind": kind, "file": file, "line": line, "detail": detail}
    if ref not in entry[role]:
        entry[role].append(ref)


def _scan_named_connections(
    body: str,
    *,
    absolute_body_start: int,
    masked: str,
    known_units: set[str],
) -> list[dict[str, Any]]:
    if not known_units:
        return []

    names = sorted(known_units, key=len, reverse=True)
    type_re = re.compile(
        r"(?<![A-Za-z0-9_$])(?P<type>"
        + "|".join(re.escape(name) for name in names)
        + r")(?![A-Za-z0-9_$])"
    )
    connections: list[dict[str, Any]] = []

    for match in type_re.finditer(body):
        pos = _skip_space(body, match.end())
        if pos < len(body) and body[pos] == "#":
            pos = _skip_space(body, pos + 1)
            after_params = _skip_balanced(body, pos, "(", ")")
            if after_params is None:
                continue
            pos = _skip_space(body, after_params)

        instance_match = _IDENTIFIER_RE.match(body, pos)
        if instance_match is None:
            continue
        instance = instance_match.group(0)
        pos = _skip_space(body, instance_match.end())

        while pos < len(body) and body[pos] == "[":
            after_dim = _skip_balanced(body, pos, "[", "]")
            if after_dim is None:
                break
            pos = _skip_space(body, after_dim)

        if pos >= len(body) or body[pos] != "(":
            continue
        end = _skip_balanced(body, pos, "(", ")")
        if end is None:
            continue

        connection_text = body[pos + 1:end - 1]
        i = 0
        while i < len(connection_text):
            dot = connection_text.find(".", i)
            if dot < 0:
                break
            port_match = _IDENTIFIER_RE.match(connection_text, dot + 1)
            if port_match is None:
                i = dot + 1
                continue
            port = port_match.group(0)
            cursor = _skip_space(connection_text, port_match.end())
            if cursor >= len(connection_text) or connection_text[cursor] != "(":
                i = port_match.end()
                continue
            conn_end = _skip_balanced(connection_text, cursor, "(", ")")
            if conn_end is None:
                break
            expression = connection_text[cursor + 1:conn_end - 1].strip()
            absolute = absolute_body_start + pos + 1 + dot
            connections.append(
                {
                    "type": match.group("type"),
                    "instance": instance,
                    "port": port,
                    "expression": expression,
                    "line": _line_number(masked, absolute),
                }
            )
            i = conn_end

    return connections


def build_connectivity_index(project: ProjectConfig) -> dict[str, Any]:
    """Build conservative source-level driver/load connectivity."""
    parsed: list[dict[str, Any]] = []

    for path in project.source_files():
        raw = path.read_text(encoding="utf-8", errors="replace")
        masked = _mask_non_code(raw)
        rel = _relative_path(project, path)
        for region in _find_unit_regions(masked):
            if region.kind not in {"module", "interface", "program"}:
                continue
            parsed.append(
                {
                    "file": rel,
                    "masked": masked,
                    "region": region,
                    "ports": _scan_ports(
                        masked,
                        region_start=region.start,
                        region_end=region.end,
                    ),
                }
            )

    port_maps: dict[str, dict[str, str]] = {
        item["region"].name: {
            port["name"]: port["direction"] for port in item["ports"]
        }
        for item in parsed
    }
    known_units = set(port_maps)
    nets: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved_named_connections = 0

    for item in parsed:
        region = item["region"]
        masked = item["masked"]
        file = item["file"]
        unit = region.name
        body = masked[region.body_start:region.end]

        for port in item["ports"]:
            name = port["name"]
            direction = port["direction"]
            if direction in {"input", "inout"}:
                _add_ref(
                    nets, unit=unit, signal=name, role="drivers",
                    kind=f"{direction}-port", file=file, line=port["line"],
                    detail=f"{direction} boundary port {name}",
                )
            if direction in {"output", "inout"}:
                _add_ref(
                    nets, unit=unit, signal=name, role="loads",
                    kind=f"{direction}-port", file=file, line=port["line"],
                    detail=f"{direction} boundary port {name}",
                )

        continuous_spans: list[tuple[int, int]] = []
        for match in _ASSIGN_RE.finditer(body):
            continuous_spans.append(match.span())
            lhs = _base_signal(match.group("lhs"))
            line = _line_number(masked, region.body_start + match.start())
            if lhs:
                _add_ref(
                    nets, unit=unit, signal=lhs, role="drivers",
                    kind="continuous-assignment", file=file, line=line,
                    detail=f"assign {match.group('lhs').strip()} = ...",
                )
            for signal in _expression_identifiers(match.group("rhs")):
                _add_ref(
                    nets, unit=unit, signal=signal, role="loads",
                    kind="continuous-assignment", file=file, line=line,
                    detail=f"read by assign to {lhs or match.group('lhs').strip()}",
                )

        for match in _PROC_ASSIGN_RE.finditer(body):
            if any(start <= match.start() < end for start, end in continuous_spans):
                continue
            lhs = _base_signal(match.group("lhs"))
            if lhs is None:
                continue
            line = _line_number(masked, region.body_start + match.start())
            _add_ref(
                nets, unit=unit, signal=lhs, role="drivers",
                kind="procedural-assignment", file=file, line=line,
                detail=f"{match.group('lhs').strip()} {match.group('op')} ...",
            )
            for signal in _expression_identifiers(match.group("rhs")):
                _add_ref(
                    nets, unit=unit, signal=signal, role="loads",
                    kind="procedural-assignment", file=file, line=line,
                    detail=f"read by assignment to {lhs}",
                )

        for connection in _scan_named_connections(
            body,
            absolute_body_start=region.body_start,
            masked=masked,
            known_units=known_units,
        ):
            direction = port_maps.get(connection["type"], {}).get(connection["port"])
            if direction is None:
                unresolved_named_connections += 1
                continue

            for signal in _expression_identifiers(connection["expression"]):
                detail = (
                    f"{connection['instance']}.{connection['port']} "
                    f"({connection['type']} {direction})"
                )
                if direction in {"output", "inout"}:
                    _add_ref(
                        nets, unit=unit, signal=signal, role="drivers",
                        kind="instance-port", file=file, line=connection["line"],
                        detail=detail,
                    )
                if direction in {"input", "inout"}:
                    _add_ref(
                        nets, unit=unit, signal=signal, role="loads",
                        kind="instance-port", file=file, line=connection["line"],
                        detail=detail,
                    )

    entries = list(nets.values())
    for entry in entries:
        entry["drivers"].sort(
            key=lambda ref: (ref["file"], ref["line"], ref["kind"], ref["detail"])
        )
        entry["loads"].sort(
            key=lambda ref: (ref["file"], ref["line"], ref["kind"], ref["detail"])
        )
    entries.sort(key=lambda entry: (entry["unit"], entry["signal"]))

    return {
        "schema_version": 1,
        "project": project.name,
        "analysis": "source-level-conservative",
        "nets": entries,
        "summary": {
            "nets": len(entries),
            "drivers": sum(len(entry["drivers"]) for entry in entries),
            "loads": sum(len(entry["loads"]) for entry in entries),
            "unresolved_named_connections": unresolved_named_connections,
        },
        "limitations": [
            "Source-level analysis only; generate/parameter elaboration is not resolved.",
            "Named instance ports are direction-aware when the child unit is indexed.",
            "Complex aliasing, interfaces/modports, force/release, DPI, and dynamic references are not fully resolved.",
        ],
    }


def query_connectivity(
    index: dict[str, Any],
    signal: str,
    *,
    unit: str | None = None,
) -> list[dict[str, Any]]:
    matches = [
        entry
        for entry in index.get("nets", [])
        if entry.get("signal") == signal
        and (unit is None or entry.get("unit") == unit)
    ]
    if not matches:
        qualifier = f" in unit '{unit}'" if unit else ""
        raise RuntimeError(f"Signal '{signal}' was not found{qualifier}.")
    return matches


def write_connectivity_index(project: ProjectConfig) -> dict[str, Any]:
    index = build_connectivity_index(project)
    out_dir = (project.root / ".zddv" / "design").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "connectivity.json"
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return {**index, "path": str(path)}
