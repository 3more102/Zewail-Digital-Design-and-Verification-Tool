# Debug Studio Generated Review

The **Generated Review** pane exposes the existing staged generated-verification
workflow without creating a second apply path.

Only manifests under `.zddv/generated/drafts/<draft-id>/manifest.json` are listed.
A draft is reviewable only when:

- it is a generated-verification `DRAFT`;
- review is required and automatic apply is disabled;
- `execution_enabled` is false;
- its content remains inside the matching draft directory;
- the current content SHA-256 exactly matches the manifest; and
- no applied record already exists for that draft.

Selection and preview are read-only. Applying requires the user to manually enter the
exact staged-content SHA-256 and explicitly approve the reviewed bytes. The desktop then
delegates to `zddv.generated_artifacts.apply_generated_artifact`.

The core apply gate still rejects tampered content, malformed draft identity,
project-root escape, destinations under `.zddv`, non-SystemVerilog destinations, and
overwrites of existing project files. Successful apply copies the exact reviewed bytes,
records provenance under `.zddv/generated/applied/`, and keeps
`execution_enabled=false`.

The pane does not invoke AI, compile, build, or simulate generated code.
