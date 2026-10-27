# Product UX contract

Apply the owner's default product UX contract to this project. This copy was
recovered on 2026-10-27 from the EDL Manager work ledger's `ux_contract` field
in its local `common.json`.
The original AGENTS.md containing it has not been located; do not claim otherwise.

- Use concise, familiar labels and a mature network-management product feel.
- Make dense tables readable. Give charts a clear purpose, precise axes and units,
  restrained semantic colors, compact legends and honest missing-data gaps.
- Keep navigation consistent and layouts responsive. Provide accessible contrast,
  keyboard navigation and visible focus.
- Provide intentional loading, empty, error and disabled states.
- Keep JSON, prompts, stack traces and developer variable names out of ordinary
  screens. Use progressive disclosure for useful technical context.
- Preserve functionality, integrations, validation and data contracts. Avoid
  unrelated rewrites.
- Inspect affected rendered screens and fix observed defects. Explicitly disclose
  any visual inspection that could not be performed.
- Never fabricate tests, telemetry or success.

# Administrative access

Keep an original working administrative session open during ACL, credentials or
management-access firewall changes. Before closing it, establish a fresh session
from each affected authorized source with intended credentials and verify both
authentication and required privileges. Verify the target sees the intended
source, and separately verify the repair. Retain the original session and correct
or safely roll back a failed access change. Report untested paths.
