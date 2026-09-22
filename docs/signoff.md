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
