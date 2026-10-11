# Private Web Integration, 2026-10-11

## Scope

OpenCAD is a Web service, not an installed end-user desktop application.
GitLab on r640HA is the primary; Gitee and GitHub receive the same reviewed
main commit. See `docs/REPOSITORIES.md` for addresses and explicit synchronization.
Code synchronization is not production deployment or industrial parity.

The resumed release is assembled in the independent
`/data/dev/wt-opencad-private-release-20261011` tree, based on the primary's
latest repository-sync commits. It must retain `tools/github_api_sync.py`,
its tests and the current `docs/REPOSITORIES.md`. The primary working tree's
nine untracked assets remain protected; remote main can be published from the
clean reviewed release tree without overwriting these local assets.

The integration worktree preserves the primary workspace's nine original
untracked private-entry files. Only independent copies were edited. The
experimental `tools/stage_private_entry.py` failed independent safety review
and is excluded from this delivery; do not use it for another migration.
Its earlier copy result does not certify later tool versions.

## Changes

The private HTTP entry uses cookie authentication, owner-authorized projects,
request-local language context, isolated documents and local durable SQLite
heads/history/receipts. Editing uses expected revision, snapshot SHA and request
ID. Generic import/history validates stored industrial extensions before
confirmation. Measured assembly/drawing candidates have integrity seals.

Document commands and history no longer obtain a sketch before dispatch.
Sketch creation occurs inside the actual edit transaction, and state reads
do not create a missing sketch. An assembly-only confirmed document therefore
survives undo/redo without acquiring an unrelated empty sketch.

Drawing saves notify the parameter panel of the actual submitted base.
Only a directly matching continuous version chain, matching fresh receipt/
document hashes and unchanged parameter definitions can rebind the panel.
Unsaved parameter inputs are retained. Real parameter rebuilds refresh
associated drawings. A stale panel cannot silently rebind across another edit.

Receipt recovery no longer reloads the whole page. It blocks writes, confirms
the original receipt once, then checks the latest state and displayed model.
View failures retain a GET-only recovery path, not a second mutation.
Private state/mesh project, revision and snapshot SHA must agree. The unlock
also requires the latest known base to equal the verified view. Depth inputs
marked dirty and existing feature-parameter drafts survive this refresh.
This is not a certification of every industrial panel's unsaved state.

Drawing definition saves compare the current editor bytes with the submitted
text before clearing dirty state. Text entered while the response is pending
is retained through the following drawing refresh.

## Evidence

All logs, screenshots, models and databases are outside Git.
Evidence directories below are under `/hf3fs-jd/data/tmp/` unless stated.
Each result binds exact working source bytes and retains its original verdict.

- `opencad-continuation-candidate-all-20261011-v1`: PASS at
  2026-10-12 00:06:25 +08:00, 2292 passed, 13 original skips, no failures,
  errors or source drift, plus 15 HTTP checks. This precedes the review-tool
  rejection-contract correction, not a new product change.
- `opencad-continuation-s1-20261011-v1`: 22 real HTTP security tests PASS.
- `opencad-continuation-s2-20261011-v1`: 18 independent durable-store tests
  PASS; CAS/ABA, receipt replay, concurrent writes and failed COMMIT checked.
- `opencad-continuation-s3-20261011-v1/20261011T160250057154Z`: 106 real
  parameter rebuild assertions PASS; STEP readback agrees with BRep volume,
  residual/DoF and failed-input atomicity recorded.
- `opencad-continuation-s4-20261011-v1`: 30 exact surface-kernel tests PASS.
  The first gateway supplement retained a false-negative FAIL: it required
  422 but the surface contract correctly rejected malformed input with 400.
  No receipt assertion after that failed assertion is claimed as tested.
  `opencad-continuation-s4-gateway-fixed-20261012-v1` then passed all eight
  real gateway tests at 00:18:26 +08:00 with exact per-domain rejection,
  unchanged complete heads and no receipts.
- `opencad-continuation-s5-20261011-v1`: 70 real assembly checks PASS.
- `opencad-continuation-s6-20261011-v1`: 82 real HTTP/browser drawing checks
  PASS at 2026-10-12 00:05:37 +08:00. The later-draft race is separately
  exercised by the full Web test, not by these 82 checks.
- `opencad-continuation-browser-20261012-v1`: 30 desktop/mobile browser
  checks PASS, including confirmed-receipt recovery without losing drafts.
- `opencad-continuation-render-20261011-v1`: 35 real cookie/DB/HTTP render
  checks PASS; actual RTX3080, 14 PNG responses, explicit CPU fallback and
  unchanged project heads/receipts across three original CLI restarts.
  `opencad-continuation-render-readback-20261011-v1` independently verified
  all 14 saved PNGs and their hashes. No representative speedup is claimed.
- `opencad-private-integrated-full-20261011-v4`: all-stage PASS,
  2304 tests, 2291 passed, 13 original skips, no failures/errors/source drift,
  plus 15 HTTP selftest checks. This precedes the later drawing-draft fix.
- `opencad-private-integrated-assembly-20261011-v3`: 70 checks PASS,
  no source drift; actual private HTTP, mesh, mates, BOM and exact history.
- `opencad-private-integrated-drawing-20261011-v7`: 82 checks PASS,
  no source drift; real HLR/sections, browser 40-to-60 mm parameter change,
  same-version SVG/PDF/DXF export, dirty/stale parameter controls.
- `opencad-private-integrated-browser-20261011-v6`: 30 checks PASS,
  desktop/390-pixel views, original page/input retention and receipt recovery.
- `opencad-drawing-later-draft-before-20261011-v1`: one test FAIL against
  the old product, after real commit and a held response; later text was
  overwritten. This negative result is preserved, not reclassified.

Final-source reruns use new evidence directories, not the previous results.
Reproduction entries are `tools/acceptance.py --stage all`,
`tools/assembly_private_review.py`, `tools/review_private_drawing.py --stage all`,
and `tools/private_web_browser.py`. Supply a new external evidence directory
and local persistent database root. Do not use isolated Python mode with
`tools/acceptance.py`; it imports its adjacent bootstrap module.

The original six-seat tool limit remains disclosed. Flash stays denied/off.
The reviewed source includes startup listener/origin validation before storage
creation and admitted-request draining before database close. Automatic cleanup
is permitted only for the tools' own newly created reproducible request/fixture
directories; existing user data and immutable 3FS evidence must not be removed.
The independent release tree still requires its own complete regression,
browser, Git-archive closure and three-remote readback after named commits.

## Unfinished Boundaries

The 13 original skips include absent OCP/OCCT-specific coverage and one
fixture-not-needed test. FreeCAD BRep/HLR checks passing do not prove the
unavailable OCP path. No test was deleted or converted to a new skip.

Production TLS/proxy acceptance, abuse controls/quotas, coordinated backup/
restore, deployment cutover and cross-database revocation atomicity remain
separate work. The earlier permission incident remains OPEN_UNATTRIBUTED;
no permission repair or production operation is authorized by these tests.
The static release review always reports production_ready=false.

Industrial surfacing breadth, assembly scale, complete standards/GD&T/MBD,
manufacturing/simulation/enterprise domains, complete official competitive
research and accepted RTX 3080 server-rendering benefit are not complete.
The long-term goal remains active. Do not claim CATIA/SolidWorks full parity.

Rollback source changes with named `git revert` commits. Do not erase remote
history, overwrite primary untracked files, copy live SQLite files without
their WAL, or change credentials/known-host records. Source publication and
service restart are distinct operations with distinct recovery evidence.
