# Definition Backend Release

## Frozen Source And Scope

Checkpoint: 2026-10-12, Asia/Shanghai. The accepted backend source is
`6a2dd2a8a8bd505b72ea6f18de86819d061a506b`, clean in
`/data/dev/wt-opencad-definition-integrated-20261012`.
Git tree: `94f2ede6f2ac1ddb407a249f6cb51d32f011a2e6`.
The complete committed source has 426 files. The all runner's byte/path source
SHA256 is `30e686747542c96e40a678885c98120e526c646f54836e2c1464840106a30b93`;
the definition gate's byte/mode source SHA256 is
`40ae94ce2f1cf8af3fb3d9b0788419fb60f78caff394790927842b2f2aacf4a7`.
These fingerprints are not Git commit IDs.

Five ordinary forward commits follow `16bd37d5c453a80403948d699ca5c6ba57cf3531`:

- `c4621d716c529440ad316796b34b7a19368e86fd`: atomic configuration definition API.
- `260d4c550e838b550072c42d9697d3df3dfb8481`: complete response evidence.
- `61c412946a44f23bc2b9825588112cc916ac902d`: response workflow identities.
- `20c9f35231f03206252a8806eb87ded0086f7883`: generic integration source binding.
- `6a2dd2a8a8bd505b72ea6f18de86819d061a506b`: nested Git metadata refusal.

This handoff is the sole write in the independent branch
`codex/definition-release-docs-20261012`, worktree
`/data/dev/wt-opencad-definition-release-docs-20261012`, based on 6a2dd.
Its new documentation bytes are not covered by the recorded 426-file all.
It does not change product, repository roles, research or production.

## Accepted Backend Evidence

The chair received all parent session **80118's original rc 0**. It finished
at **11:37:39.190946**: 2487 tests, 2474 passes, 13 unchanged original skips,
zero failures/errors, plus 15 HTTP checks. Source/HEAD remained clean and
stable. Evidence:
`/hf3fs-jd/data/tmp/opencad-definition-integrated-all-20261012-v1/`.
Result SHA256:
`8555b1557dcd3b3502a794584c0a88e495e0bebc2b50b3e3759479e20aca3e85`.
Twelve skips require the absent OCP/OCCT path; one concerns unnecessary
fixture teardown under the real module. Skips are not passes.

Fermat's real core parent **55611 rc 0** passed 29 tests with 10 measurements;
real HTTP parent **84851 rc 0** passed 24 tests with 134 captured responses
and eight measurements. Offline readers **83450/35476 rc 0** verified their
respective complete evidence. The 134 responses are observed output, not
a hardcoded completeness threshold. Evidence roots:

- `/hf3fs-jd/data/tmp/opencad-definition-integrated-core-20261012-v1/`
- `/hf3fs-jd/data/tmp/opencad-definition-integrated-http-20261012-v1/`
- `/hf3fs-jd/data/tmp/opencad-definition-integrated-core-readback-20261012-v1/`
- `/hf3fs-jd/data/tmp/opencad-definition-integrated-http-readback-20261012-v1/`

Parent **77019 rc 0** accepted twelve negative controls; each actual reader
returned the expected rc 1/FAIL, not a product success.
Evidence: `/hf3fs-jd/data/tmp/opencad-definition-integrated-response-controls-20261012-v1/`.
Aquinas independently received **22542/36199 rc 0**, 29/24 readback checks:
`/hf3fs-jd/data/tmp/opencad-definition-integrated-aquinas-20261012-v1/`.
Euler's source-bound all consumer **56281 rc 0** completed five checks:
`/hf3fs-jd/data/tmp/opencad-definition-integrated-euler-all-readback-20261012-v1/`.
These original exits were received by their named owners/the chair; this
document does not relabel them as exits received by its author.

Anscombe independently received offline controls **1735 rc 0**, 16/16,
and nested controls **28222 rc 0**, five cases with child exits 0/0/1/1/1.
Ordinary/root-worktree Git metadata passed; nested directory/file/symlink
metadata failed with `nested_git_metadata`. Complete HTTP offline reader
**18921 rc 0** verified 24 checks. Core flow mutants were actually refused:
**61199 rc 1** `http_flow_exchange_set_mismatch`,
**58673 rc 1** `http_flow_test_fixture_mismatch`,
**15956 rc 1** `http_flow_actor_slot_mismatch`, and
**49097 rc 1** `http_project_origin_missing`.
Evidence: `/hf3fs-jd/data/tmp/opencad-definition-integrated-anscombe-20261012-v1/`.
The 11:32:03/11:35:59 source preflights differ only in capture time.
No new HTTP, FreeCAD or all execution was performed by that offline review.

## Publication And Open Gates

The chair received synchronization parent **53185's original rc 0** at
**11:42:38.632405**. It returned SYNCED/PASS, 1238 scanned objects and the
exact five-commit tail above. Final main reads all returned 6a2dd:
GitLab **11:42:35.264567**, Gitee **11:42:36.009875**,
GitHub **11:42:36.676103**.
Evidence: `/hf3fs-jd/data/tmp/opencad-definition-integrated-three-repo-sync-20261012-v1/`.
Result SHA256:
`e25eb48baa88b92c14768b1563300769697f99f7ac80d336f68fe39c12c57a37`.
GitLab on r640HA remains primary; Gitee and GitHub are explicit copies.
Historical Gitee master and the original workspace's protected assets remain
unchanged. This is sequential code synchronization, not automatic mirroring,
global atomicity, Git HTTPS recovery, HA failover or service deployment.

Euler received postpublication parent **35728's original rc 0**; its result
and marker completed at **11:45:06.078502**, PASS with seven checks and
`LIVE_THREE_REF_VERIFIED`. The chair received this original-exit handoff.
It binds the 426-file committed byte/mode set, all stage/log/count/skip closure,
recorded transport/exact tail/primary-first order and fresh three main refs.
Source/input drift is empty and HEAD drift is false. Fresh refs returned 6a2dd
at GitLab **11:45:04.630025**, Gitee **11:45:05.304046** and
GitHub **11:45:05.861971**; this consumer read no credentials.
Evidence: `/hf3fs-jd/data/tmp/opencad-definition-integrated-euler-postpub-20261012-v1/`.
Result SHA256:
`347409b2b178860a59f269aff4bb199ef09d68d00c968a36d3bea47564fd8bc7`.
This closes the pending original-parent receipt, not by inference from the
recorded `actual_rc` field. This one-file handoff is a later documentation-only
successor, not part of the recorded 6a2dd synchronization. It is not pushed;
the chair will select it into the later UI publication batch for separate
reviewed publication. No UI acceptance is implied by that plan.

The current UI P2 remains HOLD, with Darwin owning its isolated repair and
Aquinas assigned independent review. Fermat's four-commit UI integration is
in a separate worktree; it is not part of the accepted backend freeze.
Do not inherit the backend all for a later UI source or browser result.
Industrial full-function parity, complete GD&T/MBD, new-source RTX performance,
GPU admission and private-production cutover remain unaccepted.
No production, credential, permission or service changes are claimed.

Original failures remain immutable, including the fce response-flow before-red
at `/hf3fs-jd/data/tmp/opencad-definition-response-flow-before-20261012-v1/`,
the b263 false-success nested-Git result under
`/hf3fs-jd/data/tmp/opencad-definition-source-binding-anscombe-review-20261012-v1/independent-nested-git-negative/`,
and `/hf3fs-jd/data/tmp/opencad-config-integration-all-20261012-v1/`.
Expected negative FAIL markers are preserved, not rewritten as PASS.
Unreadable-directory traversal was not newly certified by this review.

The actual infra resolver edit attempt returned rc 2/FAIL, `outside-root`,
for this OpenCAD document path. The resolver supports the infra tree, not
this independent repository. Under the chair's explicit scope, work follows
OpenCAD AGENTS.md and the fully read current global/applicable rules.
No router, rule source, identity chain or permission was changed or bypassed.
Recovery uses named forward commits/reverts and the existing reviewed
primary-first synchronizer, never force, history rewrite or unknown cleanup.
