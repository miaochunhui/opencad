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

## Continuity Contract

Edges are `u_min/u_max/v_min/v_max`. Seam input indices in a sew recipe are
one-based, sequential references to that recipe's inputs, not persistent
topological references. `reverse=True` explicitly reverses correspondence.

`check_seam` defaults: `continuity="G2"`, `samples=17`,
`position_tolerance_nm=100`, `angle_tolerance_rad=1e-6`,
`curvature_tolerance_per_mm=1e-6`. A sew seam also includes `left/right`.
Only declared seam pairs are continuity-checked; cube corners need not be G1.

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

No arbitrary trim loops/holes, guide-constrained loft, whole-domain G2 proof,
periodic surfaces, healing of broken shells, persistent topo naming,
self-intersecting offset repair, feature-tree/Web dispatch, or industrial
scale acceptance is claimed. OCC/FreeCAD exceptions retain their original
type/message and traceback; a failed operation is not automatically retried.
Gateway integration and project/version transactions remain chair-owned.

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
Final counts and acceptance status come only from the same run's `tests.log`,
`result.json`, process exit code and zero source drift, not this document.
