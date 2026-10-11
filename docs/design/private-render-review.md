# Private Render Review

Scope: independent review only. Product/gateway/secure/viewer source, existing
credentials, driver and production are not modified. No nested agents or push.
Only `tools/private_render_review.py` and this document are owned in this wave.
The existing official research ledger and failed fetch evidence are unchanged;
withdrawn chair claims are not new body capture evidence.

## Reproduction

The review loads the renderer and adapter from its own worktree. `--gateway-root`
selects the chair's read-only source for the real auth controller, project store,
unmodified gateway, secure HTTP server, Workspace and FreeCAD kernel. Exact source
origins and before/after SHA256 are saved. Never point it at deployed data: there
is no existing-data or remote-service argument.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-research-gpu-20261011/tools/private_render_review.py --gateway-root /data/dev/opencad --handler-only --require-gpu --out /hf3fs-jd/data/tmp/opencad-research-gpu-20261011/private-review-v4-final
```

Every run requires a never-existing output directory. New fixture accounts use
generated in-memory credentials; no existing identity or secret is read or
changed, and no token/password/cookie is logged. Fixture SQLite databases,
workspace temporaries, PNGs, headers and logs are retained only in 3FS.
The command above is an already-executed coordinate; re-execution must select a
new output directory, for example `chair-private-render-01` under the same root.

## Acceptance Boundaries

The fallback review chain calls the real `authenticate`, `_authorized_head` and
`_workspace`, then `bind_snapshot` and `handler`. It is explicitly **not a new
gateway route**. Owner checks must precede workspace construction/tessellation.
`bind_snapshot` authenticates neither owner nor project: it checks exact snapshot
bytes and revision consistency. A same-snapshot metadata-only binding is accepted;
this negative demonstration makes the caller's authorization duty explicit.

In `--handler-only` mode, two users own distinct canonical in-memory fixture
heads of 60x40x20 and 60x40x10 mm solids. The input-head shim does not alter
gateway dispatch and is explicitly not a durable store. Real auth, owner checks,
Workspace, FreeCAD and EGL still execute; durable DB end-to-end is NOT_TESTED.
FreeCAD geometry extents and 12 triangular faces are checked independently.
CPU/GPU PNGs are decoded by a separate bounded PNG framing/CRC/RGBA checker.
It checks center/background/alpha, PNG and frame SHA, projected geometry SHA,
revision, snapshot, model/document headers, camera and no-store. Corrupted
revision/snapshot/frame/PNG CRC decoys must be rejected by the acceptance ruler.
Requested GPU must observe RTX3080 and never SwiftShader/ANGLE/browser WebGL.
GPU/CPU edge differences are reported; production performance is not measured.

Negative controls include both directions of cross-owner access with stolen
version metadata, anonymous/forged sessions, missing project, wrong snapshot
expectations, wrong head/workspace binding, stale revision, URL/path/client mesh
and oversize frame. Instrumented counters demonstrate rejection before mesh and
render. Real unavailable CUDA ordinal63 must return503; an explicitly allowed CPU
fallback must preserve the cause and equal CPU pixels.

The actual unmodified private HTTP server is also exercised: `/api/state` must
match the selected head; `/api/render` anonymous/cross-owner/CSRF must be denied.
If an authorized `/api/render` returns404, the overall result is **PARTIAL**,
with handler/auth-chain acceptance separately PASS and HTTP PNG success
NOT_INTEGRATED. The review does not patch/subclass gateway dispatch to fake it.
`PARTIAL` exits0 for a successfully completed bounded review, not product
acceptance; callers must read `verdict`, `gateway_route` and `untested`.

Full DB mode fingerprints the entire logical project database, including snapshots
and receipts. Handler-only mode fingerprints the fixture heads, not a DB. Both
must remain unchanged after all read-only requests. Existing production/deployment, browser
panel integration, enterprise industrial scope and performance are not accepted
by this fixture. The earlier whole-repository smoke failure remains independent.

## Evidence

All evidence paths in this section are relative to
`/hf3fs-jd/data/tmp/opencad-research-gpu-20261011/`.

The initial `private-review-v1/result.json` is FAIL, with the original traceback:
`ProjectStore._prepare_root` rejects 3FS with
`storage root must be on a durable local filesystem`. No mount/path spoofing or
store monkeypatch bypass is permitted. Since evidence must remain on 3FS and no
local durable fixture exception was authorized, full DB acceptance is blocked.
The changed, explicit handler-only mode is a narrower review, not a retry that
turns this failure green. Process existence/source presence/exit code alone are
not evidence of private render integration.

`private-review-v2-handler/result.json` remains FAIL after real handler GPU and
negative controls: the review's local `http` function shadowed the standard
library package at `http.client.HTTPConnection`. Only the review helper was
renamed to `http_call`; product source and failed artifacts are unchanged.

`private-review-v3-handler/result.json` records PARTIAL at
2026-10-11T03:06:12Z: real RTX3080 hardware PASS, auth/handler fixture PASS,
25 named checks plus four rejected ruler decoys, 6.327612 seconds wall, source
drift false. Alice GPU/CPU differs on 2/16384 boundary pixels, Bob on zero.
The pinned Alice hardware frame matches the earlier accepted renderer frame.
`alice-gpu.png`, `bob-gpu.png` and corresponding headers are retained in that
directory. Unauthorized HTTP responses are403/401/403 for cross-owner/anonymous/
CSRF; authorized render returns404/not_found. This is not a DB-backed HTTP PNG
acceptance result. Header backend/status checks and an unbound-workspace control
were subsequently added to the review; the final same-source run is listed below.

`private-review-v4-final/result.json` records PARTIAL at
2026-10-11T03:08:13Z: 26 named checks PASS, four corruption decoys REJECTED,
`hardware_stage=PASS`, `private_chain=AUTH_HANDLER_FIXTURE_PASS`,
`durable_db=NOT_TESTED`, `gateway_route=NOT_INTEGRATED` (404/not_found), source
drift false, 6.101667 seconds wall. This is the current review-code acceptance.
`private-review-scan-v2/result.json` independently scanned 303 source files with
zero credential matches and no drift. Neither result upgrades the earlier
whole-repository smoke or official capture status.
