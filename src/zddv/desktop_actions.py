from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.generated_artifacts import apply_generated_artifact, stage_generated_artifact


def _project_path(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    try:
        path.relative_to(project.root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path must remain inside the project root: {path}") from exc
    return path


def _drafts_root(project: ProjectConfig) -> Path:
    return (project.root / ".zddv" / "generated" / "drafts").resolve()


def list_desktop_generated_drafts(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List review-isolated generated drafts without modifying project state."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    root = _drafts_root(project)
    if not root.exists():
        return []

    rows: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob("draft-*/manifest.json"), reverse=True):
        try:
            detail = read_desktop_generated_draft(project, manifest_path)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            continue

        manifest = detail["manifest"]
        draft_id = str(manifest["draft_id"])
        applied_record = (
            project.root / ".zddv" / "generated" / "applied" / f"{draft_id}.json"
        ).resolve()
        rows.append(
            {
                "draft_id": draft_id,
                "status": "APPLIED" if applied_record.is_file() else "DRAFT",
                "kind": manifest.get("kind"),
                "name": manifest.get("name"),
                "content_sha256": detail["content_sha256"],
                "suggested_target_path": manifest.get("suggested_target_path"),
                "manifest_path": str(manifest_path.resolve()),
                "content_path": str(detail["content_path"]),
                "applied_record_path": (
                    str(applied_record) if applied_record.is_file() else None
                ),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def read_desktop_generated_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Read and re-hash one staged draft for exact-byte review."""
    manifest_file = _project_path(project, manifest_path)
    root = _drafts_root(project)

    if manifest_file.name != "manifest.json" or manifest_file.parent.parent != root:
        raise ValueError(
            "Desktop review accepts only .zddv/generated/drafts/<draft-id>/manifest.json"
        )
    if not manifest_file.is_file():
        raise FileNotFoundError(manifest_file)

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Generated draft manifest is not valid JSON: {manifest_file}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Generated draft manifest root must be a JSON object")
    if manifest.get("analysis") != "generated_verification_draft":
        raise ValueError("Manifest is not a generated verification draft")
    if manifest.get("status") != "DRAFT":
        raise ValueError("Generated artifact manifest status must be DRAFT")

    draft_id = str(manifest.get("draft_id") or "")
    if manifest_file.parent.name != draft_id:
        raise ValueError("Generated draft manifest path does not match draft_id")

    content_value = manifest.get("content_path")
    if not isinstance(content_value, str) or not content_value:
        raise ValueError("Generated draft manifest is missing content_path")
    content_path = _project_path(project, content_value)
    if content_path.parent != manifest_file.parent:
        raise ValueError("Generated draft content path does not match draft_id")
    if not content_path.is_file():
        raise FileNotFoundError(content_path)

    content_bytes = content_path.read_bytes()
    actual_sha256 = hashlib.sha256(content_bytes).hexdigest()
    manifest_sha256 = str(manifest.get("content_sha256") or "").lower()
    if actual_sha256 != manifest_sha256:
        raise RuntimeError(
            "Generated draft content changed after staging; review a fresh draft"
        )

    return {
        "manifest": manifest,
        "manifest_path": str(manifest_file),
        "content_path": str(content_path),
        "content_sha256": actual_sha256,
        "content": content_bytes.decode("utf-8", errors="replace"),
    }


def stage_desktop_generated_proposal(
    project: ProjectConfig,
    proposal_path: str | Path,
) -> dict[str, Any]:
    """Stage a proposal through the existing review-isolated core workflow."""
    return stage_generated_artifact(project, proposal_path)


def apply_desktop_reviewed_artifact(
    project: ProjectConfig,
    manifest_path: str | Path,
    *,
    expected_sha256: str,
    reviewed: bool,
    destination: str | Path | None = None,
) -> dict[str, Any]:
    """Apply exact staged bytes only after explicit review + SHA confirmation."""
    if reviewed is not True:
        raise RuntimeError(
            "Desktop apply requires explicit confirmation that the exact staged bytes were reviewed"
        )
    return apply_generated_artifact(
        project,
        manifest_path,
        destination=destination,
        expected_sha256=expected_sha256,
        approve_reviewed=True,
    )


def attach_desktop_actions_tab(
    notebook: Any,
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> None:
    """Attach explicit review-gated generated-artifact actions to the Debug Studio."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop review actions require Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Review Actions")

    status_var = tk.StringVar(
        value=(
            "Browsing is read-only. Staging writes only under .zddv/generated; "
            "apply requires exact SHA-256 + explicit reviewed confirmation."
        )
    )

    stage_row = ttk.Frame(tab)
    stage_row.pack(fill="x", pady=(0, 8))
    proposal_var = tk.StringVar(value="")
    ttk.Label(stage_row, text="Proposal JSON").pack(side="left")
    ttk.Entry(stage_row, textvariable=proposal_var, width=72).pack(
        side="left", padx=(6, 8), fill="x", expand=True
    )

    drafts_tree = ttk.Treeview(
        tab,
        columns=("status", "kind", "name", "sha", "target"),
        show="headings",
        height=7,
        selectmode="browse",
    )
    for column, title, width in (
        ("status", "Status", 90),
        ("kind", "Kind", 90),
        ("name", "Name", 170),
        ("sha", "Content SHA-256", 360),
        ("target", "Suggested target", 260),
    ):
        drafts_tree.heading(column, text=title)
        drafts_tree.column(column, width=width, anchor="w")
    drafts_tree.pack(fill="both", expand=True)

    preview = tk.Text(tab, height=13, wrap="none")
    preview.configure(state="disabled")
    preview.pack(fill="both", expand=True, pady=(8, 8))

    apply_row = ttk.Frame(tab)
    apply_row.pack(fill="x")
    expected_sha_var = tk.StringVar(value="")
    destination_var = tk.StringVar(value="")
    reviewed_var = tk.BooleanVar(value=False)

    ttk.Label(apply_row, text="Expected SHA-256").grid(row=0, column=0, sticky="w")
    ttk.Entry(apply_row, textvariable=expected_sha_var, width=68).grid(
        row=0, column=1, sticky="ew", padx=(6, 12)
    )
    ttk.Label(apply_row, text="Destination (optional)").grid(
        row=1, column=0, sticky="w", pady=(6, 0)
    )
    ttk.Entry(apply_row, textvariable=destination_var, width=68).grid(
        row=1, column=1, sticky="ew", padx=(6, 12), pady=(6, 0)
    )
    ttk.Checkbutton(
        apply_row,
        text="I reviewed the exact staged bytes shown above",
        variable=reviewed_var,
    ).grid(row=2, column=1, sticky="w", padx=(6, 12), pady=(6, 0))
    apply_row.columnconfigure(1, weight=1)

    ttk.Label(tab, textvariable=status_var, anchor="w").pack(fill="x", pady=(8, 0))

    state: dict[str, Any] = {"rows": {}}

    def set_preview(text: str) -> None:
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.insert("1.0", text)
        preview.configure(state="disabled")

    def selected_row() -> dict[str, Any] | None:
        selection = drafts_tree.selection()
        if not selection:
            return None
        return state["rows"].get(selection[0])

    def refresh_drafts() -> None:
        children = drafts_tree.get_children()
        if children:
            drafts_tree.delete(*children)
        state["rows"] = {}
        for row in list_desktop_generated_drafts(project, limit=limit):
            item = drafts_tree.insert(
                "",
                "end",
                values=(
                    row["status"],
                    row["kind"] or "-",
                    row["name"] or "-",
                    row["content_sha256"],
                    row["suggested_target_path"] or "-",
                ),
            )
            state["rows"][item] = row
        set_preview("")
        expected_sha_var.set("")
        destination_var.set("")
        reviewed_var.set(False)

    def show_selected(_event=None) -> None:
        row = selected_row()
        if row is None:
            return
        try:
            detail = read_desktop_generated_draft(project, row["manifest_path"])
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            set_preview("")
            status_var.set(f"Review error: {exc}")
            return
        set_preview(detail["content"])
        expected_sha_var.set("")
        destination_var.set("")
        reviewed_var.set(False)
        status_var.set(
            f"Review exact bytes for {row['draft_id']} · SHA-256={detail['content_sha256']}. "
            "Type/paste that full SHA manually before Apply."
        )

    def stage_proposal() -> None:
        value = proposal_var.get().strip()
        if not value:
            status_var.set("Enter a proposal JSON path before staging.")
            return
        try:
            result = stage_desktop_generated_proposal(project, value)
        except (FileNotFoundError, FileExistsError, OSError, RuntimeError, ValueError) as exc:
            status_var.set(f"Stage error: {exc}")
            return
        refresh_drafts()
        status_var.set(
            f"Staged {result['draft_id']} under .zddv/generated only; "
            f"project sources unchanged · SHA-256={result['content_sha256']}"
        )

    def apply_reviewed() -> None:
        row = selected_row()
        if row is None:
            status_var.set("Select one staged draft before applying.")
            return
        if row["status"] == "APPLIED":
            status_var.set("Selected draft already has an applied record.")
            return

        expected = expected_sha_var.get().strip().lower()
        destination = destination_var.get().strip() or None
        try:
            result = apply_desktop_reviewed_artifact(
                project,
                row["manifest_path"],
                expected_sha256=expected,
                reviewed=bool(reviewed_var.get()),
                destination=destination,
            )
        except (FileNotFoundError, FileExistsError, OSError, RuntimeError, ValueError) as exc:
            status_var.set(f"Apply blocked: {exc}")
            return

        refresh_drafts()
        status_var.set(
            f"Applied exact reviewed bytes to {result['destination']} · "
            "execution remains disabled."
        )

    ttk.Button(stage_row, text="Stage for review", command=stage_proposal).pack(side="right")
    ttk.Button(apply_row, text="Apply reviewed bytes", command=apply_reviewed).grid(
        row=0, column=2, rowspan=3, sticky="ns"
    )
    drafts_tree.bind("<<TreeviewSelect>>", show_selected)

    refresh_drafts()
