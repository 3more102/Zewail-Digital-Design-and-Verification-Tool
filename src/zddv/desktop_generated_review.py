from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.generated_artifacts import apply_generated_artifact


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_reviewable_generated_drafts(project: ProjectConfig) -> list[dict[str, Any]]:
    """Inspect staged generated-verification drafts without changing project state."""
    drafts_root = (project.root / ".zddv" / "generated" / "drafts").resolve()
    applied_root = (project.root / ".zddv" / "generated" / "applied").resolve()
    if not drafts_root.exists():
        return []

    rows: list[dict[str, Any]] = []
    for manifest_path in sorted(drafts_root.glob("*/manifest.json")):
        row: dict[str, Any] = {
            "manifest_path": str(manifest_path.resolve()),
            "draft_id": manifest_path.parent.name,
            "status": "INVALID",
            "reviewable": False,
            "already_applied": False,
            "error": None,
            "content": None,
        }
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("manifest must contain a JSON object")

            draft_id = str(payload.get("draft_id") or "").strip()
            if not draft_id or draft_id != manifest_path.parent.name:
                raise ValueError("manifest draft_id does not match its directory")

            content_value = payload.get("content_path")
            if not isinstance(content_value, str) or not content_value:
                raise ValueError("manifest is missing content_path")
            content_path = Path(content_value)
            if not content_path.is_absolute():
                content_path = project.root / content_path
            content_path = content_path.resolve()
            if content_path.parent != manifest_path.parent.resolve():
                raise ValueError("draft content is outside its draft directory")
            if not content_path.is_file():
                raise FileNotFoundError(content_path)

            expected_sha = str(payload.get("content_sha256") or "").lower()
            actual_sha = _sha256_file(content_path)
            integrity = "MATCH" if actual_sha == expected_sha else "MISMATCH"
            applied_path = applied_root / f"{draft_id}.json"
            already_applied = applied_path.is_file()

            safeguards = (
                payload.get("analysis") == "generated_verification_draft"
                and payload.get("status") == "DRAFT"
                and payload.get("review_required") is True
                and payload.get("auto_apply") is False
                and payload.get("execution_enabled") is False
            )
            reviewable = safeguards and integrity == "MATCH" and not already_applied
            row.update(
                {
                    "draft_id": draft_id,
                    "status": "APPLIED" if already_applied else str(payload.get("status") or "INVALID"),
                    "kind": payload.get("kind"),
                    "language": payload.get("language"),
                    "name": payload.get("name"),
                    "source": payload.get("source"),
                    "suggested_target_path": payload.get("suggested_target_path"),
                    "content_path": str(content_path),
                    "content_sha256": expected_sha,
                    "actual_sha256": actual_sha,
                    "integrity": integrity,
                    "review_required": payload.get("review_required"),
                    "auto_apply": payload.get("auto_apply"),
                    "execution_enabled": payload.get("execution_enabled"),
                    "reviewable": reviewable,
                    "already_applied": already_applied,
                    "applied_record_path": str(applied_path) if already_applied else None,
                    "content": content_path.read_text(encoding="utf-8", errors="replace"),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def apply_reviewed_generated_draft(
    project: ProjectConfig,
    *,
    manifest_path: str | Path,
    destination: str | Path | None,
    reviewed_sha256: str,
    approve_reviewed: bool,
) -> dict[str, Any]:
    """Delegate generated-code apply to the existing SHA-confirmed core gate."""
    return apply_generated_artifact(
        project,
        manifest_path,
        destination=destination,
        expected_sha256=reviewed_sha256,
        approve_reviewed=approve_reviewed,
    )


def attach_desktop_generated_review_tab(
    notebook: Any,
    project: ProjectConfig,
) -> None:
    """Attach a separate exact-byte generated-code review/apply pane."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop generated review requires Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Generated Review")

    intro = (
        "Review only staged generated SystemVerilog. Apply requires intact staged bytes, "
        "the exact SHA-256, and explicit approval; apply never compiles or executes code."
    )
    ttk.Label(tab, text=intro, anchor="w", wraplength=1050).pack(fill="x", pady=(0, 8))

    tree = ttk.Treeview(
        tab,
        columns=("status", "kind", "name", "target", "sha"),
        show="headings",
        height=7,
        selectmode="browse",
    )
    for column, title, width in (
        ("status", "Status", 90),
        ("kind", "Kind", 100),
        ("name", "Draft", 220),
        ("target", "Suggested target", 300),
        ("sha", "Content SHA-256", 430),
    ):
        tree.heading(column, text=title)
        tree.column(column, width=width, anchor="w")
    tree.pack(fill="x")

    preview_frame = ttk.LabelFrame(tab, text="Exact staged content", padding=6)
    preview_frame.pack(fill="both", expand=True, pady=(8, 8))
    preview = tk.Text(preview_frame, height=12, wrap="none")
    preview.pack(fill="both", expand=True)
    preview.configure(state="disabled")

    form = ttk.Frame(tab)
    form.pack(fill="x")
    destination_var = tk.StringVar(value="")
    sha_var = tk.StringVar(value="")
    approve_var = tk.BooleanVar(value=False)
    status_var = tk.StringVar(value="Select a staged draft.")

    ttk.Label(form, text="Destination").grid(row=0, column=0, sticky="w")
    ttk.Entry(form, textvariable=destination_var, width=62).grid(
        row=0, column=1, sticky="ew", padx=(6, 12)
    )
    ttk.Label(form, text="Reviewed SHA-256").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(form, textvariable=sha_var, width=70).grid(
        row=1, column=1, sticky="ew", padx=(6, 12), pady=(6, 0)
    )
    ttk.Checkbutton(
        form,
        text="I reviewed the exact staged content and approve applying these bytes",
        variable=approve_var,
    ).grid(row=2, column=1, sticky="w", pady=(6, 0))
    form.columnconfigure(1, weight=1)

    ttk.Label(tab, textvariable=status_var, anchor="w", wraplength=1050).pack(
        fill="x", pady=(8, 0)
    )
    state: dict[str, dict[str, Any]] = {}

    def set_preview(text: str) -> None:
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.insert("1.0", text)
        preview.configure(state="disabled")

    def refresh_drafts() -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)
        state.clear()
        set_preview("")
        destination_var.set("")
        sha_var.set("")
        approve_var.set(False)

        rows = list_reviewable_generated_drafts(project)
        for index, row in enumerate(rows):
            item_id = f"draft-{index}"
            state[item_id] = row
            tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    row.get("status") or "INVALID",
                    row.get("kind") or "-",
                    row.get("name") or row.get("draft_id") or "-",
                    row.get("suggested_target_path") or "-",
                    row.get("content_sha256") or "-",
                ),
            )
        status_var.set(
            "No staged generated-verification drafts found."
            if not rows
            else f"{len(rows)} staged draft(s) found. Selection is read-only."
        )

    def on_select(_event: Any = None) -> None:
        selection = tree.selection()
        if not selection:
            return
        row = state.get(selection[0])
        if row is None:
            return
        destination_var.set(str(row.get("suggested_target_path") or ""))
        sha_var.set("")
        approve_var.set(False)
        set_preview(str(row.get("content") or ""))

        if row.get("reviewable"):
            status_var.set(
                f"Review {row['draft_id']} and manually enter SHA-256 "
                f"{row.get('content_sha256') or '-'} before apply."
            )
            return

        reason = row.get("error")
        if not reason and row.get("already_applied"):
            reason = "draft already has an applied record"
        if not reason and row.get("integrity") == "MISMATCH":
            reason = "staged content SHA-256 no longer matches the manifest"
        status_var.set(f"Draft is not reviewable: {reason or 'manifest safeguards failed'}.")

    def apply_selected() -> None:
        selection = tree.selection()
        if not selection:
            status_var.set("Select one staged draft first.")
            return
        row = state.get(selection[0])
        if row is None or not row.get("reviewable"):
            status_var.set("Selected draft is not eligible for apply.")
            return

        reviewed_sha = sha_var.get().strip().lower()
        if reviewed_sha != row.get("content_sha256"):
            status_var.set("Reviewed SHA-256 does not exactly match the staged manifest.")
            return
        if not approve_var.get():
            status_var.set("Explicit reviewed-content approval is required.")
            return

        destination = destination_var.get().strip() or None
        try:
            result = apply_reviewed_generated_draft(
                project,
                manifest_path=str(row["manifest_path"]),
                destination=destination,
                reviewed_sha256=reviewed_sha,
                approve_reviewed=True,
            )
        except (FileNotFoundError, FileExistsError, RuntimeError, ValueError) as exc:
            status_var.set(f"Apply blocked: {exc}")
            return

        refresh_drafts()
        status_var.set(
            f"APPLIED exact reviewed bytes to {result['destination']}; execution remains disabled."
        )

    tree.bind("<<TreeviewSelect>>", on_select)
    buttons = ttk.Frame(tab)
    buttons.pack(fill="x", pady=(8, 0))
    ttk.Button(buttons, text="Refresh drafts", command=refresh_drafts).pack(side="right")
    ttk.Button(buttons, text="Apply reviewed draft", command=apply_selected).pack(side="left")

    refresh_drafts()
