from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zddv.config import ProjectConfig
from zddv.generated_artifacts import (
    apply_generated_artifact,
    stage_generated_artifact,
)


_MAX_PREVIEW_BYTES = 256 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _drafts_root(project: ProjectConfig) -> Path:
    return (project.root / ".zddv" / "generated" / "drafts").resolve()


def _manifest_path(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    root = _drafts_root(project)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "Desktop review manifests must remain under .zddv/generated/drafts"
        ) from exc
    if path.name != "manifest.json":
        raise ValueError("Desktop review path must identify a generated manifest.json")
    return path


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Generated draft manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Generated draft manifest root must be a JSON object")
    return payload


def inspect_desktop_review_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
    *,
    include_content: bool = False,
    max_preview_bytes: int = _MAX_PREVIEW_BYTES,
) -> dict[str, Any]:
    """Inspect one staged generated artifact without mutating project state."""
    if max_preview_bytes < 1:
        raise ValueError("max_preview_bytes must be >= 1")

    manifest_file = _manifest_path(project, manifest_path)
    if not manifest_file.is_file():
        raise FileNotFoundError(manifest_file)

    manifest = _load_manifest(manifest_file)
    draft_id = str(manifest.get("draft_id") or "").strip()
    expected_dir = manifest_file.parent.resolve()
    integrity = "VALID"
    error = None

    if manifest.get("analysis") != "generated_verification_draft":
        integrity = "INVALID"
        error = "manifest analysis is not generated_verification_draft"
    elif manifest.get("status") != "DRAFT":
        integrity = "INVALID"
        error = "manifest status is not DRAFT"
    elif manifest.get("review_required") is not True:
        integrity = "INVALID"
        error = "manifest does not require review"
    elif manifest.get("auto_apply") is not False:
        integrity = "INVALID"
        error = "manifest does not preserve auto_apply=false"
    elif not draft_id or expected_dir.name != draft_id:
        integrity = "INVALID"
        error = "manifest draft_id does not match its directory"

    content_path: Path | None = None
    content_bytes: bytes | None = None
    actual_sha256 = None
    content_value = manifest.get("content_path")
    if isinstance(content_value, str) and content_value.strip():
        candidate = Path(content_value)
        if not candidate.is_absolute():
            candidate = project.root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(_drafts_root(project))
        except ValueError:
            integrity = "INVALID"
            error = "draft content escapes .zddv/generated/drafts"
        else:
            if candidate.parent != expected_dir:
                integrity = "INVALID"
                error = "draft content path does not match the manifest draft_id"
            elif not candidate.is_file():
                integrity = "MISSING_CONTENT"
                error = f"draft content is missing: {candidate}"
            else:
                content_path = candidate
                content_bytes = candidate.read_bytes()
                actual_sha256 = _sha256(content_bytes)
                expected_sha256 = str(manifest.get("content_sha256") or "").lower()
                if actual_sha256 != expected_sha256:
                    integrity = "CHANGED"
                    error = "draft bytes no longer match manifest content_sha256"
    else:
        integrity = "INVALID"
        error = "manifest is missing content_path"

    applied_record = (
        project.root / ".zddv" / "generated" / "applied" / f"{draft_id}.json"
        if draft_id
        else None
    )
    applied = bool(applied_record is not None and applied_record.is_file())
    status = "APPLIED" if applied and integrity == "VALID" else integrity

    result: dict[str, Any] = {
        "draft_id": draft_id,
        "status": status,
        "integrity": integrity,
        "error": error,
        "kind": manifest.get("kind"),
        "language": manifest.get("language"),
        "name": manifest.get("name"),
        "source": manifest.get("source"),
        "manifest_path": str(manifest_file),
        "content_path": None if content_path is None else str(content_path),
        "content_sha256": manifest.get("content_sha256"),
        "actual_sha256": actual_sha256,
        "suggested_target_path": manifest.get("suggested_target_path"),
        "review_required": manifest.get("review_required"),
        "auto_apply": manifest.get("auto_apply"),
        "execution_enabled": manifest.get("execution_enabled"),
        "applied": applied,
        "applied_record_path": (
            None if applied_record is None else str(applied_record.resolve())
        ),
    }

    if include_content and content_bytes is not None:
        truncated = len(content_bytes) > max_preview_bytes
        preview = content_bytes[:max_preview_bytes]
        result["content"] = preview.decode("utf-8", errors="replace")
        result["content_truncated"] = truncated
        result["content_bytes"] = len(content_bytes)

    return result


def list_desktop_review_drafts(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List staged generated artifacts for review without changing them."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    root = _drafts_root(project)
    if not root.is_dir():
        return []

    rows: list[tuple[int, dict[str, Any]]] = []
    for manifest_file in root.glob("*/manifest.json"):
        try:
            row = inspect_desktop_review_draft(project, manifest_file)
            modified = manifest_file.stat().st_mtime_ns
        except (OSError, ValueError) as exc:
            row = {
                "draft_id": manifest_file.parent.name,
                "status": "INVALID",
                "integrity": "INVALID",
                "error": str(exc),
                "kind": None,
                "language": None,
                "name": None,
                "source": None,
                "manifest_path": str(manifest_file.resolve()),
                "content_path": None,
                "content_sha256": None,
                "actual_sha256": None,
                "suggested_target_path": None,
                "review_required": None,
                "auto_apply": None,
                "execution_enabled": None,
                "applied": False,
                "applied_record_path": None,
            }
            try:
                modified = manifest_file.stat().st_mtime_ns
            except OSError:
                modified = 0
        rows.append((modified, row))

    rows.sort(key=lambda item: (item[0], str(item[1]["draft_id"])), reverse=True)
    return [row for _, row in rows[:limit]]


def stage_desktop_review_proposal(
    project: ProjectConfig,
    proposal_path: str | Path,
) -> dict[str, Any]:
    """Stage a proposal through the authoritative generated-artifact core API."""
    return stage_generated_artifact(project, proposal_path)


def apply_desktop_review_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
    *,
    destination: str | Path | None,
    expected_sha256: str,
    approve_reviewed: bool,
) -> dict[str, Any]:
    """Apply a reviewed draft only through the authoritative SHA-confirmed core gate."""
    draft = inspect_desktop_review_draft(project, manifest_path)
    if draft["integrity"] != "VALID":
        raise RuntimeError(
            f"Generated draft is not reviewable: {draft['status']}"
            + (f" ({draft['error']})" if draft.get("error") else "")
        )
    if draft["applied"]:
        raise RuntimeError("Generated draft already has an applied provenance record")

    return apply_generated_artifact(
        project,
        draft["manifest_path"],
        destination=destination,
        expected_sha256=expected_sha256,
        approve_reviewed=approve_reviewed,
    )


def attach_desktop_review_tab(
    notebook: Any,
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> None:
    """Attach review-gated staging/apply controls without adding execution actions."""
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop review actions require Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Review Actions")

    status_var = tk.StringVar(
        value="No automatic apply: select a staged draft and verify its exact SHA-256."
    )
    ttk.Label(tab, textvariable=status_var, anchor="w").pack(fill="x", pady=(0, 6))

    columns = ("status", "kind", "name", "target", "sha256")
    tree = ttk.Treeview(tab, columns=columns, show="headings", height=8, selectmode="browse")
    for column, title, width in (
        ("status", "Status", 100),
        ("kind", "Kind", 100),
        ("name", "Name", 220),
        ("target", "Suggested target", 300),
        ("sha256", "Content SHA-256", 470),
    ):
        tree.heading(column, text=title)
        tree.column(column, width=width, anchor="w")
    tree.pack(fill="both", expand=True)

    preview = tk.Text(tab, height=12, wrap="none")
    preview.configure(state="disabled")
    preview.pack(fill="both", expand=True, pady=(8, 8))

    controls = ttk.Frame(tab)
    controls.pack(fill="x")
    destination_var = tk.StringVar(value="")
    sha_var = tk.StringVar(value="")
    approve_var = tk.BooleanVar(value=False)

    ttk.Label(controls, text="Destination").grid(row=0, column=0, sticky="w")
    ttk.Entry(controls, textvariable=destination_var, width=48).grid(
        row=0, column=1, padx=(6, 12), sticky="ew"
    )
    ttk.Label(controls, text="Type exact SHA-256").grid(row=1, column=0, sticky="w")
    ttk.Entry(controls, textvariable=sha_var, width=68).grid(
        row=1, column=1, padx=(6, 12), sticky="ew"
    )
    ttk.Checkbutton(
        controls,
        text="I reviewed these exact bytes and approve applying them",
        variable=approve_var,
    ).grid(row=2, column=1, sticky="w", pady=(4, 0))
    controls.columnconfigure(1, weight=1)

    state: dict[str, Any] = {"rows": {}}

    def _set_preview(text: str) -> None:
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.insert("1.0", text)
        preview.configure(state="disabled")

    def refresh() -> None:
        for item in tree.get_children():
            tree.delete(item)
        state["rows"] = {}
        for row in list_desktop_review_drafts(project, limit=limit):
            item = tree.insert(
                "",
                "end",
                values=(
                    row["status"],
                    row.get("kind") or "-",
                    row.get("name") or "-",
                    row.get("suggested_target_path") or "-",
                    row.get("content_sha256") or "-",
                ),
            )
            state["rows"][item] = row
        status_var.set(
            f"{len(state['rows'])} staged draft(s). "
            "Staging/apply reuse the existing generated-artifact core gates."
        )

    def show_selected(*_args: Any) -> None:
        selection = tree.selection()
        if not selection:
            return
        row = state["rows"].get(selection[0])
        if row is None:
            return
        destination_var.set(str(row.get("suggested_target_path") or ""))
        sha_var.set("")
        approve_var.set(False)
        try:
            detail = inspect_desktop_review_draft(
                project,
                row["manifest_path"],
                include_content=True,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            _set_preview("")
            status_var.set(f"Draft inspection error: {exc}")
            return

        header = (
            f"draft_id: {detail['draft_id']}\n"
            f"status: {detail['status']}\n"
            f"content_sha256: {detail.get('content_sha256') or '-'}\n"
            f"actual_sha256: {detail.get('actual_sha256') or '-'}\n"
            f"target: {detail.get('suggested_target_path') or '-'}\n"
            f"execution_enabled: {detail.get('execution_enabled')}\n\n"
        )
        suffix = "\n\n[preview truncated]" if detail.get("content_truncated") else ""
        _set_preview(header + str(detail.get("content") or "") + suffix)
        status_var.set(
            "Review the preview and visible digest. The SHA entry is intentionally blank."
        )

    def stage_proposal() -> None:
        proposal = filedialog.askopenfilename(
            parent=tab.winfo_toplevel(),
            title="Select generated verification proposal",
            filetypes=(("JSON proposal", "*.json"), ("All files", "*.*")),
        )
        if not proposal:
            return
        try:
            result = stage_desktop_review_proposal(project, proposal)
        except (OSError, RuntimeError, ValueError) as exc:
            status_var.set(f"Stage error: {exc}")
            return
        status_var.set(
            f"Staged {result['draft_id']} for review; project sources were not modified."
        )
        refresh()

    def apply_selected() -> None:
        selection = tree.selection()
        if not selection:
            status_var.set("Select one staged draft before applying.")
            return
        row = state["rows"].get(selection[0])
        if row is None:
            status_var.set("Selected draft is no longer available.")
            return

        expected = sha_var.get().strip().lower()
        visible = str(row.get("content_sha256") or "").lower()
        if expected != visible:
            status_var.set("Typed SHA-256 does not match the staged content digest.")
            return
        if not approve_var.get():
            status_var.set("Explicit reviewed approval is required before apply.")
            return

        destination = destination_var.get().strip() or None
        try:
            result = apply_desktop_review_draft(
                project,
                row["manifest_path"],
                destination=destination,
                expected_sha256=expected,
                approve_reviewed=True,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            status_var.set(f"Apply error: {exc}")
            return

        status_var.set(
            f"Applied exact reviewed bytes to {result['destination']}; "
            "no compilation or simulation was started."
        )
        sha_var.set("")
        approve_var.set(False)
        refresh()

    tree.bind("<<TreeviewSelect>>", show_selected)
    button_row = ttk.Frame(tab, padding=(0, 8, 0, 0))
    button_row.pack(fill="x")
    ttk.Button(button_row, text="Stage proposal...", command=stage_proposal).pack(
        side="left"
    )
    ttk.Button(button_row, text="Refresh drafts", command=refresh).pack(
        side="left", padx=(8, 0)
    )
    ttk.Button(button_row, text="Apply reviewed draft", command=apply_selected).pack(
        side="right"
    )

    refresh()
