from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
from pathlib import Path, PurePosixPath
from typing import Any
import zipfile

from zddv.config import ProjectConfig


_RELEASE_MANIFEST_PATH = "release/manifest.json"
_RELEASE_SIGNOFF_PATH = "release/signoff.json"
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_EXPECTED_REPRODUCIBILITY = {
    "archive_format": "zip-stored",
    "member_order": "lexicographic",
    "member_timestamp": "1980-01-01T00:00:00",
    "deterministic": True,
}


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _pretty_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_project_path(
    project: ProjectConfig,
    path: str | Path,
    *,
    label: str,
) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = project.root / candidate
    candidate = candidate.resolve()
    root = project.root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the project root: {candidate}") from exc
    if candidate == root:
        raise ValueError(f"{label} must identify a file inside the project root")
    return candidate


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "project": payload.get("project"),
        "simulator": payload.get("simulator"),
        "top": payload.get("top"),
    }


def _project_identity(project: ProjectConfig) -> dict[str, str]:
    return {
        "project": project.name,
        "simulator": project.simulator,
        "top": project.top,
    }


def _validate_signoff_project_identity(
    project: ProjectConfig,
    signoff: dict[str, Any],
) -> None:
    if _identity(signoff) != _project_identity(project):
        raise ValueError(
            "Release signoff project/simulator/top identity does not match "
            "the active project configuration"
        )


def _validate_signoff_payload(payload: dict[str, Any]) -> None:
    if payload.get("analysis") != "verification_signoff_bundle":
        raise ValueError("Release input is not a ZDDV verification signoff bundle")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Signoff bundle is missing provenance")

    evidence = payload.get("evidence")
    policy = payload.get("policy")
    if not isinstance(evidence, dict) or not isinstance(policy, dict):
        raise ValueError("Signoff bundle is missing evidence or policy")

    expected_evidence = str(provenance.get("evidence_sha256", ""))
    actual_evidence = _sha256_bytes(_canonical_json_bytes(evidence))
    if not expected_evidence or not hmac.compare_digest(
        expected_evidence,
        actual_evidence,
    ):
        raise ValueError("Signoff evidence SHA-256 verification failed")

    expected_policy = str(provenance.get("policy_sha256", ""))
    actual_policy = _sha256_bytes(_canonical_json_bytes(policy))
    if not expected_policy or not hmac.compare_digest(expected_policy, actual_policy):
        raise ValueError("Signoff policy SHA-256 verification failed")

    core = {key: value for key, value in payload.items() if key != "provenance"}
    expected_signoff = str(provenance.get("signoff_sha256", ""))
    actual_signoff = _sha256_bytes(_canonical_json_bytes(core))
    if not expected_signoff or not hmac.compare_digest(expected_signoff, actual_signoff):
        raise ValueError("Signoff SHA-256 verification failed")


def _load_signoff(
    project: ProjectConfig,
    path: str | Path,
) -> tuple[Path, dict[str, Any], bytes]:
    source = _resolve_project_path(project, path, label="Signoff input")
    if not source.is_file():
        raise FileNotFoundError(f"Signoff bundle not found: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid signoff JSON: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Signoff bundle must contain one JSON object")
    _validate_signoff_payload(payload)
    canonical_file_bytes = _pretty_json_bytes(payload)
    return source, payload, canonical_file_bytes


def _load_ed25519_private_key(path: str | Path):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise RuntimeError(
            "Ed25519 signing requires the optional 'signing' dependency: "
            "pip install 'zddv[signing]'"
        ) from exc

    key_path = Path(path).expanduser().resolve()
    if not key_path.is_file():
        raise FileNotFoundError(f"Signing private key not found: {key_path}")
    try:
        key = serialization.load_pem_private_key(
            key_path.read_bytes(),
            password=None,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid unencrypted PEM private key: {key_path}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Release signing key must be an Ed25519 private key")
    return key


def _load_ed25519_public_key(path: str | Path):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError(
            "Ed25519 verification requires the optional 'signing' dependency: "
            "pip install 'zddv[signing]'"
        ) from exc

    key_path = Path(path).expanduser().resolve()
    if not key_path.is_file():
        raise FileNotFoundError(f"Trusted public key not found: {key_path}")
    try:
        key = serialization.load_pem_public_key(key_path.read_bytes())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid PEM public key: {key_path}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Release verification key must be an Ed25519 public key")
    return key


def _public_key_sha256(public_key) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _sha256_bytes(raw)


def _unsigned_manifest(
    *,
    project: ProjectConfig,
    signoff: dict[str, Any],
    signoff_file_sha256: str,
    key_id: str,
    public_key_sha256: str,
) -> dict[str, Any]:
    provenance = signoff["provenance"]
    summary = signoff.get("summary", {})
    return {
        "schema_version": 1,
        "artifact": "zddv_verification_release",
        "project": project.name,
        "simulator": project.simulator,
        "top": project.top,
        "semantics": (
            "This archive authenticates one exact READY_FOR_REVIEW ZDDV signoff "
            "bundle. The Ed25519 signature proves the manifest was signed by the "
            "holder of the corresponding private key; it does not independently "
            "prove specification-complete verification."
        ),
        "signoff": {
            "archive_path": _RELEASE_SIGNOFF_PATH,
            "file_sha256": signoff_file_sha256,
            "signoff_sha256": provenance["signoff_sha256"],
            "evidence_sha256": provenance["evidence_sha256"],
            "policy_sha256": provenance["policy_sha256"],
            "review_state": summary.get("review_state"),
        },
        "files": [
            {
                "path": _RELEASE_SIGNOFF_PATH,
                "sha256": signoff_file_sha256,
            }
        ],
        "signature": {
            "algorithm": "ed25519",
            "key_id": key_id,
            "public_key_sha256": public_key_sha256,
        },
        "reproducibility": dict(_EXPECTED_REPRODUCIBILITY),
    }


def _signed_manifest(
    unsigned: dict[str, Any],
    private_key,
) -> dict[str, Any]:
    signed_payload = _canonical_json_bytes(unsigned)
    signature = private_key.sign(signed_payload)
    payload = json.loads(json.dumps(unsigned))
    payload["signature"]["signed_payload_sha256"] = _sha256_bytes(signed_payload)
    payload["signature"]["value_base64"] = base64.b64encode(signature).decode("ascii")
    return payload


def _zip_member(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.extra = b""
    info.comment = b""
    return info, data


def _canonical_release_archive_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.comment = b""
        for name in sorted(members):
            info, data = _zip_member(name, members[name])
            archive.writestr(info, data)
    return buffer.getvalue()


def export_verification_release(
    project: ProjectConfig,
    *,
    signoff: str | Path = ".zddv/signoff/signoff.json",
    expected_signoff_sha256: str,
    private_key: str | Path,
    key_id: str,
    output: str | Path = ".zddv/signoff/release.zip",
) -> dict[str, Any]:
    expected = str(expected_signoff_sha256).strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected_signoff_sha256 must be a 64-character lowercase hex SHA-256")

    normalized_key_id = str(key_id).strip()
    if not normalized_key_id:
        raise ValueError("key_id must be non-empty")

    _, signoff_payload, signoff_bytes = _load_signoff(project, signoff)
    _validate_signoff_project_identity(project, signoff_payload)
    review_state = str(signoff_payload.get("summary", {}).get("review_state", ""))
    if review_state != "READY_FOR_REVIEW":
        raise ValueError(
            "Release export requires a READY_FOR_REVIEW signoff bundle; "
            f"found {review_state or 'UNKNOWN'}"
        )

    actual_signoff_sha256 = str(signoff_payload["provenance"]["signoff_sha256"]).lower()
    if not hmac.compare_digest(expected, actual_signoff_sha256):
        raise ValueError(
            "Expected signoff SHA-256 does not match the verified signoff bundle"
        )

    signing_key = _load_ed25519_private_key(private_key)
    public_key = signing_key.public_key()
    signoff_file_sha256 = _sha256_bytes(signoff_bytes)
    unsigned = _unsigned_manifest(
        project=project,
        signoff=signoff_payload,
        signoff_file_sha256=signoff_file_sha256,
        key_id=normalized_key_id,
        public_key_sha256=_public_key_sha256(public_key),
    )
    manifest = _signed_manifest(unsigned, signing_key)
    manifest_bytes = _pretty_json_bytes(manifest)

    destination = _resolve_project_path(project, output, label="Release output")
    destination.parent.mkdir(parents=True, exist_ok=True)

    members = {
        _RELEASE_MANIFEST_PATH: manifest_bytes,
        _RELEASE_SIGNOFF_PATH: signoff_bytes,
    }
    archive_bytes = _canonical_release_archive_bytes(members)
    destination.write_bytes(archive_bytes)
    return {
        "path": str(destination),
        "archive_sha256": _sha256_bytes(archive_bytes),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "signoff_sha256": actual_signoff_sha256,
        "key_id": normalized_key_id,
        "public_key_sha256": manifest["signature"]["public_key_sha256"],
        "manifest": manifest,
    }


def _validate_archive_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"Unsafe release archive member path: {name}")


def verify_verification_release(
    archive_path: str | Path,
    *,
    public_key: str | Path,
) -> dict[str, Any]:
    source = Path(archive_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Release archive not found: {source}")

    try:
        with zipfile.ZipFile(source, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Release archive contains duplicate member names")
            for name in names:
                _validate_archive_member_name(name)
            expected_names = {_RELEASE_MANIFEST_PATH, _RELEASE_SIGNOFF_PATH}
            if set(names) != expected_names:
                raise ValueError(
                    "Release archive member set is not the exact expected ZDDV release layout"
                )
            manifest_bytes = archive.read(_RELEASE_MANIFEST_PATH)
            signoff_bytes = archive.read(_RELEASE_SIGNOFF_PATH)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid release ZIP archive: {source}") from exc

    archive_bytes = source.read_bytes()
    canonical_archive_bytes = _canonical_release_archive_bytes(
        {
            _RELEASE_MANIFEST_PATH: manifest_bytes,
            _RELEASE_SIGNOFF_PATH: signoff_bytes,
        }
    )
    if archive_bytes != canonical_archive_bytes:
        raise ValueError(
            "Release archive does not use the canonical deterministic ZDDV ZIP layout"
        )

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        signoff = json.loads(signoff_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Release archive contains invalid UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or not isinstance(signoff, dict):
        raise ValueError("Release manifest and signoff must be JSON objects")

    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported release manifest schema version")
    if manifest.get("artifact") != "zddv_verification_release":
        raise ValueError("Archive manifest is not a ZDDV verification release")
    if manifest.get("reproducibility") != _EXPECTED_REPRODUCIBILITY:
        raise ValueError(
            "Release manifest reproducibility metadata does not match "
            "the canonical ZDDV archive contract"
        )
    signature = manifest.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519":
        raise ValueError("Release manifest does not contain an Ed25519 signature")

    trusted_key = _load_ed25519_public_key(public_key)
    trusted_fingerprint = _public_key_sha256(trusted_key)
    recorded_fingerprint = str(signature.get("public_key_sha256", ""))
    if not recorded_fingerprint or not hmac.compare_digest(
        recorded_fingerprint,
        trusted_fingerprint,
    ):
        raise ValueError("Trusted public key fingerprint does not match the release manifest")

    signature_b64 = signature.get("value_base64")
    signed_payload_sha256 = str(signature.get("signed_payload_sha256", ""))
    if not isinstance(signature_b64, str) or not signature_b64:
        raise ValueError("Release manifest signature value is missing")

    unsigned = json.loads(json.dumps(manifest))
    unsigned_signature = unsigned["signature"]
    unsigned_signature.pop("value_base64", None)
    unsigned_signature.pop("signed_payload_sha256", None)
    signed_payload = _canonical_json_bytes(unsigned)
    actual_payload_sha256 = _sha256_bytes(signed_payload)
    if not signed_payload_sha256 or not hmac.compare_digest(
        signed_payload_sha256,
        actual_payload_sha256,
    ):
        raise ValueError("Release manifest signed-payload SHA-256 verification failed")

    try:
        signature_bytes = base64.b64decode(signature_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Release manifest signature is not valid base64") from exc

    try:
        trusted_key.verify(signature_bytes, signed_payload)
    except Exception as exc:
        try:
            from cryptography.exceptions import InvalidSignature
        except ImportError:
            InvalidSignature = Exception
        if isinstance(exc, InvalidSignature):
            raise ValueError("Release manifest Ed25519 signature verification failed") from exc
        raise

    files = manifest.get("files")
    if not isinstance(files, list) or files != [
        {
            "path": _RELEASE_SIGNOFF_PATH,
            "sha256": _sha256_bytes(signoff_bytes),
        }
    ]:
        raise ValueError("Release manifest file inventory or SHA-256 verification failed")

    _validate_signoff_payload(signoff)
    if _identity(manifest) != _identity(signoff):
        raise ValueError(
            "Release manifest project/simulator/top identity does not match "
            "the bundled signoff"
        )

    signoff_meta = manifest.get("signoff")
    if not isinstance(signoff_meta, dict):
        raise ValueError("Release manifest signoff metadata is missing")

    expected_meta = {
        "archive_path": _RELEASE_SIGNOFF_PATH,
        "file_sha256": _sha256_bytes(signoff_bytes),
        "signoff_sha256": signoff["provenance"]["signoff_sha256"],
        "evidence_sha256": signoff["provenance"]["evidence_sha256"],
        "policy_sha256": signoff["provenance"]["policy_sha256"],
        "review_state": signoff.get("summary", {}).get("review_state"),
    }
    if signoff_meta != expected_meta:
        raise ValueError("Release manifest signoff metadata does not match the bundled signoff")
    if expected_meta["review_state"] != "READY_FOR_REVIEW":
        raise ValueError("Bundled signoff is not READY_FOR_REVIEW")

    return {
        "status": "VERIFIED",
        "path": str(source),
        "archive_sha256": _sha256_bytes(archive_bytes),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "signoff_sha256": expected_meta["signoff_sha256"],
        "key_id": signature.get("key_id"),
        "public_key_sha256": trusted_fingerprint,
    }
