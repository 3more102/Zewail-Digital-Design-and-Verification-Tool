# Xcelium backend foundation

ZDDV supports a Cadence Xcelium execution foundation through `xrun`.

## Build contract

`zddv build` uses the documented Xcelium compile/elaborate flow:

```text
xrun -64bit -sv -uvm -top <top> \
  -xmlibdirname <build>/xcelium.d \
  [-access +rwc] \
  -elaborate <sources...>
```

The build artifact is the Xcelium compiled simulation database at
`.zddv/build/xcelium.d`. Waveform-enabled projects request read/write/connect
access so the run step can create probes.

## Run contract

`zddv run` reuses the compiled database instead of recompiling:

```text
xrun -64bit -R -xmlibdirname <build>/xcelium.d
```

ZDDV maps its normal runtime controls onto Xcelium as follows:

- deterministic SystemVerilog seed: `-svseed <seed>`
- logical ZDDV test label: `+ZDDV_TEST=<test>`
- UVM test selection: `+UVM_TESTNAME=<test>` unless the caller already supplied one
- arbitrary runtime plusargs: passed through unchanged
- timeout: enforced by the ZDDV process runner and recorded as `TIMEOUT`

If UVM report text is present in the run log, the existing simulator-independent
UVM log normalizer is invoked and correlated with the recorded run.

## VCD waveform capture

For waveform-enabled projects ZDDV writes `zddv_xcelium.tcl` in the isolated
run directory and executes it with `-input`. The script opens a VCD database,
probes the top-level design recursively, and runs the simulation:

```tcl
database -open zddv_vcd -vcd -into waveform.vcd
probe -create -database zddv_vcd [scope -tops] -depth all -all
run
```

This intentionally produces VCD so the existing ZDDV waveform index,
waveform probe, and protocol-from-waveform flows can consume the artifact
without a simulator-specific waveform parser.

## Coverage boundary

Native Xcelium/IMC coverage capture and merge are not implemented in this
foundation. When project coverage is requested, build/run manifests record
`coverage_requested=true` and `coverage_capture=pending-native-support`;
ZDDV does not fabricate a coverage artifact or numeric snapshot.

## Evidence basis

The adapter is limited to public Cadence-documented/community-confirmed xrun
contracts used here: `-elaborate`, `-R`, `-xmlibdirname`, `-svseed`,
`+UVM_TESTNAME`, `-input`, and Tcl `database`/`probe` VCD capture.
Native coverage remains a separate milestone until its exact Xcelium/IMC
artifact and report contracts are implemented and tested.
