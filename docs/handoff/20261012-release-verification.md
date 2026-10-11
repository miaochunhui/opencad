# Frozen Release Verification And Primary Outage

## Current Result

The reviewed Web-only release is committed locally at `9a62904`.
Its clean frozen worktree is
`/data/dev/wt-opencad-private-release-20261011`.
Do not edit it or overwrite the nine original untracked private-entry
files in `/data/dev/opencad`.

The repository roles are configured: r640HA GitLab is primary; Gitee and
GitHub are code copies. The new four-commit tail is SYNCED: all three
main refs independently returned the frozen release at
2026-10-12 01:04:01-01:04:03 +08:00. An earlier attempt failed before any
push at 00:43:51 and is preserved below. This is not deployment or
industrial parity.
This documentation-only continuation has its own worktree:
`/data/dev/wt-opencad-release-handoff-20261012`.

## Final Acceptance

Evidence roots below are under `/hf3fs-jd/data/tmp/`.
The main agent collected the original all-stage process exit 0 and read
its terminal markers/result. Independent review recomputed all five
test-log hashes and confirmed counts and completion markers.

- `opencad-private-release-all-20261012-v1`: PASS, completed
  2026-10-12 00:38:43 +08:00; 2305 tests, 2292 passed, 13 original skips,
  zero failures/errors, plus 15 HTTP selftest checks. All five child exits
  were 0; before/after source fingerprints agree and source drift is empty.
- `opencad-private-release-browser-20261012-v1`: 30/30 PASS, completed
  00:27:37 +08:00; desktop and 390-pixel screenshots exist and were viewed.
  Receipt recovery retains the current page and unsaved inputs.
- `opencad-private-release-render-20261012-v1`: 35/35 PASS, completed
  00:27:33 +08:00; actual RTX 3080, real cookie/project database/HTTP path,
  14 PNG responses, explicit CPU fallback and three original CLI restarts.
  `opencad-private-release-render-readback-20261012-v1` independently
  reread all 14 PNGs, response hashes and CLI logs at 00:35:50 +08:00:
  `PRIVATE_RENDER_EVIDENCE verdict=EVIDENCE_VERIFIED suite=PASS`.
  This is correctness evidence, not representative performance benefit.
- `opencad-private-release-static-20261012-v2`: static PASS, completed
  00:27:27 +08:00; 192 runtime files and no Git-archive gaps.
  `opencad-private-release-static-readback-20261012-v1` rechecked the
  198-file source binding at 00:41:00 +08:00, with empty source drift.
  The tool explicitly retains `production_ready=false`.

The final Web regression includes candidate-seal corruption controls and
the surface durable-history/replay/domain guard. These do not replace or
expand the earlier limited assembly/gateway claims. Earlier FAIL results
remain in their original evidence directories.
See `20261011-private-integration.md` for their scope and coordinates.

The 13 skips are not passes: twelve require the absent OCP/OCCT path,
and one checks teardown of a fixture unnecessary with the real module
present. No test was deleted or changed into a new skip.

## Retained Synchronization Failure

`opencad-private-release-sync-plan-20261012-v1` completed at
00:36:09 +08:00 with PLANNED/PASS, 747 reachable objects scanned,
source stable and previous main `1c67344` read from all three services.
The four linear commits fit the reviewed exact-object API limits.

`opencad-private-release-sync-20261012-v1` ended with original process
exit 1 and `REMOTE_QUERY_RC_128`. Its last Git command was the
GitLab `ls-remote`, exit 128, with `Permission denied (publickey)`.
Independent review counted 84 command records: zero pushes and zero API
writes. The failure preceded mutation; this was not a partial push.
GitHub Git HTTPS's older failure is separately retained.

Read-only checks during that outage found:

- The original infra administrative SSH route responds as `r640-61`.
  The host reports boot time 2026-10-12 00:42:11 +08:00.
- Strict host verification succeeds and the unchanged existing GitLab
  key is offered, but the remote rejects GitLab authentication.
- `gitlab-runsvdir.service` is enabled but inactive/dead, with no active
  start job. All eight listed GitLab services report runsv not running.
  Port 80 refuses connections.
- The GitLab data mount is present and writable XFS. This does not prove
  database integrity or crash recovery.
- Current-boot GitLab logs contain mount/timer events, but no supervisor
  startup event. The host also reports a failed legacy mirror service.
  Its membership has not been audited for OpenCAD; do not claim that it
  provides OpenCAD's three-target synchronization.
- Gitee main remains `1c67344`; historical master remains `2918a47`.
  The last successful three-way readback was at 00:36. GitLab's later
  state is unavailable, not a newly verified remote SHA.

The reboot precedes the failed attempt, but its requester and why GitLab
did not start are UNATTRIBUTED. The causal relation to SSH rejection is
not proven. No service, permission, key, credential, known-host record,
network entry or production document was changed here.
Do not guess credential damage or silently select another endpoint.

## Resume And Recovery

The outage subsequently recovered without a service operation by this
OpenCAD task. The host's journal records supervisor startup at
00:53:39 +08:00; the responsible operator and underlying cause remain
unattributed. At 01:01, read-only checks obtained HTTP readiness 200,
all eight GitLab services running and main `1c67344` through the same
unchanged strict-key route. This changed external state, rather than an
unmodified retry, permitted a new plan.

`opencad-private-release-recovered-plan-20261012-v1` completed
01:01:56 +08:00 with PASS. The explicit synchronization in
`opencad-private-release-recovered-sync-20261012-v1` completed
01:04:04 +08:00 with original process exit 0, SYNCED/PASS, 747 scanned
objects and stable source/configuration. GitLab and Gitee actual pushes
returned 0. GitHub returned the exact four existing commit IDs before its
non-forced main update. All three final refs returned `9a62904` at
01:04:01, 01:04:02 and 01:04:03 respectively; this is sequential
verification, not globally atomic publication.

The prior failed attempt still has zero push/API writes and exit 1.
Its failure is not reclassified by the successful recovery.
For another outage, first confirm maintenance has finished and starting
GitLab is authorized. OpenCAD restart permission is not authority to
interrupt unrelated GitLab maintenance.

Read-only checks use the original infra SSH route, service status, HTTP
health, strict-key authentication and repository ref query. Preserve logs,
diagnose the supervisor's failure to start, then perform only the explicitly
authorized recovery. Independently accept HTTP, database-backed SSH
authentication and current main before resuming synchronization.
Do not reconfigure, rewrite SSH configuration or replace keys as a shortcut.
Starting/stopping GitLab affects other users and needs a scoped recovery plan.

After the external state has actually changed, use the existing reviewed
tool with new evidence directories:

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-private-release-20261011/tools/github_api_sync.py --action plan --source /data/dev/wt-opencad-private-release-20261011 --commit "$(sudo -n -u infra git -C /data/dev/wt-opencad-private-release-20261011 rev-parse HEAD)" --out /hf3fs-jd/data/tmp/opencad-private-release-recovered-plan-v1
sudo -n -u infra python3 -B /data/dev/wt-opencad-private-release-20261011/tools/github_api_sync.py --action sync --source /data/dev/wt-opencad-private-release-20261011 --commit "$(sudo -n -u infra git -C /data/dev/wt-opencad-private-release-20261011 rev-parse HEAD)" --out /hf3fs-jd/data/tmp/opencad-private-release-recovered-sync-v1
```

Inspect the plan before sync. No concurrent synchronizers, blind retry,
force, deletion or bypass of primary ordering. API transport must preserve
exact blob/tree/commit IDs and `force=false`. Read all three final refs and
actual exits. This separate documentation commit can follow after its
product/runtime diff is verified empty; it does not inherit whole-tree
acceptance. Do not merge over the protected primary untracked files.

For new product work use a new branch/worktree and repeat acceptance for
changed product source. Source rollback uses named revert commits, not
rewritten remote history. Do not copy live SQLite files without their WAL
or restore over live data.

## Goal Correction And Remaining Work

The existing long-term goal stays active. Its current tool permits only
complete/blocked status changes, not objective edits; this handoff is the
current ordering correction, not a claim that the objective was rewritten.

The accepted release's primary-first three-target synchronization is closed
for this dated snapshot; future commits do not automatically synchronize.
Current priority is production TLS/proxy,
coordinated backup/restore, revocation/quotas/abuse controls and industrial
breadth/scale. Complete official research and representative RTX performance
acceptance remain unfinished. No OpenCAD production cutover occurred.

Owner is the main agent unless separately assigned. Closure requires
actual remote readbacks for synchronization, real proxy/storage/recovery
acceptance for deployment, and per-workflow numerical and negative controls
for industrial capability. Full GD&T/MBD, manufacturing, simulation,
enterprise domains and large assemblies are not completed by this regression.
Do not close the overall goal or claim CATIA/SolidWorks full-function parity.
