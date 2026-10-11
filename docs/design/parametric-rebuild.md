# Parametric Rebuild

This is a measured, single-document rebuild boundary for the Web gateway.
It is not an industrial CAD parity claim or a production transaction manager.
Only the three owned new files implement this change; shared document, server,
sketch solver and feature replay modules remain unchanged.

## API

```python
from cadcore.parametric import prepare_rebuild, document_sha256

prepared = prepare_rebuild(
    document,
    kernel=kernel,
    configuration="wide",
    overrides={"width": "60 * mm"},
)
prepared.verify_integrity()
```

`document` must be a real `cadcore.docmodel.Document` isolated from concurrent
writers. `kernel` must be `cadcore.kernel.subproc.SubprocessKernel` with a
working FreeCAD backend. Missing backend or a failed rebuild is an error.
Neither argument is replaced by a mock numeric adapter.

`PreparedRebuild` contains:

- `document` and `l1`: the candidate and its canonical UTF-8 L1 text.
- `base_document_sha256`: SHA-256 of the exact input L1, before any candidate changes.
- `document_sha256`: SHA-256 of the returned L1 after solving and reader normalization.
- `shape` and `recipe`: the replayable shape and complete JSON recipe.
- `mesh`: actual backend vertices, triangles, mass properties and solid count,
  bound to the candidate through `document_sha256`, `recipe_sha256` and `mesh_sha256`.
- `parameters`: exact `Fraction` quantities, dependency order, configuration and overrides.
- `sketch_reports`: measured convergence, DoF, residual in nm and iteration count.
- `feature_order`: every live feature UUID in stored replay order.

The wrapper is frozen, but its nested Document/mesh are mutable. Call
`verify_integrity()` immediately before committing; do not expose the candidate
for concurrent writes. It detects changed Document, L1, recipe or measured mesh.
Fingerprints bind content, not an adversarial process capable of rewriting
trusted Python objects and recomputing all fingerprints.

## Persisted Specification

The specification lives at `Document.extra["parametric"]`, which serializes to
the L1 root `parametric` field. Version 1 permits exactly these fields:
`version`, `parameters`, `configurations`, `bindings`, `checks`,
`geometry_guards`, `active_configuration`, `active_overrides`.

```json
{
  "version": 1,
  "parameters": {
    "width": {"expression": "40 * mm", "unit": "length", "positive": true},
    "height": {"expression": "20 * mm", "unit": "length", "positive": true},
    "hole_x": {"expression": "width / 2", "unit": "length", "positive": true},
    "radius": {"expression": "width / 10", "unit": "length", "positive": true},
    "depth": {"expression": "width / 5", "unit": "length", "positive": true}
  },
  "configurations": {"wide": {"width": "60 * mm"}}
}
```

This excerpt is not a complete rebuild specification: it additionally needs
nonempty `bindings` resolving existing document UUIDs. Binding shapes are:

```python
{
    "plate_width": {
        "kind": "dimension",
        "sketch_uuid": existing_sketch_uuid,
        "target_uuid": existing_driving_dimension_uuid,
        "expression": "width",
    },
    "boss_depth": {
        "kind": "feature",
        "target_uuid": existing_feature_uuid,
        "field": "distance_nm",
        "expression": "depth",
    },
}
```

Dimension bindings update existing `linear`, `radial` or `diameter` driving
dimensions through the real sketch command; reference dimensions are rejected.
Feature bindings update an existing scalar integer `_nm` parameter. Unknown,
ambiguous or duplicate targets fail. Geometry coordinates are not directly
assigned by bindings.

Semantic keys are stable application identifiers backed by stored sketch,
dimension and feature UUIDs. Deleted/replaced UUIDs fail explicitly; keys do
not infer replacement geometry. No binding promises persistent B-Rep face/edge
identity after arbitrary topology changes. Feature history is replayed in its
stored order; the parameter DAG is topologically sorted, not the feature tree.

`checks` is a list of `{key, left, relation, right}` with relations
`>`, `>=`, `<`, `<=`, `==`; both expressions must have the same dimension.
`geometry_guards` maps semantic keys to:

```python
{
    "kind": "circle_in_rectangle",
    "rectangle_sketch_uuid": plate_sketch_uuid,
    "circle_sketch_uuid": hole_sketch_uuid,
    "circle_uuid": hole_entity_uuid,
    "clearance": "0 * mm",
}
```

This guard checks the solved coordinates: exactly four nonconstruction,
axis-aligned XY lines making a rectangle and an XY nonconstruction circle.
The circle must have strictly more than the requested nonnegative clearance
to every side. It rejects an outside/no-op hole rather than relying only on
validity of the final cut. Other profile containment types are not implemented.

## Expressions And Configurations

Only numeric decimal literals, names, parentheses and `+ - * /` are accepted.
Calls, attributes, indexing, containers, booleans, comprehensions and powers
are rejected by an AST whitelist. No `eval` is used.
Units are `nm`, `um`, `mm`, `cm`, `m`, `inch`; bare numbers are dimensionless.
Lengths resolve exactly to integer nm; implicit units and sub-nm rounding are
not supported. Addition and comparisons require matching dimensions.
Multiplication/division propagate dimensions, with intermediate length power
bounded to +/-3 and quantity magnitude bounded to `2**53`.

Resource boundaries: 128 parameters, 32 configurations, 256 bindings,
256 checks/guards, 2048 expression characters, 256 AST nodes, depth 64,
256-bit rational numerator/denominator, and decimal literal precision/exponent
at most 77. Exponents are checked before constructing a Fraction.
Cycles and unknown names fail before changing the candidate. Syntax/name
validation covers all declared configurations; numeric evaluation and cycle
detection apply to the selected effective graph.

`configuration=None` retains persisted active configuration and overrides.
An explicit configuration switches configurations and resets prior overrides.
`configuration=""` selects base parameters and resets overrides.
`overrides` maps declared names to expression strings and wins over selected
configuration values. New overrides are persisted in the candidate; they are
not written into the original parameter definitions.

## Rebuild And Commit Boundary

The implementation deep-copies the isolated Document, evaluates the entire
parameter DAG, updates driving dimensions/feature parameters, solves every
constrained sketch, checks guards, normalizes through the actual L1 reader,
then replays the whole live feature history and tessellates the real B-Rep.
The solver tolerance is `0.01 nm`; integer geometry that cannot satisfy this
strict boundary is rejected, not presented as an exact dimension-driven rebuild.
Unconstrained sketches are reported as not measured for solving, not DoF zero.
Solver iteration cost is returned in reports but removed from persisted
`sketch.solved` so repeat rebuilds do not change docSHA just from execution cost.
Reader extension diagnostics are preserved, not suppressed; shared schema
registration is a separate integration concern.

Real `shape_from_document()` is lazy: calling it alone is not acceptance.
Preparation calls `tessellate()` to construct the full B-Rep and require valid,
positive-volume solids with nonempty finite vertices and in-range triangle
indices. The returned mesh is tied to the exact L1 and recipe.
The dedicated regression additionally measures mass properties, independently
integrates the mesh, and exports/reimports STEP.

Gateway integration must:

1. Authorize the project/user and capture a deep Document snapshot under that
   project's lock. Never pass a live shared Document to the unlocked rebuild.
2. Prepare outside the project lock using that immutable snapshot.
3. Reacquire the same lock, verify candidate integrity and compare current
   canonical docSHA with `base_document_sha256`. A late edit returns conflict
   and is not overwritten.
4. Use the gateway's durable transaction to install the candidate L1, its mesh
   cache and exactly one history entry together. Do not call the old per-feature
   edit/rebuild sequence. Derive the public response from this committed state.

The module itself never writes caller Workspace/cache/history. Failed parameter,
cycle, guard, solver or B-Rep preparation leaves the original Document unchanged;
kernel scratch jobs intentionally remain external evidence. Production atomic
durability, project authorization, concurrent HTTP editing and undo/redo require
gateway integration acceptance and are not claimed by the isolated test holder.
Preserve exception chaining: B-Rep failures carry `brep_rebuild_failed`,
`kernel_reason` and the actual backend failure as `__cause__`; sketch validation
can raise the existing `SketchError` and must not become a generic success.

## Acceptance

The real fixture uses existing sketch commands/constraints and
`sw_features.extrude_boss` plus `extrude_cut`, not handmade numeric outputs.
It drives width 40 -> 60 mm, hole center x 20 -> 30 mm, radius 4 -> 6 mm and
both boss/cut depth 8 -> 12 mm. Both sketches solve to DoF 0. Analytic volume is
`(width * height - pi * radius**2) * depth`; B-Rep tolerance is `1e-5 mm3`.
Mesh volume is independently integrated from real triangles with a closed
edge-incidence check and a 0.5% tessellation allowance. STEP roundtrip uses a
fresh real import feature. Reopening/rebuilding retains exact candidate docSHA.
Negative dimensions, cycles, outside holes, conflicting constraints, oversized
real fillet, missing/duplicate bindings, candidate tampering and late CAS edits
are rejection cases.

From any isolated checkout:

```bash
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_parametric.py --acceptance-smoke
```

Each run writes a unique timestamp directory under
`/hf3fs-jd/data/tmp/opencad-parametric-20261011/seat-parametric/`, including
`tests.log`, `result.json`, exact source hashes, candidate L1s, STEP and kernel
job results. `--acceptance-smoke` also runs this checkout's existing independent
acceptance entry point and preserves its result/console log. Missing FreeCAD
fails rather than skipping. Full repository and browser/Web integration gates
are separate; this command does not claim them.

The bootstrap `cce5335` independent smoke has a separately retained FAIL at
`/hf3fs-jd/data/tmp/opencad-parametric-20261011/seat-parametric/20261011T022132792795Z/acceptance-smoke/result.json`.
Its test legs have zero failures/errors (128 unittest cases: 127 passed, one
existing store skip; real HTTP selftest also passed). Its outer source-drift
gate correctly rejects a newly generated
`oss-alternatives/apps/opencad/build/opencad.dxf`: the old `server.selftest()`
calls `build_server()` without `out_root`, bypassing the acceptance process's
external output-root environment. This seat does not change shared server or
waive the drift gate. Do not rerun that affected smoke until the chair fixes
and independently verifies its output-root boundary. For just the owned
parametric suite, omit `--acceptance-smoke`; its verdict does not stand for
the independent smoke or full/Web acceptance. The initial 23-case two-failure
run at `20261011T014644764248Z` and one-failure run at
`20261011T014841904349Z` remain evidence; later passes do not rewrite them.

Not implemented/accepted here: arbitrary cross-sketch geometry dependency
constraints, face-attached sketches, angle/vector/array parameter bindings,
feature suppression configurations, general persistent topology naming,
incremental partial rebuild, advanced surfaces/assemblies, large-model solver
scaling, production crash-atomic commit and actual Web editing/undo interaction.
