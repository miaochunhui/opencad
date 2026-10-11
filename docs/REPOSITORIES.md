# OpenCAD Repository Roles

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
run can certify a stable final source. A new all-stage run must bind the
final committed worktree. Do not promote phase-level green or repository
synchronization into industrial parity, private production readiness, or
full regression acceptance.
