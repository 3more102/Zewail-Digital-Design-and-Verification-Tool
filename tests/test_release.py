from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from zddv.cli import main
from zddv.config import initialize_project
from zddv.release import export_verification_release, verify_verification_release
from zddv.signoff import write_verification_signoff_bundle
from zddv.storage import record_run


def _canonical_json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _resign_manifest(manifest: dict, private_key_path: Path) -> bytes:
    from cryptography.hazmat.primitives import serialization

    unsigned = json.loads(json.dumps(manifest))
    unsigned["signature"].pop("value_base64", None)
    unsigned["signature"].pop("signed_payload_sha256", None)
    signed_payload = _canonical_json_bytes(unsigned)
    private_key = serialization.load_pem_private_key(
        private_key_path.read_bytes(),
        password=None,
    )
    manifest["signature"]["signed_payload_sha256"] = hashlib.sha256(
        signed_payload
    ).hexdigest()
    manifest["signature"]["value_base64"] = base64.b64encode(
        private_key.sign(signed_payload)
    ).decode("ascii")
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_canonical_release_archive(
    destination: Path,
    *,
    manifest_bytes: bytes,
    signoff_bytes: bytes,
) -> None:
    members = {
        "release/manifest.json": manifest_bytes,
        "release/signoff.json": signoff_bytes,
    }
    with zipfile.ZipFile(destination, "w") as target:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.extra = b""
            info.comment = b""
            target.writestr(info, members[name])


def _run_record(run_id: str, status: str = "PASS") -> dict:
    return {
        "run_id": run_id,
        "created_at": "2026-09-22T20:00:00+00:00",
        "project": "demo",
        "simulator": "verilator",
        "simulator_version": "Verilator test",
        "top": "tb_top",
        "test": "smoke",
        "seed": 1,
        "status": status,
        "returncode": 0 if status == "PASS" else 1,
        "duration_ms": 10.0,
        "run_dir": f"/tmp/{run_id}",
        "log": f"/tmp/{run_id}.log",
        "waveform": None,
        "coverage": None,
        "timeout_s": 10.0,
        "command": ["zddv_sim"],
        "plusargs": [],
    }


def _write_keypair(tmp_path: Path) -> tuple[Path, Path]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "release-private.pem"
    public_path = tmp_path / "release-public.pem"
    private_path.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def _ready_signoff(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-pass"))
    signoff = write_verification_signoff_bundle(project)
    return project, signoff


def test_release_export_is_byte_reproducible_and_verifiable(tmp_path: Path):
    project, signoff = _ready_signoff(tmp_path)
    private_key, public_key = _write_keypair(tmp_path)

    first = export_verification_release(
        project,
        expected_signoff_sha256=signoff["provenance"]["signoff_sha256"],
        private_key=private_key,
        key_id="test-release-key",
        output=".zddv/signoff/release-a.zip",
    )
    second = export_verification_release(
        project,
        expected_signoff_sha256=signoff["provenance"]["signoff_sha256"],
        private_key=private_key,
        key_id="test-release-key",
        output=".zddv/signoff/release-b.zip",
    )

    assert first["archive_sha256"] == second["archive_sha256"]
    assert Path(first["path"]).read_bytes() == Path(second["path"]).read_bytes()

    verified = verify_verification_release(first["path"], public_key=public_key)
    assert verified["status"] == "VERIFIED"
    assert verified["signoff_sha256"] == signoff["provenance"]["signoff_sha256"]


def test_release_export_requires_exact_reviewed_signoff_sha(tmp_path: Path):
    project, _ = _ready_signoff(tmp_path)
    private_key, _ = _write_keypair(tmp_path)

    with pytest.raises(ValueError, match="does not match"):
        export_verification_release(
            project,
            expected_signoff_sha256="0" * 64,
            private_key=private_key,
            key_id="test-release-key",
        )


def test_release_export_rejects_blocked_signoff(tmp_path: Path):
    project = initialize_project(tmp_path / "demo")
    record_run(project, _run_record("run-fail", status="FAIL"))
    signoff = write_verification_signoff_bundle(project)
    private_key, _ = _write_keypair(tmp_path)

    with pytest.raises(ValueError, match="READY_FOR_REVIEW"):
        export_verification_release(
            project,
            expected_signoff_sha256=signoff["provenance"]["signoff_sha256"],
            private_key=private_key,
            key_id="test-release-key",
        )


def test_release_verification_detects_tampered_signoff(tmp_path: Path):
    project, signoff = _ready_signoff(tmp_path)
    private_key, public_key = _write_keypair(tmp_path)
    release = export_verification_release(
        project,
        expected_signoff_sha256=signoff["provenance"]["signoff_sha256"],
        private_key=private_key,
        key_id="test-release-key",
    )

    archive_path = Path(release["path"])
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive_path, "r") as source:
        manifest_bytes = source.read("release/manifest.json")
        payload = json.loads(source.read("release/signoff.json"))
    payload["project"] = "tampered"
    tampered_signoff = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    _write_canonical_release_archive(
        tampered,
        manifest_bytes=manifest_bytes,
        signoff_bytes=tampered_signoff,
    )

    with pytest.raises(ValueError, match="inventory|SHA-256|metadata"):
        verify_verification_release(tampered, public_key=public_key)


def test_release_verification_rejects_noncanonical_zip_metadata(tmp_path: Path):
    project, signoff = _ready_signoff(tmp_path)
    private_key, public_key = _write_keypair(tmp_path)
    release = export_verification_release(
        project,
        expected_signoff_sha256=signoff["provenance"]["signoff_sha256"],
        private_key=private_key,
        key_id="test-release-key",
    )

    source_path = Path(release["path"])
    noncanonical = tmp_path / "noncanonical.zip"
    with zipfile.ZipFile(source_path, "r") as source, zipfile.ZipFile(
        noncanonical, "w"
    ) as target:
        for name in source.namelist():
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 23, 9, 25, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            target.writestr(info, source.read(name))

    with pytest.raises(ValueError, match="timestamp is not canonical"):
        verify_verification_release(noncanonical, public_key=public_key)


def test_release_verification_rejects_signed_manifest_identity_mismatch(
    tmp_path: Path,
):
    project, signoff = _ready_signoff(tmp_path)
    private_key, public_key = _write_keypair(tmp_path)
    release = export_verification_release(
        project,
        expected_signoff_sha256=signoff["provenance"]["signoff_sha256"],
        private_key=private_key,
        key_id="test-release-key",
    )

    source_path = Path(release["path"])
    mismatched = tmp_path / "identity-mismatch.zip"
    with zipfile.ZipFile(source_path, "r") as source:
        manifest = json.loads(source.read("release/manifest.json"))
        signoff_bytes = source.read("release/signoff.json")
    manifest["project"] = "different-project"
    manifest_bytes = _resign_manifest(manifest, private_key)
    _write_canonical_release_archive(
        mismatched,
        manifest_bytes=manifest_bytes,
        signoff_bytes=signoff_bytes,
    )

    with pytest.raises(ValueError, match="identity does not match bundled signoff"):
        verify_verification_release(mismatched, public_key=public_key)


def test_release_cli_export_and_verify(tmp_path: Path, capsys):
    project, signoff = _ready_signoff(tmp_path)
    private_key, public_key = _write_keypair(tmp_path)

    rc = main(
        [
            "--project",
            str(project.root),
            "release-export",
            "--signoff",
            ".zddv/signoff/signoff.json",
            "--expected-signoff-sha256",
            signoff["provenance"]["signoff_sha256"],
            "--private-key",
            str(private_key),
            "--key-id",
            "cli-test",
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    assert "RELEASE EXPORTED" in output
    archive = project.root / ".zddv" / "signoff" / "release.zip"
    assert archive.is_file()

    rc = main(
        [
            "--project",
            str(project.root),
            "release-verify",
            ".zddv/signoff/release.zip",
            "--public-key",
            str(public_key),
        ]
    )
    output = capsys.readouterr().out
    assert rc == 0
    assert "RELEASE VERIFIED" in output
