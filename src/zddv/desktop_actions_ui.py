from __future__ import annotations

import json
from typing import Any

from zddv.config import ProjectConfig
from zddv.desktop_actions import execute_desktop_action, prepare_desktop_action


def attach_desktop_actions_tab(notebook: Any, project: ProjectConfig) -> None:
    """Attach explicit review-gated lint/build/run actions to the desktop."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop actions require Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Actions")

    notice = (
        "No action runs automatically. Prepare a structured request, review it, "
        "then enter its full SHA-256 exactly before Execute is enabled by policy."
    )
    ttk.Label(tab, text=notice, anchor="w", wraplength=1000).pack(
        fill="x", pady=(0, 8)
    )

    controls = ttk.Frame(tab)
    controls.pack(fill="x", pady=(0, 8))

    action_var = tk.StringVar(value="lint")
    test_var = tk.StringVar(value="")
    seed_var = tk.StringVar(value="")
    timeout_var = tk.StringVar(value="")
    plusargs_var = tk.StringVar(value="")
    confirmation_var = tk.StringVar(value="")
    sha_var = tk.StringVar(value="Prepared SHA-256: -")
    status_var = tk.StringVar(value="No reviewed action prepared.")

    ttk.Label(controls, text="Action").grid(row=0, column=0, sticky="w")
    action_box = ttk.Combobox(
        controls,
        textvariable=action_var,
        values=("lint", "build", "run"),
        state="readonly",
        width=10,
    )
    action_box.grid(row=0, column=1, sticky="w", padx=(4, 14))

    ttk.Label(controls, text="Test").grid(row=0, column=2, sticky="w")
    ttk.Entry(controls, textvariable=test_var, width=18).grid(
        row=0, column=3, sticky="w", padx=(4, 14)
    )
    ttk.Label(controls, text="Seed").grid(row=0, column=4, sticky="w")
    ttk.Entry(controls, textvariable=seed_var, width=10).grid(
        row=0, column=5, sticky="w", padx=(4, 14)
    )
    ttk.Label(controls, text="Timeout (s)").grid(row=0, column=6, sticky="w")
    ttk.Entry(controls, textvariable=timeout_var, width=10).grid(
        row=0, column=7, sticky="w", padx=(4, 0)
    )

    ttk.Label(controls, text="Plusargs (space-separated)").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
    )
    ttk.Entry(controls, textvariable=plusargs_var, width=70).grid(
        row=1, column=2, columnspan=6, sticky="ew", padx=(4, 0), pady=(8, 0)
    )
    controls.columnconfigure(7, weight=1)

    ttk.Label(tab, textvariable=sha_var, anchor="w").pack(fill="x", pady=(0, 4))

    review_frame = ttk.LabelFrame(tab, text="Reviewed request", padding=6)
    review_frame.pack(fill="both", expand=True)
    review_text = tk.Text(review_frame, height=10, wrap="none")
    review_text.pack(fill="both", expand=True)
    review_text.configure(state="disabled")

    confirm_frame = ttk.Frame(tab, padding=(0, 8, 0, 8))
    confirm_frame.pack(fill="x")
    ttk.Label(confirm_frame, text="Exact SHA-256 confirmation").pack(side="left")
    ttk.Entry(
        confirm_frame,
        textvariable=confirmation_var,
        width=68,
    ).pack(side="left", padx=(8, 8))

    result_frame = ttk.LabelFrame(tab, text="Execution result", padding=6)
    result_frame.pack(fill="both", expand=True)
    result_text = tk.Text(result_frame, height=8, wrap="none")
    result_text.pack(fill="both", expand=True)
    result_text.configure(state="disabled")

    ttk.Label(tab, textvariable=status_var, anchor="w").pack(
        fill="x", pady=(6, 0)
    )

    state: dict[str, Any] = {"prepared": None}

    def set_text(widget: Any, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

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

    def optional_float(value: str, label: str) -> float | None:
        text = value.strip()
        if not text:
            return None
        try:
            parsed = float(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number") from exc
        if parsed <= 0:
            raise ValueError(f"{label} must be > 0")
        return parsed

    def prepare() -> None:
        action = action_var.get()
        try:
            if action == "run":
                test_name = test_var.get().strip() or None
                seed = optional_int(seed_var.get(), "Seed")
                timeout_s = optional_float(timeout_var.get(), "Timeout")
                plusargs = [
                    item
                    for item in plusargs_var.get().split()
                    if item
                ]
            else:
                test_name = None
                seed = None
                timeout_s = None
                plusargs = []

            prepared = prepare_desktop_action(
                project,
                action,
                test_name=test_name,
                seed=seed,
                plusargs=plusargs,
                timeout_s=timeout_s,
            )
        except (RuntimeError, ValueError) as exc:
            state["prepared"] = None
            sha_var.set("Prepared SHA-256: -")
            set_text(review_text, "")
            set_text(result_text, "")
            status_var.set(f"Prepare blocked: {exc}")
            return

        state["prepared"] = prepared
        confirmation_var.set("")
        sha_var.set(f"Prepared SHA-256: {prepared['request_sha256']}")
        set_text(review_text, prepared["review_text"])
        set_text(result_text, "")
        status_var.set(
            "Review complete only after you independently re-enter the exact SHA-256."
        )

    def execute() -> None:
        prepared = state["prepared"]
        if prepared is None:
            status_var.set("Prepare and review an action before execution.")
            return

        try:
            result = execute_desktop_action(
                project,
                prepared["request"],
                expected_sha256=confirmation_var.get(),
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            set_text(result_text, "")
            status_var.set(f"Execution blocked/failed: {exc}")
            return

        set_text(result_text, json.dumps(result, indent=2, sort_keys=True))
        status_var.set(
            f"{result['action'].upper()} completed with status {result['status']}. "
            "Use Refresh to reload verification evidence."
        )
        state["prepared"] = None
        confirmation_var.set("")
        sha_var.set("Prepared SHA-256: -")
        set_text(review_text, "")

    buttons = ttk.Frame(confirm_frame)
    buttons.pack(side="right")
    ttk.Button(buttons, text="Prepare for review", command=prepare).pack(
        side="left", padx=(0, 6)
    )
    ttk.Button(buttons, text="Execute reviewed action", command=execute).pack(
        side="left"
    )
