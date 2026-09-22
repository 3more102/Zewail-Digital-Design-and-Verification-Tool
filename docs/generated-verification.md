# Review-Gated Generated Verification Artifacts

ZDDV keeps generated SystemVerilog assertions/tests reviewable and opt-in by separating
**staging** from **application**. This layer governs proposed code; it does not infer the
correct assertion/test semantics and it does not compile or execute generated artifacts.

## Proposal format

A proposal is a JSON object stored inside the project root:

```json
{
  "kind": "assertion",
  "language": "systemverilog",
  "name": "p_req_ack_review_example",
  "target_path": "reviewed_generated/p_req_ack_review_example.sv",
  "source": "reviewed-generator",
  "evidence": {
    "reason": "explicit review context"
  },
  "content": "module ...\nendmodule\n"
}
```

Supported `kind` values are `assertion` and `test`. The language is currently
`systemverilog`, and target files must use `.sv` or `.svh`.

## Stage for review

```text
zddv --project <project> generated-stage proposal.json
```

Staging writes only under `.zddv/generated/drafts/<draft-id>/`. The manifest records:

- the proposal SHA-256;
- the exact generated-content SHA-256;
- the isolated draft path;
- the optional suggested destination;
- source/evidence metadata;
- `review_required=true`;
- `approved=false`;
- `auto_apply=false`;
- `execution_enabled=false`.

The project source tree is not modified.

## Explicit reviewed apply

After inspecting the exact staged file, copy its reported SHA-256 into the apply command:

```text
zddv --project <project> generated-apply \
  .zddv/generated/drafts/<draft-id>/manifest.json \
  --expected-sha256 <reviewed-sha256> \
  --approve-reviewed
```

The proposal's `target_path` is used unless `--destination` is supplied. Apply is
refused when:

- `--approve-reviewed` is absent;
- the reviewed SHA-256 differs from the staged bytes;
- the draft changed after staging;
- the target escapes the project root;
- the target is under `.zddv`;
- the target is not `.sv`/`.svh`;
- the target already exists.

Successful apply copies the exact reviewed bytes and records provenance under
`.zddv/generated/applied/`. It still does not compile or execute the artifact.

## Evidence boundary

This workflow is a governance/safety boundary for generated verification code, not a
semantic code generator. The proposal content may come from a human, another tool, or a
future generator, but ZDDV preserves it exactly and requires explicit review plus digest
confirmation before it can enter project sources. Existing files are never silently
overwritten.
