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

## Response Evidence Closure

The response-closure repair owns only
`oss-alternatives/packages/cadcore/tests/test_parametric_definitions.py`,
`oss-alternatives/apps/opencad/tests/test_parametric_definition_api.py` and this
document. The HTTP test's actual filename is not
`test_parametric_definitions_http.py`. The source gate rejects product changes
relative to `bbdd7280d3d995f7caa97e0161bb0dfbb46091f3`; no API implementation is
changed or inherited from another integration tree.

Every exchange stores `responses/<index>.body` and a matching JSON record.
The body is the received wire bytes, including login JSON which contains no
session token. Cookie/Set-Cookie, authorization and login password are never
stored. The safe login request records the username and an explicit
`password_omitted=true`, not a password or its hash. Necessary response headers
are an explicit allowlist: content type/length, no-store, nosniff, document SHA
and revision. All generated credentials are checked against evidence bytes
before writing and again at finalization.

The reader requires the exact response schema, continuous exchange identities,
source-discovered test identity, exact per-exchange record equality, raw body
length/hash and a bidirectional response-file set. It also checks status/body
agreement, canonical document and candidate hashes, request/revision identity,
login-derived actor and project-owner origin, and exact replay identity.
Measurement witnesses bind their before/definition/mesh/STEP/after exchanges:
retained STEP bytes and headers match the measured same-version document and
the before/after state. Every successful STEP exchange must have one witness.

An intentionally lost response has an empty retained body, an explicit loss
flag and the observed nonempty Content-Length; it is not a claimed full download.
Its later successful replay must match request, actor, project, route and test.
Offline verification checks the recorded FreeCAD measurement, not a new kernel
measurement. Evidence is integrity/semantic binding, not protection against a
party replacing all evidence and its source together.

The original `bbdd` 21/16-test evidence and 134 HTTP execution records remain
unchanged. They lack this new response schema and cannot be upgraded to complete
response closure by rehashing. The repair-before failure is retained at
`/hf3fs-jd/data/tmp/opencad-definition-response-closure-before-20261012-v1`:
the old reader failed to reject missing response fields. New evidence must come
from this repaired source, with separate original parent exit collection.

For retained real-response mutants, run the HTTP script with
`--response-controls <new real HTTP producer root> --evidence-dir <fresh 3FS root>`.
This offline entry starts no HTTP server or kernel. It retains twelve mutated
copies, recomputes their artifact/ledger/terminal hashes as applicable, and calls
the actual offline reader once per case. Each reader must return original rc1
and the named first cause for missing response fields, false hash, changed body,
rehashed wrong project/status/actor/test and incomplete exchange set. The parent
returns rc0 only when every intended rejection and source stability is confirmed.
The additional controls remove the terminal response and its files while
rehashing all remaining records, swap two known test identities, select another
logged-in actor in the same fixture, and select another recorded project's ID
while rehashing the body. These are evidence controls, not industrial workflows.

## Required Flow Closure

The first `fce0884` repair remains HOLD for complete response closure: its
continuous indices/file-set checks allowed the response list and files to shrink
together, and membership in a known test set did not establish actual ownership.
The retained before-red evidence is
`/hf3fs-jd/data/tmp/opencad-definition-response-flow-before-20261012-v1/result.json`.
At 2026-10-12 09:31:54 +08:00 the unchanged fce offline CLI returned original
rc0/EVIDENCE_VERIFIED for both the deleted terminal 409/body/record and an
ordinary response assigned to another known test. The counterexample parent
returned original rc1/`response_flow_mutants_accepted`. Neither original producer
nor prior 24/19-test and eight-control evidence is modified or upgraded.

Schema 2 adds a per-test `flow_index` and the actual client's `user` slot.
The producer's literal `HTTP_FLOW` is the required workflow source. The reader
parses it statically, requires its test keys to equal all declared HTTP/CLI test
methods, derives schema-refusal cases and comparison cases from the test source,
and refuses unknown recipes. It never uses a historical HTTP count or derives
the expected set by filtering observed rows. The actual client claims a required
slot before each request, checking method/route/action/user/project presence and
body-loss flag. The producer saves the expanded plan and source hash; the reader
recomputes that same plan and requires exact equality.

Required `(test, flow_index)` identities and actual responses are equal in both
directions, with no omissions, extras or duplicates. One fixture belongs to one
test; login replies bind each account slot across restarts, and actual actor,
project creation and required action/status are checked for every exchange,
including ordinary errors without geometry witnesses. Every source-required
geometry witness binds its exact five slots. Sequential slots preserve order;
the two measured CAS requests may complete in either order but require distinct
request IDs, both named candidates, one success and one conflict, and placement
before replay. This is a bounded workflow contract, not generic source execution
analysis or protection against replacement of both source and all evidence.

For the retained before-red reproduction only, use
`--flow-controls-before <original fce real HTTP root> --evidence-dir <fresh 3FS>`.
It invokes the original byte-checked fce reader on newly copied artifacts and
always preserves a FAIL diagnostic. It starts no HTTP server or kernel, does not
clean evidence, and is not an admission/retry path. New positive evidence requires
the repaired source; a schema 1 record is not silently filled or rebound.

Not accepted: UI CRUD, production, GPU performance, arbitrary UUID rebinding,
baseline suppression editing, industrial parity or all-config BRep validity.
The previous UI slice and its original STEP download-race failure remain intact.

## Current Integration Source Binding

The c6 repair's unconditional `PRODUCT_BASE`/three-path owner check rejected
legitimate clean integration successors with `product_source_changed`.
The original CLI failure is retained at
`/hf3fs-jd/data/tmp/opencad-definition-source-binding-before-20261012-v1`.
It used an unchanged c6 CLI in a new local Git repository, followed by one
committed documentation-only edit. It is not a failed product workflow or a
replacement of the earlier c6 response-closure evidence.

Default `--source-scope integration` accepts no uncommitted source: every current
tracked regular file must equal the actual HEAD blob and Git executable mode,
the index must equal HEAD in both directions, and the complete physical file set
must equal that tree, including ignored generated files. HEAD is captured before
and after, the entire bytes/mode manifest is saved and rechecked, and optional
`--expect-head` pins a full commit. No historical product/HTTP count or expanded
allowlist decides integration acceptance. A new product HEAD needs its own
measurement/HTTP/all evidence; this source check does not inherit prior PASS.

Explicit `--source-scope repair-owner` keeps the original bbdd-relative three-path
development boundary as an additional check. It is not the normal integration
mode, and still requires the current source to be committed and clean.
`--source-preflight` is source/runtime-only: no kernel, HTTP, GPU or product test.
`--source-binding-controls` tests actual child CLI/Git gates in a newly created
isolated fixture. Its runner may have only the three owned development edits;
that diagnostic snapshot is explicitly not accepted product source. All child
positives use committed clean snapshots; original child exits and output hashes
are external and retained. No existing source or evidence is changed or cleaned.
`core.hooksPath=/dev/null` is a one-command option only for commits inside the
new owned fixture; the helper rejects any other root. It does not edit any Git
configuration or disable hooks in the actual development/publication repository.
The source controls also run the actual guarded acceptance entry with normal
discovery of the one core definition test file. Its original discovery/test
counts and CPU geometry are separate from source-only controls and full all;
this command starts no HTTP/GPU service.

Normal unittest discovery cannot silently create its own default runtime. It
requires the real same-source `tools/acceptance.py --suite/--top` process, exact
process arguments, an already-active acceptance SQLite guard, matching persistent
local runtime/TMPDIR/tempfile, and an existing external evidence parent. It binds
the parent entry bytes and full child source before fixture creation, rechecks
them around tests, and saves `discovery-source.json` below the parent evidence
root. Direct unittest discovery without that parent fails loudly. It remains a
local process/source boundary, not a security attestation against code replacing
both source and evidence.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-definition-source-binding-20261012/oss-alternatives/packages/cadcore/tests/test_parametric_definitions.py --source-binding-controls --evidence-dir /hf3fs-jd/data/tmp/opencad-definition-source-binding-controls-20261012-next
sudo -n -u infra python3 -B /data/dev/wt-opencad-definition-source-binding-20261012/oss-alternatives/packages/cadcore/tests/test_parametric_definitions.py --source-preflight --expect-head FULL_COMMIT --evidence-dir /hf3fs-jd/data/tmp/opencad-definition-source-binding-preflight-20261012-next
```

### Root Git Metadata Boundary

The original b263 nested-Git CLI counterexample remains immutable at
`/hf3fs-jd/data/tmp/opencad-definition-source-binding-anscombe-review-20261012-v1/independent-nested-git-negative/`.
At 10:44:49 Asia/Shanghai the source-only CLI returned rc 0/SOURCE_VERIFIED
with 404 files despite extra ignored `__pycache__/nested-source-negative/.git`.
This is a source-ruler failure, not accepted integrated product evidence.

Only the source root's `.git` directory (ordinary repository) or pointer file
(worktree) is excluded. A nested `.git` directory, file or symlink fails with
`nested_git_metadata` before exclusion, even when Git status ignores it.
`--nested-git-before` runs the immutable b263 child CLI on fresh isolated
fixtures and returns FAIL/rc 1 when its three false-success cases reproduce.
`--nested-git-controls` uses committed repair snapshots, records the two root
positives and three expected child rc 1 refusals, and never deletes fixtures.
It does not modify b263, production, parent acceptance or Git hooks.

The original 20 source controls remain. Adding `--source-http-discovery` to
`--source-binding-controls` requests one further normal HTTP-file discovery
under the actual guarded, same-source parent, after normal core-file discovery.
This uses new local generated accounts, SQLite files and loopback HTTP only;
it is not full all or inherited acceptance of a different source. The actual
HTTP request count is not collected by this discovery entry and is null, not
reported as zero.
