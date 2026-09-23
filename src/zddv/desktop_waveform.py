from __future__ import annotations

from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.connectivity import build_connectivity_index
from zddv.crossprobe import build_crossprobe, load_persisted_elaborated_evidence
from zddv.design_index import build_design_index
from zddv.waveform import build_waveform_index, select_waveform_run
from zddv.waveform_probe import probe_vcd_signals


def build_desktop_waveform_snapshot(
    project: ProjectConfig,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build an in-memory waveform navigation model without writing artifacts."""
    run = select_waveform_run(project, run_id=run_id)
    waveform_path = Path(str(run["waveform_path"])).resolve()
    index = build_waveform_index(
        waveform_path,
        run_id=str(run["run_id"]),
        project_name=project.name,
    )
    return {
        "run_id": str(run["run_id"]),
        "test_name": run.get("test_name"),
        "status": str(run["status"]),
        "path": str(waveform_path),
        "format": str(index["format"]),
        "parse_status": str(index["parse_status"]),
        "timescale": index.get("timescale"),
        "artifact": dict(index["artifact"]),
        "summary": dict(index["summary"]),
        "scopes": list(index.get("scopes", [])),
        "signals": list(index.get("signals", [])),
        "note": index.get("note"),
    }


def probe_desktop_waveform_signal(
    project: ProjectConfig,
    signal: str,
    *,
    run_id: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    max_changes: int = 500,
) -> dict[str, Any]:
    """Probe one recorded VCD signal in memory without persisting a probe report."""
    navigation = build_desktop_waveform_snapshot(project, run_id=run_id)
    if navigation["format"] != "vcd" or navigation["parse_status"] != "indexed":
        raise RuntimeError(
            "Desktop waveform probing currently requires a recorded indexed VCD artifact."
        )

    result = probe_vcd_signals(
        navigation["path"],
        [signal],
        start_time=start_time,
        end_time=end_time,
        max_changes=max_changes,
    )
    result["run_id"] = navigation["run_id"]
    result["test_name"] = navigation["test_name"]
    return result



def crossprobe_desktop_waveform_signal(
    project: ProjectConfig,
    signal: str,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Cross-probe one recorded signal entirely in memory for the desktop viewer."""
    navigation = build_desktop_waveform_snapshot(project, run_id=run_id)
    if navigation["parse_status"] != "indexed":
        raise RuntimeError(
            "Desktop waveform cross-probing requires an indexed waveform artifact."
        )

    elaborated_evidence = load_persisted_elaborated_evidence(project)
    elaborated_index = (
        elaborated_evidence.get("index")
        if elaborated_evidence.get("status") == "PRESENT"
        else None
    )
    waveform_index = {
        "parse_status": navigation["parse_status"],
        "run_id": navigation["run_id"],
        "format": navigation["format"],
        "artifact": navigation["artifact"],
        "signals": navigation["signals"],
    }
    report = build_crossprobe(
        project,
        signal,
        waveform_index,
        design_index=build_design_index(project),
        connectivity_index=build_connectivity_index(project),
        elaborated_index=elaborated_index,
    )
    report["elaborated_evidence"] = {
        key: value
        for key, value in elaborated_evidence.items()
        if key != "index"
    }
    return report


def _desktop_boundary_role_item_is_normalized(
    item: Any,
    role: str,
) -> bool:
    """Validate one persisted boundary-role item against the core producer contract."""
    if not isinstance(item, dict):
        return False

    for key in ("instance_path", "pin", "parent_instance_path", "parent_signal"):
        value = item.get(key)
        if not isinstance(value, str) or not value:
            return False

    query_side = item.get("query_side")
    if query_side not in {"parent_signal", "child_port"}:
        return False

    direction = item.get("port_direction")
    relationship = item.get("relationship")
    expected_relationship = {
        "input": "parent_signal_to_child_input",
        "output": "child_output_to_parent_signal",
        "inout": "bidirectional_child_port",
        None: "direct_pin_varref",
    }.get(direction)
    if expected_relationship is None or relationship != expected_relationship:
        return False

    if role == "DRIVER":
        return (
            query_side == "parent_signal" and direction in {"output", "inout"}
        ) or (
            query_side == "child_port" and direction in {"input", "inout"}
        )
    if role == "LOAD":
        return (
            query_side == "parent_signal" and direction in {"input", "inout"}
        ) or (
            query_side == "child_port" and direction in {"output", "inout"}
        )
    if role == "UNCLASSIFIED":
        return direction is None
    return False


def desktop_crossprobe_evidence_rows(
    report: dict[str, Any],
    *,
    elaborated_limit: int = 20,
) -> list[tuple[str, str]]:
    """Format bounded cross-probe evidence without adding desktop-side inference."""
    if elaborated_limit <= 0:
        raise ValueError("Elaborated evidence row limit must be > 0")

    rows: list[tuple[str, str]] = []

    hierarchy = report.get("hierarchy") or {}
    hierarchy_detail = report.get("note") or "No hierarchy match."
    if isinstance(hierarchy, dict) and hierarchy:
        hierarchy_detail = (
            f"{report.get('hierarchy_resolution') or 'unknown'} · "
            f"{hierarchy.get('design_path') or '-'} · "
            f"{hierarchy.get('type') or '-'}"
        )
    rows.append(("Hierarchy", str(hierarchy_detail)))

    source = report.get("source") or {}
    source_detail = "No RTL declaration match."
    if isinstance(source, dict) and source:
        declaration = source.get("declaration") or {}
        location = str(source.get("file") or "-")
        if isinstance(declaration, dict) and declaration.get("line") is not None:
            location += f":{declaration['line']}"
        source_detail = f"{source.get('unit') or '-'} · {location}"
    rows.append(("RTL source", source_detail))

    connectivity = report.get("connectivity") or {}
    if isinstance(connectivity, dict):
        drivers = connectivity.get("drivers") or []
        loads = connectivity.get("loads") or []
    else:
        drivers = []
        loads = []
    rows.append(("Drivers", f"{len(drivers)} source-structural item(s)"))
    rows.append(("Loads", f"{len(loads)} source-structural item(s)"))

    elaborated = report.get("elaborated_evidence") or {}
    elaborated_detail = (
        str(elaborated.get("status") or "NOT_PRESENT")
        if isinstance(elaborated, dict)
        else "NOT_PRESENT"
    )
    if isinstance(elaborated, dict) and elaborated.get("error"):
        elaborated_detail += f" · {elaborated['error']}"
    rows.append(("Elaboration", elaborated_detail))

    elaborated_port = report.get("elaborated_port")
    if isinstance(elaborated_port, dict):
        port_detail = str(elaborated_port.get("status") or "UNKNOWN")
        port = elaborated_port.get("port")
        if elaborated_port.get("status") == "MATCHED" and isinstance(port, dict):
            port_detail += (
                f" · {port.get('module') or '-'}."
                f"{port.get('name') or elaborated_port.get('signal') or '-'}"
                f" · direction={port.get('direction') or 'unknown'}"
            )
        elif elaborated_port.get("reason"):
            port_detail += f" · {elaborated_port['reason']}"
        rows.append(("Elaborated port", port_detail))

    elaborated_connectivity = report.get("elaborated_connectivity")
    trusted_elaborated_connectivity = (
        isinstance(elaborated_connectivity, dict)
        and elaborated_connectivity.get("analysis_level")
        == "simulator_elaborated_direct_pin_varref"
    )
    if trusted_elaborated_connectivity:
        parent_bindings = [
            item
            for item in elaborated_connectivity.get("parent_signal_bindings", [])
            if isinstance(item, dict)
        ]
        instance_bindings = [
            item
            for item in elaborated_connectivity.get("instance_port_bindings", [])
            if isinstance(item, dict)
        ]
        unsupported_bindings = [
            item
            for item in elaborated_connectivity.get(
                "unsupported_instance_port_bindings", []
            )
            if isinstance(item, dict)
        ]
        connectivity_detail = (
            "simulator_elaborated_direct_pin_varref · "
            f"parent-signal bindings={len(parent_bindings)} · "
            f"instance-port bindings={len(instance_bindings)}"
        )
        relationships = sorted(
            {
                str(item.get("relationship"))
                for item in parent_bindings + instance_bindings
                if item.get("relationship")
            }
        )
        if relationships:
            connectivity_detail += f" · relationships={','.join(relationships)}"
        if unsupported_bindings:
            connectivity_detail += f" · unsupported={len(unsupported_bindings)}"
        rows.append(("Elaborated connectivity", connectivity_detail))

        evidence_items: list[tuple[str, dict[str, Any]]] = [
            ("normalized", item) for item in parent_bindings + instance_bindings
        ]
        evidence_items.extend(
            ("unsupported", item) for item in unsupported_bindings
        )

        for kind, binding in evidence_items[:elaborated_limit]:
            child = (
                f"{binding.get('instance_path') or '-'}."
                f"{binding.get('pin') or '-'}"
            )
            direction = binding.get("port_direction") or "unknown"
            if kind == "unsupported":
                expression = binding.get("expression_type") or "unknown"
                detail = (
                    f"{child} · UNSUPPORTED · direction={direction} · "
                    f"expression={expression} · no direct VARREF relation"
                )
            else:
                parent = (
                    f"{binding.get('parent_instance_path') or '-'}."
                    f"{binding.get('parent_signal') or '-'}"
                )
                relationship = (
                    binding.get("relationship") or "direct_pin_varref"
                )
                if relationship == "parent_signal_to_child_input":
                    relation = f"{parent} -> {child}"
                elif relationship == "child_output_to_parent_signal":
                    relation = f"{child} -> {parent}"
                elif relationship == "bidirectional_child_port":
                    relation = f"{parent} <-> {child}"
                else:
                    relation = f"parent={parent} · child={child}"
                detail = f"{relation} · {direction} · {relationship}"
            rows.append(("Elaborated pin", detail))

        hidden = len(evidence_items) - min(len(evidence_items), elaborated_limit)
        if hidden:
            rows.append(
                (
                    "Elaborated pin",
                    f"{hidden} additional evidence item(s) not shown",
                )
            )

        boundary_role_groups = (
            ("DRIVER", elaborated_connectivity.get("boundary_drivers")),
            ("LOAD", elaborated_connectivity.get("boundary_loads")),
            (
                "UNCLASSIFIED",
                elaborated_connectivity.get("boundary_unclassified_bindings"),
            ),
        )
        boundary_roles_normalized = all(
            isinstance(items, list)
            and all(
                _desktop_boundary_role_item_is_normalized(item, role)
                for item in items
            )
            for role, items in boundary_role_groups
        )
        if boundary_roles_normalized:
            boundary_drivers = list(elaborated_connectivity["boundary_drivers"])
            boundary_loads = list(elaborated_connectivity["boundary_loads"])
            boundary_unclassified = list(
                elaborated_connectivity["boundary_unclassified_bindings"]
            )
            rows.append(
                (
                    "Elaborated boundary roles",
                    f"drivers={len(boundary_drivers)} · "
                    f"loads={len(boundary_loads)} · "
                    f"unclassified={len(boundary_unclassified)}",
                )
            )

            role_items: list[tuple[str, dict[str, Any]]] = []
            role_items.extend(("DRIVER", item) for item in boundary_drivers)
            role_items.extend(("LOAD", item) for item in boundary_loads)
            role_items.extend(
                ("UNCLASSIFIED", item) for item in boundary_unclassified
            )
            for role, binding in role_items[:elaborated_limit]:
                parent = (
                    f"{binding.get('parent_instance_path') or '-'}."
                    f"{binding.get('parent_signal') or '-'}"
                )
                child = (
                    f"{binding.get('instance_path') or '-'}."
                    f"{binding.get('pin') or '-'}"
                )
                rows.append(
                    (
                        "Elaborated role",
                        f"{role} · query_side="
                        f"{binding.get('query_side') or 'unknown'} · "
                        f"parent={parent} · child={child} · "
                        f"direction={binding.get('port_direction') or 'unknown'}",
                    )
                )

            hidden_roles = len(role_items) - min(
                len(role_items),
                elaborated_limit,
            )
            if hidden_roles:
                rows.append(
                    (
                        "Elaborated role",
                        f"{hidden_roles} additional boundary role item(s) not shown",
                    )
                )

    elaborated_internal_connectivity = report.get(
        "elaborated_internal_connectivity"
    )
    trusted_internal_connectivity = (
        isinstance(elaborated_internal_connectivity, dict)
        and elaborated_internal_connectivity.get("analysis_level")
        == "simulator_elaborated_module_root_assignw_direct_varref"
        and elaborated_internal_connectivity.get("evidence_contract")
        == "verilator_module_root_assignw_direct_varref_only"
        and elaborated_internal_connectivity.get("role_semantics")
        == "direct_continuous_assignment"
        and elaborated_internal_connectivity.get("status")
        in {"NORMALIZED", "PARTIAL"}
    )
    if trusted_internal_connectivity:
        internal_status = elaborated_internal_connectivity["status"]
        query_instance_path = elaborated_internal_connectivity.get(
            "query_instance_path"
        )
        query_module = elaborated_internal_connectivity.get("query_module")
        internal_drivers = elaborated_internal_connectivity.get("drivers")
        internal_loads = elaborated_internal_connectivity.get("loads")
        unresolved_assignments = elaborated_internal_connectivity.get(
            "unresolved_assignments"
        )

        def trusted_internal_edge(item: Any) -> bool:
            return (
                isinstance(item, dict)
                and item.get("kind") == "continuous_assignment"
                and item.get("assignment_type") == "ASSIGNW"
                and item.get("instance_path") == query_instance_path
                and item.get("module") == query_module
                and isinstance(item.get("source_signal"), str)
                and bool(item.get("source_signal"))
                and isinstance(item.get("target_signal"), str)
                and bool(item.get("target_signal"))
            )

        def trusted_unresolved_assignment(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            query_references = item.get("query_references")
            return (
                item.get("status") == "UNSUPPORTED"
                and item.get("assignment_type") == "ASSIGNW"
                and item.get("instance_path") == query_instance_path
                and item.get("module") == query_module
                and isinstance(query_references, list)
                and bool(query_references)
                and all(
                    reference in {"lhs", "rhs"}
                    for reference in query_references
                )
            )

        internal_roles_normalized = (
            isinstance(query_instance_path, str)
            and bool(query_instance_path)
            and isinstance(query_module, str)
            and bool(query_module)
            and isinstance(internal_drivers, list)
            and isinstance(internal_loads, list)
            and isinstance(unresolved_assignments, list)
            and all(
                trusted_internal_edge(item)
                for item in internal_drivers
            )
            and all(
                trusted_internal_edge(item)
                for item in internal_loads
            )
            and all(
                trusted_unresolved_assignment(item)
                for item in unresolved_assignments
            )
        )
        status_consistent = (
            internal_status == "PARTIAL"
            if unresolved_assignments
            else internal_status == "NORMALIZED"
        )
        if internal_roles_normalized and status_consistent:
            rows.append(
                (
                    "Elaborated internal connectivity",
                    "simulator_elaborated_module_root_assignw_direct_varref · "
                    f"status={internal_status} · "
                    f"drivers={len(internal_drivers)} · "
                    f"loads={len(internal_loads)} · "
                    f"unresolved={len(unresolved_assignments)}",
                )
            )

            internal_items: list[tuple[str, dict[str, Any]]] = []
            internal_items.extend(("DRIVER", item) for item in internal_drivers)
            internal_items.extend(("LOAD", item) for item in internal_loads)
            internal_items.extend(
                ("UNRESOLVED", item) for item in unresolved_assignments
            )

            for role, item in internal_items[:elaborated_limit]:
                location = item.get("location")
                location_text = "-"
                if isinstance(location, dict):
                    location_text = str(location.get("path") or "-")
                    if location.get("line") is not None:
                        location_text += f":{location['line']}"

                instance_path = item["instance_path"]
                if role == "UNRESOLVED":
                    query_sides = ",".join(
                        str(value)
                        for value in item["query_references"]
                    )
                    rows.append(
                        (
                            "Elaborated internal unresolved",
                            f"UNRESOLVED · instance={instance_path} · "
                            f"query_references={query_sides} · "
                            f"lhs={item.get('lhs_expression_type') or 'unknown'} · "
                            f"rhs={item.get('rhs_expression_type') or 'unknown'} · "
                            f"location={location_text}",
                        )
                    )
                    continue

                rows.append(
                    (
                        "Elaborated internal edge",
                        f"{role} · {instance_path}.{item['source_signal']} -> "
                        f"{instance_path}.{item['target_signal']} · ASSIGNW · "
                        f"location={location_text}",
                    )
                )

            hidden_internal = len(internal_items) - min(
                len(internal_items),
                elaborated_limit,
            )
            if hidden_internal:
                rows.append(
                    (
                        "Elaborated internal edge",
                        f"{hidden_internal} additional internal evidence item(s) "
                        "not shown",
                    )
                )

    elaborated_source_correlation = report.get("elaborated_source_correlation")
    trusted_source_correlation = (
        isinstance(elaborated_source_correlation, dict)
        and elaborated_source_correlation.get("analysis_level")
        == "simulator_elaborated_to_source_structural_correlation"
        and elaborated_source_correlation.get("role_semantics")
        == "source_structural_only"
    )
    if trusted_source_correlation:
        correlations = [
            item
            for item in elaborated_source_correlation.get("correlations", [])
            if isinstance(item, dict)
        ]
        status_counts = {
            status: sum(
                1
                for item in correlations
                if str(item.get("status") or "UNKNOWN") == status
            )
            for status in ("MATCHED", "NOT_FOUND", "AMBIGUOUS", "UNAVAILABLE")
        }
        correlation_detail = (
            "simulator_elaborated_to_source_structural_correlation · "
            f"correlations={len(correlations)} · "
            f"matched={status_counts['MATCHED']} · "
            f"not-found={status_counts['NOT_FOUND']} · "
            f"ambiguous={status_counts['AMBIGUOUS']} · "
            f"unavailable={status_counts['UNAVAILABLE']} · "
            "roles=source_structural_only"
        )
        rows.append(("Elaborated/source correlation", correlation_detail))

        matched = [
            item
            for item in correlations
            if item.get("status") == "MATCHED"
            and isinstance(item.get("source_edge"), dict)
        ]
        if len(matched) == 1:
            item = matched[0]
            source_edge = item["source_edge"]
            source_roles = ",".join(
                str(role)
                for role in item.get("source_roles", [])
                if role
            ) or "-"
            source_location = str(source_edge.get("file") or "-")
            if source_edge.get("line") is not None:
                source_location += f":{source_edge['line']}"
            rows.append(
                (
                    "Correlated source edge",
                    (
                        f"{item.get('parent_instance_path') or '-'}."
                        f"{item.get('parent_signal') or '-'} ↔ "
                        f"{item.get('instance_path') or '-'}."
                        f"{item.get('pin') or '-'} · "
                        f"binding_side={item.get('binding_side') or '-'} · "
                        f"source={item.get('source_unit') or '-'} · "
                        f"source_roles={source_roles} · "
                        f"location={source_location}"
                    ),
                )
            )

    elaborated_boundary = report.get("elaborated_boundary")
    if isinstance(elaborated_boundary, dict):
        boundary_detail = str(elaborated_boundary.get("status") or "UNKNOWN")
        if elaborated_boundary.get("flow"):
            boundary_detail += f" · flow={elaborated_boundary['flow']}"
        if elaborated_boundary.get("reason"):
            boundary_detail += f" · {elaborated_boundary['reason']}"
        rows.append(("Elaborated boundary", boundary_detail))

    return rows

def attach_desktop_waveform_tab(notebook: Any, project: ProjectConfig) -> None:
    """Attach a self-contained read-only waveform navigation tab to a Tk notebook."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop waveform navigation requires Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Waveform")

    info_var = tk.StringVar(value="No recorded waveform selected.")
    ttk.Label(tab, textvariable=info_var, anchor="w").pack(fill="x", pady=(0, 6))

    signal_tree = ttk.Treeview(
        tab,
        columns=("path", "width", "type"),
        show="headings",
        height=10,
        selectmode="browse",
    )
    for column, title, width in (
        ("path", "Signal", 620),
        ("width", "Width", 80),
        ("type", "VCD type", 120),
    ):
        signal_tree.heading(column, text=title)
        signal_tree.column(column, width=width, anchor="w")
    signal_tree.pack(fill="both", expand=True)

    controls = ttk.Frame(tab, padding=(0, 8, 0, 6))
    controls.pack(fill="x")
    start_var = tk.StringVar(value="")
    end_var = tk.StringVar(value="")
    max_var = tk.StringVar(value="500")

    ttk.Label(controls, text="Start").pack(side="left")
    ttk.Entry(controls, textvariable=start_var, width=10).pack(
        side="left", padx=(4, 10)
    )
    ttk.Label(controls, text="End").pack(side="left")
    ttk.Entry(controls, textvariable=end_var, width=10).pack(
        side="left", padx=(4, 10)
    )
    ttk.Label(controls, text="Max changes").pack(side="left")
    ttk.Entry(controls, textvariable=max_var, width=8).pack(
        side="left", padx=(4, 10)
    )

    probe_tree = ttk.Treeview(
        tab,
        columns=("time", "value"),
        show="headings",
        height=8,
    )
    probe_tree.heading("time", text="Time")
    probe_tree.heading("value", text="Value")
    probe_tree.column("time", width=180, anchor="w")
    probe_tree.column("value", width=700, anchor="w")
    probe_tree.pack(fill="both", expand=True)

    evidence_tree = ttk.Treeview(
        tab,
        columns=("kind", "details"),
        show="headings",
        height=9,
    )
    evidence_tree.heading("kind", text="Cross-probe evidence")
    evidence_tree.heading("details", text="Resolved detail")
    evidence_tree.column("kind", width=150, anchor="w")
    evidence_tree.column("details", width=760, anchor="w")
    evidence_tree.pack(fill="both", expand=True, pady=(8, 0))

    state: dict[str, Any] = {"waveform": None}

    def clear(tree: Any) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def optional_int(value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be an integer") from exc
        if parsed < 0:
            raise ValueError(f"{label} must be >= 0")
        return parsed

    def refresh_waveform() -> None:
        clear(signal_tree)
        clear(probe_tree)
        clear(evidence_tree)
        try:
            navigation = build_desktop_waveform_snapshot(project)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            state["waveform"] = None
            info_var.set(f"No usable recorded waveform: {exc}")
            return

        state["waveform"] = navigation
        summary = navigation["summary"]
        info_var.set(
            f"{navigation['run_id']} · {navigation['format'].upper()} · "
            f"{summary['signals']} signal(s) · {summary['scopes']} scope(s) · "
            f"timescale={navigation.get('timescale') or 'unknown'}"
        )
        for signal in navigation["signals"]:
            signal_tree.insert(
                "",
                "end",
                values=(
                    signal["path"],
                    signal["width"],
                    signal["var_type"],
                ),
            )

    def probe_selected() -> None:
        navigation = state["waveform"]
        if navigation is None:
            info_var.set("No recorded waveform is available to probe.")
            return

        selection = signal_tree.selection()
        if not selection:
            info_var.set("Select one waveform signal before probing.")
            return

        values = signal_tree.item(selection[0], "values")
        signal_path = str(values[0])
        try:
            start_time = optional_int(start_var.get(), "Start time")
            end_time = optional_int(end_var.get(), "End time")
            max_changes = int(max_var.get().strip() or "500")
            result = probe_desktop_waveform_signal(
                project,
                signal_path,
                run_id=navigation["run_id"],
                start_time=start_time,
                end_time=end_time,
                max_changes=max_changes,
            )
            crossprobe = crossprobe_desktop_waveform_signal(
                project,
                signal_path,
                run_id=navigation["run_id"],
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            info_var.set(f"Probe error: {exc}")
            return

        clear(probe_tree)
        clear(evidence_tree)
        signal = result["signals"][0]
        for change in signal["changes"]:
            probe_tree.insert(
                "",
                "end",
                values=(change["time"], change["value"]),
            )
        for kind, details in desktop_crossprobe_evidence_rows(crossprobe):
            evidence_tree.insert("", "end", values=(kind, details))

        suffix = " (truncated)" if signal["truncated"] else ""
        info_var.set(
            f"{result['run_id']} · {signal_path} · "
            f"{len(signal['changes'])} change(s){suffix} · "
            f"timescale={result.get('timescale') or 'unknown'}"
        )

    ttk.Button(controls, text="Refresh waveform", command=refresh_waveform).pack(
        side="right"
    )
    ttk.Button(controls, text="Probe + cross-probe", command=probe_selected).pack(
        side="left"
    )

    refresh_waveform()
