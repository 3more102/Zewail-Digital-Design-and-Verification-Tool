# Formal Counterexample and Witness Artifact Contract

`collect_formal_artifact_manifest()` turns artifact paths from one
`FormalCheckResult` into explicit, inspectable evidence without interpreting
vendor-specific trace contents.

## Roles

- failed assertion trace -> `COUNTEREXAMPLE`
- covered formal cover trace -> `WITNESS`
- trace attached to any other property outcome -> `TRACE_EVIDENCE`
- non-property artifact returned by the backend -> `SUPPORTING`

A top-level artifact that is already linked as a property trace is not duplicated.

## Path and file evidence

Relative paths are resolved against the formal run directory. Absolute paths remain
absolute. Every manifest row preserves both the adapter-supplied path and the resolved
path.

When file verification is enabled (the default), an existing regular file gets:

- exact byte size
- SHA-256 fingerprint
- a conservative format label based only on its suffix when the suffix is known

Known suffix labels currently include VCD, FST, WLF, VPD, FSDB, JSON, TEXT, and LOG.
The suffix label describes the artifact container only; ZDDV does not claim the trace
has been parsed or semantically validated.

Missing files remain in the manifest with `exists = false`. They are evidence of an
adapter-reported path, not silently discarded.

## Bound evidence

A property artifact records the property depth when supplied. Otherwise it inherits
the request depth as the effective evidence bound. This does not upgrade BMC evidence
into an unbounded proof.

## Native VCD trace normalization

VCD counterexamples and witnesses can be translated explicitly into the normalized
formal trace contract with `zddv formal-vcd-trace`. The importer preserves hierarchical
signal names, declaration widths and VCD metadata, textual four-state logic values,
ordered timestamps, the original file SHA-256, and the property role supplied by the
caller. Optional repeated `--signal` selectors limit ingestion to exact hierarchical
paths or unambiguous short names.

The importer snapshots the known selected-signal state after all value changes at each
VCD timestamp. It does not invent clock cycles, radix, signedness, or property semantics.

When a persisted `FormalCheckResult` contains a backend-reported VCD trace for a failed
assertion or a covered goal, ZDDV now normalizes it automatically into a run-local
`normalized-traces/*.json` file. Relative trace paths are resolved against the formal run
directory. Missing, malformed, evidence-only, or unsupported-format traces remain explicit
normalization-status evidence and do not abort result persistence.

## Current boundary

The artifact-manifest layer itself remains a classifier/fingerprinter. VCD contents can
now be normalized explicitly or automatically during result persistence, while ZDDV does
not yet:

- parse non-VCD vendor-native waveform formats such as FST/WLF/VPD/FSDB;
- reconstruct semantic formal state transitions beyond timestamped signal values;
- cross-probe normalized formal trace signals to RTL;
- persist formal trace samples in SQLite;
- calculate proof or formal-coverage metrics from a waveform.
