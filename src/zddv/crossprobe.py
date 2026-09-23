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

    pin_bindings = index.get("pin_bindings")
    if pin_bindings is not None and not isinstance(pin_bindings, list):
        errors.append("pin_bindings is not a list")

    pin_binding_evidence = index.get("pin_binding_evidence")
    if pin_binding_evidence is not None and not isinstance(
        pin_binding_evidence,
        dict,
    ):
        errors.append("pin_binding_evidence is not an object")
    elif (
        isinstance(pin_binding_evidence, dict)
        and pin_binding_evidence.get("status") == "NORMALIZED"
        and not isinstance(pin_bindings, list)
    ):
        errors.append("normalized pin_binding_evidence requires a pin_bindings list")

    internal_assignments = index.get("internal_assignments")
    if internal_assignments is not None and not isinstance(
        internal_assignments,
        list,
    ):
        errors.append("internal_assignments is not a list")

    internal_assignment_evidence = index.get("internal_assignment_evidence")
    if internal_assignment_evidence is not None and not isinstance(
        internal_assignment_evidence,
        dict,
    ):
        errors.append("internal_assignment_evidence is not an object")
    elif (
        isinstance(internal_assignment_evidence, dict)
        and internal_assignment_evidence.get("status") == "NORMALIZED"
        and not isinstance(internal_assignments, list)
    ):
        errors.append(
            "normalized internal_assignment_evidence requires an "
            "internal_assignments list"
        )
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
    ports = elaborated_index.get("ports")
    if (
        not isinstance(evidence, dict)
        or evidence.get("status") != "NORMALIZED"
        or not isinstance(ports, list)
    ):
        return {}

    directions: dict[tuple[str, str], str] = {}
    ambiguous: set[tuple[str, str]] = set()
    for port in ports:
        if not isinstance(port, dict):
            continue
        module = port.get("module")
        direction = port.get("direction")
        if not module or not direction:
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
        direction_value = str(direction).lower()
        for alias in aliases:
            key = (str(module), alias)
            current = directions.get(key)
            if current is not None and current != direction_value:
                ambiguous.add(key)
                continue
            directions[key] = direction_value
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
    unsupported_instance_port_bindings: list[dict[str, Any]] = []
    boundary_drivers: list[dict[str, Any]] = []
    boundary_loads: list[dict[str, Any]] = []
    boundary_unclassified_bindings: list[dict[str, Any]] = []

    for binding in elaborated_index.get("pin_bindings", []):
        if not isinstance(binding, dict):
            continue
        child_path = binding.get("instance_path")
        parent_path = binding.get("parent_instance_path")
        pin = binding.get("pin")
        child_module = binding.get("instance_module")

        if binding.get("status") != "NORMALIZED":
            if (
                binding.get("status") == "UNSUPPORTED"
                and child_path
                and pin
                and str(child_path) == instance_path
                and str(pin) == signal_name
            ):
                direction = port_directions.get((str(child_module), str(pin)))
                unsupported_instance_port_bindings.append(
                    {
                        "status": "UNSUPPORTED",
                        "instance_path": str(child_path),
                        "instance_module": (
                            str(child_module)
                            if child_module is not None
                            else None
                        ),
                        "pin": str(pin),
                        "parent_instance_path": (
                            str(parent_path) if parent_path is not None else None
                        ),
                        "port_direction": direction,
                        "expression_type": binding.get("expression_type"),
                        "generate_scopes": list(
                            binding.get("generate_scopes", [])
                        ),
                        "pin_location": binding.get("pin_location"),
                    }
                )
            continue

        parent_signal = binding.get("signal")
        if not child_path or not parent_path or not pin or not parent_signal:
            continue

        direction = port_directions.get((str(child_module), str(pin)))
        normalized = {
            "instance_path": str(child_path),
            "instance_module": (
                str(child_module) if child_module is not None else None
            ),
            "pin": str(pin),
            "pin_aliases": sorted(
                {
                    str(value)
                    for value in (
                        binding.get("pin"),
                        binding.get("pin_elaborated_name"),
                        binding.get("pin_verilog_name"),
                        binding.get("pin_original_name"),
                    )
                    if value
                }
            ),
            "parent_instance_path": str(parent_path),
            "parent_signal": str(parent_signal),
            "port_direction": direction,
            "relationship": _pin_relationship(direction),
            "generate_scopes": list(binding.get("generate_scopes", [])),
            "pin_location": binding.get("pin_location"),
            "signal_location": binding.get("signal_location"),
        }

        if str(parent_path) == instance_path and str(parent_signal) == signal_name:
            parent_signal_bindings.append(normalized)
            role_entry = {**normalized, "query_side": "parent_signal"}
            if direction in {"output", "inout"}:
                boundary_drivers.append(role_entry)
            if direction in {"input", "inout"}:
                boundary_loads.append(role_entry)
            if direction not in {"input", "output", "inout"}:
                boundary_unclassified_bindings.append(role_entry)

        if str(child_path) == instance_path and str(pin) == signal_name:
            instance_port_bindings.append(normalized)
            role_entry = {**normalized, "query_side": "child_port"}
            if direction in {"input", "inout"}:
                boundary_drivers.append(role_entry)
            if direction in {"output", "inout"}:
                boundary_loads.append(role_entry)
            if direction not in {"input", "output", "inout"}:
                boundary_unclassified_bindings.append(role_entry)

    if (
        not parent_signal_bindings
        and not instance_port_bindings
        and not unsupported_instance_port_bindings
    ):
        return None

    return {
        "analysis_level": "simulator_elaborated_direct_pin_varref",
        "evidence_contract": evidence.get("contract"),
        "query_instance_path": instance_path,
        "query_signal": signal_name,
        "parent_signal_bindings": sorted(
            parent_signal_bindings,
            key=lambda item: (
                item["instance_path"],
                item["pin"],
            ),
        ),
        "instance_port_bindings": sorted(
            instance_port_bindings,
            key=lambda item: (
                item["parent_instance_path"],
                item["parent_signal"],
            ),
        ),
        "boundary_drivers": sorted(
            boundary_drivers,
            key=lambda item: (
                item["query_side"],
                item["instance_path"],
                item["pin"],
                item["parent_instance_path"],
                item["parent_signal"],
            ),
        ),
        "boundary_loads": sorted(
            boundary_loads,
            key=lambda item: (
                item["query_side"],
                item["instance_path"],
                item["pin"],
                item["parent_instance_path"],
                item["parent_signal"],
            ),
        ),
        "boundary_unclassified_bindings": sorted(
            boundary_unclassified_bindings,
            key=lambda item: (
                item["query_side"],
                item["instance_path"],
                item["pin"],
                item["parent_instance_path"],
                item["parent_signal"],
            ),
        ),
        "unsupported_instance_port_bindings": sorted(
            unsupported_instance_port_bindings,
            key=lambda item: (
                item["instance_path"],
                item["pin"],
                str(item.get("expression_type") or ""),
            ),
        ),
    }


def _elaborated_internal_assignment_connectivity(
    elaborated_index: dict[str, Any],
    *,
    instance_path: str,
    instance_module: str | None,
    signal_name: str,
) -> dict[str, Any] | None:
    """Project normalized direct module assignments onto one exact instance."""
    evidence = elaborated_index.get("internal_assignment_evidence")
    assignments = elaborated_index.get("internal_assignments")
    if (
        not isinstance(evidence, dict)
        or evidence.get("status") != "NORMALIZED"
        or not isinstance(assignments, list)
        or not instance_module
    ):
        return None

    module_records = [
        item
        for item in assignments
        if isinstance(item, dict)
        and item.get("module") == instance_module
    ]
    if not module_records:
        return None

    variants = {
        str(item["module_elaborated_name"])
        for item in module_records
        if item.get("module_elaborated_name")
    }
    if len(variants) > 1:
        return {
            "analysis_level": "simulator_elaborated_direct_assignment_varref",
            "evidence_contract": evidence.get("contract"),
            "status": "UNAVAILABLE",
            "reason": "module_elaborated_variant_ambiguous",
            "query_instance_path": instance_path,
            "query_module": instance_module,
            "query_signal": signal_name,
            "module_elaborated_candidates": sorted(variants),
            "direct_assignment_drivers": [],
            "direct_assignment_loads": [],
            "unsupported_assignments": [],
            "completeness": "subset_only",
        }

    drivers: list[dict[str, Any]] = []
    loads: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for item in module_records:
        if item.get("status") == "NORMALIZED":
            lhs = item.get("lhs_signal")
            rhs = item.get("rhs_signal")
            if not lhs or not rhs:
                continue
            edge = {
                "instance_path": instance_path,
                "module": instance_module,
                "module_elaborated_name": item.get("module_elaborated_name"),
                "assignment_type": item.get("assignment_type"),
                "lhs_signal": str(lhs),
                "rhs_signal": str(rhs),
                "location": item.get("location"),
                "lhs_location": item.get("lhs_location"),
                "rhs_location": item.get("rhs_location"),
            }
            if str(lhs) == signal_name:
                drivers.append(
                    {
                        **edge,
                        "source_signal": str(rhs),
                        "sink_signal": str(lhs),
                        "query_role": "sink",
                    }
                )
            if str(rhs) == signal_name:
                loads.append(
                    {
                        **edge,
                        "source_signal": str(rhs),
                        "sink_signal": str(lhs),
                        "query_role": "source",
                    }
                )
            continue

        if item.get("status") != "UNSUPPORTED":
            continue
        referenced = [
            str(value)
            for value in item.get("referenced_signals", [])
            if value
        ]
        if signal_name not in referenced:
            continue
        unsupported.append(
            {
                "instance_path": instance_path,
                "module": instance_module,
                "module_elaborated_name": item.get("module_elaborated_name"),
                "assignment_type": item.get("assignment_type"),
                "lhs_expression_type": item.get("lhs_expression_type"),
                "rhs_expression_type": item.get("rhs_expression_type"),
                "referenced_signals": sorted(set(referenced)),
                "location": item.get("location"),
            }
        )

    if not drivers and not loads and not unsupported:
        return None

    edge_key = lambda item: (
        str(item.get("assignment_type") or ""),
        str(item.get("lhs_signal") or ""),
        str(item.get("rhs_signal") or ""),
        str((item.get("location") or {}).get("path") or ""),
        int((item.get("location") or {}).get("line") or 0),
    )
    return {
        "analysis_level": "simulator_elaborated_direct_assignment_varref",
        "evidence_contract": evidence.get("contract"),
        "status": "MATCHED",
        "query_instance_path": instance_path,
        "query_module": instance_module,
        "query_signal": signal_name,
        "module_elaborated_name": (
            next(iter(variants)) if len(variants) == 1 else None
        ),
        "direct_assignment_drivers": sorted(drivers, key=edge_key),
        "direct_assignment_loads": sorted(loads, key=edge_key),
        "unsupported_assignments": sorted(
            unsupported,
            key=lambda item: (
                str(item.get("assignment_type") or ""),
                str((item.get("location") or {}).get("path") or ""),
                int((item.get("location") or {}).get("line") or 0),
            ),
        ),
        "completeness": "subset_only",
    }


def _correlate_elaborated_pin_bindings_with_source(
    elaborated_connectivity: dict[str, Any] | None,
    *,
    elaborated_index: dict[str, Any],
    design_index: dict[str, Any],
    connectivity_index: dict[str, Any],
) -> dict[str, Any] | None:
    """Correlate exact elaborated pin bindings with source instance-port edges.

    Driver/load roles remain source-structural evidence. This helper only proves
    that a normalized direct pin binding and an already-qualified source
    instance-port edge describe the same module-boundary connection.
    """
    if elaborated_connectivity is None:
        return None

    elaborated_instances = [
        item
        for item in elaborated_index.get("instances", [])
        if isinstance(item, dict) and item.get("path")
    ]
    instances_by_path = {
        str(item["path"]): item
        for item in elaborated_instances
    }

    def correlate(
        binding: dict[str, Any],
        *,
        binding_side: str,
    ) -> dict[str, Any]:
        child_path = str(binding.get("instance_path") or "")
        parent_path = str(binding.get("parent_instance_path") or "")
        pin = str(binding.get("pin") or "")
        parent_signal = str(binding.get("parent_signal") or "")
        identity = {
            "binding_side": binding_side,
            "instance_path": child_path,
            "instance_module": binding.get("instance_module"),
            "pin": pin,
            "parent_instance_path": parent_path,
            "parent_signal": parent_signal,
        }

        parent_instance = instances_by_path.get(parent_path)
        if parent_instance is None:
            return {
                **identity,
                "status": "UNAVAILABLE",
                "reason": "parent_elaborated_instance_not_found",
            }

        parent_unit = _design_unit_for_elaborated_instance(
            parent_instance,
            design_index,
        )
        if parent_unit is None:
            return {
                **identity,
                "status": "UNAVAILABLE",
                "reason": "parent_source_unit_not_resolved",
            }

        try:
            navigation = signal_navigation(
                connectivity_index,
                unit=str(parent_unit["name"]),
                signal=parent_signal,
            )
        except ValueError:
            return {
                **identity,
                "status": "NOT_FOUND",
                "reason": "parent_signal_not_in_source_connectivity",
                "source_unit": parent_unit["name"],
            }

        qualified = qualify_signal_navigation_with_elaboration(
            navigation,
            instance_path=parent_path,
            elaborated_instances=elaborated_instances,
        )
        pin_aliases = {
            str(value)
            for value in binding.get("pin_aliases", [])
            if value
        }
        pin_aliases.add(pin)

        candidates: list[tuple[dict[str, Any], str]] = []
        for role_key in ("drivers", "loads"):
            for edge in qualified.get(role_key, []):
                if edge.get("kind") != "instance_port":
                    continue
                resolution = edge.get("elaborated_child_resolution")
                if (
                    resolution == "exact"
                    and edge.get("elaborated_child_path") == child_path
                ):
                    match_basis = "source_edge_exact_child_path"
                elif (
                    resolution == "ambiguous"
                    and child_path
                    in {
                        str(value)
                        for value in edge.get(
                            "elaborated_child_candidates",
                            [],
                        )
                        if value
                    }
                ):
                    match_basis = (
                        "direct_pin_resolves_source_generated_candidate"
                    )
                else:
                    continue
                if str(edge.get("port") or "") not in pin_aliases:
                    continue
                child_module = binding.get("instance_module")
                if (
                    child_module
                    and edge.get("child_type")
                    and edge.get("child_type") != child_module
                ):
                    continue
                candidates.append((edge, match_basis))

        if not candidates:
            return {
                **identity,
                "status": "NOT_FOUND",
                "reason": "no_exact_source_instance_port_edge",
                "source_unit": parent_unit["name"],
            }

        groups: dict[
            tuple[Any, ...],
            list[tuple[dict[str, Any], str]],
        ] = {}
        for edge, match_basis in candidates:
            key = (
                edge.get("file"),
                edge.get("line"),
                edge.get("instance"),
                edge.get("child_type"),
                edge.get("port"),
                edge.get("expression"),
            )
            groups.setdefault(key, []).append((edge, match_basis))

        if len(groups) != 1:
            return {
                **identity,
                "status": "AMBIGUOUS",
                "source_unit": parent_unit["name"],
                "candidate_count": len(groups),
            }

        grouped_candidates = next(iter(groups.values()))
        edges = [edge for edge, _ in grouped_candidates]
        source_edge = {
            key: value
            for key, value in edges[0].items()
            if key != "role"
        }
        return {
            **identity,
            "status": "MATCHED",
            "source_unit": parent_unit["name"],
            "match_basis": sorted(
                {match_basis for _, match_basis in grouped_candidates}
            ),
            "source_roles": sorted(
                {
                    str(edge["role"])
                    for edge in edges
                    if edge.get("role")
                }
            ),
            "source_edge": source_edge,
        }

    correlations: list[dict[str, Any]] = []
    for binding_side, key in (
        ("parent_signal", "parent_signal_bindings"),
        ("instance_port", "instance_port_bindings"),
    ):
        for binding in elaborated_connectivity.get(key, []):
            if isinstance(binding, dict):
                correlations.append(
                    correlate(binding, binding_side=binding_side)
                )

    if not correlations:
        return None

    return {
        "analysis_level": (
            "simulator_elaborated_to_source_structural_correlation"
        ),
        "elaborated_analysis_level": elaborated_connectivity.get(
            "analysis_level"
        ),
        "source_analysis_level": connectivity_index.get("analysis_level"),
        "role_semantics": "source_structural_only",
        "correlations": sorted(
            correlations,
            key=lambda item: (
                item["parent_instance_path"],
                item["parent_signal"],
                item["instance_path"],
                item["pin"],
                item["binding_side"],
            ),
        ),
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
    elaborated_internal_connectivity_payload: dict[str, Any] | None = None
    elaborated_source_correlation: dict[str, Any] | None = None
    source_connectivity_index = connectivity_index
    status = "PARTIAL"
    note: str | None = None

    if elaborated_node is not None and elaborated_index is not None:
        elaborated_connectivity_payload = _elaborated_pin_connectivity(
            elaborated_index,
            instance_path=str(elaborated_node["path"]),
            signal_name=str(signal.get("name", "")),
        )
        elaborated_internal_connectivity_payload = (
            _elaborated_internal_assignment_connectivity(
                elaborated_index,
                instance_path=str(elaborated_node["path"]),
                instance_module=(
                    str(elaborated_node["module"])
                    if elaborated_node.get("module")
                    else None
                ),
                signal_name=str(signal.get("name", "")),
            )
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

        if source_connectivity_index is None:
            source_connectivity_index = build_connectivity_index(project)
        connectivity = source_connectivity_index
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

    if elaborated_connectivity_payload is not None and elaborated_index is not None:
        if source_connectivity_index is None:
            source_connectivity_index = build_connectivity_index(project)
        elaborated_source_correlation = (
            _correlate_elaborated_pin_bindings_with_source(
                elaborated_connectivity_payload,
                elaborated_index=elaborated_index,
                design_index=design,
                connectivity_index=source_connectivity_index,
            )
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
        "elaborated_internal_connectivity": (
            elaborated_internal_connectivity_payload
        ),
        "elaborated_source_correlation": elaborated_source_correlation,
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

