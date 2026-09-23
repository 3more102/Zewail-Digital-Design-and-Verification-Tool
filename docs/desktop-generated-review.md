# Desktop Generated Review

The `Generated Review` pane exposes the existing generated-verification draft review
gate in the desktop without creating a second apply mechanism.

A draft is reviewable only when its manifest identifies a generated verification
`DRAFT`, review is required, automatic apply is disabled, its content remains in the
same draft directory, and the current content SHA-256 matches the staged manifest.

Applying delegates to `apply_generated_artifact`. The user must provide explicit
review approval and the exact staged-content SHA-256. Existing core safeguards still
reject tampered bytes, writes outside the project root, writes back into `.zddv`,
unsupported target suffixes, and overwrites of existing source files.

The pane never invokes AI, compiles the draft, or runs simulation. Applied generated
verification code remains execution-disabled until handled by a separate reviewed
workflow.
