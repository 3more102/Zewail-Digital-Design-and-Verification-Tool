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

## Current boundary

This layer fingerprints and classifies artifacts only. It does not yet:

- parse counterexample or witness waveforms;
- reconstruct formal state transitions;
- cross-probe trace signals to RTL;
- persist formal artifacts in SQLite;
- calculate proof or formal-coverage metrics from a waveform.
