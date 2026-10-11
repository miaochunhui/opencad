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
`--selftest` exercises sixteen validator positive/negative controls without a CAD
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
undo and export/reopen. A separate generated project imports each independent
immutable part through actual HTTP before it submits a displaced pose and
measured axis/plane mates. Residual count, finite numeric values, rank and DoF
are mandatory; empty, boolean, nonfinite and oversized residuals fail the
validator selftests. Overconstraint rejection must leave both the entire
stored head and the request receipt unchanged.

The solved assembly is explicitly saved through `/api/save`; a newly opened
SQLite connection must recover the identical full snapshot and original
mate/save receipts. Replaying the saved request from its old base must recover
the exact confirmation without a new revision. A fresh project reopens the
saved L1 and must rebuild identical parts, mates, BOM and measured mesh.
Two concurrent real HTTP edits then start from one acknowledged version:
exactly one may commit, with one revision advance, exact prior undo bytes, a
winner receipt and no loser receipt. These are local connection/HTTP tests,
not a crash, service restart or production durability claim.

Missing routes are dependency failures, not replaced by a test gateway subclass.

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
`/hf3fs-jd/data/tmp/opencad-assembly-private-review-20261011/20261011T034318423209Z/result.json`.
Finished at `2026-10-11T03:43:38.436933+00:00`: **FAIL**, 70 checks,
68 passed, two failed, zero stage errors, zero source drift, no skips.
The 19.988-second run is new-source diagnostic evidence, not an attempt to
declare the two known failures usable. No automatic retry was performed.
Result SHA256:
`3e30b07b2f1a7ce9919b7421096d25c5e0e538ca38a5654cb364fa8dbfc375ed`.
Owned tool SHA256:
`4f8b4761da8251777869edcc119a1b9a05b94950820fc857b5d5a3c423166040`.
Sixteen validator selftests passed at
`20261011T034249812447Z/result.json` under the same evidence parent.
The result verifier reread all source/artifact hashes and returned
`evidence_valid=True`, retaining FAIL with exit code 1.

The read-only chair source root is `/data/dev/opencad`. HEAD `40dae2c` alone
does not bind the newly untracked integration files. Exact tested SHA256 values:

| File under `oss-alternatives/apps/opencad/` | SHA256 |
| --- | --- |
| `project_gateway.py` | `3665d3d9b0b0b35d109919f02867bada70520ba42169981f2f774950aca6e9a8` |
| `secure_web.py` | `80781a5ee7cf85ff405fa6b53288c5ef0455d423b088dd5dff10138913feafac` |
| `web/js/industrial-panels.js` | `2c2fd1dac64625406f2a53d04691f5905df319f217b3f73f4bd0a2b2f4ad7a93` |
| `web/js/project-session.js` | `7caec535cda662eff23b310b0eab8784fbda712c231ff86dbcdde74eea1110e8` |
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

The new real HTTP project separately imported ring and pin with distinct
immutable SHA references. Its displaced pin interfered with the ring wall.
Axis mate submission confirmed rank 4, DoF 2 and a 2 nm linear residual;
axis plus plane confirmed rank 5, DoF 1, linear residuals 0/3 nm and angular
residuals 0. The corrected BRep retained the measured 1 mm radial gap.
Duplicate axis overconstraint returned 422 `mate_overconstrained`, without
changing any head bytes or inserting a receipt.

`/api/save` confirmed revision 7 with an unchanged solved snapshot. A new store
connection recovered identical head bytes and both mate/save receipts. The
old-base save replay returned the same confirmation without a new revision.
A second project reopened the solved L1 and rebuilt equal part bindings,
stored mates, BOM and measured mesh. Two concurrently submitted mate removals
then returned 409/200 from revision 7, producing only revision 8 and only the
winning receipt. Full measured responses and store snapshots are retained in
`mate-persistence.json`; generated session secrets are never retained.

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
The previous `20261011T032136413927Z` result remains historical 52-check
evidence. The current 70-check run rebinds the updated transport/session and
independently reproduces the same two failures; CLI alias repair does not
repair either semantic confirmation boundary.

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
