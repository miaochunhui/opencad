# OpenCAD Repository Roles

The intended primary is r640HA GitLab, `infra/opencad`. Gitee
`sdfwefqaa/opencad` and GitHub `miaochunhui/opencad` are one-way code
copies, not independent development authorities. These roles are the user's
2026-10-11 instruction, not proof that synchronization has completed.

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
