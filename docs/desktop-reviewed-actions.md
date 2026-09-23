# Desktop Reviewed Generated-Artifact Action

The desktop action helper deliberately reuses the existing generated-artifact review
gate rather than inventing a second apply path.

A staged draft is reviewable only when its manifest is a DRAFT under
`.zddv/generated/drafts/<draft-id>/`, review is required, automatic apply is
disabled, the content remains in the same draft directory, and the current content
SHA-256 still matches the manifest.

Applying a draft delegates to `apply_generated_artifact`. The caller must provide:

- explicit reviewed-content approval;
- the exact 64-character SHA-256 of the staged content; and
- either a destination or a staged suggested target.

The core apply path still refuses project-root escape, writes back into `.zddv`,
overwrites of existing project files, tampered draft bytes, and malformed draft
identity. Applied generated verification code remains execution-disabled; this
action does not compile, simulate, or invoke AI.
