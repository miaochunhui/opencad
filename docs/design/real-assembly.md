# Real Hierarchical Assembly

## API and Ownership

Independent API in `cadcore.assembly`; no changes to server, docmodel, shared
feature registry or subproc. The worker composes its own adapter on a copy of
the existing FreeCAD kernel profile and uses the existing bounded subprocess
launcher. It never modifies the caller's adapter.

- `PartReference.from_document(part_id, document, configuration="default")`
  captures exact immutable L1 bytes and SHA256. The same part ID can have
  distinct configurations. Embedded documents avoid mutable external paths.
- `Transform(rotation, translation_nm)` uses a proper orthonormal row-major
  3x3 matrix, column-vector convention, and integer-nanometre translation.
- `Component(component_id, part_id=None, parent_id=None, transform=Transform(),
  configuration="default", fixed=False)` represents a group or a physical leaf.
  World placement is `parent_rotation @ local_rotation` and
  `parent_rotation @ local_translation + parent_translation`.
- `Mate(mate_id, kind, a, a_face, b, b_face, offset_nm=0, aligned=True)` accepts
  `plane` and `axis`. Zero-based face indices bind to each immutable part's
  actual analytic plane/cylinder surface. No inferred bounding-box references.
- `Assembly(parts, components, mates=())` validates the whole immutable graph.
- `evaluate_assembly(assembly, kernel, mesh=True)` returns per-instance BRep
  mass/bounds/mesh, local face references, world placements and every pair's
  true shared volume, minimum surface distance and nearest-point witnesses.
- `solve_mates(assembly, kernel, tolerance_nm=100, tolerance_rad=1e-7,
  max_iterations=60)` returns `(new_assembly, report)` and does not mutate input.
- `bom(assembly)` counts physical leaves by part/configuration/SHA, not groups.
- `save_assembly(assembly, path)` and `load_assembly(path)` round-trip exact
  references, placements, hierarchy and mates. Save fsyncs file and directory.

## Geometry and Solver Contract

Each distinct part is reconstructed independently from its own L1 document.
Each physical instance copies and rigidly transforms that BRep. The assembly
compound is not fused: independent identities and solid counts remain visible.
Collision uses real BRep `common`, contact `section`, then `distToShape`.
A pin inside a through-hole can have overlapping bounding boxes and still be
separated; shifting it into the cylindrical wall must report positive shared
volume. No current-solid translated-copy assembly shortcut is used.

Plane mates constrain directed normal alignment and signed normal separation;
axis mates constrain directed axis alignment and transverse separation.
Plane mates leave in-plane translation/spin free; coaxial axes leave axial
slide/spin free. The numerical solver uses finite-difference Jacobians,
damped least squares and bounded rotational increments. Final placements are
rounded to integer nm, their residuals checked again, and actual placed BReps
rebuilt. Reports expose residuals, local tangent Jacobian rank, variable count
and DoF; gauge freedom is not silently removed. `fixed` holds local placement,
so fixed descendants still follow moving parents.

Contradictory constraints raise `mate_solve_failed` with measured residuals.
Wholly redundant mates raise `mate_overconstrained`; overlapping orientation
equations in the ordinary axis-plus-plane locating joint are permitted.
Rigid subassembly solving uses the nearest non-fixed ancestor as each leaf's
motion frame (or the fixed world if there is none). A plane/axis mate whose
endpoints share that frame is an internal validation constraint: moving their
common parent cannot change their relative geometry. Its actual analytic BRep
residual must meet tolerance before solving external constraints, and every
internal and external residual is checked again after integer-nm rounding.
An inconsistent internal mate fails immediately with measured residuals and
the active Jacobian rank; it is never repaired by moving a fixed local child.
Reports expose `rigid_mates` with their `motion_frame_id`, `active_mate_ids`,
signed plane residuals and transverse axis residuals. Internal validation
does not consume or invent parent DoF. Active constraints still use a measured
finite-difference Jacobian, including independent flexible children.

Duplicate declarations still fail, including endpoint-swapped declarations.
For a plane mate, swapping endpoints negates `offset_nm` when `aligned=True`
and preserves it when `aligned=False`; this follows the directed normal
distance, not an unordered absolute distance. Both variants have independent
nonzero-offset, rotated-BRep acceptance and wrong-sign rejection controls.
This is a bounded local numerical solver, not a global nonlinear existence
proof or an industrial complete mate catalog. Face indices are stable only
within the referenced immutable document SHA. A revised part requires explicit
new references and reassessment; there is no automatic topological naming.

## Bounds and Verification

Maximum 32 referenced parts, 32 physical instances, 64 hierarchy nodes,
8 hierarchy levels, 64 mates, 12 moving nodes, 100 solver iterations.
Per document: 2 MiB, 128 features, 2048 sketch entities.
Whole assembly: 8 MiB; coordinates: absolute 1e12 nm.
Proper rotations reject scale, reflection, nonfinite numbers and boolean
lengths. Worker bounds analytic face inventory and mesh triangle counts;
the existing subprocess timeout fails loudly and is never a success criterion.

Run the real acceptance from this independent worktree:

```bash
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_real_assembly.py
```

The direct entry saves source SHA before/after, test log, raw FreeCAD jobs and
results, actual exit codes/stdout/stderr, artifact SHA, numeric mate reports and saved/reopened assembly under
`/hf3fs-jd/data/tmp/opencad-assembly-20261011/`. Discovery runs the same tests
without mocks or skips. Required cases include independent pin/hole vs wall
collision, two-level rotations, numeric axis/plane residual and DoF, conflicting
and redundant mates, actual surface-type rejection, BOM configurations,
immutable SHA rejection, bounded inputs and save/reopen BRep equivalence.
Pass `--discovery` to verify discovery of the identical real tests and record
that result in a distinct evidence directory.

## Rigid Subassembly Scale Acceptance

The scale entry selects new cases only and stops at the first failure:

```bash
sudo -n -u infra -B python3 -B tools/assembly_scale_acceptance.py --scope all --out /hf3fs-jd/data/tmp/opencad-assembly-scale-new-run
sudo -n -u infra -B python3 -B tools/assembly_scale_acceptance.py --stage regression --scope all --out /hf3fs-jd/data/tmp/opencad-assembly-regression-new-run
```

It binds the actual worktree HEAD, every source byte, loaded CAD origins,
selected/executed counts, original child process exits, timestamps, raw FreeCAD
jobs and artifact hashes. A fresh local runtime child under `/data/tmp/` hosts
only tool-created fixtures. No dependencies, production services or existing
user data are changed. Failure evidence is retained rather than overwritten.

The new model has eleven physical BReps from four distinct immutable L1
sources, two bush configurations with repeated references, six hierarchy
levels and non-coaxial Z/X/Y rotations. Eight internal rigid mates are
validated while two external locating mates solve the movable carriage.
All 55 physical pairs use actual BRep collision; four through-hole shaft
pairs remain separated, and a displaced shaft penetrates an actual wall.
World transforms are independently composed for every instance, face
inventories remain source-bound, BOM quantities sum to eleven, and exact
save/load rebuild agrees. Additional cases cover flexible children, all-fixed
and empty-mate systems, conflicting external signed residuals, rigid internal
inconsistency and duplicate constraints. This implements rigid solve semantics,
not merely a higher fixture count or a larger configured limit.

The existing `industrial_assembly.prepare_assembly` automatically applies
this solver to `reopen`, `solve` and `checkpoint` candidates. These continue
through the existing generic assembly route without a new action or schema.
New Web tests verify pure candidates before confirmation, sealed solver
reports, real local SQLite confirmation/restart, exact configuration/BOM/source
reopen and rejected-input workspace/head invariance. They do not replace
the chair's real gateway/authentication acceptance. The baseline already has
Web integration; the prior statement that HTTP/CAS was absent was obsolete.

Bounds are unchanged. Cross-revision topological naming, automatic
configuration evaluation, additional mate types, global solution completeness,
UI face picking, manufacturing BOM and representative large-assembly
performance remain unfinished. Eleven-instance correctness is not industrial
full-function parity or large-assembly performance evidence.
