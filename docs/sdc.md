# SDC parser and constraint-lint foundation

ZDDV includes a simulator-independent parser for a practical timing-constraint
subset. The output is a normalized command/query model plus static lint evidence.
It is not a static-timing-analysis engine and it does not claim timing signoff.

## Initial normalized command surface

The parser recognizes:

- create_clock
- create_generated_clock
- set_clock_uncertainty
- set_clock_latency
- set_input_delay
- set_output_delay
- set_false_path
- set_multicycle_path
- set_max_delay
- set_min_delay
- set_clock_groups
- set_input_transition
- set_load
- set_driving_cell

Unknown commands are retained in the parsed document and reported as informational
lint evidence instead of being silently discarded.

The tokenizer keeps Tcl-style bracket commands and brace groups together, accepts
backslash line continuation and top-level semicolon command separators, and
preserves repeated options such as multiple -group or -through arguments.

## Current lint checks

The first lint layer checks evidence that can be established without a linked
netlist or timing library:

- missing, zero, negative, or non-numeric create_clock periods;
- duplicate clock names when a stable name can be identified;
- generated clocks without -source;
- conflicting or invalid divide/multiply scaling;
- invalid multicycle counts;
- non-numeric set_max_delay/set_min_delay values;
- optional get_ports resolution against a caller-supplied port set;
- files with no clock creation commands;
- unsupported commands that are preserved but not normalized.

analyze_sdc_file writes a JSON artifact at .zddv/constraints/sdc.json by default.
Its scope is explicitly static-sdc-parse-and-lint. Status values are LINT_CLEAN,
LINT_WARNINGS, or LINT_ERRORS; none mean timing closure or signoff.

## Evidence basis

OpenSTA/OpenROAD examples use SDC/Tcl forms including create_clock,
set_input_delay, set_output_delay, clock groups, timing exceptions, bracket
queries, brace groups, and line continuations. This parser follows those visible
forms while keeping unsupported semantics explicit.

References:

- https://openroad.readthedocs.io/en/latest/main/src/sta/doc/Examples.html
- https://github.com/The-OpenROAD-Project/OpenSTA
