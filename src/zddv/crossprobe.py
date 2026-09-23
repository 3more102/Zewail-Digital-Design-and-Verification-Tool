from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.connectivity import (
    build_connectivity_index,
    qualify_signal_navigation_with_elaboration,
    signal_navigation,
    write_connectivity_index,
)
from zddv.design_index import build_design_index, write_design_index
from zddv.design_revision import design_revision_fingerprint
from zddv.waveform import write_waveform_index


_DECLARATION_KEYWORD_RE = re.compile(
    r"\b(?:input|output|inout|wire|wand|wor|tri|logic|reg|bit|byte|shortint|"
    r"int|longint|integer|time|realtime|event|genvar)\b"
)


def _hierarchy_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = [node]
    for child in node.get("children", []):
        nodes.extend(_hierarchy_nodes(child))
    return nodes


def _match_waveform_signal(
    signals: list[dict[str, Any]],
    query: str,
) -> tuple[dict[str, Any], str]:
    normalized = query.strip()
    if not normalized:
        raise ValueError("Signal query must not be empty")

    exact = [item for item in signals if item.get("path") == normalized]
    if len(exact) == 1:
        return exact[0], "exact"

    suffix = [
        item
        for item in signals
        if str(item.get("path", "")).endswith("." + normalized)
    ]
    if len(suffix) == 1:
        return suffix[0], "path-suffix"

    by_name = [item for item in signals if item.get("name") == normalized]
    if len(by_name) == 1:
        return by_name[0], "unique-name"

    candidates = suffix or by_name
    if candidates:
        names = ", ".join(str(item.get("path")) for item in candidates[:8])
        extra = "" if len(candidates) <= 8 else f", ... (+{len(candidates) - 8} more)"
        raise RuntimeError(
            f"Signal query '{normalized}' is ambiguous: {names}{extra}. "
            "Use a longer hierarchical path."
        )

    raise RuntimeError(f"Signal '{normalized}' was not found in the waveform index.")


def _match_hierarchy_scope(
    scope: str,
    hierarchy: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    resolved = [
        node
        for node in _hierarchy_nodes(hierarchy)
        if node.get("resolved", False) and node.get("path")
    ]

    exact = [node for node in resolved if node["path"] == scope]
    if len(exact) == 1:
        return exact[0], "exact"

    suffix = [
        node
        for node in resolved
        if scope.endswith("." + str(node["path"]))
    ]
    if not suffix:
        return None, None

    longest = max(len(str(node["path"])) for node in suffix)
    best = [node for node in suffix if len(str(node["path"])) == longest]
    if len(best) == 1:
        return best[0], "scope-suffix"
    return None, None


def _match_elaborated_scope(
    scope: str,
    instances: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    candidates = [
        item
        for item in instances
        if isinstance(item, dict)
        and item.get("path")
        and item.get("module")
    ]

    exact = [item for item in candidates if str(item["path"]) == scope]
    if len(exact) == 1:
        return exact[0], "exact"

    suffix = [
        item
        for item in candidates
        if scope.endswith("." + str(item["path"]))
    ]
    if not suffix:
        return None, None

    longest = max(len(str(item["path"])) for item in suffix)
    best = [item for item in suffix if len(str(item["path"])) == longest]
    if len(best) == 1:
        return best[0], "scope-suffix"
    return None, None


def _match_elaborated_port_evidence(
    elaborated_index: dict[str, Any] | None,
    elaborated_node: dict[str, Any] | None,
    signal_name: str,
) -> dict[str, Any] | None:
    """Match one waveform signal to normalized simulator module-port evidence."""
    if elaborated_index is None or elaborated_node is None:
        return None

    evidence = elaborated_index.get("port_evidence")
    if not isinstance(evidence, dict):
        return {
            "status": "UNAVAILABLE",
            "reason": "port_evidence_metadata_missing",
        }

    evidence_status = str(evidence.get("status") or "UNAVAILABLE")
    if evidence_status != "NORMALIZED":
        return {
            "status": evidence_status,
            "source_format": evidence.get("source_format"),
            "reason": (
                evidence.get("reason")
                or "module_port_evidence_not_normalized"
            ),
        }

    ports = elaborated_index.get("ports")
    if not isinstance(ports, list):
        return {
            "status": "INVALID",
            "reason": "normalized_port_evidence_requires_ports_list",
        }

    module_name = elaborated_node.get("module")
    if not module_name:
        return {
            "status": "UNAVAILABLE",
            "reason": "matched_elaborated_instance_has_no_module_name",
        }

    matches: list[dict[str, Any]] = []
    for port in ports:
        if not isinstance(port, dict) or port.get("module") != module_name:
            continue
        aliases = {
            str(value)
            for value in (
                port.get("name"),
                port.get("elaborated_name"),
                port.get("verilog_name"),
                port.get("original_name"),
            )
            if value
        }
        if signal_name in aliases:
            matches.append(port)

    common = {
        "instance_path": elaborated_node.get("path"),
        "module": module_name,
        "signal": signal_name,
        "evidence": dict(evidence),
    }
    if len(matches) == 1:
        return {
            **common,
            "status": "MATCHED",
            "port": matches[0],
        }
    if not matches:
        return {
            **common,
            "status": "NOT_A_PORT",
        }
    return {
        **common,
        "status": "AMBIGUOUS",
        "candidate_count": len(matches),
        "candidates": matches,
    }


def _elaborated_identity_errors(
    project: ProjectConfig,
    index: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    expected = {
        "project": project.name,
        "top": project.top,
        "simulator": project.simulator,
    }
    for key, value in expected.items():
        if index.get(key) != value:
            errors.append(
                f"{key}={index.get(key)!r} (expected {value!r})"
            )
    if not isinstance(index.get("instances"), list):
        errors.append("instances is not a list")

    ports = index.get("ports")
    if ports is not None and not isinstance(ports, list):
        errors.append("ports is not a list")

    port_evidence = index.get("port_evidence")
    if port_evidence is not None and not isinstance(port_evidence, dict):
        errors.append("port_evidence is not an object")
    elif (
        isinstance(port_evidence, dict)
        and port_evidence.get("status") == "NORMALIZED"
        and not isinstance(ports, list)
    ):
        errors.append("normalized port_evidence requires a ports list")
    return errors


def load_persisted_elaborated_evidence(
    project: ProjectConfig,
) -> dict[str, Any]:
    path = (project.root / ".zddv" / "design" / "elaborated.json").resolve()
    evidence: dict[str, Any] = {
        "status": "NOT_PRESENT",
        "path": str(path),
    }
    if not path.exists():
        return evidence

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            **evidence,
            "status": "INVALID",
            "error": str(exc),
        }

    if not isinstance(payload, dict):
        return {
            **evidence,
            "status": "INVALID",
            "error": "Elaborated index root is not a JSON object.",
        }

    identity_errors = _elaborated_identity_errors(project, payload)
    current_fingerprint = design_revision_fingerprint(project)
    stored_fingerprint = payload.get("design_fingerprint")
    if stored_fingerprint != current_fingerprint:
        if stored_fingerprint is None:
            identity_errors.append("design_fingerprint is missing")
        else:
            identity_errors.append(
                "design_fingerprint does not match the current RTL/config revision"
            )
    if identity_errors:
        return {
            **evidence,
            "status": "STALE",
            "error": "; ".join(identity_errors),
            "design_fingerprint": stored_fingerprint,
            "current_design_fingerprint": current_fingerprint,
        }

    return {
        **evidence,
        "status": "PRESENT",
        "index": payload,
        "simulator_version": payload.get("simulator_version"),
        "source_format": payload.get("source_format"),
        "design_fingerprint": stored_fingerprint,
        "current_design_fingerprint": current_fingerprint,
    }


def _design_unit_for_node(
    node: dict[str, Any],
    design_index: dict[str, Any],
) -> dict[str, Any] | None:
    node_type = node.get("type")
    node_file = node.get("file")
    matches = [
        unit
        for unit in design_index.get("units", [])
        if unit.get("name") == node_type
        and (node_file is None or unit.get("file") == node_file)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _design_unit_for_elaborated_instance(
    instance: dict[str, Any],
    design_index: dict[str, Any],
) -> dict[str, Any] | None:
    module_name = instance.get("module")
    if not module_name:
        return None

    matches = [
        unit
        for unit in design_index.get("units", [])
        if unit.get("name") == module_name
    ]
    location = instance.get("location") or {}
    location_path = location.get("path")
    if location_path:
        source_matches = [
            unit for unit in matches if unit.get("file") == location_path
        ]
        if len(source_matches) == 1:
            return source_matches[0]
    if len(matches) == 1:
        return matches[0]
    return None


def _elaborated_port_directions(
    elaborated_index: dict[str, Any],
) -> dict[tuple[str, str], str]:
    evidence = elaborated_index.get("port_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "NORMALIZED":
        return {}

    directions: dict[tuple[str, str], str] = {}
    ambiguous: set[tuple[str, str]] = set()
    for port in elaborated_index.get("ports", []):
        if not isinstance(port, dict):
            continue
        module = port.get("module")
        name = port.get("name")
        direction = port.get("direction")
        if not module or not name or not direction:
            continue
        key = (str(module), str(name))
        value = str(direction).lower()
        current = directions.get(key)
        if current is not None and current != value:
            ambiguous.add(key)
            continue
        directions[key] = value
    for key in ambiguous:
        directions.pop(key, None)
    return directions


def _pin_relationship(direction: str | None) -> str:
    if direction == "input":
        return "parent_signal_to_child_input"
    if direction == "output":
        return "child_output_to_parent_signal"
    if direction == "inout":
        return "bidirectional_child_port"
    return "direct_pin_varref"


def _elaborated_binding_role(
    direction: str | None,
    *,
    query_side: str,
) -> str:
    """Classify one direct boundary binding relative to the queried signal."""
    if query_side not in {"parent_signal", "instance_port"}:
        raise ValueError("query_side must be parent_signal or instance_port")
    if direction == "inout":
        return "bidirectional"
    if direction not in {"input", "output"}:
        return "unclassified"

    if query_side == "parent_signal":
        return "load" if direction == "input" else "driver"
    return "driver" if direction == "input" else "load"


def _elaborated_pin_connectivity(
    elaborated_index: dict[str, Any],
    *,
    instance_path: str,
    signal_name: str,
) -> dict[str, Any] | None:
    evidence = elaborated_index.get("pin_binding_evidence")
    if not isinstance(evidence, dict) or evidence.get("status") != "NORMALIZED":
        return None

    port_directions = _elaborated_port_directions(elaborated_index)
    parent_signal_bindings: list[dict[str, Any]] = []
    instance_port_bindings: list[dict[str, Any]] = []
    drivers: list[dict[str, Any]] = []
    loads: list[dict[str, Any]] = []
    bidirectional: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []

    role_buckets = {
        "driver": drivers,
        "load": loads,
        "bidirectional": bidirectional,
        "unclassified": unclassified,
    }

    def record_match(
        normalized: dict[str, Any],
        *,
        query_side: str,
        destination: list[dict[str, Any]],
    ) -> None:
        role = _elaborated_binding_role(
            normalized.get("port_direction"),
            query_side=query_side,
        )
        annotated = {
            **normalized,
            "query_side": query_side,
            "query_role": role,
        }
        destination.append(annotated)
        role_buckets[role].append(annotated)

    for binding in elaborated_index.get("pin_bindings", []):
        if not isinstance(binding, dict) or binding.get("status") != "NORMALIZED":
            continue
        child_path = binding.get("instance_path")
        parent_path = binding.get("parent_instance_path")
        pin = binding.get("pin")
        parent_signal = binding.get("signal")
        child_module = binding.get("instance_module")
        if not child_path or not parent_path or not pin or not parent_signal:
            continue

        direction = port_directions.get((str(child_module), str(pin)))
        normalized = {
            "instance_path": str(child_path),
            "instance_module": (
                str(child_module) if child_module is not None else None
            ),
            "pin": str(pin),
            "parent_instance_path": str(parent_path),
            "parent_signal": str(parent_signal),
            "port_direction": direction,
            "relationship": _pin_relationship(direction),
            "generate_scopes": list(binding.get("generate_scopes", [])),
            "pin_location": binding.get("pin_location"),
            "signal_location": binding.get("signal_location"),
        }

        if str(parent_path) == instance_path and str(parent_signal) == signal_name:
            record_match(
                normalized,
                query_side="parent_signal",
                destination=parent_signal_bindings,
            )
        if str(child_path) == instance_path and str(pin) == signal_name:
            record_match(
                normalized,
                query_side="instance_port",
                destination=instance_port_bindings,
            )

    if not parent_signal_bindings and not instance_port_bindings:
        return None

    def binding_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            str(item["instance_path"]),
            str(item["pin"]),
            str(item["parent_instance_path"]),
            str(item["parent_signal"]),
            str(item["query_side"]),
        )

    return {
        "analysis_level": "simulator_elaborated_direct_pin_varref",
        "driver_load_scope": "direct_instance_boundary_only",
        "evidence_contract": evidence.get("contract"),
        "query_instance_path": instance_path,
        "query_signal": signal_name,
        "parent_signal_bindings": sorted(
            parent_signal_bindings,
            key=binding_sort_key,
        ),
        "instance_port_bindings": sorted(
            instance_port_bindings,
            key=binding_sort_key,
        ),
        "drivers": sorted(drivers, key=binding_sort_key),
        "loads": sorted(loads, key=binding_sort_key),
        "bidirectional": sorted(bidirectional, key=binding_sort_key),
        "unclassified": sorted(unclassified, key=binding_sort_key),
    }

def _source_path(project: ProjectConfig, file_value: str) -> Path:
    path = Path(file_value)
    if not path.is_absolute():
        path = project.root / path
    return path.resolve()


def _find_source_declaration(
    project: ProjectConfig,
    unit: dict[str, Any],
    signal_name: str,
) -> dict[str, Any] | None:
    file_value = str(unit["file"])
    source = _source_path(project, file_value)
    if not source.exists():
        return None

    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(unit.get("line", 1)))
    end = min(len(lines), int(unit.get("end_line", len(lines))))
    token_re = re.compile(
        rf"(?<![A-Za-z0-9_$]){re.escape(signal_name)}(?![A-Za-z0-9_$])"
    )

    for line_number in range(start, end + 1):
        text = lines[line_number - 1]
        token = token_re.search(text)
        if token is None:
            continue
        prefix = text[: token.start()]
        if _DECLARATION_KEYWORD_RE.search(prefix) is None:
            continue
        return {
            "file": file_value,
            "line": line_number,
            "text": text.strip(),
            "confidence": "declaration-line",
        }

    return None


def build_crossprobe(
    project: ProjectConfig,
    signal_query: str,
    waveform_index: dict[str, Any],
    *,
    design_index: dict[str, Any] | None = None,
    connectivity_index: dict[str, Any] | None = None,
    elaborated_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Correlate a waveform signal with elaborated/source hierarchy and RTL evidence."""
    if waveform_index.get("parse_status") not in {
        "indexed",
        "indexed-via-fst2vcd",
    }:
        raise RuntimeError(
            "Cross-probing requires a signal-indexed waveform. "
            f"Current parse status: {waveform_index.get('parse_status', 'unknown')}"
        )

    design = design_index or build_design_index(project)
    if elaborated_index is not None:
        identity_errors = _elaborated_identity_errors(project, elaborated_index)
        if identity_errors:
            raise ValueError(
                "Elaborated index identity mismatch: " + "; ".join(identity_errors)
            )

    signal, signal_match = _match_waveform_signal(
        list(waveform_index.get("signals", [])),
        signal_query,
    )
    scope = str(signal.get("scope", ""))

    source_hierarchy_node, source_hierarchy_match = _match_hierarchy_scope(
        scope,
        design["hierarchy"],
    )
    elaborated_node: dict[str, Any] | None = None
    elaborated_match: str | None = None
    if elaborated_index is not None:
        elaborated_node, elaborated_match = _match_elaborated_scope(
            scope,
            list(elaborated_index.get("instances", [])),
        )

    elaborated_port = _match_elaborated_port_evidence(
        elaborated_index,
        elaborated_node,
        str(signal.get("name", "")),
    )

    selected_kind: str | None = None
    unit: dict[str, Any] | None = None
    if elaborated_node is not None:
        selected_kind = "simulator_elaborated"
        unit = _design_unit_for_elaborated_instance(elaborated_node, design)
    if unit is None and source_hierarchy_node is not None:
        if selected_kind is None:
            selected_kind = "source_structural"
        unit = _design_unit_for_node(source_hierarchy_node, design)

    source: dict[str, Any] | None = None
    connectivity_payload: dict[str, Any] | None = None
    elaborated_connectivity_payload: dict[str, Any] | None = None
    status = "PARTIAL"
    note: str | None = None

    if elaborated_node is not None and elaborated_index is not None:
        elaborated_connectivity_payload = _elaborated_pin_connectivity(
            elaborated_index,
            instance_path=str(elaborated_node["path"]),
            signal_name=str(signal.get("name", "")),
        )

    if elaborated_node is None and source_hierarchy_node is None:
        note = (
            "The waveform signal was found, but its scope could not be mapped "
            "to the available elaborated or source hierarchy evidence."
        )
    elif unit is None:
        note = (
            "The waveform scope matched hierarchy evidence, but the source "
            "design unit could not be resolved uniquely."
        )
    else:
        declaration = _find_source_declaration(
            project,
            unit,
            str(signal.get("name", "")),
        )
        source = {
            "unit": unit["name"],
            "kind": unit["kind"],
            "file": unit["file"],
            "unit_line": unit["line"],
            "unit_end_line": unit["end_line"],
            "declaration": declaration,
        }

        connectivity = connectivity_index or build_connectivity_index(project)
        try:
            navigation = signal_navigation(
                connectivity,
                unit=str(unit["name"]),
                signal=str(signal.get("name", "")),
            )
        except ValueError:
            navigation = None
        if navigation is not None:
            if elaborated_node is not None and elaborated_index is not None:
                navigation = qualify_signal_navigation_with_elaboration(
                    navigation,
                    instance_path=str(elaborated_node["path"]),
                    elaborated_instances=list(
                        elaborated_index.get("instances", [])
                    ),
                )
            connectivity_payload = {
                "analysis_level": connectivity.get("analysis_level"),
                "unit": navigation["unit"],
                "signal": navigation["signal"],
                "drivers": navigation["drivers"],
                "loads": navigation["loads"],
            }
            if navigation.get("instance_path"):
                connectivity_payload["instance_path"] = navigation["instance_path"]
                connectivity_payload["instance_qualification"] = navigation[
                    "instance_qualification"
                ]

        if declaration is not None:
            status = "MATCHED"
        else:
            note = (
                "The waveform scope matched hierarchy evidence, but no "
                "same-line SystemVerilog declaration was found for the signal."
            )

    source_hierarchy_payload = None
    if source_hierarchy_node is not None:
        source_hierarchy_payload = {
            "waveform_scope": scope,
            "design_path": source_hierarchy_node["path"],
            "instance": source_hierarchy_node["instance"],
            "type": source_hierarchy_node["type"],
            "file": source_hierarchy_node.get("file"),
            "line": source_hierarchy_node.get("line"),
            "match": source_hierarchy_match,
            "analysis_level": "source_hierarchy",
        }

    elaborated_hierarchy_payload = None
    if elaborated_node is not None:
        location = elaborated_node.get("location") or {}
        elaborated_hierarchy_payload = {
            "waveform_scope": scope,
            "design_path": elaborated_node["path"],
            "instance": elaborated_node.get("name"),
            "type": elaborated_node.get("module"),
            "file": location.get("path"),
            "line": location.get("line"),
            "match": elaborated_match,
            "analysis_level": "simulator_elaborated",
            "generate_scopes": list(elaborated_node.get("generate_scopes", [])),
        }

    hierarchy_payload = (
        elaborated_hierarchy_payload
        if elaborated_hierarchy_payload is not None
        else source_hierarchy_payload
    )
    if hierarchy_payload is None:
        selected_kind = None

    return {
        "schema_version": 1,
        "project": project.name,
        "status": status,
        "query": signal_query,
        "signal_match": signal_match,
        "hierarchy_resolution": selected_kind,
        "waveform": {
            "run_id": waveform_index.get("run_id"),
            "format": waveform_index.get("format"),
            "parse_status": waveform_index.get("parse_status"),
            "artifact": waveform_index.get("artifact"),
            "adapter": waveform_index.get("adapter"),
            "signal": signal,
        },
        "hierarchy": hierarchy_payload,
        "source_hierarchy": source_hierarchy_payload,
        "elaborated_hierarchy": elaborated_hierarchy_payload,
        "elaborated_port": elaborated_port,
        "source": source,
        "connectivity": connectivity_payload,
        "elaborated_connectivity": elaborated_connectivity_payload,
        "note": note,
    }


def write_crossprobe_report(
    project: ProjectConfig,
    signal_query: str,
    *,
    run_id: str | None = None,
    input_path: str | Path | None = None,
    output: str | Path = ".zddv/debug/crossprobe.json",
    fst_converter: str | Path | None = None,
    fst_converter_timeout_s: float = 120.0,
) -> dict[str, Any]:
    design = write_design_index(project)
    connectivity = write_connectivity_index(project)
    waveform = write_waveform_index(
        project,
        run_id=run_id,
        input_path=input_path,
        fst_converter=fst_converter,
        fst_converter_timeout_s=fst_converter_timeout_s,
    )
    elaborated_evidence = load_persisted_elaborated_evidence(project)
    elaborated_index = (
        elaborated_evidence.get("index")
        if elaborated_evidence.get("status") == "PRESENT"
        else None
    )
    report = build_crossprobe(
        project,
        signal_query,
        waveform,
        design_index=design,
        connectivity_index=connectivity,
        elaborated_index=elaborated_index,
    )
    report["design_index_path"] = design["path"]
    report["connectivity_index_path"] = connectivity["path"]
    report["waveform_index_path"] = waveform["path"]
    report["elaborated_evidence"] = {
        key: value
        for key, value in elaborated_evidence.items()
        if key != "index"
    }
    if elaborated_evidence.get("status") == "PRESENT":
        report["elaborated_index_path"] = elaborated_evidence["path"]

    destination = Path(output)
    if not destination.is_absolute():
        destination = project.root / destination
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return {**report, "report_path": str(destination)}

