# Private Surface Download Boundary

GET `/api/surfaces/step` and `/api/surfaces/brep` consume the selected private
project's complete saved surface recipe. The surface adapter rebuilds exact
BRep geometry and returns artifact bytes, never a user-supplied path or mesh
substitution. The existing part STEP route retains its domain boundary.

Authentication and project ownership precede request workspace construction.
Each route accepts no query fields. POST is refused; downloads cannot create
revision/history/receipt records. Before returning bytes the gateway verifies
the document and artifact SHA256 and rechecks authorization and current
revision/snapshot SHA. A late edit returns conflict; a revoked session returns
an authentication error, not old artifact bytes.

Headers bind the captured document, downloaded artifact, authoritative project
revision and complete snapshot SHA. `X-OpenCAD-Model-Version` uses the same
authoritative revision as `X-OpenCAD-Revision`, consistent with private drawing
exports; it is not a client-provided recipe version. Fixed ASCII attachment filenames and
no-store caching avoid leaking request runtime paths. Empty or mixed-domain
models, invalid formats, malformed recipes and backend failures remain errors.
This is not general part/surface fusion, assembly export or industrial parity.

`tests/test_project_gateway.py` adds real private HTTP exports of the concave
two-hole surface and its thickened solid. It compares all four project tables
before/after each read and tests anonymous/forged/other-owner denials, strict
queries, methods and domains. Controlled wrappers still execute the real
export, then perform a real late save or session revocation before reply.
An independent byte-corruption negative control changes actual completed STEP
and BRep exports and checks the artifact binding guard. A separate real recipe
edit changes an open surface into a thickened solid after export; the old
download must fail without altering the new document, undo entry or edit
receipt. Separate FreeCAD artifact readback is required for geometric acceptance.

Run the frozen integrated source through `tools/acceptance.py --stage all`,
using a fresh external evidence directory and local runtime. Before that
integration the gateway branch depends on the separately reviewed
`industrial_surfaces.export_surfaces` adapter; it is not independently a
complete deployable feature. New source must not inherit an earlier PASS.
