# SymbiYosys bounded formal backend

ZDDV v0.7 includes an executable formal backend for bounded safety checks through
SymbiYosys (SBY). This first execution slice is intentionally limited to BMC and
the smtbmc engine.

## Contract

The backend accepts the existing FormalCheckRequest model with mode=bmc. A
requested depth is written to the SBY depth option. When no depth is supplied,
ZDDV makes the SBY default explicit and records an effective depth of 20.

A request timeout is rounded up to whole seconds for the SBY timeout option and
is also protected by a small outer process-timeout guard.

The generated check.sby contains:

- an [options] section with mode bmc and the effective depth;
- an [engines] section using smtbmc;
- a [script] section that reads .v and .sv compilation units with read -formal
  and prepares the configured project top;
- a [files] section that snapshots all matched project sources into the SBY
  work tree, including headers such as .vh and .svh.

Project-relative source paths are retained as SBY destination paths. Sources
outside the project root receive deterministic _external paths.

## Evidence and status boundary

Each check runs in a unique .zddv/formal/runs/<run-id> directory and retains the
generated SBY file, combined tool log, formal.json execution manifest, and any
VCD/FST/Yosys witness artifacts found beneath the SBY work directory.

ZDDV classifies a completed invocation only from an explicit terminal SBY
DONE(...) status marker. A zero process exit code without that marker is ERROR,
rather than an inferred PASS. Native TIMEOUT and the outer process timeout are
normalized to UNKNOWN.

This slice does not parse individual property outcomes, apply property filters,
claim unbounded proof, execute cover mode, persist formal history in SQLite, or
compute formal coverage.

## Upstream behavior used by this adapter

The SBY reference defines bmc as bounded checking for safety assertions, defines
depth for bmc/cover with a default of 20, documents timeout, and lists smtbmc as
a supported BMC engine. The SBY file-format reference documents [script] and
[files], including destination/source file mappings. The formal-Verilog
reference documents read -formal and its FORMAL macro behavior.

References:

- https://yosyshq.readthedocs.io/projects/sby/en/latest/reference.html
- https://yosyshq.readthedocs.io/projects/sby/en/latest/verilog.html
- https://yosyshq.readthedocs.io/projects/sby/en/latest/usage.html
