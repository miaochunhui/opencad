# Assembly Private Entry Review

## Boundary

Owned additions are `tools/assembly_private_review.py` and this document only.
The selected source root, private gateway, secure transport, project store,
assembly adapter/worker and browser resources are read-only dependencies.
No source overlay, injected server route, production request, real account,
shared index write, push or nested delegation is used.

The review creates generated users and disposable projects in an external
local SQLite root. Test credentials stay in memory and never enter HTTP/CDP
logs. FreeCAD jobs, screenshots, fingerprints and negative-control evidence are
external 3FS artifacts. The existing software-browser CDP helper is reused.

## Reproduction

From the assembly worktree:

```bash
sudo -n -u infra python3 -B tools/assembly_private_review.py --source-root /data/dev/opencad
```

Default evidence is a new timestamp under
`/hf3fs-jd/data/tmp/opencad-assembly-private-review-20261011/`.
The process returns nonzero for missing integration, failed negative controls,
source drift, stage errors or missing real dependencies; no skips are introduced.
`--verify-result PATH --source-root /data/dev/opencad` rereads source and artifact
hashes without starting a browser, kernel or server. A verified historical FAIL
remains FAIL, not a successful product acceptance.
`--selftest` exercises ten validator positive/negative controls without a CAD
worker, browser or HTTP service. `--inspect-result PATH` prints only existing
undo/redo JSON differences; it does not reapprove an old result.

## Acceptance

Independent components are rebuilt from distinct immutable L1/SHA references;
real mesh, configured BOM, hole gap vs hole-wall volume, candidate isolation,
reopen byte equality, numeric plane/axis mate residuals/DoF and actual surface
reference/overconstraint rejections are checked. Private persistence uses the actual
`secure_web` and `ProjectGateway` over isolated loopback HTTP, testing save/open,
full snapshot CAS, repeat receipts, stale writes, undo/redo, project standard vs
independent part configuration and cross-user access.

`/api/open` must reject an assembly-only L1 with a deliberately corrupted part
SHA before acknowledging a new revision. Validating only top-level solid
features is insufficient: the assembly extension also contains executable part
documents and constraints. Candidate/part/configuration bindings must be
validated on every import/history restore path before durable confirmation.

The actual `/api/assembly` GET/POST routes must operate on an authorized private
workspace, with writes using request receipts and atomic CAS. Existing routes
are tested for mesh/BOM/collision, replay, stale versions, bad mate rejection,
undo and export/reopen. Missing routes are dependency failures, not replaced by
a test gateway subclass.

The actual private page must load and mount the panel when its lazy `details`
is opened through a real browser click. A browser
diagnostic mount may load the unchanged panel assets and exercise the actual
private fetch wrapper, but is explicitly recorded separately and never signs
automatic product integration PASS. Missing route errors must be visible, with
no accepted state or fabricated geometry. The real private fetch wrapper is
also tested against a second-client stale write and must block subsequent edits
pending deliberate conflict resolution. Desktop/narrow screenshots and page
overflow observations are retained.

## Source-Bound Result

Latest actual evidence:
`/hf3fs-jd/data/tmp/opencad-assembly-private-review-20261011/20261011T032136413927Z/result.json`.
Finished at `2026-10-11T03:21:50.130955+00:00`: **FAIL**, 52 checks,
50 passed, two failed, zero stage errors, zero source drift, no skips.
Result SHA256:
`8ede58b4a7db737ebd25f2cbd1875b26918e993e6656a9a54a4055a513c3ba6e`.
Owned tool SHA256:
`bd205cf4bdcd5cbd162b64f2edf7a67be32fd635eecad8433fd8ddac6f4400ac`.
Ten validator selftests passed at
`20261011T032136170465Z/result.json` under the same evidence parent.
The result verifier reread all source/artifact hashes and returned
`evidence_valid=True`, retaining FAIL with exit code 1.

The read-only chair source root is `/data/dev/opencad`. HEAD `5c86c01` alone
does not bind the newly untracked integration files. Exact tested SHA256 values:

| File under `oss-alternatives/apps/opencad/` | SHA256 |
| --- | --- |
| `project_gateway.py` | `3665d3d9b0b0b35d109919f02867bada70520ba42169981f2f774950aca6e9a8` |
| `secure_web.py` | `36a06e8d5ab21dcde96ec493d53653181b698b9e101ed0f5172c9d99a9eb78c9` |
| `web/js/industrial-panels.js` | `2c2fd1dac64625406f2a53d04691f5905df319f217b3f73f4bd0a2b2f4ad7a93` |
| `web/js/project-session.js` | `871d93a42f16fe7636b8854d9b322f8dea8510f53e291e2e20ee6ce23a58d7ce` |
| `server.py` | `459119589183e0fad538fe98f3691a19fb994dd8da00e18e675ca6041a14ebbf` |

These are test coordinates, not a claim that the chair has frozen the product.
The complete dependency inventory is in `source_before`/`source_after`.

### P1: Invalid Part SHA Accepted by Generic Import

`project_gateway.py:436` imports L1, but `:453` rebuilds only when top-level
`candidate.features` is nonempty. `_validate_domains` at `:157` rejects mixed
domains; it does not validate immutable part SHA or executable assembly content.
The history loop at `:465` likewise checks only generic L1 blocking problems.

The exact negative document changes only one part's SHA to 64 zero characters.
The assembly adapter and `/api/assembly` reopen reject `part_digest_mismatch`,
without revision/history changes. In contrast, real cookie-authenticated
`POST /api/open` returns 200 and advances revision 1 to 2. This is an
acknowledged invalid assembly, not a presumed validation risk.

Minimum owner repair: dispatch semantic extension validation on generic import,
current snapshot and all history candidates before commit. Check part bytes,
bounded independent geometry and stored constraints without silently rewriting
the imported document or letting a bad receipt masquerade as confirmation.
Keep the existing negative HTTP control unchanged.

### P1: Undo Pollutes the Future Redo Snapshot

`server.py:1282` calls the mutating `sketch` accessor before selecting the
command. On an assembly-only document with zero sketches, `:292` creates one.
At `:1440`, undo pushes this already altered current document to redo.

The assembly route itself correctly records the last confirmed document, and
undo restores its exact bytes. The subsequent redo returns 200 but differs from
the acknowledged assembly edit solely at `$.sketches.length`: 0 becomes 1.
`undo-boundary.json` retains full confirmed/edit/history/undo/redo documents;
the result contains the independent structural difference.

Minimum owner repair: do not obtain/create a sketch for undo/redo or assembly
document commands. Move a needed sketch accessor inside the actual sketch
mutation boundary. Preserve exact document bytes, including extensions, rather
than normalizing a restored confirmation to a new document.

### Passed and Unassessed Boundaries

The new actual gateway and private lazy panel are wired: authenticated reads,
real BRep mesh/BOM/hole clearance/wall interference, numeric mate failures,
assembly candidate reopen, export/reopen, CAS, receipt replay and same-ID/different
body rejection, wrong SHA at the same revision, read/write action separation,
repeated/unknown query rejection and cross-user read/write denial all passed.
Incompatible generic sketch editing is explicitly rejected; it is not a failed
assembly positive control. Changing project standard from `iso` to `gbt` leaves
the independent part configurations untouched. A newly opened local store
reads the exact last confirmation. This is not a service restart/crash test.

The browser opened the real private panel with generated HttpOnly-cookie
authentication. Its actual pose form committed and updated measured geometry;
a second-client pose edit then made the next real form submit stale. The
private session rejected it and blocked the subsequent write, leaving the
stored revision/document unchanged. The canvas still shows its older confirmed
two-part model together with an explicit conflict error, not the unaccepted pose.

Screenshots `browser/desktop.png` (1440x900) and `browser/narrow.png` (420x900)
were inspected natively. Both show the measured ring/pin, configured BOM and
visible stale error, with no whole-page horizontal overflow. Narrow JSON/face
inventories have their own scrolling area. `browser/scope.json` records actual
product mount true, diagnostic mount false, production requests zero.
Failed private worker/request evidence is retained externally in
`private-requests/`. No GPU performance, real hardware renderer, service crash,
production or industrial-parity conclusion is made.

Earlier evidence is retained, not overwritten: `20261011T030324578948Z` and
`20261011T030855786721Z` showed the then-missing routes and empty-sketch undo
boundary. `20261011T031355310539Z` captured a gateway source change and is
explicitly not stable-source acceptance. The current result supersedes the
missing-wiring finding only; its two independent P1 controls remain red.

## Integration Contract

Shared ProjectStore commits exactly `doc`, `undo`, `redo`, `standard`, plus the
request receipt. Assembly state remains in `doc.industrial_assembly_v1`; project
snapshot SHA, exact document SHA, canonical assembly SHA and independent part
SHA are different domains. The private session owns global POST serialization,
version injection, pending-write receipt replay and conflict gating; the panel
only owns its local operation queue. No new persistence/schema is introduced.

`read_assembly(workspace, {"action": "state"})` is a read-only authorized
snapshot; the default GET action is `state`. `prepare_assembly(workspace, body)`
returns `AssemblyCandidate(before_document_sha256, doc, response)` without a
commit. The gateway binds the candidate to current document bytes, appends exact
prior bytes only for an actual change, clears redo, then atomically commits the
snapshot and receipt. HTTP metadata comes from that receipt, not from the pure
adapter. All POST actions pass through the global private session queue.

`secure_web.private_3d_page` mounts a host inside lazy details;
`industrial-panels.js` supplies actual private `window.fetch` transport to the
panel. The panel verifies document/assembly/evaluation/part hashes and requires
the receipt's `project_id`, `revision` and snapshot `sha256`. Project revision
and part configuration are not interchangeable. Neither GET reads nor rejected
POST candidates may manufacture a saved revision.
