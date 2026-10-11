# Industrial Assembly Web Adapter

## Owned Surface

The adapter, panel JS/CSS, independent test and this document are the only new
files. `secure_web`, `project_gateway`, `project_store`, `server`, docmodel,
catalogs and the frozen assembly/worker remain unchanged. No production
requests, client-side installation, shared-state writes or automatic export.

## Dispatch Contract

`GET /api/assembly?action=state|mesh|collisions|bom|export` calls
`read_assembly(workspace, {"action": action})`.

`POST /api/assembly` accepts one of these complete operation payloads plus the
gateway's `expected_revision`, `expected_sha`, `request_id`:

| Action | Operation Fields |
| --- | --- |
| `import_part` | `part`, `component` |
| `put_component` | `component` |
| `put_mate` | `mate` |
| `remove_component` | `component_id` |
| `remove_mate` | `mate_id` |
| `solve`, `checkpoint` | none |
| `reopen` | `assembly_json` |

`part` is `PartReference.as_dict()` with exact uploaded `l1_text`, SHA256, ID and
configuration, not a server path or a translated copy of the current document.
`component` and `mate` use the frozen core schemas. Local row-major proper 3x3
rotation matrices act on column vectors; translation/offset are integer nm.
Plane/cylinder face references are zero-based within the immutable part SHA.

`prepare_assembly(workspace, body, *, kernel=None)` returns
`AssemblyCandidate(before_document_sha256, doc, response)`. It never mutates
workspace/history or commits storage. Standalone Documents require an explicit
kernel; gateway workspaces use their isolated external `out_root`.

The chair registers the route, authenticates/authorizes the project, validates
fields and checks receipts **before** executing geometry. Then check the
expected revision/SHA against the durable head, construct an isolated workspace,
prepare the candidate, install `document_from_l1(candidate.doc)` into that
private workspace, append the previous L1 to undo and clear redo (unless an
identical checkpoint), validate the complete snapshot/history, reauthorize and
call `ProjectStore.commit` with the complete request payload and candidate
response. Return success plus `project_id/revision/sha256` **only after** commit.
A repeated request must replay the existing receipt without rebuilding or
appending history; stale writes must return 409 without altering the head.

`document_sha256` hashes the exact current L1; `assembly_sha256` hashes canonical
assembly JSON; gateway `sha256` hashes the full workspace snapshot. These three
hash domains must not be interchanged. Saved assembly lives in the L1 root
extension `industrial_assembly_v1` (via `Document.extra`), so the existing
project snapshot/reopen/undo mechanism persists it without a parallel database.
The docmodel product-extension registry is chair-owned integration work; unknown
extensions already roundtrip but may have informational diagnostics. The
candidate is normalized through the existing loader and explicitly checked for
an exact second roundtrip before its document SHA is emitted. The evaluation
also carries both document/assembly SHA values; placed mesh instances carry
their immutable part SHA.

## Browser Mount

Load the panel CSS and JS, then
`window.opencadAssembly.mount(element, {transport, onCommitted, labels})`.
`transport({method, path, body})` must return a rejected Promise on HTTP/JSON
failure, and include current project/CSRF headers. POST must attach the current
CAS base and a stable request ID, handle receipts and resolve only on durable
commit. The panel checks revision metadata and document/assembly hashes before
accepting responses, queues its own reads/writes, disables controls while busy
and calls `onCommitted(response)` for the chair's viewer refresh.
The return value exposes `ready`, `refresh()`, `command(body)`, `select(id)`,
`state`, `busy`, `error`, `triangles` and `destroy()`. Failed `ready`, refresh or
command Promises still reject; the visible error is also preserved. Component
and mate JSON forms use the frozen core schema, including parent IDs and
non-translation rotations, and translation input converts mm to bounded nm.

The existing project-session fetch adapter can supply headers and CAS fields,
but the chair must coordinate its writes with other editing panels; panel-local
serialization is not a cross-panel transaction lock. No speculative retry or
automatic rebase occurs after a stale-version error. Public `refresh()` uses GET
only. Explicit save calls the durable checkpoint operation; explicit download
creates a client assembly JSON file; reopen uploads that file and prepares a
new project revision. Nothing is exported or read from server paths implicitly.
Gateway maximum request bytes must accommodate a bounded assembly import plus
its CAS envelope; the adapter rejects assembly JSON above 8 MiB and parts above
2 MiB, but does not change the chair-owned transport's body cap.

UI supports independent L1 files, hierarchy/local poses, plane/axis mates,
actual measured collision/BOM/face inventory and a WebGL view of returned placed
BRep triangle meshes. Selection chooses a component for editing, not a claimed
industrial assembly-picking subsystem. Labels can be provided by the chair's
catalog; the default panel is simplified Chinese with native part identifiers.

## Limits

Core bounds remain 32 independent parts/physical leaves, 64 nodes, depth 8,
64 mates, 12 moving nodes, 2 MiB per part, 8 MiB assembly and 500k total mesh
triangles (250k per instance). Every modified assembly with mates must solve before a
candidate is returned; redundant or inconsistent constraints fail loudly.
Immutable part/configuration IDs cannot be rebound to different bytes.
Removing a referenced parent/component fails rather than cascading silently.

No LOD, GPU instancing, robust topological naming, arbitrary surface mates,
large-assembly performance promise, server RTX rendering or industrial parity
is inferred from this bounded Web demonstration. The client's WebGL renderer
and the server's FreeCAD BRep evaluator are separate execution boundaries.
Curved-surface BRep bounding boxes under rotated placements can contain small
kernel approximation differences; the exact two-level rotation test uses a
rectangular prism, while hole/contact/interference tests use actual cylinders.
The panel displays the measured values rather than an analytic bbox substitute.

## Reproducible Acceptance

From the assembly worktree:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_industrial_assembly.py --discovery --gateway-root /data/dev/opencad
```

Default gateway source is this repository if `project_store.py` exists; the
older standalone seat otherwise reads the chair's store from `/data/dev/opencad`
without copying it. `--gateway-root` selects an explicit read-only source.
Evidence defaults to a fresh timestamp under
`/hf3fs-jd/data/tmp/opencad-industrial-assembly-20261011/`.
The same 19 real tests run under unittest discovery without mocks or skips.
The report fingerprints owned files, core worker dependencies and the selected
ProjectStore source before/after. Raw FreeCAD jobs/process exits, HTTP results
and browser desktop/narrow screenshots are retained.

Coverage includes independent file-input submits, exact prism rotation,
cylindrical through-hole vs wall interference, plane/axis residual and DoF,
contradictory/redundant mates, immutable part rejection, stable candidate bytes,
real SQLite reopen/dual-client CAS, real HTTP duplicate receipts/stale writes,
actual pose/mate/save/reopen UI, an 850ms delayed serialized pair, rejected error
Promises and document/assembly/mesh SHA and old-revision negative controls.
The browser helper deliberately uses SwiftShader: this verifies Web rendering,
not NVIDIA hardware or performance. Authentication and chair route integration
are reported as unassessed/not integrated rather than inferred from a fixture.
