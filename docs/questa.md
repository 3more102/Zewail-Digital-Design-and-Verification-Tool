# Siemens Questa Adapter

ZDDV v0.6 includes an initial Siemens Questa/QuestaSim execution adapter.

## Execution contract

The adapter uses the established classic command-line flow:

1. `vlib work` creates the simulation library.
2. `vlog -sv -work work ...` compiles the SystemVerilog sources.
3. `vsim -c -lib <work-path> <top> ... -do "run -all; quit -f"` runs in batch mode.

ZDDV maps a named test to both `+ZDDV_TEST=<name>` and, unless the user supplied
one explicitly, `+UVM_TESTNAME=<name>`. A ZDDV seed is passed through Questa's
`-sv_seed` option. User plusargs remain available through the normal ZDDV run
and regression interfaces.

The implementation follows Siemens' documented Questa command-line model. Siemens
describes the traditional Questa flow as `vlog` / `vopt` / `vsim` and documents
batch `vsim` usage with `-do "run -all; quit"`. Siemens also documents that
Questa releases include precompiled UVM support.

References:

- https://blogs.sw.siemens.com/verificationhorizons/2021/07/07/qrun-ing-optimized-build-flows-in-questasim/
- https://blogs.sw.siemens.com/verificationhorizons/2011/03/08/using-the-uvm-10-release-with-questa/

## Setup

Ensure the Siemens Questa environment is initialized so `vlib`, `vlog`, and
`vsim` are available on `PATH`, then select the backend:

```bash
zddv doctor --simulator questa
zddv --project my_project config simulator questa
zddv --project my_project build
zddv --project my_project run --test smoke --seed 17
```

A run is recorded in the same ZDDV SQLite run database used by Verilator. The
simulation transcript is saved as `simulation.log`, and the adapter requests a
per-run `waveform.wlf` artifact when waveform capture is enabled.

For UVM logs, the normalized v0.6 ingestion layer can correlate the report with
the originating simulation run:

```bash
zddv --project my_project uvm-analyze --run <run-id>
zddv --project my_project uvm-history --run <run-id>
```

## Current boundaries

This first adapter does not claim normalized Questa UCDB coverage ingestion or
native WLF signal parsing in ZDDV Debug Studio. Existing Verilator-specific
`lint` and normalized code-coverage commands remain Verilator-only. The Questa
adapter establishes compile/run/regression/UVM-log execution plumbing without
claiming those later integrations.
