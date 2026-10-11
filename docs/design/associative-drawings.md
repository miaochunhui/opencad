# Associative BRep Drawings

## Contract

The module is `cadcore.draw.associative`. Public entry points:

```python
rebuild_drawing(document, *, model_version, spec, kernel=None, work_dir=...)
synchronize_drawing(previous, document, *, model_version, spec=None,
                    kernel=None, work_dir=...)
assert_current_drawing(snapshot, document, *, model_version)
export_drawing(snapshot, *, document, model_version, out_dir)
readback_export(snapshot, *, export_dir)
```

`DrawingSnapshot` is immutable canonical JSON. `to_dict()` returns a fresh
copy. The source SHA is SHA-256 of `document_to_l1(document)` UTF-8 bytes,
not an arbitrary client SHA. Each snapshot binds document SHA, model version,
spec SHA, worker SHA, producer SHA, composed kernel adapter SHA and the actual
job STEP bytes. Synchronization rejects a regressed version or changed document
without a newer version. Same-source reuse also checks drawing/adapter code.
Export requires the current document/version and refuses stale snapshots before
creating output. These hashes establish consistency, not cryptographic trust
against a malicious owner of the server filesystem.

The module never modifies the input document or previous snapshot. Consumers
must publish only a successful returned snapshot and recheck their authoritative
revision under their workspace commit lock. This module alone does not install
an event listener, implement server CAS, or publish a Web route. The Web owner
must invoke synchronization after changes and before current drawing reads.
Keep the drawing cache outside the source model payload, or changing drawing
metadata will deliberately change the source document SHA.

Example specification:

```json
{
  "title": "Hole plate",
  "views": ["top", "front", "right", "iso"],
  "sections": [
    {"name": "section-A", "axis": "y", "offset_nm": 40000000,
     "keep": "positive"}
  ],
  "dimensions": [
    {"id": "width", "ref": ["hole-cut", "extent:x"], "kind": "extent",
     "axis": "x", "view": "top"},
    {"id": "hole", "ref": ["hole-circle", "diameter"], "kind": "diameter",
     "view": "top"}
  ],
  "bom_refs": [["document-uuid", "model"]]
}
```

Unknown fields, including file paths, are rejected. Coordinates are integer
nanometres. Printable ASCII text is the explicit initial export profile;
unsupported text fails rather than dropping glyphs. Model version is a
nonnegative integer issued by the integrating workspace, not a client counter.
Document canonical UTF-8 is limited to 16 MiB. The generated STEP worker input
is independently limited to 64 MiB.

## Real Geometry

The existing subprocess kernel replays the full L1 feature sequence and exports
a newly generated STEP in a fresh external job directory. A second FreeCAD
process reads only its fixed `model.step`, checks the digest, validity, positive
volume and exactly one solid. It never reads a requested user filename.

`Drawing.projectEx` is the required installed FreeCAD exact BRep HLR interface.
Its local API documents ten buckets `[V,V1,VN,VO,VI,H,H1,HN,HO,HI]`. All five
visible and five hidden buckets are classified; no mesh visibility test,
outline-only fallback, or arbitrary wire substitute exists. The BRep is rotated
to an explicit view basis before HLR. Curved HLR edges are sampled only for
rendering after exact visibility classification; full circles retain exact
center/radius. Polyline rendering is not a claim of exact spline export.

Principal sections intersect the solid with an actual plane to obtain material
faces, area and wires, including hole gaps. The retained half-space is another
real solid and gets its own HLR. Material profile lines have a separate SECTION
layer. No silhouette fill is called a material hatch; automatic standard hatch
patterns are not implemented.

Extent references name the final active feature and axis. Values come from the
real BRep bounds and aligned real BRep vertex witnesses; shapes without such
witnesses fail this limited dimension profile. Circle references name an active,
nonconstruction XY sketch circle. Radius, axis and center line must match real
closed BRep circle edges. Diameter witnesses use the measured circle, not an
unverified sketch value. This is semantic-reference rebuilding, not a general
stable topological-naming solution.

BOM references resolve a real current document (`model`) or a live component
UUID (`component`). Each selected instance contributes quantity one. Component
part keys are preserved; external part geometry, material and mass remain
UNMEASURED. Nested assemblies and dangling/suppressed references are rejected.
The table does not pretend that a part name is verified external BRep geometry.

## Export And Acceptance

PDF is generated with reportlab. Poppler independently extracts the actual
body and renders actual pixel bytes. The readback checks source/version text,
dimensions/BOM and raster coverage at measured geometry primitives, including
hidden and section layers. A text-only PDF cannot pass by supplying a magic
header or nonwhite text pixels.

DXF uses explicit LINE/CIRCLE/TEXT geometry, millimetre units and layers with
actual dashed hidden line definitions. The independent repository DXF reader
parses entities, types, layers, coordinates, radii and body text; unchanged
read/write must return identical bytes. Dimensions are measured witness lines
and labels, not native editable CAD DIMENSION objects. Curves other than full
circles are discretized rendering geometry.

Outputs require a new external directory. Rebuild/export failures preserve
the previous snapshot and keep failure logs. Partial failed export directories
are evidence, never a successful publication. Runtime cleanup/retention belongs
to the service owner and is not performed here.

Real tests run with FreeCAD, Poppler, reportlab, Pillow and the repository DXF
reader; missing dependencies fail, without mock/skip:

```bash
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_associative_drawings.py --out /hf3fs-jd/data/tmp/opencad-associative-drawings-20261011/fresh-owned-run
```

The direct runner saves source hashes, drift, test counts and logs. Normal
unittest discovery builds the same real fixtures. Use `tools/acceptance.py`
for independent repository checks; its broad legacy infra coupling is not
silently waived by this new feature test.

The plate workflow changes width 120->140 mm, hole 10->16 mm, thickness 6->8 mm;
checks analytic volume, principal bounds, exact section material area,
hidden-line positions and measured dimensions, then exports both versions.
Negative controls reject stale versions/references, boundary sections, changed
STEP digests, linked external STEP files, bad STEP bodies, corrupted DXF,
PDF text-only geometry, and tampered snapshots.

GB/ISO conformity is NOT_ASSESSED. No paid standard text is copied. Complete
GD&T/MBD, PMI, standards-compliant drawing composition, tolerances, arbitrary
section orientations, large assemblies and full industrial parity are not
implemented or claimed. Web/secure gateway integration is a separate change;
these module tests do not establish production authorization or multi-tenancy.
