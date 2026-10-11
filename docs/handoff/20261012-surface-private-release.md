# Private Surface Download Release

## Source And Repository Roles

The frozen product and verification source is
`83e66d1c98918a5197c71e6bf30436513ba06efc`, in the clean worktree
`/data/dev/wt-opencad-surface-private-release-20261012`.
Its seven ordinary forward commits include the exact surface adapter,
private download gateway, independent download consumer, browser identity
isolation, local Unix/TLS bridge and two acceptance-tool corrections.
All commits retain infra DCO sign-off. No remote history is rewritten.

On 2026-10-12 at 06:16:36-06:16:38 Asia/Shanghai, a GET/ref-only plan
returned `78871af28e4ea3cbec359b1dd4ce4b27a8385688` for all three main refs.
The plan had an empty tail, 939 objects scanned and original rc 0.
Evidence: `/hf3fs-jd/data/tmp/opencad-repository-refcheck-20261012-0620-v1/`.
That read is not a new push of this later source.

GitLab on r640HA remains primary:
`ssh://git@192.168.1.181/infra/opencad.git`.
Gitee `sdfwefqaa/opencad` and GitHub `miaochunhui/opencad` are explicit
copies, not installed automatic mirrors. Current code uses main; historical
Gitee master is preserved. Existing credentials, permissions and host keys
are unchanged. The GitHub PAT remains file-sourced and is never printed.
Its existing mode 0664 is a known local readability risk, not remediated here.

The original `/data/dev/opencad` local main and its nine untracked private
entry assets remain untouched. Do not force a checkout over those assets.
Code synchronization does not deploy the production Web service.

## Final Source Acceptance

The final all-stage regression against the frozen 83e source completed at
2026-10-12 07:04:34.367700 Asia/Shanghai, with original parent rc 0:
2396 tests, 2383 passes, 13 unchanged original skips, zero failures/errors
and 15 HTTP checks. Evidence:
`/hf3fs-jd/data/tmp/opencad-surface-private-release-all-20261012-v2/`.
All five child logs have rc 0 and reconciled counts; the complete 408-file
source before/after fingerprint is
`8aa4779fd1e3fe4c095445bbc0f0a09a815ac5ff5ba0575064124101de1aa406`,
with zero source drift. Euler and Fermat independently reviewed the final
counts and source/log closure. The 13 skips remain visible: 12 unavailable
OCP/OCCT checks and one inapplicable injection-fixture teardown check.
The earlier 8306 result is separate and is not inherited by 83e.
This byte/length fingerprint differs from the byte/mode fingerprint below;
neither is a Git commit ID or proof of industrial parity.

Actual private downloads completed at 06:52:53.925128 with original parent
rc 0: 181 workflow checks, 56 HTTP requests, 14 downloaded STEP/BRep files
and 113 independent geometry checks, all passing. The complete saved recipe
produces a concave two-hole surface of area 50 mm2 and a 2 mm thick solid
of volume 100 mm3; material, both holes and the concave notch have independent
world-coordinate witnesses. Reads compare all ProjectStore tables and schema.
Save, undo, redo, CLI restart and HTTP document-open transitions are checked.
The gateway binds document, recipe, actual artifact, revision/model-version
and snapshot headers, rejects unauthorized/late-conflicting requests, and
downloads do not add history or receipts.

Evidence: `/hf3fs-jd/data/tmp/opencad-surface-private-release-download-20261012-v2/`.
Its result SHA256 is
`00b0558be617788f4afe9c73e84ee6b63bd1c21b7c8b0a8a35a7dc4854252e5e`.
The actual saved terminal candidate was reread and independently verified
before PASS publication. Both chair and Fermat offline consumers passed;
they do not reexecute HTTP/FreeCAD. The 408-member full byte/mode source
fingerprint is
`4fd5e70ee1be6d3f78f821da1ce828e159314d2f48664dfce1d50b62f9cf8f19`.
Selected source and executor remain clean and unchanged. This SHA256 is
not a Git commit ID.

Final desktop/mobile identity acceptance completed at 06:51:57 with original
rc 0, 44 unique passing checks and unchanged 83e HEAD/source. Independent
screenshot and offline review passed. Its 27-test control suite also returned
original rc 0, no failures/errors/skips and no drift.
Evidence roots: `opencad-surface-private-release-browser-20261012-v3` and
`opencad-surface-private-release-browser-selftest-20261012-v2`, under 3FS tmp.
This checks the stated viewer workflow, not all-site language or every panel.

The final local TLS/Unix bridge acceptance returned original rc 0 at 06:52:26:
8 tests, 22 workflow checks and 57 HTTP requests, with zero drift.
Offline readback passed 27 checks. Evidence:
`/hf3fs-jd/data/tmp/opencad-surface-private-release-bridge-20261012-v2/`.
This is a fresh local fixture, not remote SSH forwarding or production cutover.

## Retained Failures And Boundaries

The first actual surface-download run retains FAIL: 56 requests and 14 files
completed, but FreeCAD returned rc 0 without the required stdout terminal.
Its separate JSON contained 113 geometry checks; that alone did not satisfy
the gate. Paired real FreeCAD replay of the same bytes proved that explicit
stdout flushing restores the terminal. The fix preserves measured rc,
exact terminal and complete JSON checks. The final new-source run passed;
the original failure and diagnostic first-failure remain unchanged.
The final consumer selftest passed 15 tests and 1789 mutation rejections,
with the retained real failure included, no errors or skips.

The first integrated browser run retains FAIL at zero checks because its
Chromium Unix socket path was too long. A shorter independent runtime passed
the unchanged 44-check workflow. The committed preflight now rejects an
oversized socket path before launch, retaining the original coordinates.
Neither failure was hidden by deleting a check or silently retrying.

Private-production cutover, remote SSH bridge traffic, physical HA failover,
automatic mirroring and new-source RTX 3080 performance remain unaccepted.
Existing anonymous legacy production data has not been migrated or replaced.
The latest official research remains PARTIAL, not industrial parity.
General surface/part fusion, assembly export, all industrial disciplines and
CATIA/SolidWorks full-function equivalence are not delivered by this batch.

Configuration/suppression source `6596f022` and GPU offline trust/error-handling
source `6f5d06e` have separate acceptance and reviews, but are not included in
this freeze. Configuration UI is an independent in-progress worktree.
The GPU tool's offline PASS is not a new GPU execution or performance benefit.
The older configuration smoke retains rc -7/SIGBUS and outer rc 1. A test
fixture defect is confirmed: default temporary SQLite files landed on
fuse.hf3fs despite a separate local runtime declaration. No native crash
stack or fixed-source filesystem comparison has established the direct
crash cause. This separate diagnostic gate remains closed; later targeted
configuration passes do not overwrite it.
The long-term goal stays active; its current API cannot edit the objective,
so this handoff corrects repository status and immediate work ordering.

Native concurrency remains six available leaders, with the eight-leader
convention disclosed as tool-capacity-limited. Flash remains DENY/off.
Recovery uses named forward revert commits and the reviewed primary-first
synchronizer. Do not force refs, delete unknown work, alter credentials,
reuse evidence directories or assume source publication is deployment.
