# Primary Repository And Recovery Tool Delivery

## Verified Repository Roles

Current collection: 2026-10-12, Asia/Shanghai.
The independent OpenCAD repository is `/data/dev/opencad`, not the shared
infra repository. Existing configuration was read rather than overwritten:

- `origin`: `ssh://git@192.168.1.181/infra/opencad.git`, r640HA GitLab primary.
- `gitee`: `git@gitee.com:sdfwefqaa/opencad.git`, code copy.
- `github`: `https://github.com/miaochunhui/opencad.git`, code copy.
- Local `main` tracks `origin/main`; historical `master` tracks Gitee master.

The existing GitLab SSH identity and strict host verification are preserved.
GitHub authentication uses the user-specified classic PAT file
`/home/mch2/.passwd_github_miaochunhui` through the reviewed scoped tool.
No credential value is included in Git, arguments, URLs or evidence.
No key, known-host record, permission, proxy, service or scheduler was changed.
The earlier finding that other users can read the PAT file remains a
separate credential-permission risk; this delivery does not remediate it.

The original workspace's nine untracked private-entry files remain protected.
Its local main remains `1c67344`; a clean independent publication worktree
contains the newer reviewed source:
`/data/dev/wt-opencad-industrial-reliability-release-20261012`,
branch `codex/industrial-reliability-release-20261012`.
Do not force a local update over the protected files.
Gitee's retained default master is historical; current development is main.

## Actual Publication And Acceptance

The resumed read-only plan at 02:58:17 returned three main refs equal to
`67e0562c5137fa39defc2a0773ddf155dac573d6`.
It was `PLANNED/PASS`, not a new push. Its original process exit was 0:
`/hf3fs-jd/data/tmp/opencad-three-repo-resumed-readback-20261012-v1/`.
The preceding actual 02:40 synchronization is separately retained under
`opencad-gateway-release-sync-20261012-v1`.

Named DCO commit `a43ecef01023efa7abaaeb3888f88ac17f5902ac`,
created at 03:02:43, adds only:

- `tools/service_recovery.py`
- `tools/test_service_recovery.py`
- `docs/design/service-recovery.md`

The 49-test integrated recovery result finished at 02:44:41 with
`SERVICE_RECOVERY_TESTS verdict=PASS`, original rc 0, no failures/errors,
no skips and no source drift:
`/hf3fs-jd/data/tmp/opencad-service-recovery-integrated-20261012-v1/`.
Its three source hashes and test-log SHA were independently reread before
the named commit. The product directory is unchanged relative to `67e0562`.
An independent reviewer found no P0/P1 within this recovery scope.

The new plan finished at 03:04:55, then actual synchronization finished
at 03:06:15 with original process exit 0, `SYNCED/PASS`, stable source
and configuration, 809 reachable objects scanned and one exact linear
commit. GitLab and Gitee pushes each returned 0. GitHub accepted the
existing blob/tree/commit IDs before the non-forced main update.
Fresh final readbacks all returned `a43ecef01023efa7abaaeb3888f88ac17f5902ac`:

- GitLab: 03:06:12.943277
- Gitee: 03:06:13.633471
- GitHub: 03:06:14.230507

Evidence roots are
`/hf3fs-jd/data/tmp/opencad-recovery-three-repo-plan-20261012-v1/` and
`/hf3fs-jd/data/tmp/opencad-recovery-three-repo-sync-20261012-v1/`.
This is sequential source publication, not a globally atomic update,
automatic mirror, physical HA failover test or production deployment.
The documentation follow-up carrying this handoff is not included in
the recorded `a43ecef` snapshot and requires its own synchronization.

The last full product regression is
`opencad-gateway-release-all-20261012-v1`: 2341 tests, 2328 passed,
13 original skips, zero failures/errors and 15 HTTP checks, completed
02:26:21 with stable source. Its readback is
`opencad-gateway-release-readback-20261012-v1`.
Recovery acceptance is separate; no full-regression rerun is claimed here.
No existing failure, skip or historical evidence was rewritten.

## Remaining Work And Recovery

Source publication is complete for the stated commit, not for every
later independent seat commit. Exact-loop product integration has been
dispatched to a new independent worktree based on `67e0562`; it requires
fresh full regression and private HTTP/STEP acceptance before publication.
GPU, official research and deployment-inspection tools have separate
reviewed commits awaiting integration. Their old source-snapshot results
must not be promoted to acceptance of a newer product.

The live inspection at 02:54:54 completed successfully but its separate
private-deployment verdict is FAIL: the production unit still runs the
legacy server, and anonymous state returns a document while private auth
and project endpoints are absent. No production restart or migration was
performed here. The legacy drawing root must not be overwritten by the
new private-project database. A future authorized cutover needs saved
drawings, a new private root, account provisioning, proxy/auth acceptance,
verified coordinated recovery and a reversible service/config switch.

The recovery tool verifies two SQLite databases, their immutable history
and receipts, includes committed WAL, rejects corrupt/overlapping/existing
targets and revokes all old sessions on restore. Historical enabled/admin
flags still require entitlement reconciliation before service adoption;
the gateway does not yet enforce that operator policy. Production recovery,
power-loss durability, encrypted/offsite retention and full CAD semantic
replay remain untested. GPU performance and CATIA/SolidWorks industrial
full-function parity are not certified.

The existing long-term goal remains active. Its current tool cannot edit
the objective, so this handoff records the corrected repository status
and pending ordering. Native capacity is six seats: an attempted extra
seat was rejected by the tool. Flash remains DENY/off; no nested agents.

Future synchronization uses the reviewed repository tools with one fixed
full commit, clean source and a new external evidence directory. Inspect
the plan before sync; never run two synchronizers concurrently, force,
delete refs, disable hooks or retry an unchanged failed state.
Rollback source changes with named `git revert` commits and forward
synchronization. Do not erase remote history or modify credential files.
