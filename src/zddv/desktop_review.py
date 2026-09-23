from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from zddv.config import ProjectConfig
from zddv.generated_artifacts import apply_generated_artifact


_DRAFT_ID_RE = re.compile(r"^draft-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _generated_root(project: ProjectConfig) -> Path:
    return (project.root / ".zddv" / "generated").resolve()


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"draft manifest is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("draft manifest root must be a JSON object")
    return payload


def _validated_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
) -> dict[str, Any]:
    root = _generated_root(project)
    drafts_root = (root / "drafts").resolve()

    manifest = Path(manifest_path)
    if not manifest.is_absolute():
        manifest = project.root / manifest
    manifest = manifest.resolve()

    try:
        manifest.relative_to(drafts_root)
    except ValueError as exc:
        raise ValueError("draft manifest must remain under .zddv/generated/drafts") from exc
    if manifest.name != "manifest.json" or not manifest.is_file():
        raise FileNotFoundError(manifest)

    payload = _load_json_object(manifest)
    if payload.get("analysis") != "generated_verification_draft":
        raise ValueError("manifest is not a generated verification draft")
    if payload.get("status") != "DRAFT":
        raise ValueError("generated draft manifest status must be DRAFT")
    if payload.get("review_required") is not True or payload.get("auto_apply") is not False:
        raise ValueError("generated draft manifest lacks review/opt-in safeguards")

    draft_id = str(payload.get("draft_id") or "").strip()
    if not _DRAFT_ID_RE.fullmatch(draft_id):
        raise ValueError("generated draft manifest has an invalid draft_id")
    if manifest.parent.name != draft_id:
        raise ValueError("generated draft manifest path does not match its draft_id")

    content_value = payload.get("content_path")
    if not isinstance(content_value, str) or not content_value:
        raise ValueError("generated draft manifest is missing content_path")
    content_path = Path(content_value)
    if not content_path.is_absolute():
        content_path = project.root / content_path
    content_path = content_path.resolve()
    if content_path.parent != manifest.parent:
        raise ValueError("generated draft content path does not match its draft_id")
    if not content_path.is_file():
        raise FileNotFoundError(content_path)

    expected_sha = str(payload.get("content_sha256") or "")
    if not _SHA256_RE.fullmatch(expected_sha):
        raise ValueError("generated draft manifest has an invalid content_sha256")
    content = content_path.read_bytes()
    actual_sha = sha256(content).hexdigest()
    if actual_sha != expected_sha:
        raise RuntimeError("generated draft content changed after staging")

    applied_path = root / "applied" / f"{draft_id}.json"
    return {
        "draft_id": draft_id,
        "status": "APPLIED" if applied_path.is_file() else "REVIEWABLE",
        "kind": payload.get("kind"),
        "language": payload.get("language"),
        "name": payload.get("name"),
        "source": payload.get("source"),
        "evidence": payload.get("evidence") or {},
        "manifest_path": str(manifest),
        "content_path": str(content_path),
        "content_sha256": actual_sha,
        "suggested_target_path": payload.get("suggested_target_path"),
        "bytes": len(content),
        "content": content.decode("utf-8", errors="replace"),
        "review_required": True,
        "approved": False,
        "auto_apply": False,
        "execution_enabled": False,
    }


def list_desktop_review_drafts(
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List staged generated-verification drafts without mutating project state."""
    if limit < 1:
        raise ValueError("limit must be >= 1")

    drafts_root = _generated_root(project) / "drafts"
    if not drafts_root.is_dir():
        return []

    rows: list[dict[str, Any]] = []
    manifests = sorted(drafts_root.glob("draft-*/manifest.json"))
    for manifest in manifests[:limit]:
        try:
            rows.append(_validated_draft(project, manifest))
        except (OSError, RuntimeError, ValueError) as exc:
            rows.append(
                {
                    "draft_id": manifest.parent.name,
                    "status": "INVALID",
                    "manifest_path": str(manifest.resolve()),
                    "kind": None,
                    "name": None,
                    "content_sha256": None,
                    "suggested_target_path": None,
                    "bytes": None,
                    "error": str(exc),
                }
            )
    return rows


def load_desktop_review_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Load and hash-check one staged draft for exact-content human review."""
    return _validated_draft(project, manifest_path)


def apply_desktop_reviewed_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
    *,
    confirmation_sha256: str,
    approved: bool,
    destination: str | Path | None = None,
) -> dict[str, Any]:
    """Apply only after explicit approval and exact manually supplied SHA confirmation."""
    if not approved:
        raise RuntimeError("Desktop apply requires explicit reviewed approval.")

    draft = _validated_draft(project, manifest_path)
    if draft["status"] != "REVIEWABLE":
        raise RuntimeError(f"Generated draft is not reviewable: {draft['status']}")

    confirmation = str(confirmation_sha256 or "").strip()
    if not _SHA256_RE.fullmatch(confirmation):
        raise ValueError("confirmation SHA-256 must be 64 lowercase hexadecimal characters")
    if confirmation != draft["content_sha256"]:
        raise RuntimeError("Confirmed SHA-256 does not match the reviewed staged content")

    target = None
    if destination is not None and str(destination).strip():
        target = str(destination).strip()

    return apply_generated_artifact(
        project,
        draft["manifest_path"],
        destination=target,
        expected_sha256=confirmation,
        approve_reviewed=True,
    )


def attach_desktop_review_actions_tab(
    notebook: Any,
    project: ProjectConfig,
    *,
    limit: int = 100,
) -> None:
    """Attach an explicit SHA-confirmed generated-artifact review/apply tab."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            "Desktop review actions require Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Review Actions")

    status_var = tk.StringVar(
        value="Select a staged draft. Applying requires exact SHA confirmation."
    )
    ttk.Label(tab, textvariable=status_var).pack(fill="x", pady=(0, 6))

    columns = ("status", "kind", "name", "sha256", "target")
    tree = ttk.Treeview(tab, columns=columns, show="headings", height=8)
    for column, title, width in (
        ("status", "Status", 100),
        ("kind", "Kind", 100),
        ("name", "Name", 180),
        ("sha256", "Content SHA-256", 440),
        ("target", "Suggested target", 280),
    ):
        tree.heading(column, text=title)
        tree.column(column, width=width, anchor="w")
    tree.pack(fill="both", expand=True)

    preview_frame = ttk.LabelFrame(tab, text="Exact staged content", padding=6)
    preview_frame.pack(fill="both", expand=True, pady=(8, 0))
    preview = tk.Text(preview_frame, height=12, wrap="none")
    preview.pack(fill="both", expand=True)
    preview.configure(state="disabled")

    controls = ttk.Frame(tab, padding=(0, 8, 0, 0))
    controls.pack(fill="x")
    confirmation_var = tk.StringVar(value="")
    destination_var = tk.StringVar(value="")
    approved_var = tk.BooleanVar(value=False)

    ttk.Label(controls, text="Confirm SHA-256").grid(row=0, column=0, sticky="w")
    ttk.Entry(controls, textvariable=confirmation_var, width=68).grid(
        row=0, column=1, sticky="ew", padx=(6, 12)
    )
    ttk.Label(controls, text="Destination").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(controls, textvariable=destination_var, width=68).grid(
        row=1, column=1, sticky="ew", padx=(6, 12), pady=(6, 0)
    )
    ttk.Checkbutton(
        controls,
        text="I reviewed the exact staged content and approve applying this SHA.",
        variable=approved_var,
    ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
    controls.columnconfigure(1, weight=1)

    state: dict[str, Any] = {"rows": {}, "selected": None}

    def _set_preview(text: str) -> None:
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.insert("1.0", text)
        preview.configure(state="disabled")

    def refresh_drafts() -> None:
        for item in tree.get_children():
            tree.delete(item)
        state["rows"] = {}
        state["selected"] = None
        confirmation_var.set("")
        destination_var.set("")
        approved_var.set(False)
        _set_preview("")

        rows = list_desktop_review_drafts(project, limit=limit)
        for row in rows:
            item = tree.insert(
                "",
                "end",
                values=(
                    row["status"],
                    row.get("kind") or "-",
                    row.get("name") or row["draft_id"],
                    row.get("content_sha256") or "-",
                    row.get("suggested_target_path") or "-",
                ),
            )
            state["rows"][item] = row

        status_var.set(
            f"{len(rows)} staged draft(s). Select one to review exact content and SHA."
            if rows
            else "No staged generated-verification drafts found."
        )

    def select_draft(_event=None) -> None:
        selection = tree.selection()
        if not selection:
            return
        row = state["rows"].get(selection[0])
        if row is None:
            return

        state["selected"] = row
        confirmation_var.set("")
        destination_var.set(str(row.get("suggested_target_path") or ""))
        approved_var.set(False)

        if row["status"] == "INVALID":
            _set_preview(row.get("error") or "Invalid staged draft.")
            status_var.set(f"{row['draft_id']} is invalid and cannot be applied.")
            return

        try:
            detail = load_desktop_review_draft(project, row["manifest_path"])
        except (OSError, RuntimeError, ValueError) as exc:
            _set_preview(str(exc))
            status_var.set(f"Draft validation failed: {exc}")
            return

        state["selected"] = detail
        _set_preview(detail["content"])
        status_var.set(
            f"{detail['draft_id']} · SHA-256 {detail['content_sha256']} · "
            f"status={detail['status']}. Re-enter the SHA manually to apply."
        )

    def apply_selected() -> None:
        row = state.get("selected")
        if not row:
            status_var.set("Select a staged draft first.")
            return

        try:
            result = apply_desktop_reviewed_draft(
                project,
                row["manifest_path"],
                confirmation_sha256=confirmation_var.get(),
                approved=bool(approved_var.get()),
                destination=destination_var.get(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            status_var.set(f"Apply blocked: {exc}")
            return

        status_var.set(
            f"APPLIED {result['draft_id']} to {result['destination']} · "
            "no compile or simulation was executed."
        )
        refresh_drafts()

    tree.bind("<<TreeviewSelect>>", select_draft)
    buttons = ttk.Frame(tab, padding=(0, 8, 0, 0))
    buttons.pack(fill="x")
    ttk.Button(buttons, text="Refresh drafts", command=refresh_drafts).pack(side="left")
    ttk.Button(
        buttons,
        text="Apply reviewed draft",
        command=apply_selected,
    ).pack(side="left", padx=(8, 0))

    refresh_drafts()
