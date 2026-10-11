# Private Surface Downloads

## Adapter Contract

`industrial_surfaces.export_surfaces(workspace, {"format": "step" | "brep"})`
returns a frozen `SurfaceExport` with immutable `data: bytes`,
`content_type: str` and read-only `headers`. No output path is accepted or
returned. Only the complete captured saved recipe is exported; unsaved browser
recipe text is not submitted by download.

Headers include:

- `X-OpenCAD-Document-SHA256`: captured canonical L1 document bytes.
- `X-OpenCAD-Artifact-SHA256`: exactly the returned bytes.
- `X-OpenCAD-Surface-Recipe-SHA256`: canonical persisted recipe.
- `Content-Disposition`: fixed ASCII `surface.step` or `surface.brep`.

STEP uses `application/step`; BRep uses `application/octet-stream`.
These document/recipe hashes are not project revision or snapshot hashes.
The gateway owns authentication, GET-only route admission, rejection of every
query field and final `_current(head, principal)` authorization/head check.
The agreed routes are `/api/surfaces/step` and `/api/surfaces/brep`.
After that check it supplies project/revision/snapshot version metadata.
This adapter does not change the gateway, session transport or shared kernel.

## Exact Geometry And Failure

A missing saved surface returns 404. Invalid formats or extra input fields
return 400. Part features, nonempty sketches or assembly namespaces combined
with a surface return `unsupported_geometry_domain`/422 before a worker starts.
An entire persisted surface recipe, including concave outer UV loops, holes
and thickening, is replayed by the existing real isolated FreeCAD worker.
Its BRep and STEP are independently reimported by that worker, with topology,
closed wires, area and applicable volume checked. The exported shape is not
reconstructed from a mesh.

The adapter requires successful measured worker exit and the exact two kernel
source hashes before/after. A fixed artifact child is opened with held parent
and job directory descriptors and `O_NOFOLLOW`; no client-provided filename
is involved. Regular/nonempty bounded size, actual length and SHA are checked
on the single byte buffer that is returned. Kernel source or workspace document
drift refuses export. Workspaces, undo/redo stacks and model caches are not
published or changed. Job logs and failed artifacts remain in the caller's
external runtime; public failures expose safe reason/first-cause text, not
artifact paths or backend tracebacks. The exception chain preserves the original
backend failure for server-side diagnostics.

The 64 MiB artifact limit is a controlled download limit, not scale acceptance.
Concurrent project mutation or account revocation after capture is specifically
the gateway's final-read responsibility, not solved by this adapter's local
workspace comparison.

## Verification Boundary

The owned `test_industrial_surfaces.py` includes real reference HTTP exports,
frozen receipt checks, concave dual-hole open surfaces and 2 mm thickened
solids, canonical save/reopen, and separate FreeCAD BRep/STEP file readbacks.
Readbacks verify exact downloaded-byte SHA, topology, area 50 mm2, solid
volume 100 mm3 and solid inside/outside hole witnesses.

Negative controls include format/empty/mixed-domain refusal before build,
postbuild missing/tampered/symlink artifacts, bad worker source maps,
source/document drift and a real focal-crossing kernel failure. Postbuild
mutations wrap an actual FreeCAD build; they are not mock geometry acceptance.
Original failure evidence is retained.

Desktop and 390-pixel browser controls use real mouse clicks and typed recipe
drafts, save both downloaded files and independently read them. They require
the same editor node/draft and unchanged workspace after downloads. The panel
checks the response artifact SHA before producing the download.

Run with a fresh external evidence parent:

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-surface-download-20261012/oss-alternatives/apps/opencad/tests/test_industrial_surfaces.py --evidence-dir /hf3fs-jd/data/tmp/opencad-surface-download-20261012-v1
```

There are no dependency skips or geometry fallbacks. Missing real dependencies
are failures. This reference HTTP/browser fixture is not private gateway
authorization acceptance, production deployment, GPU performance, industrial
parity or arbitrary-size STEP support. Integration needs fresh gateway security,
head-race and durable private-project acceptance after the chair's source freeze.
