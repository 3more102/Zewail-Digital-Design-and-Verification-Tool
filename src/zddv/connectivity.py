from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.design_index import _mask_non_code, build_design_index


_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_PORT_DIRECTION = re.compile(r"\b(input|output|inout)\b")
_DECLARATION = re.compile(r"\b(?:logic|wire|reg|bit)\b(?P<body>[^;]*);", re.DOTALL)
_CONT_ASSIGN = re.compile(
    r"\bassign\s+(?P<lhs>[^;=]+?)\s*=\s*(?P<rhs>[^;]+);",
    re.DOTALL,
)
_PROC_ASSIGN = re.compile(
    r"(?m)^[ \t]*(?P<lhs>[A-Za-z_$][A-Za-z0-9_$]*(?:\s*\[[^\]]+\])?)"
    r"\s*(?P<op><=|=(?!=))\s*(?P<rhs>[^;]+);"
)

_TYPE_WORDS = {
    "input", "output", "inout", "logic", "wire", "reg", "bit", "signed",
    "unsigned", "var", "tri", "integer", "int", "longint", "shortint",
    "byte", "time",
}


def _skip_space(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _skip_balanced(
    text: str,
    pos: int,
    opening: str = "(",
    closing: str = ")",
) -> int | None:
    if pos >= len(text) or text[pos] != opening:
        return None
    depth = 0
    for index in range(pos, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    paren = bracket = brace = 0
    for index, ch in enumerate(text):
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket = max(0, bracket - 1)
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace = max(0, brace - 1)
        elif ch == "," and paren == bracket == brace == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _unit_source(
    project: ProjectConfig,
    unit: dict[str, Any],
) -> tuple[str, str, int]:
    path = Path(unit["file"])
    if not path.is_absolute():
        path = project.root / path
    lines = path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines(keepends=True)
    snippet = "".join(lines[unit["line"] - 1 : unit["end_line"]])
    masked = _mask_non_code(snippet)

    match = re.search(
        rf"\b(?:module|interface|program)\s+"
        rf"(?:(?:automatic|static)\s+)?{re.escape(unit['name'])}\b",
        masked,
    )
    if match is None:
        return masked, "", 0

    pos = _skip_space(masked, match.end())
    if pos < len(masked) and masked[pos] == "#":
        pos = _skip_space(masked, pos + 1)
        after = _skip_balanced(masked, pos)
        if after is not None:
            pos = _skip_space(masked, after)

    header_ports = ""
    if pos < len(masked) and masked[pos] == "(":
        after = _skip_balanced(masked, pos)
        if after is not None:
            header_ports = masked[pos + 1 : after - 1]
            pos = after

    semicolon = masked.find(";", pos)
    body_offset = semicolon + 1 if semicolon >= 0 else pos
    return masked, header_ports, body_offset


def _last_identifier(text: str) -> str | None:
    before_default = text.split("=", 1)[0]
    matches = [
        item.group(0)
        for item in _IDENTIFIER.finditer(before_default)
        if item.group(0) not in _TYPE_WORDS
        and not (
            item.start() > 0
            and before_default[item.start() - 1] == "'"
        )
    ]
    return matches[-1] if matches else None


def _parse_ports(header: str) -> list[dict[str, str]]:
    ports: list[dict[str, str]] = []
    current_direction: str | None = None
    for part in _split_top_level(header):
        direction_match = _PORT_DIRECTION.search(part)
        if direction_match is not None:
            current_direction = direction_match.group(1)
        name = _last_identifier(part)
        if name is None:
            continue
        ports.append(
            {
                "name": name,
                "direction": current_direction or "unknown",
            }
        )
    return ports


def _declared_signals(body: str) -> set[str]:
    signals: set[str] = set()
    for match in _DECLARATION.finditer(body):
        for part in _split_top_level(match.group("body")):
            name = _last_identifier(part)
            if name:
                signals.add(name)
    return signals


def _signal_identifiers(
    expression: str,
    known: set[str],
) -> list[str]:
    found: list[str] = []
    for match in _IDENTIFIER.finditer(expression):
        value = match.group(0)
        if value not in known:
            continue
        if (
            match.start() > 0
            and expression[match.start() - 1] == "'"
        ):
            continue
        if value not in found:
            found.append(value)
    return found


def _find_instance_ports(
    body: str,
    *,
    type_name: str,
    instance_name: str,
) -> str | None:
    type_re = re.compile(
        rf"(?<![A-Za-z0-9_$]){re.escape(type_name)}"
        rf"(?![A-Za-z0-9_$])"
    )
    for match in type_re.finditer(body):
        pos = _skip_space(body, match.end())
        if pos < len(body) and body[pos] == "#":
            pos = _skip_space(body, pos + 1)
            after_params = _skip_balanced(body, pos)
            if after_params is None:
                continue
            pos = _skip_space(body, after_params)

        name_match = _IDENTIFIER.match(body, pos)
        if (
            name_match is None
            or name_match.group(0) != instance_name
        ):
            continue
        pos = _skip_space(body, name_match.end())
        while pos < len(body) and body[pos] == "[":
            after_dim = _skip_balanced(
                body,
                pos,
                "[",
                "]",
            )
            if after_dim is None:
                break
            pos = _skip_space(body, after_dim)

        if pos >= len(body) or body[pos] != "(":
            continue
        after_ports = _skip_balanced(body, pos)
        if after_ports is None:
            continue
        return body[pos + 1 : after_ports - 1]
    return None


def _parse_connections(
    port_text: str,
    child_ports: list[dict[str, str]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    positional = 0
    child_names = [port["name"] for port in child_ports]

    for part in _split_top_level(port_text):
        named = re.match(
            r"^\.\s*(?P<port>[A-Za-z_$][A-Za-z0-9_$]*)\s*"
            r"(?:\((?P<expr>.*)\))?$",
            part,
            re.DOTALL,
        )
        if named is not None:
            port_name = named.group("port")
            expression = named.group("expr")
            if expression is None:
                expression = port_name
            result.append(
                {
                    "port": port_name,
                    "expression": expression.strip(),
                }
            )
            continue

        if positional < len(child_names):
            result.append(
                {
                    "port": child_names[positional],
                    "expression": part.strip(),
                }
            )
        positional += 1

    return result


def _add(
    nets: dict[str, dict[str, list[dict[str, Any]]]],
    signal: str,
    role: str,
    evidence: dict[str, Any],
) -> None:
    bucket = nets.setdefault(
        signal,
        {"drivers": [], "loads": []},
    )[role]
    key = (
        evidence.get("kind"),
        evidence.get("file"),
        evidence.get("line"),
        evidence.get("detail"),
    )
    existing = {
        (
            item.get("kind"),
            item.get("file"),
            item.get("line"),
            item.get("detail"),
        )
        for item in bucket
    }
    if key not in existing:
        bucket.append(evidence)


def build_connectivity_index(
    project: ProjectConfig,
) -> dict[str, Any]:
    design = build_design_index(project)

    parsed: dict[str, dict[str, Any]] = {}
    for unit in design["units"]:
        if unit["kind"] not in {
            "module",
            "interface",
            "program",
        }:
            continue

        masked, header, body_offset = _unit_source(
            project,
            unit,
        )
        body = masked[body_offset:]
        ports = _parse_ports(header)
        known = {port["name"] for port in ports}
        known.update(_declared_signals(body))

        parsed[unit["name"]] = {
            "meta": unit,
            "masked": masked,
            "body": body,
            "body_offset": body_offset,
            "ports": ports,
            "signals": known,
        }

    output_units: list[dict[str, Any]] = []
    total_drivers = 0
    total_loads = 0

    for unit_name in sorted(parsed):
        item = parsed[unit_name]
        unit = item["meta"]
        body = item["body"]
        known: set[str] = item["signals"]
        nets: dict[
            str,
            dict[str, list[dict[str, Any]]],
        ] = {
            signal: {
                "drivers": [],
                "loads": [],
            }
            for signal in sorted(known)
        }

        for port in item["ports"]:
            evidence = {
                "kind": "port",
                "file": unit["file"],
                "line": unit["line"],
                "detail": (
                    f"{port['direction']} boundary "
                    f"{port['name']}"
                ),
            }
            if port["direction"] in {"input", "inout"}:
                _add(
                    nets,
                    port["name"],
                    "drivers",
                    evidence,
                )
            if port["direction"] in {"output", "inout"}:
                _add(
                    nets,
                    port["name"],
                    "loads",
                    evidence,
                )

        continuous_spans: list[tuple[int, int]] = []
        for match in _CONT_ASSIGN.finditer(body):
            continuous_spans.append(match.span())
            line = (
                unit["line"]
                + item["masked"].count(
                    "\n",
                    0,
                    item["body_offset"] + match.start(),
                )
            )
            lhs = _signal_identifiers(
                match.group("lhs"),
                known,
            )
            rhs = _signal_identifiers(
                match.group("rhs"),
                known,
            )
            for signal in lhs:
                _add(
                    nets,
                    signal,
                    "drivers",
                    {
                        "kind": "assign",
                        "file": unit["file"],
                        "line": line,
                        "detail": (
                            f"continuous assignment to {signal}"
                        ),
                    },
                )
            for signal in rhs:
                _add(
                    nets,
                    signal,
                    "loads",
                    {
                        "kind": "assign",
                        "file": unit["file"],
                        "line": line,
                        "detail": (
                            f"continuous assignment reads {signal}"
                        ),
                    },
                )

        for match in _PROC_ASSIGN.finditer(body):
            if any(
                start <= match.start() < end
                for start, end in continuous_spans
            ):
                continue
            line = (
                unit["line"]
                + item["masked"].count(
                    "\n",
                    0,
                    item["body_offset"] + match.start(),
                )
            )
            lhs = _signal_identifiers(
                match.group("lhs"),
                known,
            )
            rhs = _signal_identifiers(
                match.group("rhs"),
                known,
            )
            for signal in lhs:
                _add(
                    nets,
                    signal,
                    "drivers",
                    {
                        "kind": "procedural",
                        "file": unit["file"],
                        "line": line,
                        "detail": (
                            f"procedural assignment to {signal}"
                        ),
                    },
                )
            for signal in rhs:
                _add(
                    nets,
                    signal,
                    "loads",
                    {
                        "kind": "procedural",
                        "file": unit["file"],
                        "line": line,
                        "detail": (
                            f"procedural assignment reads {signal}"
                        ),
                    },
                )

        for instance in unit.get("instances", []):
            child = parsed.get(instance["type"])
            if child is None:
                continue

            port_text = _find_instance_ports(
                body,
                type_name=instance["type"],
                instance_name=instance["name"],
            )
            if port_text is None:
                continue

            child_port_map = {
                port["name"]: port["direction"]
                for port in child["ports"]
            }
            connections = _parse_connections(
                port_text,
                child["ports"],
            )

            for connection in connections:
                direction = child_port_map.get(
                    connection["port"],
                    "unknown",
                )
                signals = _signal_identifiers(
                    connection["expression"],
                    known,
                )
                evidence = {
                    "kind": "instance",
                    "file": unit["file"],
                    "line": instance["line"],
                    "detail": (
                        f"{instance['name']}."
                        f"{connection['port']} "
                        f"({instance['type']} {direction})"
                    ),
                }
                for signal in signals:
                    if direction in {"output", "inout"}:
                        _add(
                            nets,
                            signal,
                            "drivers",
                            evidence,
                        )
                    if direction in {"input", "inout"}:
                        _add(
                            nets,
                            signal,
                            "loads",
                            evidence,
                        )

        serialized_nets: list[dict[str, Any]] = []
        for signal in sorted(nets):
            drivers = sorted(
                nets[signal]["drivers"],
                key=lambda evidence: (
                    str(evidence.get("file", "")),
                    int(evidence.get("line", 0)),
                    str(evidence.get("detail", "")),
                ),
            )
            loads = sorted(
                nets[signal]["loads"],
                key=lambda evidence: (
                    str(evidence.get("file", "")),
                    int(evidence.get("line", 0)),
                    str(evidence.get("detail", "")),
                ),
            )
            total_drivers += len(drivers)
            total_loads += len(loads)
            serialized_nets.append(
                {
                    "name": signal,
                    "drivers": drivers,
                    "loads": loads,
                }
            )

        output_units.append(
            {
                "name": unit_name,
                "kind": unit["kind"],
                "file": unit["file"],
                "line": unit["line"],
                "ports": item["ports"],
                "nets": serialized_nets,
            }
        )

    return {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "units": output_units,
        "summary": {
            "units": len(output_units),
            "signals": sum(
                len(unit["nets"])
                for unit in output_units
            ),
            "driver_edges": total_drivers,
            "load_edges": total_loads,
        },
    }


def write_connectivity_index(
    project: ProjectConfig,
) -> dict[str, Any]:
    index = build_connectivity_index(project)
    out_dir = (
        project.root
        / ".zddv"
        / "design"
    ).resolve()
    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    path = out_dir / "connectivity.json"
    path.write_text(
        json.dumps(index, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **index,
        "path": str(path),
    }


def query_net(
    index: dict[str, Any],
    *,
    unit_name: str,
    signal: str,
    role: str,
) -> list[dict[str, Any]]:
    if role not in {"drivers", "loads"}:
        raise ValueError(
            "role must be 'drivers' or 'loads'"
        )

    unit = next(
        (
            item
            for item in index.get("units", [])
            if item["name"] == unit_name
        ),
        None,
    )
    if unit is None:
        raise ValueError(
            f"Design unit '{unit_name}' was not found."
        )

    net = next(
        (
            item
            for item in unit.get("nets", [])
            if item["name"] == signal
        ),
        None,
    )
    if net is None:
        raise ValueError(
            f"Signal '{signal}' was not found in "
            f"design unit '{unit_name}'."
        )

    return list(net[role])
