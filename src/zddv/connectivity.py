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


_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_DIRECTION = re.compile(r"^(input|output|inout)\b")
_DECLARATION = re.compile(
    r"\b(?P<kind>input|output|inout|wire|logic|reg|bit)\b(?P<body>[^;]*);",
    re.DOTALL,
)
_ASSIGNMENT = re.compile(
    r"(?m)^[ \t]*(?:assign[ \t]+)?"
    r"(?P<lhs>[A-Za-z_$][A-Za-z0-9_$]*(?:\s*(?:\[[^\]\n]+\]|\.[A-Za-z_$][A-Za-z0-9_$]*))*)"
    r"[ \t]*(?P<op><=|=)[ \t]*(?P<rhs>[^;]+);"
)
_INSTANTIABLE_KINDS = {"module", "interface", "program"}
_DECL_KEYWORDS = {
    "input",
    "output",
    "inout",
    "wire",
    "logic",
    "reg",
    "bit",
    "signed",
    "unsigned",
    "var",
    "const",
    "automatic",
    "static",
}


def _relative_path(project: ProjectConfig, path: Path) -> str:
    try:
        return path.resolve().relative_to(project.root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _split_top_level(text: str, *, base: int = 0) -> list[tuple[str, int, int]]:
    parts: list[tuple[str, int, int]] = []
    start = 0
    paren = bracket = brace = 0
    for index, char in enumerate(text):
        if char == "(":
            paren += 1
        elif char == ")":
            paren = max(0, paren - 1)
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket = max(0, bracket - 1)
        elif char == "{":
            brace += 1
        elif char == "}":
            brace = max(0, brace - 1)
        elif char == "," and paren == bracket == brace == 0:
            parts.append((text[start:index], base + start, base + index))
            start = index + 1
    parts.append((text[start:], base + start, base + len(text)))
    return parts


def _declarator_name(text: str) -> str | None:
    before_default = text.split("=", 1)[0]
    tokens = [
        match.group(0)
        for match in _IDENTIFIER.finditer(before_default)
        if match.group(0) not in _DECL_KEYWORDS
    ]
    return tokens[-1] if tokens else None


def _header_ports(masked: str, region) -> tuple[list[dict[str, Any]], int]:
    pos = _skip_space(masked, region.body_start)
    if pos < len(masked) and masked[pos] == "#":
        pos = _skip_space(masked, pos + 1)
        after_params = _skip_balanced(masked, pos, "(", ")")
        if after_params is None:
            return [], region.body_start
        pos = _skip_space(masked, after_params)

    if pos >= len(masked) or masked[pos] != "(":
        return [], region.body_start

    after_ports = _skip_balanced(masked, pos, "(", ")")
    if after_ports is None:
        return [], region.body_start

    content_start = pos + 1
    content_end = after_ports - 1
    content = masked[content_start:content_end]
    ports: list[dict[str, Any]] = []
    current_direction: str | None = None

    for segment, absolute_start, absolute_end in _split_top_level(
        content, base=content_start
    ):
        stripped = segment.strip()
        if not stripped:
            continue
        direction_match = _DIRECTION.match(stripped)
        if direction_match:
            current_direction = direction_match.group(1)
        if current_direction is None:
            continue
        name = _declarator_name(stripped)
        if name is None:
            continue
        name_match = list(_IDENTIFIER.finditer(stripped))
        name_offset = next(
            (
                match.start()
                for match in reversed(name_match)
                if match.group(0) == name
            ),
            0,
        )
        ports.append(
            {
                "name": name,
                "direction": current_direction,
                "line": _line_number(masked, absolute_start + name_offset),
                "span": (absolute_start - region.body_start, absolute_end - region.body_start),
            }
        )
    return ports, after_ports


def _unit_signals(masked: str, region) -> tuple[dict[str, dict[str, Any]], list[tuple[int, int]]]:
    signals: dict[str, dict[str, Any]] = {}
    declaration_spans: list[tuple[int, int]] = []

    ports, header_end = _header_ports(masked, region)
    for port in ports:
        signals[port["name"]] = {
            "name": port["name"],
            "direction": port["direction"],
            "line": port["line"],
        }
        declaration_spans.append(port["span"])

    body_scan_start = max(region.body_start, header_end)
    scan_text = masked[body_scan_start:region.end]
    for match in _DECLARATION.finditer(scan_text):
        kind = match.group("kind")
        statement_start = body_scan_start + match.start()
        statement_end = body_scan_start + match.end()
        declaration_spans.append(
            (statement_start - region.body_start, statement_end - region.body_start)
        )
        for part, part_start, _ in _split_top_level(
            match.group("body"),
            base=body_scan_start + match.start("body"),
        ):
            name = _declarator_name(part)
            if name is None:
                continue
            entry = signals.setdefault(
                name,
                {
                    "name": name,
                    "direction": None,
                    "line": _line_number(masked, part_start),
                },
            )
            if kind in {"input", "output", "inout"}:
                entry["direction"] = kind
                entry["line"] = _line_number(masked, part_start)

    return signals, declaration_spans


def _span_contains(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start >= left and end <= right for left, right in spans)


def _reference(
    *,
    role: str,
    kind: str,
    unit: str,
    signal: str,
    file: str,
    line: int,
    detail: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "kind": kind,
        "unit": unit,
        "signal": signal,
        "file": file,
        "line": line,
        "detail": detail,
    }


def _add_reference(target: list[dict[str, Any]], item: dict[str, Any]) -> None:
    key = (
        item["role"],
        item["kind"],
        item["file"],
        item["line"],
        item["detail"],
    )
    if any(
        (
            existing["role"],
            existing["kind"],
            existing["file"],
            existing["line"],
            existing["detail"],
        )
        == key
        for existing in target
    ):
        return
    target.append(item)


def _scan_instance_connections(
    *,
    masked: str,
    region,
    body: str,
    unit_name: str,
    file: str,
    signals: dict[str, dict[str, Any]],
    child_ports: dict[str, dict[str, str | None]],
    instantiable_names: set[str],
    handled_spans: list[tuple[int, int]],
    refs: dict[str, list[dict[str, Any]]],
) -> None:
    if not instantiable_names:
        return

    names = sorted(instantiable_names, key=len, reverse=True)
    type_re = re.compile(
        r"(?<![A-Za-z0-9_$])(?P<type>"
        + "|".join(re.escape(name) for name in names)
        + r")(?![A-Za-z0-9_$])"
    )
    named_connection = re.compile(
        r"\.(?P<port>[A-Za-z_$][A-Za-z0-9_$]*)\s*"
        r"\(\s*(?P<expr>[^()]*)\)"
    )

    for match in type_re.finditer(body):
        pos = _skip_space(body, match.end())
        if pos < len(body) and body[pos] == "#":
            pos = _skip_space(body, pos + 1)
            after_params = _skip_balanced(body, pos, "(", ")")
            if after_params is None:
                continue
            pos = _skip_space(body, after_params)

        instance_match = _IDENTIFIER.match(body, pos)
        if instance_match is None:
            continue
        instance_name = instance_match.group(0)
        pos = _skip_space(body, instance_match.end())
        while pos < len(body) and body[pos] == "[":
            after_dim = _skip_balanced(body, pos, "[", "]")
            if after_dim is None:
                break
            pos = _skip_space(body, after_dim)

        if pos >= len(body) or body[pos] != "(":
            continue
        after_connections = _skip_balanced(body, pos, "(", ")")
        if after_connections is None:
            continue

        child_type = match.group("type")
        directions = child_ports.get(child_type, {})
        connection_text = body[pos + 1 : after_connections - 1]
        connection_base = pos + 1

        for connection in named_connection.finditer(connection_text):
            port = connection.group("port")
            direction = directions.get(port)
            port_start = connection_base + connection.start("port")
            port_end = connection_base + connection.end("port")
            handled_spans.append((port_start, port_end))

            expr = connection.group("expr")
            expr_base = connection_base + connection.start("expr")
            for token in _IDENTIFIER.finditer(expr):
                signal = token.group(0)
                if signal not in signals:
                    continue

                role_set: tuple[str, ...]
                if direction == "input":
                    role_set = ("load",)
                elif direction == "output":
                    role_set = ("driver",)
                elif direction == "inout":
                    role_set = ("driver", "load")
                else:
                    continue

                start = expr_base + token.start()
                end = expr_base + token.end()
                handled_spans.append((start, end))
                line = _line_number(masked, region.body_start + start)
                for role in role_set:
                    _add_reference(
                        refs[signal],
                        _reference(
                            role=role,
                            kind="instance-port",
                            unit=unit_name,
                            signal=signal,
                            file=file,
                            line=line,
                            detail=f"{instance_name}.{port} ({child_type} {direction})",
                        ),
                    )


def build_connectivity_index(project: ProjectConfig) -> dict[str, Any]:
    """Build conservative source-level signal driver/load references.

    This intentionally does not claim elaborated connectivity. It indexes references
    that can be established directly from source syntax: ANSI/non-ANSI ports,
    continuous/procedural assignments, named instance ports whose child direction is
    known, and remaining expression uses.
    """
    parsed: list[tuple[Path, str, Any, dict[str, dict[str, Any]], list[tuple[int, int]]]] = []
    instantiable_names: set[str] = set()

    for path in project.source_files():
        masked = _mask_non_code(path.read_text(encoding="utf-8", errors="replace"))
        for region in _find_unit_regions(masked):
            signals, declaration_spans = _unit_signals(masked, region)
            parsed.append((path, masked, region, signals, declaration_spans))
            if region.kind in _INSTANTIABLE_KINDS:
                instantiable_names.add(region.name)

    child_ports: dict[str, dict[str, str | None]] = {}
    for _, _, region, signals, _ in parsed:
        child_ports.setdefault(
            region.name,
            {name: item.get("direction") for name, item in signals.items()},
        )

    signal_entries: list[dict[str, Any]] = []
    unit_count = 0

    for path, masked, region, signals, declaration_spans in parsed:
        if region.kind not in _INSTANTIABLE_KINDS:
            continue
        unit_count += 1
        file = _relative_path(project, path)
        body = masked[region.body_start:region.end]
        refs: dict[str, list[dict[str, Any]]] = {name: [] for name in signals}
        handled_spans = list(declaration_spans)

        for signal, metadata in signals.items():
            direction = metadata.get("direction")
            if direction in {"input", "inout"}:
                _add_reference(
                    refs[signal],
                    _reference(
                        role="driver",
                        kind="port-boundary",
                        unit=region.name,
                        signal=signal,
                        file=file,
                        line=int(metadata["line"]),
                        detail=f"{direction} port boundary",
                    ),
                )
            if direction in {"output", "inout"}:
                _add_reference(
                    refs[signal],
                    _reference(
                        role="load",
                        kind="port-boundary",
                        unit=region.name,
                        signal=signal,
                        file=file,
                        line=int(metadata["line"]),
                        detail=f"{direction} port boundary",
                    ),
                )

        for assignment in _ASSIGNMENT.finditer(body):
            lhs_text = assignment.group("lhs")
            lhs_token = _IDENTIFIER.match(lhs_text)
            if lhs_token is None:
                continue
            lhs = lhs_token.group(0)
            lhs_start = assignment.start("lhs")
            lhs_end = assignment.end("lhs")
            handled_spans.append((lhs_start, lhs_end))
            if lhs in signals:
                _add_reference(
                    refs[lhs],
                    _reference(
                        role="driver",
                        kind="assignment",
                        unit=region.name,
                        signal=lhs,
                        file=file,
                        line=_line_number(masked, region.body_start + lhs_start),
                        detail=f"{assignment.group('op')} assignment",
                    ),
                )

        _scan_instance_connections(
            masked=masked,
            region=region,
            body=body,
            unit_name=region.name,
            file=file,
            signals=signals,
            child_ports=child_ports,
            instantiable_names=instantiable_names,
            handled_spans=handled_spans,
            refs=refs,
        )

        for signal in signals:
            signal_re = re.compile(
                rf"(?<![A-Za-z0-9_$]){re.escape(signal)}(?![A-Za-z0-9_$])"
            )
            for occurrence in signal_re.finditer(body):
                if _span_contains(
                    handled_spans, occurrence.start(), occurrence.end()
                ):
                    continue
                _add_reference(
                    refs[signal],
                    _reference(
                        role="load",
                        kind="expression",
                        unit=region.name,
                        signal=signal,
                        file=file,
                        line=_line_number(
                            masked, region.body_start + occurrence.start()
                        ),
                        detail="source expression",
                    ),
                )

        for signal, metadata in sorted(signals.items()):
            drivers = sorted(
                (item for item in refs[signal] if item["role"] == "driver"),
                key=lambda item: (item["file"], item["line"], item["kind"], item["detail"]),
            )
            loads = sorted(
                (item for item in refs[signal] if item["role"] == "load"),
                key=lambda item: (item["file"], item["line"], item["kind"], item["detail"]),
            )
            signal_entries.append(
                {
                    "unit": region.name,
                    "signal": signal,
                    "direction": metadata.get("direction"),
                    "declaration": {
                        "file": file,
                        "line": int(metadata["line"]),
                    },
                    "drivers": drivers,
                    "loads": loads,
                }
            )

    signal_entries.sort(key=lambda item: (item["unit"], item["signal"]))
    return {
        "schema_version": 1,
        "project": project.name,
        "top": project.top,
        "analysis": "source-level",
        "signals": signal_entries,
        "summary": {
            "units": unit_count,
            "signals": len(signal_entries),
            "drivers": sum(len(item["drivers"]) for item in signal_entries),
            "loads": sum(len(item["loads"]) for item in signal_entries),
        },
        "limitations": [
            "Generate-time choices, parameter specialization, binds, interfaces/modports, and simulator-resolved connectivity are not elaborated.",
            "Only named instance-port connections are direction-resolved in this source-level index.",
        ],
    }


def write_connectivity_index(project: ProjectConfig) -> dict[str, Any]:
    index = build_connectivity_index(project)
    out_dir = (project.root / ".zddv" / "design").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "connectivity.json"
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return {**index, "path": str(path)}


def find_signal(
    index: dict[str, Any],
    signal: str,
    *,
    unit: str | None = None,
) -> dict[str, Any]:
    query = signal.strip()
    if not query:
        raise ValueError("Signal name must not be empty.")

    if unit is None and "." in query:
        candidate_unit, candidate_signal = query.split(".", 1)
        if candidate_unit and candidate_signal:
            unit = candidate_unit
            query = candidate_signal

    matches = [
        item
        for item in index.get("signals", [])
        if item.get("signal") == query
        and (unit is None or item.get("unit") == unit)
    ]
    if not matches:
        qualified = f"{unit}.{query}" if unit else query
        raise RuntimeError(f"Signal '{qualified}' was not found in the source index.")
    if len(matches) > 1:
        candidates = ", ".join(
            f"{item['unit']}.{item['signal']}" for item in matches
        )
        raise RuntimeError(
            f"Signal '{query}' is ambiguous; use --unit or a qualified name. "
            f"Candidates: {candidates}"
        )
    return matches[0]
