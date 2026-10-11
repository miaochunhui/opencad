# Private Surface GET Export Consumer

## Ownership And Status

Fermat owns only `tools/surface_export_private_review.py` and this document.
Base is `78871af28e4ea3cbec359b1dd4ce4b27a8385688`. That base has no private
surface download routes. This consumer does not implement product adapters,
gateway routes/tests or the panel. A consumer selftest is not acceptance of
those pending changes. No existing private data, account, production service,
credentials, permissions, dependencies, GPU or remote ref may be changed.

The tool reuses the existing trim consumer for source enumeration, the original
private CLI lifecycle, HTTP transport, fixture recipe and analytic fixture.
Its own declaration, ownership, download bytes, producer gate and offline
consumer are separate; it does not inherit the old suite counts or verdict.

## Confirmed Interface V2

The chair confirmed this contract on 2026-10-12. The inspected gateway tree
was `d978479d49ee13584a1784b73da7b8122650aa13` plus three chair-owned changes.
The adapter already supplied document/artifact/recipe SHA, while the gateway
added authoritative revision/snapshot SHA. Model-Version was initially absent;
the chair explicitly confirmed it must equal the authoritative head revision
and will add that header and real GET assertion after the running Web suite.
The consumer requires it: absence or a different value is a failure, not an
optional compatibility fallback. No gateway or adapter edit belongs to Fermat.
Final execution still requires the complete clean candidate commit.

- GET `/api/surfaces/step` and `/api/surfaces/brep`; project selected through
  `X-OpenCAD-Project`. No output path or format/version query is accepted.
  The gateway selects the authorized durable head revision, as drawing export
  already does. Empty, unknown and repeated queries return 400/bad_arguments.
- A real newly registered local account obtains an HttpOnly/SameSite=Strict
  cookie through the actual login HTTP endpoint. Anonymous/forged cookie is
  401; another owner is 403. Missing project is 400/project_required.
- Successful safe GET works without Origin or X-OpenCAD-Request. This does not
  bypass cookie/owner authorization. POST surface writes still require both
  same-origin and the write-CSRF marker, with 403/cross_site_request negatives.
- An empty project returns 404/surface_not_defined, no attachment or local path.
  Error messages must not expose worker paths, tracebacks or private file data.
- Headers bind X-OpenCAD-Revision, X-OpenCAD-Model-Version,
  X-OpenCAD-Document-SHA256, X-OpenCAD-Snapshot-SHA256 and
  X-OpenCAD-Artifact-SHA256 to durable state and the actual downloaded bytes.
  X-OpenCAD-Surface-Recipe-SHA256 binds the exact saved recipe, and Cache-Control
  must be no-store. Model-Version and Revision both equal the captured revision.
  Content-Length equals nonempty bytes, with the fixed attachment filename
  `surface.step` or `surface.brep`. STEP media is exactly application/step;
  BREP media is exactly application/octet-stream, matching the actual adapter.

No browser/panel claim is made here. GET CSRF headers are not write protection;
the suite tests the current local cookie/owner and POST-CSRF boundaries, not
production TLS, proxy trust, CORS exposure or cross-process revocation races.

## Actual Workflow And Geometry Oracle

The versioned declaration is the single source of required identities for
checks, HTTP method/route/status, states, exports and geometry. Both producer
and offline reader require the exact ordered set, without empty/extra/missing,
duplicate, renamed or reordered items. Counts are computed from actual records,
not copied from an earlier acceptance or used as the completeness oracle.

The producer registers two fresh fixture users, creates owner and empty
projects, tests both download-route auth/query/empty denials and POST-CSRF.
It creates the concave double-hole planar recipe, saves, thickens by 2 mm,
undoes, redoes, downloads the durable document, restarts the original CLI
with the same new local fixture root and finally opens the downloaded document
through HTTP. Both STEP and BREP are downloaded at each declared state.
Durable receipts, exact canonical documents and history transitions are checked.
Undo/redo cannot silently substitute another or empty document.

Each export and denial takes a read-only SQLite transaction containing *all*
ProjectStore tables (including newly introduced tables), sqlite_master schema
and typed full rows. Before/after digests, dynamic table names and counts must
match. Durable snapshots contain history; receipts and create receipts are
therefore included. This checks logical store state, not WAL byte stability or
the account database (login necessarily creates sessions).

FreeCAD is discovered from an existing executable, never installed. A separate
FreeCAD/Part child reads the exact HTTP artifact bytes, not a reconstruction
through SurfaceKernel.build. It verifies nonempty byte/hash binding, valid
shape, closed wires, the exact two-hole signature, open-surface area 50 mm2
and closed 2 mm-thick solid volume 100 mm3. World-coordinate material, square
hole, triangular hole and concave notch witnesses are measured independently.
A bounding rectangle demonstrably fills both holes and the notch, proving
why an area/bounding-box-only oracle would be insufficient.

## Source And Evidence Closure

Preflight binds HEAD, status, every Git-visible tracked/untracked regular file,
path, byte count, SHA256 and executable mode. Required product paths must exist;
links and nonregular files fail. Execution requires caller-pinned full HEAD
and source manifest SHA with clean frozen source and clean executor. Selected
source and executor (including the reused helper) each have exact before/after
closure. No source-copy, old product equivalence or ancestor acceptance policy
is used. A new product freeze requires a new real measurement.

The v2 real producer writes a fresh complete terminal candidate, reads it back,
checks typed canonical equality and the complete suite against the separately
captured source/executor references, and only then publishes immutable PASS
with a candidate SHA. Producer and offline reader invoke the same physical
evidence oracle, requiring nonempty inventory, actual HTTP bytes, download
paths and exact HTTP indices, CLI logs/argv, geometry script/input/output and
retained complete ProjectStore. Empty/self-consistent metadata cannot publish
PASS. A complete evidence file inventory binds paths, bytes
and hashes, with bidirectional set equality. Only final result/candidate are
outside that inventory because they bind each other separately. Original
failures and all generated 3FS files are retained; no overwrite or cleanup.

Offline mode requires an independently supplied original result SHA, verifies
the saved candidate/final relation, complete source and evidence closure,
HTTP raw-to-export binding, headers, original CLI logs/ready markers, stored
geometry terminal and retained full ProjectStore. It does not start HTTP or
FreeCAD. The supplied SHA is a caller evidence pin, not an authenticated or
production approved-record service. Synthetic selftest samples are explicitly
not real acceptance evidence and must never be offered as such.

## Run Entry Points

Use `sudo -n -u infra python3 -B tools/surface_export_private_review.py` from
this independent tree. Every invocation requires a fresh exact 3FS directory;
none reuses or removes existing resources. The local runtime is only required
for the explicitly authorized real candidate run.

```text
--source-root <frozen-candidate> --out /hf3fs-jd/data/tmp/<new-preflight> --preflight
--out /hf3fs-jd/data/tmp/<new-selftest> --selftest
  --selftest-counterexample /hf3fs-jd/data/tmp/opencad-surface-export-consumer-selftest-20261012-v1/positive/result.json
--source-root <frozen-candidate> --expect-head <full-HEAD>
  --expect-source-sha <preflight-manifest-SHA256>
  --out /hf3fs-jd/data/tmp/<new-real-run>
  --runtime-root /data/tmp/<new-local-fixture>
--source-root <same-frozen-candidate> --out /hf3fs-jd/data/tmp/<new-offline-review>
  --verify-result /hf3fs-jd/data/tmp/<original-real-run>
  --reference-sha <independently-recorded-result-SHA256>
```

Terminal output distinguishes PREFLIGHT_ONLY, SELFTEST_ONLY,
REAL_PRIVATE_SURFACE_GET_DOWNLOAD and OFFLINE_READBACK_ONLY. FAIL records the
first cause and rc 1; malformed CLI exits with original argparse rc 2.
Child CLI exits are recorded separately and must be measured rc 0. A successful
parent process, existence of a process or a saved JSON shell is not acceptance.

Selftests cover both-end empty/deleted/extra/byte/mode/HEAD/status/root source
mutations, every workflow identity mutation, actual candidate save/read-back
corruption and incomplete-suite publication, empty artifacts, incorrect
version/header/witnesses, failed/unmeasured CLI and immutable publication.
Evidence and selftest logs are external; no code is written into runtime.
The physical-structure positive control uses explicit synthetic files and a
new local synthetic SQLite fixture without product HTTP or FreeCAD. It tests
the evidence reader structure only, never exports or geometry correctness.
Each missing-file control omits a real copied fixture file and recomputes its
inventory, proving the mandatory-file oracle rejects beyond hash equality.
Copied readback inputs and argv bind the copied directory before each omission;
each control asserts and records its own exact missing-file first cause, rather
than accepting a preceding unrelated input-binding failure.
Synthetic saved-roundtrip controls use SYNTHETIC labels, never REAL/PASS.

## Retained P1 And Resume

The original v1 selftest returned parent rc 0, 10 tests and 1768 rejected
mutations at 2026-10-12 05:52:37+08. Its nested `positive/result.json` was an
actual false PASS: 181/56/14/113 declared check/HTTP/export/geometry counts,
but no HTTP bytes, export paths/indices, CLI logs or retained database, and
an empty evidence manifest. That producer only compared empty inventories.
The parent SELFTEST_ONLY result is not real product acceptance; it and the
nested false PASS remain unchanged under
`/hf3fs-jd/data/tmp/opencad-surface-export-consumer-selftest-20261012-v1/`.

The retained v2 parent selftest ended at 2026-10-12 06:24:20.913848+08 with
original rc 0: 13 tests, 1781 rejected mutations, no failures/errors/skips,
and actual HTTP/export/geometry counts all zero. Evidence remains under
`/hf3fs-jd/data/tmp/opencad-surface-export-consumer-selftest-20261012-v2/`.
Its original false-PASS control rejected the unchanged v1 file with rc 1 and
`Evidence full file set/byte/hash closure mismatch`. The subsequent selftest
adds exact first-cause assertions and independent copied-directory bindings;
it does not repeat or promote old product measurements.

The v2 selftest must read this original file unchanged and reject it through
the physical oracle, not merely a new schema/source mismatch. Saved-candidate
mutation, every workflow identity, source HEAD/byte/mode/set, missing actual
artifact/HTTP/CLI/geometry/document/DB, and Model-Version absence/wrong-value
controls are separate. No old source result is upgraded to new-source proof.
The infra edit router continues to reject the independent OpenCAD path with
outside-root rc 2; no rule/permission was changed to bypass it. Explicit chair
authorization and this independent tree ownership govern the two-file edits.

## Remaining Acceptance

The chair must provide the exact combined clean adapter/gateway candidate commit
with the confirmed Model-Version header. Then freeze, preflight, execute this
consumer with fresh evidence/local runtime, receive original rc, inspect actual
counts and independent byte readback, and exercise offline verification with
the separately recorded result SHA. Until then real HTTP/geometry and browser
panel integration are untested. A small analytic fixture is not large assembly,
industrial parity, new-source GPU performance or production acceptance.
