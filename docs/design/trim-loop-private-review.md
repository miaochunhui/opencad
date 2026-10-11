# Private Exact UV Loop Review

`tools/trim_loop_private_review.py` is an independent, read-only-source
acceptance tool. It creates only a fresh evidence child under the supplied
`/hf3fs-jd/data/tmp/` parent and a fresh local `/data/tmp/` runtime. No product
patches, live accounts, existing services, dependency installations, permission
changes, remote writes or evidence cleanup are involved. Product-owned cleanup
of newly created reproducible request workspaces is permitted; existing data
and 3FS evidence must remain untouched.

## Source And Invocation

`--source-root` selects the exact product tree; the original `secure_web.py`
CLI is started from that tree with its package roots explicitly bound. Imported
product modules and worker source hashes must belong to the selected source.
The SHA covers every Git-visible tracked/untracked file's relative path, bytes
and executable flag. The tool separately records its own path/hash, source HEAD,
dirty status and before/after manifests. Preflight and selftest are NOT runtime
acceptance. A full SHA mismatch rejects execution before creating runtime.
Runtime requires unchanged HEAD and dirty status as well as bytes/path/mode;
dirty paths are limited to this review tool and document, never product files.
The executor's own before/after SHA is also checked when source-root differs.

```bash
sudo -n -u infra python3 -B tools/trim_loop_private_review.py --selftest --out /hf3fs-jd/data/tmp/opencad-trim-loop-private-selftest-20261012-v1
sudo -n -u infra python3 -B tools/trim_loop_private_review.py --source-root /data/dev/wt-opencad-trim-loops-20261012 --preflight --out /hf3fs-jd/data/tmp/opencad-trim-loop-private-preflight-20261012-v1
sudo -n -u infra python3 -B tools/trim_loop_private_review.py --source-root /data/dev/wt-opencad-trim-loops-20261012 --expect-source-sha FULL_PREFLIGHT_SHA --runtime-root /data/tmp/opencad-trim-loop-private-baseline-20261012-v1 --out /hf3fs-jd/data/tmp/opencad-trim-loop-private-baseline-20261012-v1
```

The baseline run is only self-acceptance of this seat's tree. It does not certify
a future chair-integrated tree. An explicitly notified new freeze requires a
new preflight, expected SHA, evidence child and local runtime.

The first selftest entry returned original rc=1 before allocating evidence or
runtime: the new review's initial path guard incorrectly rejected the authorized
`/hf3fs-jd -> /3fs/research3fs` mount alias. Only that exact verified alias is now
admitted; links below it and local-runtime links remain rejected. This was a
review-tool failure, not a product test result, and is not reclassified as PASS.

`--verify-result EXISTING_EVIDENCE_DIR --out FRESH_PARENT` verifies an existing
terminal suite without another HTTP request, worker build or database write.
It checks saved counts/CLI exits, complete local project tables, actual durable
HTTP receipts, versioned measurements, worker execution records, BRep/STEP
bytes/hashes and readback hole witnesses. The original verdict remains immutable;
failed, partial, empty and preflight-only results cannot become accepted.
Only changes to this tool and its design document are permitted when comparing
the old source with the current executor. Product byte/path/mode drift fails.
If the current HEAD differs, the original must be its verified ancestor and
every intervening commit's changed paths must stay within those two owned files.
This is reported as `OWNED_TOOL_DOCUMENT_ONLY_CONTENT_EQUIVALENT`, with both
full HEADs and `same_commit_acceptance=false`; it is not same-HEAD acceptance.

## Versioned Complete Ledger

`workflow_ledger()` is the single `trim-private-ledger-v1` declaration. It
derives exact ordered checks, HTTP labels/routes/methods/statuses, measurement
labels, named fixture specifications and BRep/STEP shape/readback labels from
the workflow groups. The HTTP producer, bad-loop producer, fixture capture and
FreeCAD READBACK must reconcile with this declaration before PASS. Counts are
calculated from actual entries, never used as the completeness specification.
The offline consumer uses the same declaration, rejects unknown versions, and
requires unique exact names/order, including all response status contracts.

The original complete ledger has 191 checks, 46 HTTP requests, seven
measurements, four named fixtures, eight shapes and 67 readback checks.
These numbers describe the preserved execution, not bare frozen thresholds.
Deletion, duplication, renaming or reordering fails even with recomputed
self-consistent counts. Fixtures also bind their exact recipe, analytic
values and witnesses to the recorded HTTP project/revision/document version.

Immutable pre-versioned evidence is admitted only for the two recorded legacy
tool SHA256 values in `LEGACY_TOOLS`, its manifest-bound producer, and the
recorded legacy READBACK script SHA. It still must satisfy the entire current
v1 label/order/HTTP/fixture contract. No old result or verdict is rewritten.

The P1 counterexample is retained under
`opencad-trim-ledger-completeness-20261012-v1/before/20261011T195512493076Z-5e22b1a4`:
original rc=1, 11 tests with one failure, `AssertionError not raised` for a
one-check/one-HTTP/one-readback PASS sample. The old positive fixture itself
was incomplete; it is replaced with a declaration-derived complete fixture.
The other nine existing controls remain, with every-item mutation, HTTP
contract, fixture binding and HEAD/dirty-state controls added.

The first stricter offline readback is also retained:
`opencad-trim-ledger-completeness-20261012-v1/original-readback-v1/20261011T200036437272Z-3f5798b4`,
original rc=1, first cause `Fixture ledger contract changed: polygon/area`.
The review erroneously compared JSON `50.0` and `50` as distinct bytes.
Finite numeric exact equality now accepts those equivalent values; booleans,
nonfinite values and numerically different values remain rejected.

`--selftest --selftest-ledger ABS_ORIGINAL_RESULT` reads an existing real
result and mutates only detached in-memory copies. It records original
result/input hashes and actual rejected mutation counts in fresh evidence:

```bash
sudo -n -u infra python3 -B tools/trim_loop_private_review.py --selftest --selftest-ledger /hf3fs-jd/data/tmp/opencad-trim-loop-private-baseline-20261012-v1/20261011T183348334275Z-a7585681/result.json --out /hf3fs-jd/data/tmp/opencad-trim-ledger-completeness-20261012-v1/after
sudo -n -u infra python3 -B tools/trim_loop_private_review.py --source-root /data/dev/wt-opencad-trim-loops-20261012 --verify-result /hf3fs-jd/data/tmp/opencad-trim-loop-private-baseline-20261012-v1/20261011T183348334275Z-a7585681 --out /hf3fs-jd/data/tmp/opencad-trim-ledger-completeness-20261012-v1/original-readback
```

This hardening runs no new product full/private acceptance, HTTP, FreeCAD
worker or service. The original complete 191/46/67 execution and its rc remain
historical evidence of its selected tree only, not a newer release freeze.

The nonfinite-measurement adversarial selftest initially failed at
`opencad-trim-loop-private-nonfinite-before-20261012-v1/20261011T184050121610Z-413a5786`:
8 tests, 1 failure, original rc=1. An injected NaN area escaped the review's
numeric error comparison. Explicit finite positive BRep/STEP measurement and
expected-value guards now reject it; nonfinite JSON constants also fail loudly.
The existing real baseline contained finite measurements and is not rewritten.

First evidence-only readback retained rc=1 with 67 passed/1 failed checks in
`opencad-trim-loop-private-readback-20261012-v1/20261011T184323926458Z-11e9cdfb`.
The tool incorrectly required the Python print marker in FreeCAD stdout,
which contains only the FreeCAD banner on this backend. The correction uses the
original measured process exit and exact completed result JSON with all
readback checks, not a stdout banner or assumed process success.

## Actual Contract

POST `/api/surfaces` uses `recipe` plus `expected_revision`, `expected_sha`
(snapshot SHA, not document SHA) and `request_id`. The schema-1 recipe remains
`{op:"trim", surface:NURBS_OR_RECTANGULAR_TRIM, outer:CLOSED_CCW_UV,
holes:[CLOSED_CW_UV,...]}`, exclusive of `bounds`.

The test performs real generated-account cookie login; anonymous, forged,
other-owner and CSRF denials; exact concave two-hole polygon POST/GET; durable
save; old receipt replay and conflicting request reuse; stale CAS rejection;
invalid UV closure/crossing/direction/bounds/holes/degeneracy and invalid open;
2 mm thickening; undo/redo; document download; original CLI graceful close and
restart with the same cookie/database; receipt replay after restart; and actual
versioned POST `/api/open` with exact document bytes and reset history.

Each negative request must match its exact status/error contract and leave
the complete four-table project database, current head, history and receipts
unchanged. Login/cookie/session values are kept in memory, never recorded or
hashed. Counts are calculated from actual checks, not hard-coded expectations.
Any unexpected failure stops affected validation, preserves original child
exits, first cause, HTTP reply and evidence coordinates. It is never retried
automatically or turned into a skip.

The planar fixture is a concave outer loop, square hole and triangular hole:
analytic area 50 mm2, bounding-rectangle area 64 mm2, 2 mm thickening volume
100 mm3. Measured BRep/STEP topology, mesh planar area, welded manifold edges
and independent tetrahedron volume are checked. Captured canonical recipes are
independently rebuilt by the selected source's real FreeCAD worker, then BRep
and STEP are reopened in another real FreeCAD process. Hole and concave-notch
world-point witnesses reject filled-hole or bounding-rectangle substitution.
The evidence binds each reconstruction to its HTTP document/project/revision.

## Boundaries

Surface GET exposes measured mesh and BRep/STEP artifact hashes/byte counts,
not downloadable artifact paths. Gateway request files may already be removed
on success. Independent rebuild/readback proves the persisted recipe and
same-version measured geometry, not a gateway STEP-download route.
Part-only `/api/mesh` and `/api/step` correctly reject surface projects with
422 `unsupported_geometry_domain`.

Surface recipes have no solver DoF or residual outputs; the tool records that
these are not measured, rather than inventing zero values. This supplement does
not certify browser draft/race behavior, curved-cylinder coverage beyond prior
kernel evidence, TLS/proxy, quotas, cross-database revocation atomicity, general
surface injectivity, all offsets, global G2, guide lofts or full industrial parity.
Source stability and successful local CLI exits are necessary, not sufficient,
for acceptance; the actual logical-store and geometry checks must also pass.
