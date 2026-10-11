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

This module is not yet integrated into HTTP, project authorization, persistent
revision/CAS, L1 assembly fields, UI picking or manufacturing BOM export.
Small fixture success does not establish large-assembly performance.
