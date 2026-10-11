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
