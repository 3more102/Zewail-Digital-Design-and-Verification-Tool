from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
import zipfile

from zddv.ai_audit import audit_ai_chain
from zddv.ai_provider import build_model_request
from zddv.ai_response import validate_ai_response_payload
from zddv.config import ProjectConfig
from zddv.generated_artifacts import normalize_generation_proposal


_MANIFEST_PATH = "audit/manifest.json"
_CONTEXT_PATH = "audit/context.json"
_RESPONSE_PATH = "audit/raw-response.json"
_VALIDATED_PATH = "audit/validated-response.json"
_REVIEW_PATH = "audit/review.json"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _pretty_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(payload), indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json_bytes(payload))


def _project_file(
    project: ProjectConfig,
    value: str | Path,
    *,
    label: str,
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    root = project.root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the project root: {path}") from exc
    if path == root:
        raise ValueError(f"{label} must identify a file inside the project root")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _project_output(project: ProjectConfig, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project.root / path
    path = path.resolve()
    allowed_root = (project.root / ".zddv" / "ai" / "audits").resolve()
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(
            "AI audit bundle output must remain under .zddv/ai/audits"
        ) from exc
    if path == allowed_root:
        raise ValueError("AI audit bundle output must identify a ZIP file")
    if path.suffix.lower() != ".zip":
        raise ValueError("AI audit bundle output must end in .zip")
    return path


def _load_json_object(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} root must be a JSON object")
    return dict(payload)


def _artifact_bytes(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    return data, _load_json_object(data, label=str(path))


def _validate_archive_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or chr(92) in name:
        raise ValueError(f"Unsafe AI audit bundle member path: {name}")
    if not name.startswith("audit/"):
        raise ValueError(f"AI audit bundle member is outside audit/: {name}")


def _zip_member(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.extra = b""
    info.comment = b""
    return info, data


def _proposal_core_matches(
    proposal: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> bool:
    try:
        expected = normalize_generation_proposal(
            {
                "kind": selected.get("kind"),
                "language": selected.get("language"),
                "name": selected.get("name"),
                "content": selected.get("content"),
                "target_path": selected.get("target_path"),
                "source": "ai-reviewed-response",
                "evidence": {},
            }
        )
    except (TypeError, ValueError):
        return False

    for key in ("kind", "language", "name", "content", "target_path", "source"):
        if proposal.get(key) != expected.get(key):
            return False
    evidence = proposal.get("evidence")
    if not isinstance(evidence, Mapping):
        return False
    return evidence.get("evidence_refs") == selected.get("evidence_refs", [])


def _validate_reviewed_proposal(
    payload: Mapping[str, Any],
    *,
    validated_payload: Mapping[str, Any],
    validated_payload_sha256: str,
    review_id: str,
    validated_file: Path | None,
    project_root: Path | None = None,
) -> tuple[dict[str, Any], int]:
    proposal = normalize_generation_proposal(payload)
    if proposal["source"] != "ai-reviewed-response":
        raise ValueError("AI audit proposal source must be ai-reviewed-response")

    evidence = proposal.get("evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("AI audit proposal evidence must be an object")
    if evidence.get("review_id") != review_id:
        raise RuntimeError("AI audit proposal review_id does not match the review")
    if evidence.get("validated_payload_sha256") != validated_payload_sha256:
        raise RuntimeError(
            "AI audit proposal validated-payload SHA-256 does not match the review"
        )

    if validated_file is not None:
        recorded = evidence.get("validated_response_path")
        if not isinstance(recorded, str) or not recorded:
            raise ValueError(
                "AI audit proposal is missing validated_response_path provenance"
            )
        candidate = Path(recorded)
        if not candidate.is_absolute():
            if project_root is None:
                raise ValueError(
                    "A project root is required to validate relative proposal provenance"
                )
            candidate = project_root / candidate
        if candidate.resolve() != validated_file.resolve():
            raise RuntimeError(
                "AI audit proposal validated_response_path does not match the "
                "validated artifact"
            )

    proposals = validated_payload.get("generated_proposals")
    if not isinstance(proposals, list):
        raise ValueError("Validated AI payload generated_proposals must be a list")

    recorded_index = evidence.get("proposal_index")
    if recorded_index is not None:
        if not isinstance(recorded_index, int) or isinstance(recorded_index, bool):
            raise ValueError("AI audit proposal proposal_index must be an integer")
        if recorded_index < 1 or recorded_index > len(proposals):
            raise ValueError("AI audit proposal proposal_index is out of range")
        selected = proposals[recorded_index - 1]
        if not isinstance(selected, Mapping) or not _proposal_core_matches(
            proposal,
            selected,
        ):
            raise RuntimeError(
                "AI audit proposal content does not match its recorded proposal_index"
            )
        return proposal, recorded_index

    matches = [
        index
        for index, selected in enumerate(proposals, start=1)
        if isinstance(selected, Mapping)
        and _proposal_core_matches(proposal, selected)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "AI audit proposal cannot be mapped uniquely to the validated payload; "
            "re-export it with the current ZDDV proposal exporter"
        )
    return proposal, matches[0]


def _portable_chain(
    *,
    context_bytes: bytes,
    response_bytes: bytes,
    validated_bytes: bytes,
    review_bytes: bytes,
    proposal_items: list[tuple[str, bytes]],
) -> dict[str, Any]:
    context = _load_json_object(context_bytes, label="bundled AI context")
    if context.get("analysis") != "ai_rca_context":
        raise ValueError("Bundled context is not a ZDDV ai_rca_context artifact")
    evidence = context.get("evidence")
    provenance = context.get("provenance")
    if not isinstance(evidence, Mapping) or not isinstance(provenance, Mapping):
        raise ValueError("Bundled AI context is missing evidence/provenance")
    evidence_sha = _canonical_sha256(evidence)
    if provenance.get("evidence_sha256") != evidence_sha:
        raise RuntimeError("Bundled context evidence SHA-256 verification failed")
    request_sha = _canonical_sha256(build_model_request(context))

    raw = _load_json_object(response_bytes, label="bundled raw provider response")
    if raw.get("analysis") != "ai_provider_response_raw":
        raise ValueError(
            "Bundled raw response is not a ZDDV ai_provider_response_raw artifact"
        )
    raw_sha = _sha256_bytes(response_bytes)
    if raw.get("context_evidence_sha256") != evidence_sha:
        raise RuntimeError(
            "Bundled raw response context evidence SHA-256 does not match context"
        )
    if raw.get("request_sha256") != request_sha:
        raise RuntimeError(
            "Bundled raw response request SHA-256 does not match reconstructed request"
        )

    validated = _load_json_object(
        validated_bytes,
        label="bundled validated AI response",
    )
    if validated.get("analysis") != "ai_response_validated":
        raise ValueError("Bundled validated artifact is not an AI response validation")
    if validated.get("status") != "VALIDATED_UNREVIEWED":
        raise ValueError(
            "Bundled validated AI response status must be VALIDATED_UNREVIEWED"
        )
    validated_payload = validated.get("validated_payload")
    if not isinstance(validated_payload, Mapping):
        raise ValueError("Bundled validated AI response is missing validated_payload")
    normalized = validate_ai_response_payload(validated_payload, context=context)
    if dict(validated_payload) != normalized:
        raise RuntimeError(
            "Bundled validated AI response payload is not in canonical validated form"
        )
    validated_payload_sha = _canonical_sha256(validated_payload)
    if validated.get("validated_payload_sha256") != validated_payload_sha:
        raise RuntimeError(
            "Bundled validated-payload SHA-256 does not match the validated payload"
        )
    if validated.get("provider") != raw.get("provider"):
        raise RuntimeError(
            "Bundled validated provider metadata does not match the raw response"
        )
    validated_provenance = validated.get("provenance")
    if not isinstance(validated_provenance, Mapping):
        raise ValueError("Bundled validated response is missing provenance")
    expected_links = {
        "context_evidence_sha256": evidence_sha,
        "provider_request_sha256": request_sha,
        "raw_response_sha256": raw_sha,
    }
    for key, expected in expected_links.items():
        if validated_provenance.get(key) != expected:
            raise RuntimeError(
                f"Bundled validated response {key} provenance verification failed"
            )
    validated_artifact_sha = _sha256_bytes(validated_bytes)

    review = _load_json_object(review_bytes, label="bundled AI review")
    if review.get("analysis") != "ai_response_review":
        raise ValueError("Bundled review is not an AI response review record")
    if review.get("status") != "APPROVED" or review.get("approved") is not True:
        raise RuntimeError("Bundled AI review record is not approved")
    if review.get("validated_payload_sha256") != validated_payload_sha:
        raise RuntimeError(
            "Bundled AI review does not match the validated payload SHA-256"
        )
    proposal_count = len(validated_payload.get("generated_proposals", []))
    if review.get("generated_proposals") != proposal_count:
        raise RuntimeError(
            "Bundled AI review proposal count does not match validated payload"
        )
    review_id = str(review.get("review_id") or "")
    if not review_id:
        raise ValueError("Bundled AI review is missing review_id")
    review_artifact_sha = _sha256_bytes(review_bytes)

    proposal_chain: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for archive_path, proposal_bytes in proposal_items:
        proposal_payload = _load_json_object(
            proposal_bytes,
            label=f"bundled proposal {archive_path}",
        )
        proposal, proposal_index = _validate_reviewed_proposal(
            proposal_payload,
            validated_payload=validated_payload,
            validated_payload_sha256=validated_payload_sha,
            review_id=review_id,
            validated_file=None,
        )
        if proposal_index in seen_indices:
            raise RuntimeError(
                f"AI audit bundle contains duplicate proposal index {proposal_index}"
            )
        seen_indices.add(proposal_index)
        proposal_chain.append(
            {
                "proposal_index": proposal_index,
                "name": proposal["name"],
                "artifact_sha256": _sha256_bytes(proposal_bytes),
                "archive_path": archive_path,
            }
        )
    proposal_chain.sort(key=lambda item: int(item["proposal_index"]))

    return {
        "context_evidence_sha256": evidence_sha,
        "provider_request_sha256": request_sha,
        "raw_response_sha256": raw_sha,
        "validated_artifact_sha256": validated_artifact_sha,
        "validated_payload_sha256": validated_payload_sha,
        "review_artifact_sha256": review_artifact_sha,
        "review_id": review_id,
        "proposals": proposal_chain,
    }


def export_ai_audit_bundle(
    project: ProjectConfig,
    *,
    context_path: str | Path,
    response_path: str | Path,
    validated_path: str | Path,
    review_path: str | Path,
    proposal_paths: list[str | Path] | tuple[str | Path, ...],
    output: str | Path = ".zddv/ai/audits/ai-audit-bundle.zip",
) -> dict[str, Any]:
    if not proposal_paths:
        raise ValueError("AI audit bundle requires at least one reviewed proposal")

    context_file = _project_file(project, context_path, label="AI context path")
    response_file = _project_file(
        project,
        response_path,
        label="Raw provider response path",
    )
    validated_file = _project_file(
        project,
        validated_path,
        label="Validated AI response path",
    )
    review_file = _project_file(project, review_path, label="AI review path")

    audit = audit_ai_chain(
        project,
        context_path=context_file,
        response_path=response_file,
        validated_path=validated_file,
        review_path=review_file,
    )
    if audit.get("status") != "PASS" or audit.get("last_verified_stage") != "human-review":
        raise RuntimeError("AI provenance chain did not verify through human review")

    context_bytes, _ = _artifact_bytes(context_file)
    response_bytes, _ = _artifact_bytes(response_file)
    validated_bytes, validated = _artifact_bytes(validated_file)
    review_bytes, review = _artifact_bytes(review_file)

    validated_payload = validated.get("validated_payload")
    if not isinstance(validated_payload, Mapping):
        raise ValueError("Validated AI response is missing validated_payload")
    validated_payload_sha = str(validated.get("validated_payload_sha256") or "")
    review_id = str(review.get("review_id") or "")

    indexed_proposals: list[tuple[int, Path, bytes, dict[str, Any]]] = []
    seen_indices: set[int] = set()
    for value in proposal_paths:
        proposal_file = _project_file(
            project,
            value,
            label="Reviewed AI proposal path",
        )
        proposal_bytes, proposal_payload = _artifact_bytes(proposal_file)
        proposal, proposal_index = _validate_reviewed_proposal(
            proposal_payload,
            validated_payload=validated_payload,
            validated_payload_sha256=validated_payload_sha,
            review_id=review_id,
            validated_file=validated_file,
            project_root=project.root.resolve(),
        )
        if proposal_index in seen_indices:
            raise ValueError(
                f"Duplicate reviewed proposal index supplied: {proposal_index}"
            )
        seen_indices.add(proposal_index)
        indexed_proposals.append(
            (proposal_index, proposal_file, proposal_bytes, proposal)
        )
    indexed_proposals.sort(key=lambda item: item[0])

    members: dict[str, bytes] = {
        _CONTEXT_PATH: context_bytes,
        _RESPONSE_PATH: response_bytes,
        _VALIDATED_PATH: validated_bytes,
        _REVIEW_PATH: review_bytes,
    }
    proposal_items: list[tuple[str, bytes]] = []
    for proposal_index, _source, proposal_bytes, proposal in indexed_proposals:
        archive_path = (
            f"audit/proposals/{proposal_index:03d}-{proposal['name']}.json"
        )
        _validate_archive_member_name(archive_path)
        members[archive_path] = proposal_bytes
        proposal_items.append((archive_path, proposal_bytes))

    chain = _portable_chain(
        context_bytes=context_bytes,
        response_bytes=response_bytes,
        validated_bytes=validated_bytes,
        review_bytes=review_bytes,
        proposal_items=proposal_items,
    )

    stage_by_path = {
        _CONTEXT_PATH: "context",
        _RESPONSE_PATH: "provider-response",
        _VALIDATED_PATH: "validated-response",
        _REVIEW_PATH: "human-review",
    }
    files: list[dict[str, Any]] = []
    for path in sorted(members):
        stage = stage_by_path.get(path, "reviewed-proposal")
        item: dict[str, Any] = {
            "path": path,
            "stage": stage,
            "sha256": _sha256_bytes(members[path]),
        }
        if stage == "reviewed-proposal":
            match = next(
                proposal
                for proposal in chain["proposals"]
                if proposal["archive_path"] == path
            )
            item["proposal_index"] = match["proposal_index"]
            item["name"] = match["name"]
        files.append(item)

    manifest = {
        "schema_version": 1,
        "artifact": "zddv_ai_audit_bundle",
        "project": project.name,
        "files": files,
        "chain": chain,
        "policy": {
            "model_invocation": False,
            "external_transmission": False,
            "generated_artifact_staging": False,
            "project_modification": False,
            "command_execution": False,
        },
        "portability": {
            "archive_format": "zip-stored",
            "member_order": "lexicographic",
            "member_timestamp": "1980-01-01T00:00:00",
            "source_absolute_paths_are_historical_metadata": True,
            "verification_uses_bundled_content_and_hash_links": True,
            "deterministic": True,
        },
        "semantics": (
            "VERIFIED means the bundled context, raw provider response, validated "
            "response, approved review, and selected reviewed proposal artifacts form "
            "a self-consistent ZDDV provenance chain. SHA-256 consistency is an "
            "integrity check, not an authenticity signature and not evidence that "
            "model hypotheses are correct."
        ),
    }
    manifest_bytes = _pretty_json_bytes(manifest)
    members[_MANIFEST_PATH] = manifest_bytes

    destination = _project_output(project, output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        archive.comment = b""
        for name in sorted(members):
            info, data = _zip_member(name, members[name])
            archive.writestr(info, data)

    return {
        "status": "EXPORTED",
        "path": str(destination),
        "archive_sha256": _sha256_bytes(destination.read_bytes()),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "review_id": chain["review_id"],
        "proposal_indices": [
            item["proposal_index"] for item in chain["proposals"]
        ],
        "manifest": manifest,
    }


def verify_ai_audit_bundle(
    archive_path: str | Path,
) -> dict[str, Any]:
    source = Path(archive_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"AI audit bundle not found: {source}")

    try:
        with zipfile.ZipFile(source, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("AI audit bundle contains duplicate member names")
            for name in names:
                _validate_archive_member_name(name)
            if _MANIFEST_PATH not in names:
                raise ValueError("AI audit bundle is missing audit/manifest.json")
            manifest_bytes = archive.read(_MANIFEST_PATH)
            manifest = _load_json_object(
                manifest_bytes,
                label="AI audit bundle manifest",
            )
            files = manifest.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("AI audit bundle manifest files must be a non-empty list")

            expected_names = {_MANIFEST_PATH}
            file_rows: dict[str, dict[str, Any]] = {}
            for item in files:
                if not isinstance(item, Mapping):
                    raise ValueError("AI audit bundle file entry must be an object")
                path = item.get("path")
                if not isinstance(path, str) or not path:
                    raise ValueError("AI audit bundle file entry path is missing")
                _validate_archive_member_name(path)
                if path == _MANIFEST_PATH:
                    raise ValueError("Manifest must not hash itself in files inventory")
                if path in file_rows:
                    raise ValueError(f"Duplicate manifest file entry: {path}")
                file_rows[path] = dict(item)
                expected_names.add(path)

            if set(names) != expected_names:
                raise ValueError(
                    "AI audit bundle member set does not match the manifest inventory"
                )

            bundled: dict[str, bytes] = {}
            for path, row in file_rows.items():
                data = archive.read(path)
                expected_sha = str(row.get("sha256") or "")
                actual_sha = _sha256_bytes(data)
                if not expected_sha or not hmac.compare_digest(
                    expected_sha,
                    actual_sha,
                ):
                    raise RuntimeError(
                        f"AI audit bundle SHA-256 verification failed for {path}"
                    )
                bundled[path] = data
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid AI audit ZIP archive: {source}") from exc

    if manifest.get("artifact") != "zddv_ai_audit_bundle":
        raise ValueError("Archive is not a ZDDV AI audit bundle")
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported ZDDV AI audit bundle schema version")

    for required in (_CONTEXT_PATH, _RESPONSE_PATH, _VALIDATED_PATH, _REVIEW_PATH):
        if required not in bundled:
            raise ValueError(f"AI audit bundle is missing required artifact: {required}")

    proposal_items = sorted(
        (
            (path, data)
            for path, data in bundled.items()
            if path.startswith("audit/proposals/")
        ),
        key=lambda item: item[0],
    )
    if not proposal_items:
        raise ValueError("AI audit bundle contains no reviewed proposal artifacts")

    chain = _portable_chain(
        context_bytes=bundled[_CONTEXT_PATH],
        response_bytes=bundled[_RESPONSE_PATH],
        validated_bytes=bundled[_VALIDATED_PATH],
        review_bytes=bundled[_REVIEW_PATH],
        proposal_items=proposal_items,
    )
    recorded_chain = manifest.get("chain")
    if not isinstance(recorded_chain, Mapping) or dict(recorded_chain) != chain:
        raise RuntimeError(
            "AI audit bundle manifest chain does not match recomputed provenance"
        )

    expected_stages = {
        _CONTEXT_PATH: "context",
        _RESPONSE_PATH: "provider-response",
        _VALIDATED_PATH: "validated-response",
        _REVIEW_PATH: "human-review",
    }
    for path, stage in expected_stages.items():
        if file_rows[path].get("stage") != stage:
            raise RuntimeError(f"AI audit bundle stage metadata mismatch for {path}")
    for proposal in chain["proposals"]:
        row = file_rows.get(str(proposal["archive_path"]))
        if row is None or row.get("stage") != "reviewed-proposal":
            raise RuntimeError("AI audit bundle proposal stage metadata is invalid")
        if row.get("proposal_index") != proposal["proposal_index"]:
            raise RuntimeError("AI audit bundle proposal index metadata is invalid")
        if row.get("name") != proposal["name"]:
            raise RuntimeError("AI audit bundle proposal name metadata is invalid")

    return {
        "status": "VERIFIED",
        "path": str(source),
        "archive_sha256": _sha256_bytes(source.read_bytes()),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "review_id": chain["review_id"],
        "proposal_indices": [
            item["proposal_index"] for item in chain["proposals"]
        ],
        "chain": chain,
    }
