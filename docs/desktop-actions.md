# Debug Studio Review-Gated Project Actions

The Debug Studio **Actions** pane provides an explicit review gate for three existing
ZDDV core operations:

- `lint` through `zddv.lint.lint_project`;
- `build` through the configured simulator backend `build()`;
- one `run` through the configured simulator backend `run()`.

Preparing an action does not invoke the simulator and does not write action artifacts.
It builds a deterministic review payload containing the selected action, runtime
parameters, project identity, the project-config SHA-256, and SHA-256 hashes for every
matched RTL/testbench source.

The pane displays a canonical `review_sha256`. Execution is blocked unless the user:

1. enters that exact SHA-256 in the confirmation field; and
2. explicitly checks the reviewed-action approval control.

Immediately before execution, ZDDV rebuilds the review payload from the live project.
If the configuration, matched source set, source bytes, or reviewed runtime parameters
changed, the SHA no longer matches and execution is refused. The action must then be
prepared and reviewed again.

The review gate does not claim that an approved action is safe or correct. It guarantees
that the action executed by the GUI is the same project/action state represented by the
reviewed SHA-256.

The Actions pane does not invoke AI and does not bypass the existing ZDDV backend/core
implementations. Any build/run/lint artifacts are created only after the review gate
passes.
