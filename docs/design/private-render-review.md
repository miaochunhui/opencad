# Private Render Review

Scope: independent review only. Product/gateway/secure/viewer source, existing
credentials, driver and production are not modified. No nested agents or push.
Only `tools/private_render_review.py` and this document are owned in this wave.
The existing official research ledger and failed fetch evidence are unchanged;
withdrawn chair claims are not new body capture evidence.

## Reproduction

The new `--http-db` mode loads the renderer and adapter from `--gateway-root`,
without replacing or instrumenting product modules. It starts the original
`secure_web.py` CLI on a new loopback port. This verifies the integrated private
server source, not a deployed production service.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-research-gpu-20261011/tools/private_render_review.py --gateway-root /data/dev/opencad --http-db --runtime-root /data/dev/opencad-private-fixtures --require-gpu --cuda-device 0 --out /hf3fs-jd/data/tmp/opencad-research-gpu-20261011/private-cookie-db-v1
```

The named local parent follows the chair's existing
`/data/dev/opencad/tools/private_web_acceptance.py` fixture recipe. Only a newly
created, uniquely named child is used; existing fixture accounts/DBs are never
opened. ProjectStore's local-filesystem restrictions are preserved. New account
and project SQLite DBs and the CLI-owned request workspace root stay in that
local child; logs, fixture coordinates, persisted-head copies, mesh responses,
PNG frames, headers and result JSON stay on 3FS. Successful request temporaries
are cleaned by the original gateway; failures retain their original coordinate.
The tool does not remove its local child or change existing permissions.

Generated fixture passwords exist only in memory and are used for actual HTTP
login. The returned HttpOnly/SameSite=Strict cookie is then used for HTTP project
creation, versioned document import, state, mesh and render. Cookies/passwords
are not logged, hashed into report entries or placed on command lines.

Historical review loads the renderer and adapter from its own worktree. `--gateway-root`
selects the chair's read-only source for the real auth controller, project store,
unmodified gateway, secure HTTP server, Workspace and FreeCAD kernel. Exact source
origins and before/after SHA256 are saved. Never point it at deployed data: there
is no existing-data or remote-service argument.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-research-gpu-20261011/tools/private_render_review.py --gateway-root /data/dev/opencad --handler-only --require-gpu --out /hf3fs-jd/data/tmp/opencad-research-gpu-20261011/private-review-v4-final
```

Every run requires a never-existing output directory. New fixture accounts use
generated in-memory credentials; no existing identity or secret is read or
changed, and no token/password/cookie is logged. Historical fixture SQLite databases,
workspace temporaries, PNGs, headers and logs are retained only in 3FS.
The historical command above is an already-executed coordinate; re-execution must select a
new output directory, for example `chair-private-render-01` under the same root.

## Acceptance Boundaries

`--http-db` creates two distinct model projects through HTTP, imports actual
60x40x20 and 60x40x10 mm FreeCAD cuboids at revision2, then compares the exact
durable DB head with HTTP state/mesh and render headers. Both front and isometric
CPU/GPU PNGs undergo independent CRC, pixel, SHA and projected-geometry checks;
GPU requests must observe RTX3080 and repeated pixels must be identical.
The original CLI is restarted over the same fixture and real cookies in three
configuration arms: owner opt-in CUDA0, owner opt-in unavailable CUDA63, and
owner GPU disabled. The latter two must fail503 without permission to fall back,
and return explicitly labelled, cause-preserving CPU pixels only when allowed.
Logical SQLite tables, snapshots and receipts must remain identical after all
render and denial requests and across reopening the DB.

All authorization, CSRF, missing project, stale/cross-project expectations,
empty model, oversized frame, camera, path/URL/client mesh/shader/device negative
controls in this mode cross real HTTP. No direct-handler result stands in for
an endpoint result. The original unmodified CLI is not instrumented, so a
no-kernel-call counter is NOT_TESTED here. Browser panel clicks, HTTPS ingress,
concurrent render/edit races, multi-process admission, industrial parity and
representative controlled performance remain separate untested gates.

Normal `--http-db` mode stops on the first failed expectation. `--diagnostic`
is an explicit evidence-collection mode: it records HTTP negative-control
mismatches as FAIL and continues unrelated controls. Any such finding keeps the
overall verdict FAIL and exit1; it is not permission to accept the service.
Generated fixture passwords/cookies are checked against every generated
evidence file and the final report before completion; values are never reported.

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
store monkeypatch bypass is permitted. That run had no local fixture argument,
so its full DB stage failed. The later explicit real-DB task uses the existing
private acceptance tool's separated local-runtime/3FS-evidence recipe; this does
not retrospectively upgrade the original failed run.
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
drift false, 6.101667 seconds wall. This is historical handler-only acceptance.
`private-review-scan-v2/result.json` independently scanned 303 source files with
zero credential matches and no drift. Neither result upgrades the earlier
whole-repository smoke or official capture status.

## Real Cookie/DB Findings

`private-cookie-db-v1/result.json` is FAIL at 2026-10-11T03:21:57Z:
19 passed checks before the first unexpected response, hardware PASS,
8.723036 seconds wall and source drift false. Real cookie login, HTTP
create/open/mesh and four owner RTX3080 frame cases passed. The missing-project
negative expected400/project_required but observed500/internal_error.

The original CLI log at
`private-cookie-db-v1/owner-opt-in-secure-cli.log` preserves the first cause:
`project_gateway.py:122` raises its imported `secure_web.HttpError`; the entry
script runs as `__main__`, so its `except HttpError` is a different class identity.
The outer generic handler then returns500. The reviewer does not fix or replace
the gateway/secure module. Chair-owned product correction plus a fresh strict
run is required before full HTTP negative-control acceptance.

`private-cookie-db-v2-diagnostic/result.json` is FAIL at
2026-10-11T03:23:42Z: 35 named checks, 34 PASS and one FAIL, 13.125039 seconds
wall, source drift false, real-cookie DB/HTTP chain FAIL, hardware PASS and
durable DB PASS. This second run explicitly enables `--diagnostic`, retains the
same known failure and collects unrelated controls, rather than weakening the
expected400 to500. Both cross-owner directions are403, anonymous/forged cookies
401, CSRF403, stale/cross-project SHA409, URL/path/mesh/shader/device/size/camera
400 and empty model422. Owner CUDA63 and owner-disabled arms return503 with
their actual causes; explicit fallbacks equal the CPU pixels. Three original CLI
children exit0, but the review exits1. Project DB counts remain3 projects,
5 snapshots, 2 receipts and3 create receipts across rendering and CLI restarts.
GPU/CPU differences are2/16384 edge pixels for Alice isometric and zero for the
other three cases; no speed benefit is claimed.

The chair subsequently added the canonical `secure_web` alias at module top
when running as `__main__`. The next strict run uses a new coordinate with the
same HTTP400 oracle, the extra exact-secret evidence scan and count
reconciliation. v1/v2 failed evidence remains unchanged:

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-research-gpu-20261011/tools/private_render_review.py --gateway-root /data/dev/opencad --http-db --runtime-root /data/dev/opencad-private-fixtures --require-gpu --cuda-device 0 --out /hf3fs-jd/data/tmp/opencad-research-gpu-20261011/private-cookie-db-v3-final
sudo -n -u infra python3 -B /data/dev/wt-opencad-research-gpu-20261011/tools/private_render_review.py --verify-result /hf3fs-jd/data/tmp/opencad-research-gpu-20261011/private-cookie-db-v3-final --out /hf3fs-jd/data/tmp/opencad-research-gpu-20261011/private-cookie-db-v3-verified
```

`--verify-result` independently reopens all14 PNG/header pairs, strict CRC/RGBA
checks, HTTP response hashes, five whole-logical-DB fingerprints, CLI log hashes,
source snapshots, check counts and four rejected corruption controls. Its
`EVIDENCE_VERIFIED` result means the saved evidence was checked, not that the
product suite passed. The actual `suite_verdict` remains explicit. All commands above
name historical or frozen evidence directories; re-running requires new output
coordinates and leaves prior failures untouched.

`private-cookie-db-v3-final/result.json` records strict PASS at
2026-10-11T03:28:36Z, 35/35 checks, 12.780553 seconds wall, hardware PASS,
cookie login PASS, durable DB PASS and source drift false. Missing project is
now400/project_required under the unchanged oracle. The14 PNG responses and
five DB fingerprints were re-read by `private-cookie-db-v3-verified`, which
reported EVIDENCE_VERIFIED with suite PASS at2026-10-11T03:28:53Z.
The exact generated-secret scan covers42 evidence files with zero matches;
the separate source scan is PASS at `private-cookie-db-scan-v3-final`.

The selected hardware is CUDA0, NVIDIA GeForce RTX3080, driver570.124.06,
10240 MiB; inventory also observes a second RTX3080, not used by this review.
Alice's128x128 isometric GPU frame is revision2, renderer
`NVIDIA GeForce RTX 3080/PCIe/SSE2`, hardware=true, backend=gpu, status=OK.
Its render elapsed header is0.404122 seconds, not an A/B speedup claim.
Full model/snapshot/geometry/frame/PNG SHA values are in
`private-cookie-db-v3-final/alice-isometric-gpu-headers.json`.
After this result note, the final document/source freeze is validated under
`private-cookie-db-v4-freeze`, then re-read under
`private-cookie-db-v4-verified`; the tool/oracle is unchanged.
