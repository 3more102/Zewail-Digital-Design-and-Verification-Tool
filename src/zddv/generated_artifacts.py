from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from zddv.config import ProjectConfig


_ALLOWED_KINDS = {"assertion", "test"}
_ALLOWED_LANGUAGE = "systemverilog"
_ALLOWED_SUFFIXES = {".sv", ".svh"}
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_DRAFT_ID_RE = re.compile(r"^draft-[0-9a-f]{16}$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _project_path(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    root = project.root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path must remain inside the project root: {path}") from exc
    if path == root:
        raise ValueError("Path must identify a file inside the project root")
    return path


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} root must be a JSON object")
    return dict(payload)


def normalize_generation_proposal(payload: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in _ALLOWED_KINDS:
        raise ValueError("generation proposal kind must be 'assertion' or 'test'")

    language = str(payload.get("language") or _ALLOWED_LANGUAGE).strip().lower()
    if language != _ALLOWED_LANGUAGE:
        raise ValueError("generation proposal language must be 'systemverilog'")

    name = str(payload.get("name") or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            "generation proposal name must start with a letter/underscore and "
            "contain only letters, digits, '_', '.', or '-'"
        )

    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("generation proposal content must be a non-empty string")
    normalized_content = content if content.endswith("\n") else content + "\n"

    target_path_raw = payload.get("target_path")
    target_path = None
    if target_path_raw is not None:
        target_path = str(target_path_raw).strip()
        if not target_path:
            raise ValueError("target_path must not be empty when supplied")
        suffix = Path(target_path).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError("generated assertion/test target_path must end in .sv or .svh")

    evidence = payload.get("evidence", {})
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, Mapping):
        raise ValueError("generation proposal evidence must be an object")

    source = str(payload.get("source") or "manual-proposal").strip() or "manual-proposal"
    return {
        "kind": kind,
        "language": language,
        "name": name,
        "content": normalized_content,
        "target_path": target_path,
        "source": source,
        "evidence": dict(evidence),
    }


def stage_generated_artifact(
    project: ProjectConfig,
    proposal_path: str | Path,
    *,
    output_root: str | Path = ".zddv/generated",
) -> dict[str, Any]:
    """Stage exact proposed verification code outside the project source tree."""

    source_path = _project_path(project, proposal_path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    proposal_raw = source_path.read_bytes()
    proposal_payload = _load_json_object(source_path, label="generation proposal")
    proposal = normalize_generation_proposal(proposal_payload)

    output_dir = _project_path(project, output_root)
    generated_root = (project.root / ".zddv" / "generated").resolve()
    try:
        output_dir.relative_to(generated_root)
    except ValueError as exc:
        raise ValueError(
            "Generated drafts must remain under .zddv/generated for review isolation"
        ) from exc

    content_bytes = proposal["content"].encode("utf-8")
    content_sha256 = _sha256(content_bytes)
    identity = json.dumps(
        {
            "kind": proposal["kind"],
            "language": proposal["language"],
            "name": proposal["name"],
            "content_sha256": content_sha256,
            "target_path": proposal["target_path"],
            "source": proposal["source"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    draft_id = f"draft-{_sha256(identity)[:16]}"
    draft_dir = output_dir / "drafts" / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)

    suffix = (
        Path(proposal["target_path"]).suffix.lower()
        if proposal["target_path"] is not None
        else ".sv"
    )
    draft_path = draft_dir / f"{proposal['name']}{suffix}"
    if draft_path.exists() and _sha256(draft_path.read_bytes()) != content_sha256:
        raise RuntimeError(f"Draft ID collision with different content: {draft_path}")
    draft_path.write_bytes(content_bytes)

    manifest_path = draft_dir / "manifest.json"
    manifest = {
        "schema_version": 1,
        "analysis": "generated_verification_draft",
        "draft_id": draft_id,
        "status": "DRAFT",
        "kind": proposal["kind"],
        "language": proposal["language"],
        "name": proposal["name"],
        "source": proposal["source"],
        "evidence": proposal["evidence"],
        "proposal_path": str(source_path),
        "proposal_sha256": _sha256(proposal_raw),
        "content_path": str(draft_path.resolve()),
        "content_sha256": content_sha256,
        "suggested_target_path": proposal["target_path"],
        "review_required": True,
        "approved": False,
        "auto_apply": False,
        "execution_enabled": False,
        "manifest_path": str(manifest_path.resolve()),
        "semantics": (
            "The staged artifact is exact proposed text isolated under .zddv/generated. "
            "ZDDV does not compile, execute, or copy it into project sources until an "
            "explicit SHA-confirmed apply command is approved."
        ),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def apply_generated_artifact(
    project: ProjectConfig,
    manifest_path: str | Path,
    *,
    destination: str | Path | None,
    expected_sha256: str,
    approve_reviewed: bool,
) -> dict[str, Any]:
    """Apply one reviewed draft after explicit approval and exact SHA confirmation."""

    if not approve_reviewed:
        raise RuntimeError(
            "Generated artifact apply requires explicit --approve-reviewed opt-in"
        )

    expected = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("expected_sha256 must be a 64-character lowercase SHA-256")

    manifest_file = _project_path(project, manifest_path)
    if not manifest_file.is_file():
        raise FileNotFoundError(manifest_file)
    manifest = _load_json_object(manifest_file, label="generated draft manifest")
    if manifest.get("analysis") != "generated_verification_draft":
        raise ValueError("manifest is not a generated verification draft")
    if manifest.get("status") != "DRAFT":
        raise ValueError("generated artifact manifest status must be DRAFT")
    if manifest.get("review_required") is not True or manifest.get("auto_apply") is not False:
        raise ValueError("generated artifact manifest lacks review/opt-in safeguards")

    draft_id = str(manifest.get("draft_id") or "").strip()
    if not _DRAFT_ID_RE.fullmatch(draft_id):
        raise ValueError("generated artifact manifest has an invalid draft_id")
    expected_draft_dir = (
        project.root / ".zddv" / "generated" / "drafts" / draft_id
    ).resolve()
    if manifest_file.parent.resolve() != expected_draft_dir:
        raise ValueError(
            "generated artifact manifest path does not match its draft_id"
        )

    content_value = manifest.get("content_path")
    if not isinstance(content_value, str) or not content_value:
        raise ValueError("generated artifact manifest is missing content_path")
    content_path = _project_path(project, content_value)
    generated_root = (project.root / ".zddv" / "generated" / "drafts").resolve()
    try:
        content_path.relative_to(generated_root)
    except ValueError as exc:
        raise ValueError(
            "generated draft content must remain under .zddv/generated/drafts"
        ) from exc
    if content_path.parent.resolve() != expected_draft_dir:
        raise ValueError(
            "generated draft content path does not match the manifest draft_id"
        )
    if not content_path.is_file():
        raise FileNotFoundError(content_path)

    content = content_path.read_bytes()
    actual_sha256 = _sha256(content)
    manifest_sha256 = str(manifest.get("content_sha256") or "").lower()
    if actual_sha256 != manifest_sha256:
        raise RuntimeError(
            "Generated draft content changed after staging; review a fresh draft"
        )
    if actual_sha256 != expected:
        raise RuntimeError(
            "Reviewed SHA-256 does not match the staged generated artifact"
        )

    target_value = destination
    if target_value is None:
        target_value = manifest.get("suggested_target_path")
    if target_value is None or not str(target_value).strip():
        raise ValueError(
            "No destination supplied and the draft has no suggested_target_path"
        )
    target = _project_path(project, str(target_value))
    if target.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise ValueError("generated assertion/test destination must end in .sv or .svh")

    zddv_root = (project.root / ".zddv").resolve()
    try:
        target.relative_to(zddv_root)
    except ValueError:
        pass
    else:
        raise ValueError(
            "Reviewed generated code must be applied to a project source path, "
            "not back into .zddv"
        )

    if target.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing project file: {target}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    applied_dir = (project.root / ".zddv" / "generated" / "applied").resolve()
    applied_dir.mkdir(parents=True, exist_ok=True)
    applied_path = applied_dir / f"{manifest['draft_id']}.json"
    result = {
        "schema_version": 1,
        "analysis": "generated_verification_apply",
        "draft_id": manifest["draft_id"],
        "status": "APPLIED",
        "kind": manifest.get("kind"),
        "language": manifest.get("language"),
        "name": manifest.get("name"),
        "source_manifest": str(manifest_file),
        "content_sha256": actual_sha256,
        "destination": str(target),
        "review_required": True,
        "approved": True,
        "auto_apply": False,
        "execution_enabled": False,
        "applied_record_path": str(applied_path),
        "semantics": (
            "The exact reviewed bytes identified by content_sha256 were copied only "
            "after explicit approval. ZDDV did not compile or execute the artifact."
        ),
    }
    applied_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
