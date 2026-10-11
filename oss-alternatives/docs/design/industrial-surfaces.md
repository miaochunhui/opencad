# Exact UV Polygon Trim Loops

This extension belongs to the isolated surface kernel and generic Web surface
recipe. It does not claim guide lofts, full industrial coverage or deployment.
The older rectangular-surface design remains at the repository-root
`docs/design/industrial-surfaces.md`; that file is outside this seat's explicit
`oss-alternatives/` write set and is not edited here.

## Recipe And Admission

The existing POST preparation request remains `{"recipe": recipe}`:

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

All fields `op/surface/outer/holes` are required; no unknown fields are allowed.
`bounds` and polygon fields are mutually exclusive. The existing
`make_trim(surface, u_min=..., u_max=..., v_min=..., v_max=...)` is unchanged;
the new form is `make_trim(surface, outer=..., holes=...)`, with holes defaulting
to an empty list only in this Python constructor, not in raw JSON.

UV coordinates are finite numbers, never booleans. Outer is explicitly closed
and counterclockwise; holes are explicitly closed and clockwise in UV.
Each ring has 3..128 distinct vertices plus closure; at most 16 holes and 512
points including closures are admitted. Points must remain within the current
finite NURBS/rectangular parent parameter range.
Exact binary-rational orientation/intersection/containment predicates reject
zero area, duplicate/backtracking edges, self-intersection, self-touch,
outer/hole touching or crossing, holes outside the outer and overlapping or
nested holes. Forward collinear vertices are allowed. There is no silent
epsilon snapping, orientation reversal, healing or fallback.
Nested polygon parents are deliberately rejected, for both rectangular and
polygon children, rather than rebuilding the support and resurrecting holes.

## Exact Topology

The real FreeCAD worker creates each edge with
`Part.Geom2d.Line2dSegment(Base.Vector2d(...), Base.Vector2d(...)).toShape(surface)`.
These are straight UV segments, generally curved in world coordinates.
The outer wire establishes `Part.Face(surface, outer_wire)`; hole wires are
added individually with `Part.Face(face, hole_wire)`.
The surface/list overload must not be substituted: on this installed API it
starts with the default rectangular face and adds extra wires.
No parent-shell projection, rectangular bounding-box cut or mesh boolean is
used. OCCT checks physical wire closure/validity, positive face area and exact
BSplineSurface support; every resulting edge must retain a curve-on-surface.
Features below OCCT physical tolerances fail visibly even when UV predicates
admit their mathematically nonzero area.

Evaluation uses the actual trimmed Face domain, not its UV bounding rectangle:
interior holes and concave excluded regions reject evaluation. Classification
at/near a physical boundary still uses OCCT's numerical tolerance.
Existing G0/G1/G2 rectangular edge APIs, derivatives, witnesses and tolerances
remain unchanged; they do not become arbitrary polygon-edge continuity APIs.
Offset/thicken regularity checks skip excluded UV samples and fail if no samples
remain. They are sampled checks, never all-domain proofs.

Properties expose aggregate `inner_wire_count` and per-face `wire_count`,
`inner_wire_count` and `wires_closed`. Real BRep and STEP readbacks must preserve
face/solid counts, sorted per-face closed-hole counts, area and solid volume.
Independent review additionally checks world-coordinate hole/notch witnesses
on reloaded geometry, including through-hole interior points after both inward
and outward thickening, and holes after sewing. Tessellation is output only.

## Web And Persistence

`industrial_surfaces.prepare_surfaces` already accepts a generic validated
recipe and returns a nonpublishing sealed candidate. No new namespace or schema
is introduced: `doc.extra["x_industrial_surfaces"]` stays schema 1.
GET rebuilds from the captured stored recipe and reports its complete-document
SHA with exact geometry and export digests; it does not edit histories/cache.
Save, undo/redo and a new Workspace reopening preserve canonical document bytes
and all loop fields. Malformed recipes reject before worker construction;
physical OCCT failure retains the original first cause and does not publish.
The existing Web recipe editor can submit this JSON unchanged.
Authentication, project versions, durable receipts/history and original CLI
gateway restart acceptance belong to the chair. This seat's HTTP reference
fixture is not that production gateway.

## Reproduction And Boundaries

Run as infra with Python bytecode disabled, in this independent worktree:

```bash
sudo -n -u infra python3 -B tools/trim_loop_review.py --out /hf3fs-jd/data/tmp/opencad-trim-loops-20261012/new-review --runtime-root /data/tmp/opencad-trim-loops-new-review
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_surfaces.py --repository-check --evidence-dir /hf3fs-jd/data/tmp/opencad-trim-loops-20261012/new-kernel
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_industrial_surfaces.py --repository-check --evidence-dir /hf3fs-jd/data/tmp/opencad-trim-loops-20261012/new-adapter
```

The review binds the actual whole-tree manifest and HEAD, retains fresh external
results/logs/BRep/STEP, and checks source drift. It measures the concave double-hole
10x10 mm plate against analytic 50 mm2 area, 2 mm bidirectional thickening against
100 mm3, a diagonal triangle against 32 mm2, sewn faces against 86 mm2, and exact
rational-cylinder support points/hole witnesses. A real rectangular replacement
is demonstrably detected by a hole witness.
Final verdicts/counts/timestamps come from that run's result and original exit,
not from this document. Missing dependencies fail rather than skip.

Preserved original evidence under the task parent includes the failed
`Base.Vector2d` API discovery, the earlier insufficient probe's false PASS
(area 196, invalid three-wire face), and the adapter-v1 failure at new test
reopen: its fixture out-root incorrectly excluded the saved file, and the
existing `path_rejected` guard correctly refused it. Corrected tests do not
weaken that guard; original results are immutable.

This slice supports arbitrary *simple polygonal UV loops* within the stated
budgets, not arbitrary curved UV edges, periodic/injective-surface proof,
nested polygon operations, persistent topological naming, general
self-intersecting offset repair, whole-domain continuity, guide lofts, large
industrial scale or complete CATIA/SolidWorks parity.
