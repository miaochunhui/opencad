# Web Parametric Candidate Contract

## Integration Boundary

This module builds real single-part geometry; it does not establish industrial
CAD parity, authorization or production multi-tenancy.

```python
read_parametric(workspace, body=None) -> dict
prepare_parametric(workspace, body) -> ParametricCandidate
```

Both functions leave the Workspace document, histories, cache and preferences
unchanged. `body=None` or `{}` is accepted for reading. Preparation requires
`action` (`create_plate` or `rebuild`) and `expected_document_sha256` (SHA256 of
the exact UTF-8 L1 before editing). Optional fields are `configuration`,
`overrides` and `dimensions`. Overrides map declared parameter names to
explicit-unit expressions. Dimensions map semantic binding keys to expressions
and are editable only when bound to a single declared parameter.

The frozen candidate exposes:

```python
candidate.before_document_sha256  # exact initial L1 fingerprint
candidate.doc                     # canonical L1 text, not a Document instance
candidate.response                # measured result, without a commit receipt
candidate.verify_integrity()      # document/artifact provenance and mesh checks
```

The chair-owned project gateway must:

1. Authorize the current project and validate outer `expected_revision`,
   `expected_sha`, and `request_id`. Strip those fields before calling prepare.
2. Prepare against the isolated Workspace for the authorized snapshot.
3. Verify candidate integrity and its before-document fingerprint, validate the
   candidate L1, append exactly one old-document history entry when changed and
   clear redo only on a change.
4. Commit the complete snapshot with the existing durable store CAS. Reauthorize
   at the commit boundary. Reject a late edit without installing the candidate.
5. Add committed `project_id`, `revision`, and `sha256` metadata and persist the
   idempotent response receipt. Only then return `candidate.response`.

`GET /api/parametric` returns the read response plus current project metadata.
`POST /api/parametric` uses the shared gateway CAS above. API errors use the
Workspace loader's `ApiError` class: malformed fields 400, stale state and
maintenance 409, rejected expressions/geometry 422, STEP filesystem failure
503. Error detail and exception chaining retain the first cause.

No route, gateway, shared server or generated 3D page is changed by this seat.
The compatibility `handler(workspace, payload)` returns a read response for
`None`, or a candidate for a POST body; it never commits.

## Real Geometry Workflow

Creation adds a dimension-driven 40x20 mm plate with radius-4 mm interior hole
and depth-8 mm boss/cut to a genuinely geometry-empty document. Existing project
metadata is retained. Existing entities, constraints, dimensions, annotations,
sheets, extra assets, diagnostics, non-XY sketches, additional sketches or
either history prevent creation. UUID datum lines replace the origin sentinel.

The `wide` configuration sets width to 60 mm; dependency expressions move the
hole center to 30 mm, increase radius to 6 mm and boss/cut depth to 12 mm.
The module clones, evaluates the bounded unit-aware DAG, solves all downstream
sketches, replays the actual feature recipe and measures a valid positive-volume
FreeCAD BRep and mesh. A full STEP export must pass the bounded Part 21 envelope
check before the candidate can be returned.

The shared L1 gate requires every `_nm` field to be an integer, including solver
diagnostics. The adapter preserves the real residual as `residual_mm` rather
than rounding it or weakening that gate. It then replays and remeasures the
final canonical L1, compares BRep properties with the original prepared geometry
and fingerprints only the final document/artifacts. Real `residual_nm`
measurements remain in the returned per-run sketch reports.

The response contains canonical `doc`, exact document fingerprints, parameters,
driving dimensions, solver reports, measured mesh and STEP byte-count/SHA
metadata. `/api/step` downloads are rebuilt from the committed same-version
document; generated STEP headers need not have identical bytes across exports.
The download's document-SHA header must be bound by the gateway. A STEP metadata
SHA is not a persistent asset locator and must not be presented as one.

Scratch directories are below the external Workspace out-root. The deployment
alias `/hf3fs-jd -> /3fs/research3fs` is accepted only with that exact canonical
target. Other/interior symlinks, missing roots and roots inside this source tree
are rejected. Successful scratch is removed; failures retain a `failure.log`,
complete traceback and kernel job records and return the evidence path.
The gateway should retain/copy request failure evidence before cleaning its
request directory if long-lived production diagnostics are required.

## Panel

Load `/web/css/parametric-panel.css` and `/web/js/parametric-panel.js` after the
chair-owned project session. Mount into `[data-parametric-host]` or the existing
3D page's `main > aside`. CSS is scoped to `.parametric-panel`.

The panel calls the current `window.fetch`, never a captured unauthenticated
fetch; the project session adds the outer base version and unique request ID.
It exposes configuration selection, expression inputs, UUID-semantic driving
dimensions, complete rebuild and re-read controls. It requires a committed
project receipt, same-version mesh and STEP metadata and refreshes the existing
real 3D viewer. Missing receipt, invalid radius or display version conflict is
visible and is never reported as a successful save.

Late background GET results cannot replace focused or dirty inputs. The
explicit re-read control discards pending inputs; normal background refresh
does not. A deterministic delayed-read browser regression covers this boundary.
The local result uses static positioning to avoid the shared viewer's absolute
`output` style obscuring the rebuild button. Local forms remain vertical at
360 px, without changing the shared viewer's mobile layout.

The panel does not lock other 3D commands or solve the chair-owned viewer race;
concurrent accepted edits are protected by the project's outer CAS and visible
version mismatch. Authentication, project routing, script/CSS inclusion and a
production save/reopen end-to-end run remain chair integration acceptance.

## Independent Acceptance

Run this checkout's test file with Python bytecode disabled:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_industrial_parametric.py
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_industrial_parametric.py --browser /home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome --discovery-check
```

The standalone runner retains exact source hashes, zero-drift checks, test logs,
real FreeCAD jobs, STEP download/roundtrip and browser screenshots under
`/hf3fs-jd/data/tmp/opencad-parametric-20261011/seat-web-parametric/`.
The HTTP fixture uses real Workspace/L1 plus an isolated durable snapshot and
models the shared CAS/idempotency contract; it is explicitly not production
authentication or the real project gateway. Browser interaction uses the real
panel, input/click events and existing real WebGL viewer on loopback.
Unittest discovery runs candidate/HTTP tests; the browser leg is explicit.
The full suite has 26 candidate/API tests plus one real-browser scenario; the
discovery check independently runs all 26 candidate/API tests without a browser
skip. Four browser negative controls reject missing receipts, wrong mesh/STEP
document versions and another project's receipt. They do not send mutations.

Scope excludes general persistent topological naming, cross-sketch constraint
graphs, assemblies, arbitrary surfacing and industrial CAD parity. Stored
semantic binding keys identify specific existing UUID targets, not enduring
BRep edge numbers. The earlier unrelated acceptance smoke source-output failure
is retained in the core design document and is not reclassified here.
