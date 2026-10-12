# Atomic Parametric Definition API

## Contract

Use the existing private `POST /api/parametric`, never `/api/open` followed by
a second rebuild. All operations require `expected_document_sha256` plus the
gateway envelope `expected_revision`, `expected_sha`, `request_id`.

New actions:

- `create_configuration`: `name`, `definition`; rejects existing names.
- `replace_configuration`: `name`, `definition`; rejects missing names.
- `delete_configuration`: `name`; rejects missing names.
- `set_suppressions`: `name`, `suppressions`; replaces one existing v2 row.

For v2, `definition` has exactly `parameters` (parameter-expression overrides)
and `suppressions` (semantic feature keys to strict booleans). Both objects are
required, including explicit empty ones. For v1, it has exactly `parameters`;
there is no implicit v1 upgrade. `set_suppressions` is v2-only.
Omitted suppression keys restore their declared `base_suppressed`, not last state.
Unknown names, fields, targets and non-boolean flags are rejected.
No action changes feature UUIDs, binding definitions, baseline flags or order.

Optional `configuration` selects a surviving named configuration, or `""` for
base, and clears previous explicit overrides. Omission retains selection and
overrides. Deleting the active configuration requires this explicit selection.
Null is rejected, not treated as an explicit replacement. Replacing an active
definition with omission keeps its explicit overrides; the UI must make this
distinction visible. Definitions are replaced completely, not shallow-merged.
Existing actions retain their existing fields and behavior.

GET adds `configuration_definitions` containing the stored parameter rows;
`configuration_suppressions`, binding metadata and canonical document remain.
The future UI can edit exact definitions without guessing from effective values.

## Safety

Core `prepare_definition_rebuild` checks saved parameter/suppression consistency
on the original before mutation. It changes only a deep copy, validates base and
every named configuration (expressions, units, cycles, design checks, dependency
flags, driven sketches and guards), and measures the selected full FreeCAD replay.
Inactive configurations are not each BRep-measured; they remain subject to full
measurement when selected. Unsupported persistent edge/face naming stays refused.

Candidate document/recipe/mesh and STEP remain sealed and same-version.
Definition candidates normalize solver diagnostics before the selected replay;
the adapter then reuses that measured canonical candidate. There is one complete
BRep replay in preparation, not a second rebuild after an open/install.
The gateway's existing industrial candidate, history, receipt replay and final
auth -> project CAS transaction are reused unchanged. A stale candidate, failed
solver/BRep/STEP, lost response or revoked session cannot be repaired by reopening
the document or manufacturing another request ID. Atomic means one project
snapshot/history/receipt commit, not a distributed transaction.

## Acceptance

The two new test files provide standalone fresh external acceptance. They use
real FreeCAD, local SQLite and HTTP, with byte/mode/HEAD/status bindings, original
first cause, logs, measurements, artifact hashes and completion markers.
The caller separately collects the original parent exit; report rc is intended.
No existing full test, browser run or `tools/acceptance.py` job is rerun here.

From this worktree, choose fresh suffixes for every invocation:

```bash
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_parametric_definitions.py --evidence-dir /hf3fs-jd/data/tmp/opencad-config-definition-core-20261012-v2 --runtime-root /data/tmp/opencad-config-definition-core-20261012-v2
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_parametric_definition_api.py --evidence-dir /hf3fs-jd/data/tmp/opencad-config-definition-http-20261012-v4 --runtime-root /data/tmp/opencad-config-definition-http-20261012-v4
```

Both accept `--controls-only`. Offline readback uses the same script and
`--verify-evidence <original producer root> --evidence-dir <fresh 3FS root>`;
it requires the current source/HEAD/status and exact test contract to match.
It starts no HTTP service or kernel. Normal unittest discovery also runs these
cases with unique retained local fixtures; it does not inherit standalone PASS.

Example inner request (add the exact gateway envelope):

```json
{
  "action": "create_configuration",
  "expected_document_sha256": "<current L1 SHA256>",
  "name": "custom",
  "definition": {
    "parameters": {"width": "50 * mm"},
    "suppressions": {"hole_cut": true}
  },
  "configuration": "custom"
}
```

The CLI fixture proves three normal starts/READY/strict proc identity/rc0 exits,
CRUD, body-loss receipt replay across restart, save, undo/redo, exact L1/mesh/STEP
and owner/logout rejection. Body loss is not SIGKILL or a power-loss test.
Real HTTP scheduling controls race two measured candidates against CAS and an
independent SQLite process against the final auth lock. STEP failure is an
explicit export-boundary injection; receipt failure uses a trigger only in this
suite's fresh local database, with no published snapshot/history/receipt.
The reader checks the actual STEP doc-SHA/revision headers and stable state
before/after; this route has no snapshot-SHA header. Mesh SHA is at the actual
outer envelope, whose single measured solid is retained verbatim.

Not accepted: UI CRUD, production, GPU performance, arbitrary UUID rebinding,
baseline suppression editing, industrial parity or all-config BRep validity.
The previous UI slice and its original STEP download-race failure remain intact.
