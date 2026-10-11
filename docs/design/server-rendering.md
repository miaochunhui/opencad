# Server Rendering Contract

Status: independent component accepted; chair auth/DB `/api/render` integration
**NOT_INTEGRATED by this seat**. This is server EGLDevice/GLES rendering, not
browser WebGL, WebGPU, SwiftShader or accelerated CAD geometry computation.

## Ownership and Runtime

Branch `codex/research-gpu-20261011`, worktree
`/data/dev/wt-opencad-research-gpu-20261011`, bootstrap base
`cce53354947cf696ebf2422dfb92c3e8eb3b1f78`.
Only the ten explicitly owned research/design/renderer/adapter/panel/test/probe
paths are changed. `server.py`, secure gateway, project store/session and viewer
main files are read-only dependencies. No production service, key, identity,
driver, dependencies or network entry point is changed. No push.

The user explicitly authorized local RTX3080 for this task, overriding the old
C2 prohibition only within this task. Live inventory is in the probe result.
Existing system `libEGL.so.1`/`libGLESv2.so.2` and NVIDIA EGL are used via ctypes.
Missing ModernGL/PyOpenGL/glcontext/VTK are diagnosed, not installed. `/libraries`
does not exist on this host; system libraries are recorded through `ldconfig`.
CPU is the default. The service owner explicitly opts into the authorized GPU.

## Chair Route Contract

At startup, once, the owner may enable GPU:

```python
import industrial_render
industrial_render.configure(enable_gpu=True, cuda_device=0)
```

After the existing auth/CSRF/project checks, read exactly one verified DB head,
construct the existing private request workspace from `head["snapshot"]`, then:

```python
with gateway._workspace(head["snapshot"], locale) as workspace:
    reply = industrial_render.handler(
        industrial_render.bind_snapshot(workspace, head), request.payload)
return reply
```

`head` is server-owned `ProjectStore.read_head()` data, NOT request metadata.
It carries `revision`, `sha256` (canonical whole-snapshot SHA) and `snapshot`
with exactly `doc`, `undo`, `redo`, `standard`. The binder checks the digest and
exact reconstructed workspace. An unbound legacy/shared workspace is refused.
The adapter checks version expectations, meshes under the workspace lock and
checks document/history/standard remain unchanged. It never commits a DB receipt,
saves a file, opens a request path, fetches a URL or executes user shaders.

Put POST `/api/render` in the gateway's **read-only branch before `_mutate`**.
Authentication, ownership, CSRF, selected project and request-body/concurrency
limits remain the gateway's responsibility. A frozen head is valid even if
another request advances the DB later; the returned revision identifies it.
This seat did not edit or accept the integrated secure production route.

The existing `project-session.js` currently treats unknown POST routes as edits.
The chair should classify render as read-only. The adapter accepts an injected
bounded `request_id` for compatibility but never persists it. It accepts the
paired `expected_revision`/`expected_sha` only for rejecting stale heads (409),
not for authorizing access. The panel checks returned snapshot/revision against
the state used to request the image, so a concurrent version mismatch is loud.

### HTTP Body

```json
{
  "camera": "isometric",
  "width": 256,
  "height": 256,
  "backend": "gpu",
  "allow_cpu_fallback": false,
  "expected_revision": 7,
  "expected_sha": "server-verified snapshot SHA from /api/state"
}
```

The example SHA description is not a literal valid request. Camera is restricted
to `front`, `top`, `right`, `isometric`, orthographic centered auto-fit. Width and
height are 16..1024 integers, product <=262144. Defaults: isometric, 256x256,
CPU, no fallback. Unsupported fields (including camera matrices, mesh, path,
URL, token, shader) are 400 before tessellation. Empty solid model is 422, not
a blank successful frame. Arbitrary orbit/perspective/zoom/pan are not implemented.

Success: `RenderReply(status=200, payload=<PNG bytes>, content_type="image/png",
headers=<dict>)`, same four attributes consumed by the existing secure Reply
writer. Errors: JSON `{"ok": false, "error": ..., "detail": ...}` with
400 invalid input, 409 stale version, 422 empty model, 503 GPU unavailable/busy,
500 render/snapshot mismatch. Geometry-kernel exceptions retain their original
cause for the gateway's existing error handling; they are not CPU fallbacks.

Required response headers:

| Header | Meaning |
| --- | --- |
| `X-OpenCAD-Revision` | Selected server DB revision. |
| `X-OpenCAD-Snapshot-SHA256` | Canonical whole DB snapshot, same as state `sha256`. |
| `X-OpenCAD-Document-SHA256`, `X-OpenCAD-Model-SHA256` | Exact L1 model bytes used for tessellation. Not the snapshot SHA. |
| `X-OpenCAD-Geometry-SHA256` | Canonical projected vertices/triangles/colors/frame size fingerprint. |
| `X-OpenCAD-Frame-SHA256` | Raw top-down RGBA8 pixels, not PNG file bytes. |
| `X-OpenCAD-PNG-SHA256` | Actual response PNG bytes. |
| `X-OpenCAD-Render-Backend`, `X-OpenCAD-Render-Status` | cpu/gpu and OK/CPU_FALLBACK. |
| `X-OpenCAD-Hardware`, `X-OpenCAD-Renderer` | Hardware boolean and actual renderer string. |
| `X-OpenCAD-Fallback-Reason` | Present only for explicitly allowed CPU fallback, retains the cause. |
| `X-OpenCAD-Render-Camera`, `X-OpenCAD-Render-Elapsed-S` | Accepted camera; component elapsed, not full request/geometry time. |
| `Cache-Control: no-store` | No private image caching. |

The independent panel is loaded by the chair with its JS/CSS and a
`data-opencad-render-panel` mount node, or `OpenCADRenderPanel.mount(node)`.
It uses the existing same-origin project fetch wrapper, HttpOnly cookie flow and
CSRF header. Buttons and real camera changes call `/api/render`; it only displays
PNG as a CSP-permitted data image and creates no canvas/WebGL/WebGPU renderer.
Main viewer/gateway markup was not modified, so mounting is still a chair item.

## Lower-Level RenderService

```python
service = RenderService(enable_gpu=True, cuda_device=0)
payload = project_mesh(
    {"vertices_mm": [[0, 0, 0], [100, 0, 0], [0, 100, 100]],
     "triangles": [[0, 1, 2]]}, view="isometric")
payload.update(width=256, height=256, backend="gpu", allow_cpu_fallback=False)
result = service.render(payload)
```

Direct payload: vertices are XYZ clip coordinates in [-1,1], triangles are three
distinct integer indices, optional colors are one RGB byte triple per face.
There is no direct `camera` field: `project_mesh` performs the allowed projection.
Return dict contains `status`, `requested_backend`, `backend`, `renderer`,
`fallback_reason`, `width`, `height`, `triangles`, `pixel_work`,
`geometry_sha256`, `frame_sha256`, `png_sha256`, `image_png_base64`, `elapsed_s`.
GPU `renderer` contains hardware/vendor/renderer/version/api/cuda_device/
egl_version; CPU explicitly identifies python-barycentric-cpu with hardware false.

Direct HTTP POST `/render` from `make_http_server` is loopback-only development
and probe transport, **not production server integration**. No root operations.
Its 1 MiB body guard rejects duplicate JSON, malformed/nonfinite numbers and
transfer encodings. The component limits 8192 vertices, 4096 triangles,
262144 pixels and 16777216 triangle-bbox pixel work. The industrial adapter also
budgets the merged solid meshes; it does not silently simplify large assemblies.

GPU uses a fixed shader, depth24 pbuffer, draw/finish/readback and closes EGL
objects per request. NVIDIA RTX3080 vendor/renderer must be observed; ANGLE,
SwiftShader, llvmpipe/software and other GPUs are refused. No hardware string
alone is accepted by the probe: analytic pixels, geometry and hashes are checked.
Only GPUUnavailable permits explicit CPU fallback. Shader/draw/readback failures
are loud. Single-service request lock plus process-global GPU lock reject busy
work without an unbounded queue. These are not cross-process admission, memory
isolation or protection against native driver hangs. Multi-process workers,
hard resource isolation and production multi-tenant load are NOT_ASSESSED.

## Reproducible Evidence

All paths below are relative to
`/hf3fs-jd/data/tmp/opencad-research-gpu-20261011/`.
Read the result JSON rather than assuming a logged READY or rc=0 is acceptance.

`probe-final-v1/result.json` (2026-10-11T02:50:17Z) records 39 tests,
zero skips/failures/errors, source drift false and hardware stage PASS.
The complete probe wall is 5.282684 seconds, not a production speedup.
Triangle/cube CPU/GPU frames are equal; real FreeCAD extruded BRep has pinned
48000 mm3 volume, 12 triangles and 2/16384 differing boundary pixels.
The GPU RGBA frame SHA is
`241d5f93fbd16fc6768a63124e22b3d6430252d612d0de928fbb58b66ddbc1ad`;
geometry SHA
`ebd8ac1a95e9506008c82707b25563875b339ac64e94614b621a4f2e8c84748a`.
Renderer: NVIDIA GeForce RTX 3080/PCIe/SSE2, OpenGL ES 3.2 NVIDIA 570.124.06,
EGL1.5, CUDA ordinal0. PNG CRC/dimensions/decompressed RGBA SHA are independently
checked, repeated hardware frame SHA is stable, and depth/background controls
are explicit. Missing ordinal63 returns HTTP503; allowed fallback is byte-equal
to CPU with a retained reason. Empty/oversize/bad index requests return400.

`probe-final-v1/adapter-result.json` records real HTTP PNG, 1440/420 browser
views, trusted pointer button clicks and keyboard camera change calling the
endpoint. No canvas, no horizontal overflow, unchanged fixture snapshot.
This is a canonical **fixture head**, not a real authenticated DB route.
Screenshots `render-panel-1440.png` and `render-panel-420.png` accompany it.

`probe-final-v1/official-source-verification.json` verifies the source ledger
against retained body bytes, SHA256, final fetch logs and literal fact anchors.
It does not promote missing pages to facts or assess industrial parity.
`scan-final-v1/result.json` (2026-10-11T02:50:11Z) records independent scan PASS,
301 files, zero credential matches and no source drift. It is not full smoke.

`probe-v6-ab/benchmark.json` is a controlled small-mesh diagnostic: identical
payload/config, warmups, same-config adjacent noise controls, three serial ABBA
repeats, 72 HTTP requests, client encoding/transport/decoding included.
Triangle GPU was slower, cube/BRep faster relative to the pure-Python CPU golden.
No production speedup is established; this is not comparison against a tuned
CPU renderer or browser, does not include geometry kernel/auth/DB latency and
does not extrapolate to large industrial models. Raw timing/resource records are
in benchmark requests/samples files. Device-wide sampling on a shared host is not
exclusive utilization or a kernel profile. Nsight tools are absent; no installation.

Preserved failures: v2 far-depth CPU arithmetic fixed; v3 probe API spelling fixed;
v7 shared `tests` import collision; v8 missing locale; v9 browser socket path;
v10 target readiness. Later passing runs do not rewrite or promote these results.
Failed official fetches remain separately recorded in the source ledger.

Fresh independent rerun, choose a never-existing output directory:

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-research-gpu-20261011/tools/gpu_render_probe.py --require-gpu --cad-geometry --adapter --verify-official --benchmark --browser /home/mch2/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome --out /hf3fs-jd/data/tmp/opencad-research-gpu-20261011/chair-render-rerun-01
```

Still open: chair `/api/render` secure DB integration and panel mount; industrial
CAD modules/roles and CATIA/SOLIDWORKS source capture; large meshes, arbitrary
camera, cross-process admission, multi-tenant load, native hang isolation and
controlled representative production performance. No whole-task complete claim.

Independent smoke `smoke-final-v1/result.json` remains FAIL: all suite legs
passed (127 passes, one existing skip), but legacy `server.py --selftest` wrote
`oss-alternatives/apps/opencad/build/opencad.dxf` and the source drift guard caught
it. The 527-byte generated file is retained as
`smoke-final-v1/selftest-opencad.dxf`, SHA256
`bb640dda4b843c4e51ab295a473d815624364f45863f437d18486742c56b3711`.
The exact move reported unsupported permission preservation on 3FS; destination
bytes were verified equal and the source build directory was verified empty.
This is inherited selftest output-path debt, not a green full-repository gate.
This seat did not change the chair/acceptance owner's files or erase the failure.
