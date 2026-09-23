from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from zddv.config import ProjectConfig
from zddv.generated_artifacts import apply_generated_artifact


_APPROVAL_PHRASE = "APPLY REVIEWED"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside_project(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    root = project.root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path must remain inside the project root: {path}") from exc
    return path


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Generated draft manifest is not readable JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"Generated draft manifest root must be an object: {path}")
    return dict(payload)


def _draft_summary(project: ProjectConfig, manifest_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "manifest_path": str(manifest_path.resolve()),
        "draft_id": manifest_path.parent.name,
        "status": "INVALID",
        "kind": None,
        "name": None,
        "suggested_target_path": None,
        "content_path": None,
        "content_sha256": None,
        "actual_sha256": None,
        "integrity": "INVALID",
        "review_required": False,
        "auto_apply": None,
        "execution_enabled": None,
        "applied": False,
        "eligible": False,
        "error": None,
    }

    try:
        manifest = _load_manifest(manifest_path)
        summary.update(
            {
                "draft_id": str(manifest.get("draft_id") or manifest_path.parent.name),
                "status": str(manifest.get("status") or "INVALID"),
                "kind": manifest.get("kind"),
                "name": manifest.get("name"),
                "suggested_target_path": manifest.get("suggested_target_path"),
                "content_sha256": manifest.get("content_sha256"),
                "review_required": manifest.get("review_required") is True,
                "auto_apply": manifest.get("auto_apply"),
                "execution_enabled": manifest.get("execution_enabled"),
            }
        )

        content_value = manifest.get("content_path")
        if not isinstance(content_value, str) or not content_value:
            raise ValueError("manifest is missing content_path")
        content_path = _inside_project(project, content_value)
        if content_path.parent.resolve() != manifest_path.parent.resolve():
            raise ValueError("manifest content_path does not stay inside its draft directory")
        if not content_path.is_file():
            summary["integrity"] = "MISSING"
            summary["content_path"] = str(content_path)
        else:
            actual_sha256 = _sha256_file(content_path)
            summary["content_path"] = str(content_path)
            summary["actual_sha256"] = actual_sha256
            summary["integrity"] = (
                "MATCH"
                if actual_sha256 == str(manifest.get("content_sha256") or "").lower()
                else "MISMATCH"
            )

        draft_id = str(summary["draft_id"])
        applied_record = (
            project.root / ".zddv" / "generated" / "applied" / f"{draft_id}.json"
        ).resolve()
        summary["applied"] = applied_record.is_file()
        if summary["applied"]:
            summary["status"] = "APPLIED"

        summary["eligible"] = bool(
            summary["status"] == "DRAFT"
            and summary["review_required"]
            and summary["auto_apply"] is False
            and summary["execution_enabled"] is False
            and summary["integrity"] == "MATCH"
            and not summary["applied"]
        )
    except (OSError, ValueError) as exc:
        summary["error"] = str(exc)

    return summary


def list_desktop_generated_drafts(project: ProjectConfig) -> list[dict[str, Any]]:
    """List staged generated verification drafts without mutating project state."""
    root = (project.root / ".zddv" / "generated" / "drafts").resolve()
    if not root.is_dir():
        return []

    return [
        _draft_summary(project, manifest)
        for manifest in sorted(root.glob("*/manifest.json"))
        if manifest.is_file()
    ]


def read_desktop_generated_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Return exact staged text plus integrity metadata for operator review."""
    target = _inside_project(project, manifest_path)
    summaries = list_desktop_generated_drafts(project)
    summary = next(
        (
            item
            for item in summaries
            if Path(str(item["manifest_path"])).resolve() == target
        ),
        None,
    )
    if summary is None:
        raise RuntimeError(f"Generated draft manifest is not registered: {target}")
    if summary["integrity"] != "MATCH" or not summary["content_path"]:
        raise RuntimeError(
            "Generated draft content is missing, invalid, or no longer matches its staged SHA-256"
        )

    content_path = Path(str(summary["content_path"]))
    return {
        **summary,
        "content": content_path.read_text(encoding="utf-8", errors="replace"),
    }


def apply_desktop_generated_draft(
    project: ProjectConfig,
    manifest_path: str | Path,
    *,
    reviewed_sha256: str,
    approval_phrase: str,
    destination: str | Path | None = None,
) -> dict[str, Any]:
    """Apply one reviewed draft only after a GUI-specific explicit confirmation."""
    if approval_phrase.strip() != _APPROVAL_PHRASE:
        raise RuntimeError(
            f"Desktop apply requires typing the exact approval phrase: {_APPROVAL_PHRASE}"
        )

    reviewed = read_desktop_generated_draft(project, manifest_path)
    if not reviewed["eligible"]:
        raise RuntimeError("Generated draft is not eligible for reviewed application")

    return apply_generated_artifact(
        project,
        reviewed["manifest_path"],
        destination=destination,
        expected_sha256=reviewed_sha256,
        approve_reviewed=True,
    )


def attach_desktop_action_tab(notebook: Any, project: ProjectConfig) -> None:
    """Attach a review-gated generated-artifact action tab to the Debug Studio."""
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError as exc:  # pragma: no cover - platform packaging dependent
        raise RuntimeError(
            "Desktop project actions require Python with tkinter support installed."
        ) from exc

    tab = ttk.Frame(notebook, padding=8)
    notebook.add(tab, text="Project Actions")

    status_var = tk.StringVar(
        value=(
            "Review-gated action only: select a staged assertion/test. "
            "Nothing is compiled or executed."
        )
    )
    ttk.Label(tab, textvariable=status_var, anchor="w").pack(fill="x", pady=(0, 6))

    draft_tree = ttk.Treeview(
        tab,
        columns=("status", "kind", "name", "integrity", "target"),
        show="tree headings",
        height=6,
        selectmode="browse",
    )
    draft_tree.heading("#0", text="Draft ID")
    draft_tree.column("#0", width=190, anchor="w")
    for column, title, width in (
        ("status", "Status", 90),
        ("kind", "Kind", 90),
        ("name", "Name", 220),
        ("integrity", "SHA integrity", 110),
        ("target", "Suggested target", 360),
    ):
        draft_tree.heading(column, text=title)
        draft_tree.column(column, width=width, anchor="w")
    draft_tree.pack(fill="x", pady=(0, 8))

    sha_display = tk.StringVar(value="Staged SHA-256: -")
    ttk.Label(tab, textvariable=sha_display, anchor="w").pack(fill="x", pady=(0, 4))

    preview = tk.Text(tab, wrap="none", height=12)
    preview.pack(fill="both", expand=True)
    preview.configure(state="disabled")

    controls = ttk.Frame(tab, padding=(0, 8, 0, 0))
    controls.pack(fill="x")

    reviewed_sha = tk.StringVar(value="")
    destination_var = tk.StringVar(value="")
    approval_var = tk.StringVar(value="")

    ttk.Label(controls, text="Reviewed SHA-256").grid(row=0, column=0, sticky="w")
    ttk.Entry(controls, textvariable=reviewed_sha, width=70).grid(
        row=0, column=1, sticky="ew", padx=(6, 10)
    )
    ttk.Label(controls, text="Destination (optional)").grid(row=1, column=0, sticky="w")
    ttk.Entry(controls, textvariable=destination_var, width=70).grid(
        row=1, column=1, sticky="ew", padx=(6, 10)
    )
    ttk.Label(controls, text=f"Type {_APPROVAL_PHRASE}").grid(
        row=2, column=0, sticky="w"
    )
    ttk.Entry(controls, textvariable=approval_var, width=28).grid(
        row=2, column=1, sticky="w", padx=(6, 10)
    )
    controls.columnconfigure(1, weight=1)

    state: dict[str, Any] = {"manifests": {}}

    def clear_preview() -> None:
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.configure(state="disabled")

    def refresh_drafts() -> None:
        children = draft_tree.get_children()
        if children:
            draft_tree.delete(*children)
        state["manifests"] = {}
        clear_preview()
        sha_display.set("Staged SHA-256: -")
        drafts = list_desktop_generated_drafts(project)
        for draft in drafts:
            item = draft_tree.insert(
                "",
                "end",
                text=draft["draft_id"],
                values=(
                    draft["status"],
                    draft["kind"] or "-",
                    draft["name"] or "-",
                    draft["integrity"],
                    draft["suggested_target_path"] or "-",
                ),
            )
            state["manifests"][item] = draft["manifest_path"]
        status_var.set(
            f"{len(drafts)} staged generated draft(s). "
            "Application still requires exact reviewed SHA plus explicit approval phrase."
        )

    def select_draft(_event=None) -> None:
        selection = draft_tree.selection()
        if not selection:
            return
        manifest = state["manifests"].get(selection[0])
        if not manifest:
            return
        try:
            draft = read_desktop_generated_draft(project, manifest)
        except (OSError, RuntimeError, ValueError) as exc:
            clear_preview()
            sha_display.set("Staged SHA-256: unavailable")
            status_var.set(f"Draft cannot be reviewed: {exc}")
            return

        preview.configure(state="normal")
        preview.delete("1.0", "end")
        preview.insert("1.0", draft["content"])
        preview.configure(state="disabled")
        sha_display.set(f"Staged SHA-256: {draft['actual_sha256']}")
        status_var.set(
            f"{draft['draft_id']} · integrity={draft['integrity']} · "
            f"eligible={'yes' if draft['eligible'] else 'no'}"
        )

    def apply_selected() -> None:
        selection = draft_tree.selection()
        if not selection:
            status_var.set("Select one staged draft before applying.")
            return
        manifest = state["manifests"].get(selection[0])
        if not manifest:
            status_var.set("Selected draft is unavailable.")
            return

        destination = destination_var.get().strip() or None
        try:
            result = apply_desktop_generated_draft(
                project,
                manifest,
                reviewed_sha256=reviewed_sha.get().strip(),
                approval_phrase=approval_var.get(),
                destination=destination,
            )
        except (FileNotFoundError, FileExistsError, OSError, RuntimeError, ValueError) as exc:
            status_var.set(f"Apply refused: {exc}")
            return

        reviewed_sha.set("")
        approval_var.set("")
        destination_var.set("")
        refresh_drafts()
        status_var.set(
            f"APPLIED {result['draft_id']} -> {result['destination']} · "
            "execution remains disabled."
        )

    draft_tree.bind("<<TreeviewSelect>>", select_draft)
    ttk.Button(controls, text="Apply reviewed draft", command=apply_selected).grid(
        row=2, column=2, sticky="e"
    )
    ttk.Button(controls, text="Refresh drafts", command=refresh_drafts).grid(
        row=0, column=2, sticky="e"
    )

    refresh_drafts()
