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
    _relative_path,
    _skip_balanced,
    _skip_space,
)


_IDENTIFIER_TEXT = r"[A-Za-z_$][A-Za-z0-9_$]*"
_IDENTIFIER_RE = re.compile(_IDENTIFIER_TEXT)
_DECLARATION_RE = re.compile(
    r"(?m)\b(?P<kind>input|output|inout|wire|logic|reg|bit)\b"
    r"(?P<body>[^;]*);"
)
_ASSIGNMENT_RE = re.compile(
    r"(?P<prefix>^[ \t]*|[;{}:][ \t]*|\)[ \t]*|\b(?:begin|else)[ \t]+)"
    rf"(?:(?P<continuous>\bassign)\s+)?"
    rf"(?P<lhs>{_IDENTIFIER_TEXT}(?:\s*\[[^;\]]+\])?)\s*"
    r"(?P<op><=|(?<![=!<>])=(?!=))\s*"
    r"(?P<rhs>[^;]+);",
    re.MULTILINE,
)
_SIZED_LITERAL_RE = re.compile(
    r"(?:\d+)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+"
)
_UNBASED_LITERAL_RE = re.compile(r"'[01xXzZ]")
_KEYWORDS = {
    "always",
    "always_comb",
    "always_ff",
    "always_latch",
    "and",
    "assign",
    "begin",
    "bit",
    "case",
    "default",
    "else",
    "end",
    "endcase",
    "for",
    "foreach",
    "if",
    "input",
    "integer",
    "logic",
    "or",
    "output",
    "parameter",
    "reg",
    "signed",
    "unsigned",
    "wire",
}


def _split_top_level(text: str) -> list[tuple[str, int]]:
    parts: list[tuple[str, int]] = []
    start = 0
    round_depth = 0
    square_depth = 0
    brace_depth = 0

    for index, char in enumerate(text):
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth = max(0, round_depth - 1)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif (
            char == ","
            and round_depth == 0
            and square_depth == 0
            and brace_depth == 0
        ):
            parts.append((text[start:index], start))
            start = index + 1

    parts.append((text[start:], start))
    return parts


def _find_header_end(masked: str, start: int, limit: int) -> int:
    round_depth = 0
    square_depth = 0
    brace_depth = 0
    index = start

    while index < limit:
        char = masked[index]
        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth = max(0, round_depth - 1)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth = max(0, square_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif (
            char == ";"
            and round_depth == 0
            and square_depth == 0
            and brace_depth == 0
        ):
            return index + 1
        index += 1

    return start


def _declarator_name(text: str) -> str | None:
    left = text.split("=", 1)[0].strip()
    match = re.search(
        rf"(?P<name>{_IDENTIFIER_TEXT})\s*(?:\[[^\]]+\]\s*)*$",
        left,
    )
    return match.group("name") if match else None


def _ansi_ports(
    masked: str,
    body_start: int,
    header_end: int,
) -> list[dict[str, Any]]:
    header = masked[body_start:header_end]
    pos = _skip_space(header, 0)

    if pos < len(header) and header[pos] == "#":
        pos = _skip_space(header, pos + 1)
        end_params = _skip_balanced(header, pos, "(", ")")
        if end_params is None:
            return []
        pos = _skip_space(header, end_params)

    open_paren = header.find("(", pos)
    if open_paren < 0:
        return []
    end_ports = _skip_balanced(header, open_paren, "(", ")")
    if end_ports is None:
        return []

    port_text = header[open_paren + 1 : end_ports - 1]
    port_base = body_start + open_paren + 1
    current_direction: str | None = None
    ports: list[dict[str, Any]] = []

    for part, offset in _split_top_level(port_text):
        stripped = part.strip()
        if not stripped:
            continue

        direction_match = re.search(r"\b(input|output|inout)\b", stripped)
        if direction_match:
            current_direction = direction_match.group(1)
        name = _declarator_name(stripped)
        if name is None:
            continue

        leading = len(part) - len(part.lstrip())
        ports.append(
            {
                "name": name,
                "direction": current_direction,
                "line": _line_number(masked, port_base + offset + leading),
            }
        )
    return ports


def _body_declarations(
    masked: str,
    start: int,
    end: int,
) -> tuple[list[dict[str, Any]], set[str]]:
    body = masked[start:end]
    ports: list[dict[str, Any]] = []
    signals: set[str] = set()

    for match in _DECLARATION_RE.finditer(body):
        kind = match.group("kind")
        statement = match.group("body")
        for part, _ in _split_top_level(statement):
            name = _declarator_name(part)
            if name is None or name in _KEYWORDS:
                continue
            signals.add(name)
            if kind in {"input", "output", "inout"}:
                ports.append(
                    {
                        "name": name,
                        "direction": kind,
                        "line": _line_number(masked, start + match.start()),
                    }
                )
    return ports, signals


def _merge_ports(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for group in groups:
        for item in group:
            name = item["name"]
            if name not in by_name:
                order.append(name)
            by_name[name] = item
    return [by_name[name] for name in order]


def _expression_identifiers(expression: str, known_signals: set[str]) -> list[str]:
    cleaned = _SIZED_LITERAL_RE.sub(" ", expression)
    cleaned = _UNBASED_LITERAL_RE.sub(" ", cleaned)
    identifiers: list[str] = []
    seen: set[str] = set()
    for match in _IDENTIFIER_RE.finditer(cleaned):
        name = match.group(0)
        if name in known_signals and name not in seen:
            identifiers.append(name)
            seen.add(name)
    return identifiers


def _base_signal(lhs: str) -> str:
    match = _IDENTIFIER_RE.search(lhs)
    return match.group(0) if match else lhs.strip()


def _connection(
    *,
    role: str,
    kind: str,
    unit: str,
    signal: str,
    file: str,
    line: int,
    detail: str,
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "role": role,
        "kind": kind,
        "unit": unit,
        "signal": signal,
        "file": file,
        "line": line,
        "detail": detail,
    }
    item.update(extra)
    return item


def _assignment_connections(
    masked: str,
    *,
    unit_name: str,
    file: str,
    start: int,
    end: int,
    known_signals: set[str],
) -> list[dict[str, Any]]:
    body = masked[start:end]
    entries: list[dict[str, Any]] = []

    for match in _ASSIGNMENT_RE.finditer(body):
        lhs = _base_signal(match.group("lhs"))
        if lhs not in known_signals:
            continue

        line = _line_number(masked, start + match.start("lhs"))
        style = (
            "continuous_assignment"
            if match.group("continuous")
            else "procedural_assignment"
        )
        rhs = match.group("rhs").strip()
        entries.append(
            _connection(
                role="driver",
                kind=style,
                unit=unit_name,
                signal=lhs,
                file=file,
                line=line,
                detail=f"{lhs} {match.group('op')} {rhs}",
                operator=match.group("op"),
            )
        )

        for signal in _expression_identifiers(rhs, known_signals):
            entries.append(
                _connection(
                    role="load",
                    kind=style,
                    unit=unit_name,
                    signal=signal,
                    file=file,
                    line=line,
                    detail=f"read by assignment to {lhs}",
                    destination=lhs,
                )
            )
    return entries


def _named_port_connection(part: str) -> tuple[str | None, str | None]:
    stripped = part.strip()
    match = re.fullmatch(
        rf"\.(?P<port>{_IDENTIFIER_TEXT})\s*\((?P<expr>.*)\)",
        stripped,
        re.DOTALL,
    )
    if match:
        return match.group("port"), match.group("expr").strip()

    implicit = re.fullmatch(rf"\.(?P<port>{_IDENTIFIER_TEXT})", stripped)
    if implicit:
        name = implicit.group("port")
        return name, name
    return None, None


def _instance_connections(
    masked: str,
    *,
    unit_name: str,
    file: str,
    start: int,
    end: int,
    known_signals: set[str],
    child_ports: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    if not child_ports:
        return [], 0

    body = masked[start:end]
    names = sorted(child_ports, key=len, reverse=True)
    type_re = re.compile(
        r"(?<![A-Za-z0-9_$])(?P<type>"
        + "|".join(re.escape(name) for name in names)
        + r")(?![A-Za-z0-9_$])"
    )
    entries: list[dict[str, Any]] = []
    unresolved = 0

    for match in type_re.finditer(body):
        child_type = match.group("type")
        pos = _skip_space(body, match.end())

        if pos < len(body) and body[pos] == "#":
            pos = _skip_space(body, pos + 1)
            after_params = _skip_balanced(body, pos, "(", ")")
            if after_params is None:
                continue
            pos = _skip_space(body, after_params)

        port_list = child_ports[child_type]
        port_by_name = {item["name"]: item for item in port_list}

        while pos < len(body):
            instance_match = _IDENTIFIER_RE.match(body, pos)
            if instance_match is None:
                break

            instance_name = instance_match.group(0)
            instance_offset = instance_match.start()
            pos = _skip_space(body, instance_match.end())

            while pos < len(body) and body[pos] == "[":
                after_dimension = _skip_balanced(body, pos, "[", "]")
                if after_dimension is None:
                    break
                pos = _skip_space(body, after_dimension)

            if pos >= len(body) or body[pos] != "(":
                break
            after_connections = _skip_balanced(body, pos, "(", ")")
            if after_connections is None:
                break

            connection_text = body[pos + 1 : after_connections - 1]
            line = _line_number(masked, start + instance_offset)

            for index, (part, _) in enumerate(_split_top_level(connection_text)):
                stripped = part.strip()
                if not stripped or stripped == ".*":
                    if stripped == ".*":
                        unresolved += 1
                    continue

                port_name, expression = _named_port_connection(stripped)
                if port_name is None:
                    if index >= len(port_list):
                        unresolved += 1
                        continue
                    port_name = port_list[index]["name"]
                    expression = stripped

                port = port_by_name.get(port_name)
                if port is None or expression is None:
                    unresolved += 1
                    continue

                signals = _expression_identifiers(expression, known_signals)
                if not signals:
                    continue

                direction = port["direction"]
                for signal in signals:
                    common = {
                        "kind": "instance_port",
                        "unit": unit_name,
                        "signal": signal,
                        "file": file,
                        "line": line,
                        "detail": (
                            f"{instance_name}.{port_name} ({direction}) connected to "
                            f"{expression}"
                        ),
                        "instance": instance_name,
                        "child_type": child_type,
                        "port": port_name,
                        "direction": direction,
                        "expression": expression,
                    }
                    if direction == "input":
                        entries.append(_connection(role="load", **common))
                    elif direction == "output":
                        entries.append(_connection(role="driver", **common))
                    elif direction == "inout":
                        entries.append(_connection(role="driver", **common))
                        entries.append(_connection(role="load", **common))
                    else:
                        unresolved += 1

            pos = _skip_space(body, after_connections)
            if pos < len(body) and body[pos] == ",":
                pos = _skip_space(body, pos + 1)
                continue
            break

    return entries, unresolved


def build_connectivity_index(project: ProjectConfig) -> dict[str, Any]:
    parsed_units: list[dict[str, Any]] = []

    for path in project.source_files():
        raw = path.read_text(encoding="utf-8", errors="replace")
        masked = _mask_non_code(raw)
        file = _relative_path(project, path)

        for region in _find_unit_regions(masked):
            if region.kind not in {"module", "interface", "program"}:
                continue

            header_end = _find_header_end(masked, region.body_start, region.end)
            if header_end == region.body_start:
                continue

            ansi = _ansi_ports(masked, region.body_start, header_end)
            body_ports, declared_signals = _body_declarations(
                masked,
                header_end,
                region.end,
            )
            ports = _merge_ports(ansi, body_ports)
            known_signals = set(declared_signals)
            known_signals.update(item["name"] for item in ports)

            parsed_units.append(
                {
                    "name": region.name,
                    "kind": region.kind,
                    "file": file,
                    "line": region.line,
                    "masked": masked,
                    "body_start": header_end,
                    "end": region.end,
                    "ports": ports,
                    "known_signals": known_signals,
                }
            )

    ports_by_unit = {
        unit["name"]: unit["ports"]
        for unit in parsed_units
        if unit["ports"]
    }

    units: list[dict[str, Any]] = []
    all_connections: list[dict[str, Any]] = []
    unresolved_connections = 0

    for unit in parsed_units:
        unit_connections: list[dict[str, Any]] = []

        for port in unit["ports"]:
            common = {
                "kind": "boundary_port",
                "unit": unit["name"],
                "signal": port["name"],
                "file": unit["file"],
                "line": port["line"],
                "detail": f"{port['direction']} port boundary",
                "direction": port["direction"],
            }
            if port["direction"] == "input":
                unit_connections.append(_connection(role="driver", **common))
            elif port["direction"] == "output":
                unit_connections.append(_connection(role="load", **common))
            elif port["direction"] == "inout":
                unit_connections.append(_connection(role="driver", **common))
                unit_connections.append(_connection(role="load", **common))

        unit_connections.extend(
            _assignment_connections(
                unit["masked"],
                unit_name=unit["name"],
                file=unit["file"],
                start=unit["body_start"],
                end=unit["end"],
                known_signals=unit["known_signals"],
            )
        )
        instance_entries, unresolved = _instance_connections(
            unit["masked"],
            unit_name=unit["name"],
            file=unit["file"],
            start=unit["body_start"],
            end=unit["end"],
            known_signals=unit["known_signals"],
            child_ports=ports_by_unit,
        )
        unit_connections.extend(instance_entries)
        unresolved_connections += unresolved

        unit_connections.sort(
            key=lambda item: (
                item["signal"],
                item["role"],
                item["line"],
                item["kind"],
                item["detail"],
            )
        )
        all_connections.extend(unit_connections)
        units.append(
            {
                "name": unit["name"],
                "kind": unit["kind"],
                "file": unit["file"],
                "line": unit["line"],
                "ports": unit["ports"],
                "signals": sorted(unit["known_signals"]),
                "connections": unit_connections,
            }
        )

    units.sort(key=lambda item: (item["name"], item["file"], item["line"]))
    all_connections.sort(
        key=lambda item: (
            item["unit"],
            item["signal"],
            item["role"],
            item["line"],
            item["kind"],
        )
    )

    return {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "analysis_level": "source_structural",
        "units": units,
        "connections": all_connections,
        "summary": {
            "units": len(units),
            "signals": sum(len(unit["signals"]) for unit in units),
            "drivers": sum(
                1 for item in all_connections if item["role"] == "driver"
            ),
            "loads": sum(
                1 for item in all_connections if item["role"] == "load"
            ),
            "unresolved_instance_connections": unresolved_connections,
        },
        "limitations": [
            "Source-level analysis; generate/elaboration choices are not resolved.",
            "Assignment navigation covers simple identifier lvalues and known-signal RHS references.",
            "Instance navigation requires a known child design unit and resolvable port direction.",
            "Macros, interfaces/modports, binds, classes, functions, and complex lvalue expressions may require simulator AST enrichment.",
        ],
    }


def signal_navigation(
    index: dict[str, Any],
    *,
    unit: str,
    signal: str,
) -> dict[str, Any]:
    selected = next(
        (item for item in index.get("units", []) if item["name"] == unit),
        None,
    )
    if selected is None:
        known = ", ".join(item["name"] for item in index.get("units", [])) or "none"
        raise ValueError(f"Unknown design unit '{unit}'. Known units: {known}")
    if signal not in selected["signals"]:
        known = ", ".join(selected["signals"]) or "none"
        raise ValueError(
            f"Unknown signal '{signal}' in unit '{unit}'. Known signals: {known}"
        )

    entries = [
        item
        for item in selected["connections"]
        if item["signal"] == signal
    ]
    return {
        "unit": unit,
        "signal": signal,
        "drivers": [item for item in entries if item["role"] == "driver"],
        "loads": [item for item in entries if item["role"] == "load"],
    }


def qualify_signal_navigation_with_elaboration(
    navigation: dict[str, Any],
    *,
    instance_path: str,
    elaborated_instances: list[dict[str, Any]],
    elaborated_pin_bindings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach deterministic elaborated-instance context to source connectivity.

    The driver/load roles remain source-structural evidence.  This helper
    qualifies that evidence with an already-resolved elaborated parent instance
    and, for instance-port edges, exact or ambiguous child-instance candidates.
    When the caller supplies evidence-gated direct pin bindings, an exact child
    edge is also correlated with its normalized or unsupported pin record without
    changing the source-derived role.
    """
    parent_path = str(instance_path).strip()
    if not parent_path:
        raise ValueError("instance_path must not be empty")

    instances = [
        item
        for item in elaborated_instances
        if isinstance(item, dict) and item.get("path")
    ]

    def child_candidates(entry: dict[str, Any]) -> list[str]:
        if entry.get("kind") != "instance_port" or not entry.get("instance"):
            return []

        source_instance = str(entry["instance"])
        child_type = entry.get("child_type")
        matches: list[str] = []
        for candidate in instances:
            candidate_path = str(candidate.get("path") or "")
            candidate_name = str(candidate.get("name") or "")
            candidate_module = candidate.get("module")
            if candidate_name != source_instance:
                continue
            if child_type and candidate_module != child_type:
                continue

            generate_scopes = [
                str(scope)
                for scope in (candidate.get("generate_scopes") or [])
                if str(scope)
            ]
            expected_path = ".".join(
                [parent_path, *generate_scopes, source_instance]
            )
            if candidate_path == expected_path:
                matches.append(candidate_path)
        return sorted(set(matches))

    def pin_binding_for_edge(
        entry: dict[str, Any],
        child_path: str,
    ) -> tuple[str, dict[str, Any] | None]:
        if elaborated_pin_bindings is None:
            return "unavailable", None

        matches = [
            binding
            for binding in elaborated_pin_bindings
            if isinstance(binding, dict)
            and str(binding.get("instance_path") or "") == child_path
            and str(binding.get("pin") or "") == str(entry.get("port") or "")
        ]
        if not matches:
            return "not_found", None
        if len(matches) != 1:
            return "ambiguous", None

        binding = matches[0]
        binding_status = str(binding.get("status") or "").upper()
        if binding_status == "NORMALIZED" and binding.get("signal"):
            return "matched", binding
        if binding_status == "UNSUPPORTED":
            return "unsupported_expression", binding
        return "invalid", binding

    def enrich(entry: dict[str, Any]) -> dict[str, Any]:
        item = {**entry, "instance_path": parent_path}
        if entry.get("kind") != "instance_port":
            return item

        candidates = child_candidates(entry)
        if len(candidates) == 1:
            child_path = candidates[0]
            item["elaborated_child_resolution"] = "exact"
            item["elaborated_child_path"] = child_path
            pin_status, binding = pin_binding_for_edge(entry, child_path)
            if elaborated_pin_bindings is not None:
                item["elaborated_pin_resolution"] = pin_status
            if binding is not None:
                item["elaborated_pin_binding"] = binding
                if pin_status == "matched":
                    item["elaborated_pin_source_consistent"] = (
                        str(binding.get("signal")) == str(entry.get("signal"))
                    )
        elif candidates:
            item["elaborated_child_resolution"] = "ambiguous"
            item["elaborated_child_candidates"] = candidates
        else:
            item["elaborated_child_resolution"] = "not_found"
        return item

    return {
        "unit": navigation["unit"],
        "signal": navigation["signal"],
        "instance_path": parent_path,
        "instance_qualification": "simulator_elaborated_scope",
        "drivers": [enrich(item) for item in navigation["drivers"]],
        "loads": [enrich(item) for item in navigation["loads"]],
    }


def write_connectivity_index(
    project: ProjectConfig,
    *,
    output: str | Path = ".zddv/design/connectivity.json",
) -> dict[str, Any]:
    index = build_connectivity_index(project)
    path = Path(output)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return {**index, "path": str(path)}
