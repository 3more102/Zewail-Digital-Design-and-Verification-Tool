# Verification Signoff Bundle

The `zddv signoff` command creates a deterministic review artifact from persisted
ZDDV evidence. It is designed as a conservative CI/review boundary rather than an
automatic claim that verification is complete.

## Evidence included

The first signoff milestone records:

- the selected recent simulation runs;
- the latest normalized coverage snapshot, when available;
- the latest normalized formal snapshot, when available;
- the latest normalized UVM log snapshot, when available.

The bundle stores canonical SHA-256 fingerprints for the evidence, policy, and the
complete signoff review payload.

## Review state

`READY_FOR_REVIEW` means the selected evidence contains no blocking condition under
the requested policy. It does **not** mean that missing optional evidence passed, and
it does not claim specification-complete verification.

`BLOCKED` is emitted when selected simulation evidence is missing or non-passing,
when a required evidence domain is absent, when a coverage threshold is missed, or
when the latest available formal/UVM evidence is non-passing.

## CLI

```bash
zddv --project my_project signoff

zddv --project my_project signoff \
  --run-limit 100 \
  --require-coverage \
  --min-coverage 90 \
  --require-formal \
  --require-uvm \
  --output .zddv/signoff/signoff.json
```

Coverage percentage comes from the newest persisted normalized coverage artifact:
native percentage score snapshots are supported as well as hit/total snapshots.

## Exact evidence pinning

For release-candidate review, signoff can pin exact persisted evidence instead of
implicitly selecting the newest records:

```bash
zddv --project my_project signoff \
  --run-id run-001 \
  --run-id run-002 \
  --coverage-snapshot-id cov-release \
  --formal-snapshot-id formal-release \
  --uvm-snapshot-id uvm-release
```

Pinned IDs are part of the signoff policy SHA-256. A requested run or snapshot that
does not exist blocks the review; ZDDV does not silently fall back to newer or older
evidence. When no pin is supplied, the original recent/latest selection behavior is
preserved.

The command returns exit code 0 only for `READY_FOR_REVIEW`; a blocked review returns
exit code 1, making the artifact usable as an explicit CI gate without hiding the
underlying evidence.


## Signed reproducible release export

A `READY_FOR_REVIEW` signoff bundle can be promoted into a deterministic release
archive only after the caller confirms the exact reviewed `signoff_sha256`.

```bash
zddv --project my_project release-export \
  --signoff .zddv/signoff/signoff.json \
  --expected-signoff-sha256 <reviewed-signoff-sha256> \
  --private-key /secure/path/release-private.pem \
  --key-id lab-release-2026 \
  --output .zddv/signoff/release.zip
```

The archive contains exactly two files: the normalized signoff JSON and a release
manifest. Members are stored in lexicographic order with fixed ZIP metadata and no
compression, so identical signoff bytes, key, and key ID produce byte-identical ZIP
archives.

The manifest is signed with Ed25519. The private key is never copied into the
archive. Verification requires a separately trusted public key:

```bash
zddv --project my_project release-verify \
  .zddv/signoff/release.zip \
  --public-key /trusted/path/release-public.pem
```

Verification checks the Ed25519 signature, public-key fingerprint, manifest payload
SHA-256, bundled-file SHA-256, signoff provenance hashes, and the
`READY_FOR_REVIEW` state. A valid signature authenticates the release manifest; it
does not independently prove that verification is specification-complete.

Install the optional signing support with `pip install 'zddv[signing]'` when the
base package was installed without development extras.


## Deterministic signoff comparison

ZDDV v1.1 adds a structural comparison step for two already-produced signoff bundles:

```bash
zddv --project my_project signoff-diff \
  .zddv/signoff/baseline.json \
  .zddv/signoff/current.json \
  --output .zddv/signoff/diff.json
```

Before comparing anything, ZDDV recomputes and verifies each bundle's evidence,
policy, and signoff SHA-256 provenance. A modified or malformed input fails closed.

The diff reports exact policy keys that changed; added, removed, or modified run IDs;
coverage/formal/UVM evidence changes; check-state changes; review-state transition; and
blocking checks added or removed. The report has its own deterministic `diff_sha256`.

The diff is descriptive only. It does not infer that a change is better or worse and
does not expand the meaning of `READY_FOR_REVIEW`.
