# OpenCAD Repository Roles

## Current Verified State

On 2026-10-12 at 03:06:12-03:06:14 Asia/Shanghai, GitLab, Gitee
and GitHub independently returned
`a43ecef01023efa7abaaeb3888f88ac17f5902ac` for `refs/heads/main`.
The explicit synchronization completed at 03:06:15 with original process
exit 0, `SYNCED/PASS` and 809 reachable objects scanned. Evidence is under
`/hf3fs-jd/data/tmp/opencad-recovery-three-repo-sync-20261012-v1/`.
This supersedes the older snapshots below, not their retained failures.

GitLab `infra/opencad` remains primary; local `origin` points to its
existing internal SSH endpoint. Gitee and GitHub are code copies. Gitee's
historical default `master` is not current Web CAD code; use `main`.
This task does not install automatic mirroring or change service deployment.

The clean publication tree is
`/data/dev/wt-opencad-industrial-reliability-release-20261012`.
The original `/data/dev/opencad` workspace retains nine untracked assets;
its local main has deliberately not been advanced over those files.
Do not use a forced checkout or remove those assets to update it.

The preceding product snapshot `67e0562` passed 2341 tests:
2328 passed, 13 original skips, zero failures/errors, plus 15 HTTP checks.
The newly committed recovery tool passed 49 separate tests with no skips,
errors or source drift; independent review and source/log hashes agree.
Its three files do not change product runtime bytes. These separate
acceptances must not be described as a new whole-tree test run or as
production/industrial parity. See
`docs/handoff/20261012-primary-repository-sync.md` for the current boundary.

The primary is r640HA GitLab, `infra/opencad`. Gitee
`sdfwefqaa/opencad` and GitHub `miaochunhui/opencad` are one-way code
copies, not independent development authorities. These roles follow the user's
2026-10-11 instruction. The verified synchronization below is a dated snapshot,
not a promise that future commits automatically reach all three services.

Use `tools/repository_sync.py` as infra. Its fixed internal GitLab URL is
`ssh://git@192.168.1.181/infra/opencad.git`, using the existing GitLab SSH
key and strict host-key verification. Gitee keeps its existing SSH identity.
The old VIP `192.168.1.52` was unreachable and the old public SSH host key
changed during investigation. Do not delete known-host records or disable
verification to make that old route pass.

GitHub uses the existing classic PAT file
`/home/mch2/.passwd_github_miaochunhui`. Never print its contents, put it in
a URL/config/argv, or commit it. The tool checks the actual account before any
repository operation. The existing token file is readable by other users;
permission remediation requires a separately approved credential operation.

## Explicit Synchronization

First inspect GitHub with `--action inspect-github`. If absent, use the
explicit `--action create-github`; creation is public, without a README or
independent initial commit. Existing wrong-owner, private, archived or
non-writable repositories fail closed.

For a clean, reviewed OpenCAD worktree, supply its complete HEAD as `--commit`
and select `--target gitlab --target gitee --target github`. `--action plan`
scans reachable commits, trees and blobs (including deleted-file history),
checks each selected remote, and changes no remote ref. `--action sync`
publishes the same fixed SHA to `refs/heads/main`, GitLab first, and verifies
each target by a fresh ref query. `--create-gitlab` explicitly authorizes
push-to-create after the specific unavailable-project response and successful
existing-key authentication; that response alone does not prove absence.

Every invocation requires a new `--out` under `/hf3fs-jd/data/tmp/`.
Results and failures are external, never source-tree artifacts. Actual
command status, partial remote outcomes and safe diagnostics are retained.
No automatic retries, forced pushes, mirror pushes, ref deletion or global
atomicity claim. Existing Gitee `master` is preserved even if the independent
OpenCAD migration started new history. New development goes to `main`;
historical branches are not silently overwritten.

Every synchronization must include GitLab. Replace refs are refused and object
reads explicitly ignore replacements. Automatic annotated-tag propagation is
disabled, so only the named main ref is sent. Source pre-push hooks still run.
Push intent is saved before execution; post-push failures retain actual status
and attempt readback. All selected refs are read again at the end with individual
timestamps, not claimed as an atomic multi-service snapshot.

The tool never stages, commits, changes source remotes/HEAD/index, accepts new
SSH keys or changes production. Existing Git hooks are not disabled. GitHub
credentials pass through a narrowly scoped Git credential helper, not through
persistent credential storage. Manual use is not an installed automatic
mirror or scheduler; that operational connection remains separately tracked.

## Reproducible Tests

Run `python3 -I -B tools/test_repository_sync.py --out NEW_EXTERNAL_DIRECTORY`
as infra. Local bare Git repositories exercise three-target ordering,
fast-forward, preserved history, dirty/wrong-HEAD refusal, divergence,
deleted secrets and rejected-primary partial outcomes. GitHub tests are
synthetic, not proof of live access. Code synchronization is not full CAD
regression, production deployment, GPU acceptance or industrial parity.

## Verified Snapshot

On 2026-10-11 at 20:34:03-20:34:05 Asia/Shanghai, each service's
`refs/heads/main` was independently read back as
`c910e483852eb179a9237ea28d8c28186dbafcd0`, after three actual successful
pushes. Evidence is under
`/hf3fs-jd/data/tmp/opencad-repository-live-sync-20261011-v1/`.
All 676 reachable objects were scanned before publication. The tool's
15-test v5 acceptance and independent review passed. This snapshot contains
the reviewed committed source, not the nine untracked private-entry files
in the primary local workspace.

GitLab and GitHub HEAD resolved to main. Gitee's original master remains
`2918a471b2d9584b7f43dbf0b3e524989f8b728a`; it is historical, not the
new Web CAD branch. Explicitly clone main:

```bash
git clone -b main ssh://git@192.168.1.181/infra/opencad.git
git clone -b main git@gitee.com:sdfwefqaa/opencad.git
git clone -b main https://github.com/miaochunhui/opencad.git
```

Choose one appropriate accessible endpoint. The GitLab internal endpoint
requires the existing registered GitLab SSH identity. Public SSH host-key
remediation remains unresolved, so no claim of a working public GitLab SSH
route is made.

The main local workspace is `/data/dev/opencad`. Its origin is GitLab,
gitee is the preserved Gitee remote, github is the GitHub remote, and local
main tracks origin/main. The repository-scoped SSH command selects the
existing GitLab private key with strict host verification; it does not install
or alter a key. Credential-bearing GitHub pushes use the sync tool, not plain
unauthenticated HTTPS push.

To undo only these local role changes, first verify config and HEAD have not
changed since this operation. Keep source commits and untracked assets. Switch
back to the retained `codex/opencad-bootstrap-s7` branch, remove only the new
origin/github remote entries, rename gitee back to origin, and unset the newly
added repository-scoped core.sshCommand. Do not delete remote repositories or
rewrite remote history. The GitLab/Gitee/GitHub code snapshots are durable
publication, not something to erase as a local-config rollback.

Complete CAD regression v1 ran 2245 tests: 2232 passed, 13 original skips,
zero test failures/errors, plus the original 15-check HTTP selftest. Its
overall verdict is FAIL because the three new sync sources changed during
the run. V2 also started before the last reviewed tool correction; neither
run certifies a stable final source. These earlier failures remain recorded.

V3 finished on 2026-10-11 at 20:56:23 Asia/Shanghai with
`INDEPENDENT_ACCEPTANCE verdict=PASS stage=all`: 2245 tests, 2232 passed,
13 original skips, zero failures/errors, and 15 HTTP selftest checks.
All 368 source files remained unchanged during the run. Its source fingerprint
was `f6f8cfad2cb39074e954c7cd48fc62b54e2ccd08a979e950c408382a02108adc`.
Evidence is under `/hf3fs-jd/data/tmp/opencad-repository-full-20261011-v3/`.
This certifies that dated source snapshot; later product edits require new
acceptance and must not inherit it.

The subsequent actual three-target synchronization finished at 21:02:54
Asia/Shanghai. GitLab, Gitee and GitHub each returned
`60756563f5b57138693a1c54220737921465c632` for main; 680 reachable objects
were scanned. Evidence is under
`/hf3fs-jd/data/tmp/opencad-repository-live-sync-20261011-v3/`.
The v2 synchronization attempt failed with `REMOTE_QUERY_RC_128`; its failure
is preserved, not reclassified as a successful push.

At 22:58 Asia/Shanghai the resumed account inspection, three-target plan and
fresh ref queries again confirmed the configured roles and the same main SHA.
The existing GitLab SSH route returned hostname `r640-61`; this verifies the
endpoint response, not physical HA failover. GitHub authentication confirmed
`miaochunhui` and `PRESENT_PUBLIC_WRITABLE`, using the user-specified PAT file
without exporting its value. New evidence is under
`/hf3fs-jd/data/tmp/opencad-repository-resume-account-20261011-v1/` and
`/hf3fs-jd/data/tmp/opencad-repository-resume-plan-20261011-v1/`.

This follow-up changes only the repository status documentation. Explicit
synchronization is still not an installed automatic mirror. Gitee's default
branch is still the retained historical master; use main for current code.
No credential permissions, host keys, service state, or private-entry files
were changed. Complete CAD regression is not industrial parity, private
production readiness, or deployment acceptance. The broader goal stays active.

## Explicit API Transport

During the resumed documentation synchronization, GitHub Git HTTPS stopped
responding before any push. Both default and HTTP/1.1 read-only HTTP probes
received zero bytes within 20 seconds, while `api.github.com` returned HTTP 200.
The stalled Git transport child was explicitly terminated; its actual Git
status 128 and signal-15 diagnostic are retained under
`/hf3fs-jd/data/tmp/opencad-repository-resume-sync-20261011-v1/`.
This was an operator-stopped failed preflight, not an automatic timeout,
successful push, or proof of the underlying network cause. A separate GitLab
query was reset by the peer; a later read-only query worked. No proxy, host key,
credential file, permission, or remote URL was changed.

`tools/github_api_sync.py` is an explicit alternative transport, not an
automatic fallback or scheduled mirror. Run it as infra with `--action plan`
first, then `--action sync`, `--source` pointing to a clean reviewed worktree,
its full `--commit` and a fresh external `--out`. It only supports up to eight
ordinary linear commits, 32 changed regular files per commit and 1 MiB per
file. Root, merge, signed/extended commits and deletions are refused before
publication. The same existing GitHub account, repository and PAT are used.
GitLab and Gitee are updated by the original checked Git path before GitHub.
The API upload must return the exact existing blob, tree and commit IDs before
the named main ref is fast-forwarded with `force=false`. All three refs are
read again. Any failure stops and retains partial outcomes; it does not erase
already published objects or retry. Never run two synchronization invocations
concurrently. This entry does not install a cross-tool synchronization lease.

Local tests independently reconstruct API blobs, trees and commits using real
Git, and cover dirty input, unsupported headers/deletions, wrong object IDs,
remote drift, primary failure, read-only planning and forbidden force/scope.
Run `python3 -B tools/test_github_api_sync.py --out NEW_EXTERNAL_DIRECTORY`.
The original v1 test fixture incorrectly deleted required LICENSE and hit the
earlier source-integrity guard; its failed result remains preserved. Synthetic
API tests are not live GitHub upload or production acceptance.

## Frozen Release And Recovered Synchronization

The private Web integration is locally committed at `9a62904` and its
frozen-source full regression completed on 2026-10-12 at 00:38:43
Asia/Shanghai: 2292 passed, 13 original skips, zero failures/errors/source
drift and 15 HTTP checks. Browser, RTX correctness and Git-archive reviews
also completed; none certifies production readiness or industrial parity.

The first attempt did not push. At 00:36:09 all three main refs
were read as `1c67344`, with a successful read-only synchronization plan.
The actual synchronization at 00:43:51 failed before mutation: GitLab
ref query returned 128/public-key rejection, with zero push/API writes.
Read-only checks found r640-61 booted at 00:42:11 and its GitLab supervisor
inactive, all eight GitLab services without runsv, and HTTP port 80 refusing
connections. The reboot requester, missing startup cause and relationship
to SSH rejection remain unconfirmed; no service or key was changed here.

GitLab subsequently recovered externally: supervisor startup is recorded
at 00:53:39, and later read-only checks obtained HTTP readiness 200, eight
running services and successful repository reads through the unchanged key.
A new plan passed at 01:01:56. The explicit recovered synchronization
completed at 01:04:04 with original exit 0: actual GitLab/Gitee pushes
succeeded, and GitHub accepted the exact existing Git objects before the
non-forced main update. All three main refs were independently read as
`9a62904` at 01:04:01-01:04:03. Evidence is under
`/hf3fs-jd/data/tmp/opencad-private-release-recovered-sync-20261012-v1/`.
The earlier failure remains retained; this is not an automatic mirror or
global multi-service atomicity.

Keep primary-first ordering for future commits. See
`docs/handoff/20261012-release-verification.md` for evidence, remaining
boundaries and recovery entries. This documentation-only continuation is
not part of the frozen full-regression source snapshot. The primary local
working tree still retains its nine original untracked assets; it has not
been merged over them or advanced to the published release.
