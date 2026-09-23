from __future__ import annotations

from typing import Any

from zddv.config import ProjectConfig
from zddv.storage import (
    list_assertion_events,
    list_formal_property_results,
    list_formal_result_snapshots,
    list_uvm_log_snapshots,
    list_uvm_report_messages,
)


def build_desktop_evidence_snapshot(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Read bounded assertion, formal, and UVM detail evidence for the desktop."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    formal_rows = list_formal_result_snapshots(project, limit=1)
    latest_formal = formal_rows[0] if formal_rows else None
    formal_properties = (
        list_formal_property_results(
            project,
            str(latest_formal["snapshot_id"]),
            limit=limit,
        )
        if latest_formal is not None
        else []
    )

    uvm_rows = list_uvm_log_snapshots(project, limit=1)
    latest_uvm = uvm_rows[0] if uvm_rows else None
    uvm_messages = (
        list_uvm_report_messages(
            project,
            str(latest_uvm["snapshot_id"]),
            limit=limit,
        )
        if latest_uvm is not None
        else []
    )

    return {
        "assertion_events": list_assertion_events(project, limit=limit),
        "latest_formal": latest_formal,
        "formal_properties": formal_properties,
        "latest_uvm": latest_uvm,
        "uvm_messages": uvm_messages,
        "limit": limit,
    }


def attach_desktop_evidence_tabs(
    notebook: Any,
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> None:
    """Attach bounded read-only Assertion, Formal, and UVM evidence tabs."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop evidence panes require Python with tkinter support installed."
        ) from exc

    assertions_tab = ttk.Frame(notebook, padding=8)
    formal_tab = ttk.Frame(notebook, padding=8)
    uvm_tab = ttk.Frame(notebook, padding=8)
    notebook.add(assertions_tab, text="Assertions")
    notebook.add(formal_tab, text="Formal")
    notebook.add(uvm_tab, text="UVM")

    assertion_status = tk.StringVar(value="No assertion detail loaded.")
    formal_status = tk.StringVar(value="No formal detail loaded.")
    uvm_status = tk.StringVar(value="No UVM detail loaded.")

    def make_header(parent: Any, variable: Any) -> Any:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(0, 6))
        ttk.Label(frame, textvariable=variable, anchor="w").pack(side="left")
        return frame

    assertion_header = make_header(assertions_tab, assertion_status)
    formal_header = make_header(formal_tab, formal_status)
    uvm_header = make_header(uvm_tab, uvm_status)

    assertion_tree = ttk.Treeview(
        assertions_tab,
        columns=("status", "name", "run", "line", "message"),
        show="headings",
    )
    for column, title, width in (
        ("status", "Status", 90),
        ("name", "Assertion", 230),
        ("run", "Run ID", 260),
        ("line", "Log line", 90),
        ("message", "Message", 500),
    ):
        assertion_tree.heading(column, text=title)
        assertion_tree.column(column, width=width, anchor="w")
    assertion_tree.pack(fill="both", expand=True)

    formal_tree = ttk.Treeview(
        formal_tab,
        columns=("status", "kind", "name", "interpretation", "depth", "message"),
        show="headings",
    )
    for column, title, width in (
        ("status", "Status", 90),
        ("kind", "Kind", 90),
        ("name", "Property", 230),
        ("interpretation", "Interpretation", 170),
        ("depth", "Depth", 90),
        ("message", "Message", 410),
    ):
        formal_tree.heading(column, text=title)
        formal_tree.column(column, width=width, anchor="w")
    formal_tree.pack(fill="both", expand=True)

    uvm_tree = ttk.Treeview(
        uvm_tab,
        columns=("severity", "report_id", "component", "time", "line", "message"),
        show="headings",
    )
    for column, title, width in (
        ("severity", "Severity", 120),
        ("report_id", "Report ID", 140),
        ("component", "Component", 220),
        ("time", "Time", 100),
        ("line", "Log line", 90),
        ("message", "Message", 410),
    ):
        uvm_tree.heading(column, text=title)
        uvm_tree.column(column, width=width, anchor="w")
    uvm_tree.pack(fill="both", expand=True)

    def clear(tree: Any) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)

    def refresh() -> None:
        try:
            snapshot = build_desktop_evidence_snapshot(project, limit=limit)
        except (RuntimeError, ValueError) as exc:
            assertion_status.set(f"Evidence unavailable: {exc}")
            formal_status.set(f"Evidence unavailable: {exc}")
            uvm_status.set(f"Evidence unavailable: {exc}")
            return

        clear(assertion_tree)
        for event in snapshot["assertion_events"]:
            assertion_tree.insert(
                "",
                "end",
                values=(
                    event["status"],
                    event["assertion_name"],
                    event["run_id"],
                    "-" if event["log_line"] is None else event["log_line"],
                    event["message"] or "",
                ),
            )
        assertion_status.set(
            f"{len(snapshot['assertion_events'])} assertion event(s) shown "
            f"(limit {snapshot['limit']})."
        )

        clear(formal_tree)
        latest_formal = snapshot["latest_formal"]
        for item in snapshot["formal_properties"]:
            depth = (
                item["effective_depth"]
                if item["effective_depth"] is not None
                else item["depth"]
            )
            formal_tree.insert(
                "",
                "end",
                values=(
                    item["status"],
                    item["kind"],
                    item["name"],
                    item["interpretation"] or "",
                    "-" if depth is None else depth,
                    item["message"] or "",
                ),
            )
        if latest_formal is None:
            formal_status.set("No persisted formal snapshot.")
        else:
            formal_status.set(
                f"{latest_formal['snapshot_id']} · {latest_formal['mode']} · "
                f"{latest_formal['status']} · "
                f"{len(snapshot['formal_properties'])} propertie(s) shown."
            )

        clear(uvm_tree)
        latest_uvm = snapshot["latest_uvm"]
        for message in snapshot["uvm_messages"]:
            uvm_tree.insert(
                "",
                "end",
                values=(
                    message["severity"],
                    message["report_id"] or "",
                    message["component"] or "",
                    message["time_text"] or "",
                    message["log_line"],
                    message["message"] or "",
                ),
            )
        if latest_uvm is None:
            uvm_status.set("No persisted UVM snapshot.")
        else:
            uvm_status.set(
                f"{latest_uvm['snapshot_id']} · {latest_uvm['status']} · "
                f"test={latest_uvm['test_name'] or '(unknown)'} · "
                f"{len(snapshot['uvm_messages'])} message(s) shown."
            )

    for header in (assertion_header, formal_header, uvm_header):
        ttk.Button(header, text="Refresh details", command=refresh).pack(side="right")

    refresh()
