# Exact Industrial Surfaces: Bounded First Slice

This is an isolated kernel implementation, not a Web feature registry or a
claim of CATIA/SolidWorks parity. FreeCAD/OCCT execute only in a child process.
The host uses standard-library Python and file-level JSON/BRep/STEP contracts.
No mesh is used to construct or repair the authoritative geometry.

## Interfaces

Code retains the migrated `oss-alternatives/packages/cadcore/` layout.
`cadcore.kernel.surfaces` exports:

- `make_nurbs(*, poles_nm, u_degree, v_degree, u_knots, v_knots,
  u_mults, v_mults, weights=None)`: rectangular rational/nonrational NURBS,
  clamped nonperiodic U/V. Coordinates are strict integer nanometres.
- `make_trim(surface, *, u_min, u_max, v_min, v_max)`: rectangular parameter
  bounds on a single NURBS face. Bounds may not escape its current domain.
- `make_trim(surface, *, outer, holes=[])`: exact closed polygonal UV outer
  ring and optional inner holes on NURBS or a rectangular-trim parent.
  Polygon fields and rectangular bounds are mutually exclusive.
- `make_sew(*, surfaces, solid=False, seams=None)`: one connected shell;
  solid requests additionally require closed, valid, positive-volume BRep.
- `make_offset(surface, *, offset_nm)`: nonzero signed normal offset, open
  face/shell result, not an implied solid.
- `make_thicken(surface, *, thickness_nm, inward=False)`: positive length
  and explicit direction; OCCT must produce one valid closed solid.
- `SurfaceKernel(executable=None, evidence_dir=..., timeout_s=60)`:
  `probe()`, `build(surface, deflection_mm=0.05)`, `evaluate(surface, uv=[...])`,
  `check_seam(left, right, ...)`. Results are detached worker JSON.

`build` returns measured backend version, shape properties, BRep-tessellated
mesh, BRep/STEP artifacts with hashes, and independently loaded roundtrip
properties. It never falls back to nano, a box, or approximate mesh geometry.
All worker jobs keep request, result, stdout, stderr, exit code, elapsed time,
and before/after source hashes in an external unique evidence directory.

## UV Polygon Recipe And Admission

The generic Web preparation request remains `{"recipe": recipe}`. For example,
this 10x10 mm support has a concave outer and square/triangular holes:

```json
{
  "op": "trim",
  "surface": {
    "op": "nurbs",
    "poles_nm": [[[0, 0, 0], [0, 10000000, 0]],
                 [[10000000, 0, 0], [10000000, 10000000, 0]]],
    "u_degree": 1, "v_degree": 1,
    "u_knots": [0, 1], "v_knots": [0, 1],
    "u_mults": [2, 2], "v_mults": [2, 2], "weights": null
  },
  "outer": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.5], [0.6, 0.5],
            [0.6, 0.9], [0.1, 0.9], [0.1, 0.1]],
  "holes": [
    [[0.2, 0.2], [0.2, 0.3], [0.3, 0.3], [0.3, 0.2], [0.2, 0.2]],
    [[0.4, 0.2], [0.4, 0.4], [0.5, 0.2], [0.4, 0.2]]
  ]
}
```

Raw JSON requires exactly `op/surface/outer/holes`; unknown fields and mixed
`bounds` are rejected. Only the Python constructor defaults omitted holes to
an empty list. UV coordinates are finite numbers, never booleans. Outer is
explicitly closed and counterclockwise; holes are explicitly closed and
clockwise in UV. Each ring has 3..128 distinct vertices plus closure; at most
16 holes and 512 points including closures are admitted.

Points must remain in the finite current NURBS/rectangular parent parameter
range. Exact binary-rational orientation/intersection/containment predicates
reject zero area, duplicate/backtracking edges, self-intersection/self-touch,
outer/hole touching or crossing, holes outside the outer, and overlapping or
nested holes. Forward collinear vertices are allowed. There is no epsilon
snapping, silent direction reversal, healing or fallback.
Nested polygon parents are deliberately rejected for both rectangular and
polygon children, rather than reconstructing a support and resurrecting holes.

The real worker creates edges with
`Part.Geom2d.Line2dSegment(Base.Vector2d(...), Base.Vector2d(...)).toShape(surface)`.
These are straight UV segments, generally curved in world coordinates.
The outer establishes `Part.Face(surface, outer_wire)`; each hole is added
individually with `Part.Face(face, hole_wire)`. The surface/list overload is
not interchangeable: on the installed API it starts with the default rectangle.
No parent-shell projection, rectangular bounding-box cut or mesh boolean is
used. OCCT checks physical wire closure/validity, positive face area and exact
BSplineSurface support; every edge must retain a curve-on-surface.
Features below OCCT physical tolerances fail visibly even if UV predicates
admit a mathematically nonzero area.

Evaluation classifies the actual trimmed Face domain, not its UV rectangle:
holes and concave excluded regions reject evaluation. At/near boundaries,
classification still uses OCCT numerical tolerance.
Properties include aggregate `inner_wire_count` and per-face `wire_count`,
`inner_wire_count` and `wires_closed`. BRep/STEP readbacks must preserve
face/solid counts, sorted per-face closed-hole counts, area and solid volume.
Independent review also tests world-coordinate hole/notch witnesses on the
reloaded shapes, including through-hole interior points after both thickening
directions and holes after sewing. Mesh is output tessellation only.

## Continuity Contract

Edges are `u_min/u_max/v_min/v_max`. Seam input indices in a sew recipe are
one-based, sequential references to that recipe's inputs, not persistent
topological references. `reverse=True` explicitly reverses correspondence.

`check_seam` defaults: `continuity="G2"`, `samples=17`,
`position_tolerance_nm=100`, `angle_tolerance_rad=1e-6`,
`curvature_tolerance_per_mm=1e-6`. A sew seam also includes `left/right`.
Only declared seam pairs are continuity-checked; cube corners need not be G1.
The existing rectangular-edge interface is unchanged; it does not become a
general polygon-edge continuity API.

- G0 measures exact support-surface points at corresponding boundary UV.
- G1 requires G0 plus matching mapped seam tangents and unoriented normals.
- G2 requires G1 plus the second fundamental form in a common world tangent
  basis, using exact first/second derivatives, not a visual smoothness score.
- Uniform samples plus knot-span endpoints/midpoints are measured.
  `whole_domain_proof=false`: finite samples are not a proof between samples.
  Singular derivatives and malformed correspondence fail visibly.

## Backend and Limits

Source-confirmed FreeCAD 0.19.2 API:
`Part.BSplineSurface.buildFromPolesMultsKnots`, `toShape(u0,u1,v0,v1)`,
`getD0`, `getDN(u,v,nu,nv)`, `TopoShape.sewShape()` and
`makeOffsetShape(offset,tolerance,inter,self_inter,offsetMode,join,fill)`.
Rectangular trims segment an exact copy of the NURBS support before constructing
the face, so OCCT bounding boxes do not include poles outside the trimmed domain.
Reported bounds use `cleaned().optimalBoundingBox()` (OCCT AddOptimal with all
triangulation removed and no shape-tolerance inflation), including true offset
displacement. FreeCAD 0.19.2's optional-argument parser is defective; the measured
safe no-argument API avoids it without suppressing any geometry error.
OCCT still includes its numerical precision margin (measured approximately
`1e-7 mm` per side); `bbox_tolerance_mm=1e-6` is explicit, not exact equality.
Sew requires every input face to belong to the returned shell; disconnected
faces beside a closed shell cannot silently disappear.
Offsets use tolerance `1e-6 mm`, skin mode `0`, intersection join `2`,
`inter=False`, `self_inter=False`; `fill=True` only for thicken.
Sew uses the version's `sewShape()` internal tolerance, not an invented
configurable Python parameter. Actual installed version is measured per job.
The complete `App.Version()` components are preserved, including distribution
build information; the display version does not invent a missing patch number.
After OCCT succeeds, offset/thicken also sample principal-curvature factors
`1 - signed_offset * curvature`; nonpositive factors reject focal crossings.
Reports include the minimum factor and witness UV. This catches the measured
5 mm-radius cylinder inward 6 mm counterexample that OCCT accepts as a valid
positive-volume solid. It is not a whole-domain self-intersection proof.
For polygon faces the regularity check skips excluded UV samples and fails
visibly when no samples remain in the actual domain.

This slice supports simple polygonal UV loops within the stated budgets,
not arbitrary curved UV edges, nested polygon operations, guide-constrained
loft, whole-domain G2/injectivity proof, periodic surfaces, healing of broken
shells, persistent topo naming, self-intersecting offset repair, or industrial
scale acceptance. OCC/FreeCAD exceptions retain their original type/message
and traceback; a failed operation is not automatically retried.
Gateway integration and project/version transactions remain chair-owned.

## Web Candidate Adapter

`apps/opencad/industrial_surfaces.py` exports read-only
`read_surfaces(workspace, body={})` and
`prepare_surfaces(workspace, body={"recipe": recipe})`.
The latter returns `SurfaceCandidate(before_document_sha256, doc, response)`.
It does not publish the document, change caches, or maintain either history.
The caller must compare the current complete-document SHA while publishing,
then confirm its authorized durable project revision CAS. A stale candidate
must not be assigned to a shared live Workspace before the durable commit.

The recipe persists in `doc.extra["x_industrial_surfaces"]`, schema 1. It is
independent of the existing solid-feature tree: no boolean merge is implied.
GET returns a captured-document SHA, persisted recipe and rebuilt exact BRep
properties/mesh/STEP-BRep roundtrip results; it does not edit cache/history.
Temporary artifact filesystem paths and tracebacks are not public response
fields. HTTP exposes artifact digests/byte counts, not an export route yet.
Null volume means an open surface, never a zero-volume solid.
Polygon loops use the same schema 1 namespace and generic recipe editor without
an adapter migration. Save, undo/redo and a new Workspace reopening preserve
canonical document bytes and all loop fields. Malformed loops reject before
worker construction; physical OCCT failures preserve the first cause without
publication. Candidate seals bind the measured geometry to the document.

Read-only numeric checks accept
`{"action":"check_seam","left":recipe,"right":recipe,"seam":settings}` through
`read_surfaces`. They return `passed=false` for discontinuous seams without
editing. This measurement must not create a project revision. Actual sew
with declared failing seams is rejected before publication.

The chair wires GET `/api/surfaces` to read and versioned POST to preparation,
stripping the gateway-owned `expected_revision/expected_sha/request_id` fields
before calling the adapter. Existing project/CSRF authorization must remain
in force; the adapter itself is not an authenticated endpoint.

`web/js/surfaces-panel.js` exports `OpenCADSurfaces.mount(host, options)`, never
auto-edits the viewer page. `options.request(path, body)` can supply the
authorized gateway transport; default fetch uses the existing project-session
adapter. `options.labels` supplies UI localization. `options.onMesh(mesh,
response)` receives actual tessellation for chair-owned viewer integration.
The panel includes an independent WebGL preview, integer-nm recipe editor,
plane/rational-cylinder presets, rectangular trim/thicken wrapping and a
declared two-plane G2 sew preset. No mesh is used as modeling input.

## Acceptance

Run from the independent repository/worktree, as infra:

```bash
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_surfaces.py --evidence-dir /hf3fs-jd/data/tmp/opencad-industrial-surfaces-20261011/seat-surfaces
```

`--only probe` is explicitly partial, never industrial acceptance. Full tests
require real FreeCAD; missing dependencies fail rather than skip. Tests cover
analytic rational-cylinder points/area, exact rectangular trimming,
continuous curved patches and G0/G1/G2 negative controls, reverse seams,
closed-cube sewing, gaps/unclosed/disconnected refusal, plane offset/thicken,
manifold solid mesh/analytic volume, export roundtrips and raw worker schema.
Polygon coverage includes concave double-hole area, diagonal triangle trims,
both thickening directions, manifold mesh/volume, sewn-face hole preservation,
rational-cylinder exact support points, and invalid/physically degenerate rings.
Final counts and acceptance status come only from the same run's `tests.log`,
`result.json`, process exit code and zero source drift, not this document.

Adapter acceptance uses an isolated real Workspace HTTP reference route (not
the production gateway), actual FreeCAD and actual desktop/mobile Chromium
clicks/pixels:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_industrial_surfaces.py --repository-check --evidence-dir /hf3fs-jd/data/tmp/opencad-industrial-surfaces-20261011/seat-surfaces-web
```

Durable gateway integration, authentication for the new route, localized UI
strings and whole-product tests require chair acceptance. This bounded suite
does not certify them or whole industrial CAD parity.

Independent UV-loop artifact review uses a fresh external output and local
runtime:

```bash
sudo -n -u infra python3 -B tools/trim_loop_review.py --out /hf3fs-jd/data/tmp/opencad-trim-loops-20261012/new-review --runtime-root /data/tmp/opencad-trim-loops-new-review
```

The review binds the whole-tree manifest and HEAD, retains results/logs/BRep/STEP,
checks source drift and artifact hashes, and measures the double-hole plate
against analytic 50 mm2, 2 mm thickening against 100 mm3, a diagonal triangle
against 32 mm2 and sewn faces against 86 mm2. Rational-cylinder support points
and actual hole locations are independently checked. A real rectangle
substitution is detected by the hole witness. This is not an all-domain proof.

### Retained UV-Loop Evidence

Original results below are under
`/hf3fs-jd/data/tmp/opencad-trim-loops-20261012/`, bound to implementation
commit `b04ebcf4e8d26d3c5dbed89fed03bed714728149` and source manifest
`4428ec749f1445fa58cb7bc483f1eb7d47714bd92f9ded56fbac6efed8d3213e`.
Their source/run records are immutable; this subsequent documentation-only
path correction has a different whole-tree manifest and does not reclassify
old results or claim another full regression.

- `kernel-final/20261011T175143864242Z-215e35d1dfb34d76935aaf5b101d56b1`:
  original rc=0, 39 tests, no failures/errors/skips or drift; real FreeCAD,
  source scan and exact unittest discovery. Finished 2026-10-12 01:52:17 +08:00.
- `adapter-v2/20261011T175143231327Z-8c9c6522eb4a41768d07e3d1211b0d09`:
  original rc=0, 30 tests, no failures/errors/skips or drift; real Workspace
  HTTP, desktop/mobile browser, save/history/new-Workspace reopen.
  Finished 2026-10-12 01:52:25 +08:00. This is not the private gateway CLI.
- `exact-readback-final/20261011T175143587808Z`: original rc=0, 117 checks,
  no drift; actual BRep/STEP loaded in a separate real FreeCAD process.
  Finished 2026-10-12 01:51:46 +08:00. Hole points remain excluded after
  trim/sew/thicken/export reload. Plate BRep/STEP areas are
  50 / 49.99999999999925 mm2; both thickening directions yield
  100 / 99.99999999999974 mm3. Installed version reports FreeCAD 0.19
  (full components retained) and OCCT 7.5.1, not an invented patch number.
- `all-final`: original rc=0, 2318 tests, 2305 passed, 13 existing skips,
  no failures/errors or drift, plus 15 HTTP selftest checks; all five child
  exits are 0. Finished 2026-10-12 02:04:54 +08:00.
  Twelve skips need the absent OCP/OCCT path; one needs no injected fixture.
  Skips are not passes.

The original `api-probe` failure used nonexistent `FreeCAD.Vector2d` before
correcting to `Base.Vector2d`. An insufficient probe then falsely reported
PASS for an invalid three-wire face with area 196; its original record is
retained, not accepted. The corrected exact API probe and subsequent review
check validity, analytic area and actual excluded hole points.
`adapter-v1/20261011T174745525592Z-037a6948fda84e42b1b0f84c39f1aadb`
retains original rc=1 and two errors: the new reopen test's fixture root
excluded its saved file, so the existing `path_rejected` guard correctly
refused it. Only the fixture root was corrected; no path guard was weakened.
