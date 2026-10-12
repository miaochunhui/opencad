# Configuration And Fixture Repair Release

## Repository Roles And Frozen Source

Collection date: 2026-10-12, Asia/Shanghai. The independent OpenCAD repository
is `/data/dev/opencad`, not the shared infra worktree.

GitLab on r640HA is primary:
`ssh://git@192.168.1.181/infra/opencad.git`.
Gitee `sdfwefqaa/opencad` and GitHub `miaochunhui/opencad` are explicit code
copies. These are not installed automatic mirrors or production deployments.
Current development uses main; historical Gitee master is preserved.
The original workspace's nine untracked private-entry assets remain protected.
No credential file, permission, host key, service or production state changed.

The preceding actual three-target synchronization was `bec25d3828b493347070f2e3943117eaef090ccc`
at 07:49:35, with original parent rc 0. A fresh GET/ref-only plan at
08:37:17-08:37:18 again returned that SHA for all three main refs.
Evidence: `/hf3fs-jd/data/tmp/opencad-config-repair-release-refplan-20261012-v1/`.
Its rc 0, 1107 scanned objects and seven-commit tail certify a plan, not a push.
GitHub uses the existing file-sourced PAT without printing it; its previously
reported readability risk remains a separate credential-permission issue.

The clean frozen candidate is
`7a48ee4fefd35538468377108d4df2d0a2105398` in
`/data/dev/wt-opencad-config-repair-release-20261012`.
Seven ordinary forward commits follow bec25d, retaining infra DCO sign-off:
configuration/suppression core, configuration UI, local SQLite guard,
dynamic publication review, real failed-all negative control,
browser terminal preservation and three discovery-fixture repairs.
This handoff is a separate documentation-only successor; it is not part of
the frozen product source or its whole-source test count.

## Actual Three-Target Publication

Named documentation-only DCO commit
5394b04d36efdf6bac60947703e458dff84ed61d was created at 08:59:14.
Prepublication readback returned original rc 0 at 09:00:01.267104,
six checks and 22/22 controls. It verified clean publication source,
the exact 419-file all freeze and the sole added Markdown successor.
Evidence: `/hf3fs-jd/data/tmp/opencad-config-repair-release-doc-prepublication-20261012-v1/`.

The explicit new plan returned original rc 0/PASS at 09:00:08.672442:
1112 reachable objects and the exact eight-commit forward tail.
Actual synchronization parent session 17786 returned original rc 0,
SYNCED/PASS at 09:02:37.589392. GitLab and Gitee original push exits were 0;
GitHub verified all eight original commit IDs before its non-forced main update.
Final main readbacks all returned 5394b04:
GitLab 09:02:34.889005, Gitee 09:02:35.603155, GitHub 09:02:36.332949.
Evidence:
`/hf3fs-jd/data/tmp/opencad-config-repair-release-plan-20261012-v1/`,
`/hf3fs-jd/data/tmp/opencad-config-repair-release-sync-20261012-v1/`.

The chair's independent recorded-transport and fresh live-ref readback
returned original rc 0/PASS at 09:03:42.190770: eight checks, 36/36 controls.
It checked exact tail/object counts, primary-first ordering, original exits,
all-source and documentation-only relation, and fresh three main refs:
`/hf3fs-jd/data/tmp/opencad-config-repair-release-publication-readback-20261012-v1/`.
No credentials were read by this consumer; GitHub used public GET.
This is sequential code synchronization, not globally atomic publication,
automatic mirroring, default-branch migration, HA failover or live deployment.
This receipt update is a later documentation-only commit and is not included
in the recorded 5394b04 synchronization or the frozen whole-source all.
It needs its own explicit synchronization, without rerunning product tests.

## Original Failed Integration And Repair

The first frozen configuration integration was
`4b3fb4b23467a3b3c42e025a04b1168f2ca9f4f4`.
Its original all parent session 92428 returned rc 1 at 08:10:37.190994.
Core: 1761 tests, 1748 passes, 13 original skips, zero failures/errors.
Web: 589 tests, 520 passes, one failure and 68 errors.
SQLite negative controls: 24 passes; bootstrap: 20 passes; HTTP: 15 checks.
DXF was not executed after the Web failure. Source/HEAD had zero drift.
Evidence remains immutable:
`/hf3fs-jd/data/tmp/opencad-config-integration-all-20261012-v1/`.

The 68 errors were 27 drawing and 41 ProjectStore cases whose fixtures used
fixed `/data/dev` roots outside the declared local SQLite runtime.
The browser long-socket-path negative fixture used local output rather than
the identity producer's required 3FS evidence root; its first failure was
`external_fixture_roots_required`, not the intended socket-path refusal.
These are fixture integration defects, not permission to weaken the guard.

Owner commit `7ce7bee2030721eb98c1bffff79d9e0aae72de21` fixes exactly five files.
The shared runtime helper uses the existing SQLite guard, respects explicit
declarations, and rejects symlinks, dotdot, empty declarations and drift.
The original three one-test red probes each returned rc 1.
Selftests passed 8/8; targeted suites passed 103/103 (27/49/27);
the complete owner Web suite passed 589/589 with 2194 SQLite connections,
zero rejection, failure, error or skip. Original parent sessions were
35820, 9452, 58392 and 86376, respectively; all parent rc 0.
Evidence: `/hf3fs-jd/data/tmp/opencad-sqlite-discovery-fixture-fix-20261012-v1/`.
The latter three results bind a stable precommit source; they are not claimed
as postcommit reruns. Aquinas independently received rc 0 for new 8/8 controls
at 08:33:46 and found no P0/P1 in the root repair.

The old SIGBUS remains directly UNATTRIBUTED. This repair does not erase its
failure, prove a native crash cause, or claim power-loss durability.
A separate P2 in this helper's unprotected final source read is assigned to
Lovelace in an independent terminal-fix tree, not edited into the running
7a48ee4 freeze. It has not been exercised as a failure in this product run.

Browser terminal repair `56eca484fae7310be0110c9fc991494a8180e8da` preserves
primary exceptions, separately reports secondary cleanup/read failures,
keeps unavailable source captures null and retains completed counts.
Chair session 97998 returned original rc 0: three tests, 19 CLI cases
(18 fault controls and one positive). The original 15 browser controls remain.
Evidence: `/hf3fs-jd/data/tmp/opencad-config-browser-terminal-chair-20261012-v1/`.

## New Frozen Acceptance

The original new browser parent session 85175 returned rc 0:
42 desktop/mobile workflow checks, including real configuration/suppression
geometry, draft preservation, overrides/base selection, save/undo/redo,
CLI restart, actual STEP completion/import, rejected input, lost-response
single receipt replay, delayed reads and second-account isolation.
Evidence: `/hf3fs-jd/data/tmp/opencad-config-repair-release-browser-20261012-v1/`.
The chair's new offline readback returned rc 0/EVIDENCE_VERIFIED.
The new frozen-source control suite returned rc 0 with 15/15 passes at
08:38:49.676253. These are not all-site language or no-reload account-switch
certification. Aquinas independently completed the read-only review at
08:48:23: 13/13 checks, original query rc 0, no P0/P1. It checked 419 source
files, 39 artifacts, four CLI PID/starttick/argv bindings and two actual STEP
downloads/imports. Each STEP was 19038 bytes; imported volume was
5997.8761403404915 mm3. Source, artifact and first-cause closure were unchanged.

The new all parent session 90283 was started against clean 7a48ee4 with a
fresh local runtime `/data/tmp/oc-crr-all-1012-v1`.
Evidence: `/hf3fs-jd/data/tmp/opencad-config-repair-release-all-20261012-v1/`.
The original parent returned rc 0; terminal timestamp is
08:49:09.349037. All six ordered children completed with original rc 0:
SQLite negative controls 24/24, bootstrap 20/20, HTTP 15 checks,
core 1761 tests (1748 passes and 13 original skips), Web 589/589 and DXF 56/56.
Totals are 2450 tests, 2437 passes, 13 original skips, zero failures/errors,
plus 15 HTTP checks. Source/HEAD/status before and after are equal, clean,
and pinned to 7a48ee4; source drift is empty.
Twelve skips require the absent OCP/OCCT path; one is an unnecessary fixture
teardown under the actual module. These are not passes or removed tests.
This result does not inherit the older 83e or owner 589-test result.

The chair's source-bound offline readback returned original rc 0 at
08:50:25.775195, six main checks and 22/22 positive/negative controls.
It independently verified all 419 committed source files, exact six-child
plan, test identities, counts, log hashes, runtime bindings and original skips:
`/hf3fs-jd/data/tmp/opencad-config-repair-release-all-chair-readback-20261012-v1/`.
The executor was the independently reviewed clean 8d4eee2 reviewer, not an
unreviewed replacement or a product rerun. Euler's separate read-only audit
returned original rc 0/PASS at 08:51:30.689849, five checks, no new P0/P1.
It independently signed off the exact six-child counts and 419-file
bytes/mode/log/runtime closure with zero source/HEAD/input/tool drift:
`/hf3fs-jd/data/tmp/opencad-config-repair-release-all-euler-review-20261012-v1/`.
Its result SHA256 is
8e5b9a2d801632fef43a9dc9af7ff96ed84ab0a0d1c37a768680d59eb0ef15a5.
The two reviewer P2 limitations remain: incomplete static scrutiny of the
executor loop body, and no per-intermediate-commit document delete/mode check.
They do not invalidate the independently read actual terminal in this run;
the publication tail here is seven product commits and one added Markdown.

The publication reviewer has dynamic source-bound counts, exact old five/new
six-child plans, original skip identities and strict document-only successor
binding. Euler independently received rc 0 for 22 tests/60 negatives at
08:26:10.855231 against clean `8d4eee2c859d3c620b28014f1c831c6acee4e216`.
Chair review of the real 4b3 FAIL returned rc 1/ALL_NOT_PASS; changing only its
top verdict cannot manufacture the missing DXF stage. All old failures remain.

## Active Follow-On Work

Atomic definition API `bbdd7280d3d995f7caa97e0161bb0dfbb46091f3` is separate.
Its original core session 10658 returned rc 0 (21 tests, 10 measurements);
HTTP session 3999 returned rc 0 (16 tests, 134 requests, eight measurements).
Fermat received independent offline core rc 0/21 checks at 08:39:19 and HTTP
rc 0/16 checks at 08:39:20. No established P0/P1 was found. One P2 remains:
the ledger reader does not bind every response hash/length and the producer
does not retain complete response bodies/headers. Therefore these are 134
source-bound execution records, not complete independent HTTP-response closure.
Evidence: `/hf3fs-jd/data/tmp/opencad-config-definition-api-independent-audit-20261012-v2/`.
It is not included in 7a48ee4 or its browser.
Darwin's four-file independent CRUD UI task is active, using this API rather
than document-open followed by a second committing rebuild. Fermat is assigned
the response-closure repair in a separate tree, not Darwin's four-file write set.

Lovelace's separate terminal-helper DCO is
a1b74c8e4c1704d936245daef9ca816139b9ea3b. Its precommit byte freeze passed
13 terminal fault controls and the original eight runtime controls, original
parent rc 0, three source hashes unchanged after commit; it is not a new-HEAD
rerun or part of 7a48ee4. Aquinas has the independent review. Evidence:
`/hf3fs-jd/data/tmp/opencad-sqlite-discovery-terminal-fix-20261012-v3/`.
Aquinas subsequently received new-HEAD original terminal rc 0/13 controls at
08:56:09 and runtime rc 0/eight controls at 08:56:07. The old runner P2 is
closed, with no P0/P1. A separate P2 remains in the newly added selftest's
own final binding read; Lovelace is assigned a new isolated two-file repair.
Evidence: `/hf3fs-jd/data/tmp/opencad-sqlite-discovery-terminal-aquinas-20261012-v1/`.

Render producer offline first-cause/single-slot repairs passed independent
controls, but new tools integration `53eb3731280b95cfc46f1dd01a3a5cfbbfea3470`
retains admission selftest FAIL: 34 tests, eight failures and two errors.
The test overwrote a tracked producer and retained old untracked/missing-chain
assumptions; the real gate refused Git source closure. Anscombe's separate
fixture-only DCO repair is 33a4f489c33f2c372e32d1092f8484b588772ce1.
Its original postcommit parent returned rc 0 at 08:46:49: 40 tests, 42 actual
child CLIs (five positive rc 0 and 37 expected rc 1), 16 diagnostics and zero
failures/errors/skips. All original 34 controls remain. Independent Euler
review is complete; this commit is not part of 7a48ee4. Evidence:
`/hf3fs-jd/data/tmp/opencad-render-admission-fixture-fix-20261012-selftest-v1/`.
Euler's fresh new-HEAD selftest returned original rc 0 at 08:55:34.356347,
the same 40 tests/42 CLI/16 diagnostics, no new P0/P1/P2 and zero drift.
Evidence: `/hf3fs-jd/data/tmp/opencad-render-admission-fixture-fix-euler-selftest-20261012-v1/`.
Candidate/check/launch-plan or offline-controls rc 0 is not GPU admission.
No new real GPU/HTTP rendering or performance benefit was measured here.

Official competitive research remains PARTIAL. Full licensed role coverage,
per-domain command manuals, vendor workflow execution, representative industrial
scale and CATIA/SolidWorks full-function parity remain unaccepted.
Production private-service cutover, automatic mirroring, native assembly STEP
hierarchy/metadata and new-source RTX performance are also unfinished.

The long-term goal remains active. Its tool currently cannot edit the objective;
this handoff corrects repository roles and current ordering. Actual native
capacity remains six leaders; the eight-leader convention was disclosed as
tool-capacity-limited. Flash is DENY/off, with no nested execution pools.

Recovery uses named forward revert commits and the existing primary-first
synchronizer. No force, remote history rewrite, unknown-file cleanup or credential
change. Future source changes require a new freeze and acceptance; code
synchronization must not be described as live service deployment.
