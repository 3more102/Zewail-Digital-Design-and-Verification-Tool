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

## Automatic persistence-time normalization

When a persisted formal result reports a trace for a failed assertion or a covered
cover property, ZDDV resolves a relative trace path against that formal run directory.
Existing VCD counterexamples/witnesses are automatically normalized into a
snapshot-specific JSON trace beside the persisted formal report. The formal property
record retains the raw path, resolved path, normalization status, normalized output
path, SHA-256, schema, and compact trace summary.

Missing VCD files, malformed VCD content, unsupported waveform suffixes, and generic
trace evidence do not make formal-result persistence fail. They remain explicit raw
evidence with a conservative normalization status instead of receiving invented
counterexample/witness semantics.

## Formal trace to RTL cross-probing

A normalized counterexample or witness can be cross-probed with the same source-level
hierarchy and connectivity engine used for simulation waveforms:

```text
zddv --project <project> formal-trace-crossprobe <normalized-trace.json> <signal>
```

The command accepts an exact formal signal path or a unique short signal name. It maps
the trace scope to the indexed design hierarchy, resolves the design unit, locates the
source declaration when possible, and reports source-structural drivers and loads. An
ambiguous short name is rejected instead of being guessed. The report retains the
formal property, property kind, trace kind, trace source, input SHA-256, and trace
summary alongside the source evidence.

## Current boundary

The artifact-manifest layer itself remains a classifier/fingerprinter. ZDDV does not yet:

- parse non-VCD vendor-native waveform formats such as FST/WLF/VPD/FSDB;
- reconstruct semantic formal state transitions beyond timestamped signal values;
- persist formal trace samples in SQLite;
- calculate proof or formal-coverage metrics from a waveform.
