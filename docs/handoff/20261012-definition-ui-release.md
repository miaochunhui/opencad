# Definition UI Release

## Frozen Source And Acceptance

Checkpoint: 2026-10-12, Asia/Shanghai. The accepted and synchronized source is
`eb19211ca7ca4b9304678fe747026009234d1515`, clean in
`/data/dev/wt-opencad-definition-ui-integrated-20261012`.
Git tree: `e31561a3ffd46836f83e425f88c05e2364f7aeb5`.
There are 429 committed files. The all runner's byte/path SHA256 is
`ac8e6d9885971cea8fa5298883b67b1578a3e31930145dc522775d0ab18b11ad`;
the independent consumer's committed byte/mode manifest SHA256 is
`7f21f5da582be28dfb9ab61223345b14abd68a96253217c534b9c0d802d2d02a`.
The browser capture's source SHA256 is
`1cdad5d37b5857966d31c1ba805b12fd0470b3b0ca912f23ba2a6bdcda4cae70`.
These different manifest schemas are not interchangeable Git IDs.

Seven ordinary forward commits follow the accepted backend 6a2dd:

- `a8654a3743be27eceabb3f38d13397a2606f1b80`: configuration CRUD UI and browser gates.
- `199181cfdf6ee7d0c75859c982d78d867e68b1aa`: sentinel and browser evidence closure.
- `349ddc18af1998b7d886b2464ee8269939ebf8ca`: history receipt revision binding.
- `188d7a528f38cef4f37be895682a28c82383cc73`: integration-compatible historical fixtures.
- `7301489daad9f95fb78597ff40c5c1618637901a`: accepted backend handoff.
- `42641de4f7fb48482c93068b0722519cbc0aebc3`: private retained-state clearing.
- `eb19211ca7ca4b9304678fe747026009234d1515`: exact shield selector subsets.

The chair received all parent **3211 original rc0**, ending
**12:37:33.173004**: 2520 tests, 2507 passes, 13 original skips, zero
failures/errors, plus 15 HTTP checks. All six children returned rc0.
Source/HEAD remained stable; no skips are counted as passes.
Euler consumer **91110 original rc0**, ending **12:39:50.457263**, passed
five source/stage/log/count/exit checks against the exact 429-file set.
Source/input drift is empty; HEAD/tool drift is false.

Darwin's real browser parent **78403 original rc0** completed at
**12:30:44.727015**: 18 workflows, nine each at desktop 1440 and mobile 390,
28 geometry measurements, 514 captured responses and 88 artifacts.
Darwin reader **64845 original rc0** verified all 18 at **12:31:51.997383**.
Aquinas's separate offline readback verified 18 at **12:33:19.831091**.
Original process exits belong to the chair/named owners; this author only
read their retained results and did not rerun browser, readers or all.

All evidence roots below are under `/hf3fs-jd/data/tmp/`.

| Evidence root / result.json | SHA256 |
| --- | --- |
| `opencad-definition-ui-integrated-all-20261012-v1` | `cf17b70e864c19c4418846cfab1ce4315b915932fcf753ab87eaf809761a4b6e` |
| `opencad-definition-ui-integrated-euler-all-readback-20261012-v1` | `1c637287982b4348dc4d27629fb4fd4ac547061c239095e210e141dc37b4e88c` |
| `opencad-config-crud-final-browser-20261012-v1` | `109dc6bbbaccfef36bcce12da508c760e7e8120f3f2aa0e2d4bf8e8ba34902ca` |
| `opencad-config-crud-final-readback-20261012-v1` | `a8a7f0156a679e0481d5e0f2141b112d5b74a4dbbfc22a4d337d6b86344760b4` |
| `opencad-definition-ui-integrated-aquinas-20261012-v1/browser-readback` | `a0e62fd5595d553a91d9c0aa4521917b5b8acc6c05266b54df29593a4fc17ab1` |

## Publication And Visible Boundaries

The chair received sync parent **32406 original rc0**, completing
**12:47:00.620775**, SYNCED/PASS: 1314 reachable objects and the exact
seven-commit tail above. Final main refs all returned eb19211:
GitLab **12:46:57.358840**, Gitee **12:46:58.132094**,
GitHub **12:46:59.343415**.
Evidence: `opencad-definition-ui-integrated-three-repo-sync-20261012-v1`.
Result SHA256:
`95ed89e1df13407309e4fab271de4d8184b5109f92a323779a322569edd6b7b3`.

Euler postpublication result/marker completed at **12:49:50.769258**:
PASS, seven checks, `LIVE_THREE_REF_VERIFIED`, exact 429 committed
byte/mode files, empty source/all/input drift and false HEAD/tool drift.
Its fresh refs all returned eb19211 at GitLab **12:49:49.254843**,
Gitee **12:49:49.949231**, GitHub **12:49:50.546862**.
Evidence: `opencad-definition-ui-integrated-euler-postpub-20261012-v1`.
Result SHA256:
`3425e74ea4c26a417a1fa914d46721025feb5f74097f405c514c16e74402f41e`.
The postpublication original-parent exit receipt is still pending the
owner's handoff; the recorded actual_rc=0 is not substituted for that receipt.
The consumer independently records both original Git push exits as 0.
Its sync outer rc is not retained in the input files; the chair's original
32406 receipt above supplies that separate fact. GitHub payload bytes and
force=false are source-checked, not independently captured HTTP payloads.

GitLab on r640HA, `infra/opencad`, remains primary; Gitee
`sdfwefqaa/opencad` and GitHub `miaochunhui/opencad` are explicit copies.
This is sequential source publication, not automatic mirrors, global atomicity,
HA failover, Git HTTPS recovery or production deployment. Historical master
and the original local worktree's protected untracked assets remain intact.

Anscombe visually read all 18 PNGs and matched their retained SHA values.
Sixteen screenshots select Simplified Chinese; the final two
`1440/expired_identity_isolation.png` and
`390/expired_identity_isolation.png` select English. Their base 3D controls
are English while the added panel labels remain Chinese. This is the added
panel's i18n gap, not evidence of zh-CN contamination. Chinese captures also
show overrides and raw English error details; model identifiers, configuration
names and units must be distinguished from translatable UI labels.
Full-site language completion is not accepted.

The captures show a perforated plate, suppression removing the hole, and
an empty second-identity project/viewport. This bounded visual observation
does not replace numerical STEP/BRep/mesh readers or certify industrial parity.
The visible renderer is ANGLE Vulkan SwiftShader, not RTX3080. No new GPU
performance or hardware-accelerated browser claim follows from these PNGs.

## Continuation And Offline Readback

The long-term industrial Web CAD goal remains active. The current goal tool
can update status but not objective; this versioned handoff corrects stale
ordering without closing the long-term goal. Three-repository source migration,
bounded private-session transaction/API closure and this configuration CRUD UI
acceptance/publication are completed within their recorded scopes.
They are not still waiting for a first migration or first API/UI integration.
Remaining work includes industrial workflow breadth, complete GD&T/MBD,
representative current-source RTX measurement/admission, panel and whole-site
i18n, and separately authorized private-production cutover. No production,
credentials, permissions or service state changed here.

Old all/browser/source-binding failures remain immutable. AutoCAD guide
fetch 99395 rc1, unknown-cache refusal, absent raw/body and empty-web corrections
remain PARTIAL; no retry or remembered product claim was adopted.
Do not inherit this source acceptance for a new language implementation.

This independent documentation successor is based on eb19211 at
`/data/dev/wt-opencad-definition-ui-release-docs-20261012`, branch
`codex/definition-ui-release-docs-20261012`. Only this handoff and the latest
checkpoint in `docs/REPOSITORIES.md` change; existing history is preserved.
It is not included in the 429-file all or the eb19211 sync and is not pushed.
The chair separately verifies and publishes these documentation bytes.

Existing offline reader for the accepted product source, with a fresh output
directory; no remote reads, browser, HTTP server or FreeCAD execution:

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-definition-ui-integrated-20261012/tools/gateway_publication_review.py --source-root /data/dev/wt-opencad-definition-ui-integrated-20261012 --commit eb19211ca7ca4b9304678fe747026009234d1515 --all-evidence /hf3fs-jd/data/tmp/opencad-definition-ui-integrated-all-20261012-v1 --baseline-evidence /hf3fs-jd/data/tmp/opencad-definition-integrated-all-20261012-v1 --publication-evidence /hf3fs-jd/data/tmp/opencad-definition-ui-integrated-three-repo-sync-20261012-v1 --out /hf3fs-jd/data/tmp/opencad-definition-ui-release-chair-offline-next
```

For the clean committed documentation successor, the same reader supports
`--all-source-root` and `--all-commit eb19211...`, with `--source-root` and
`--commit` set to the new tree/full HEAD, both exact `--allow-doc-path` values,
and `--publication-commit eb19211...`. It verifies documentation-only relation,
not new product all or publication of the successor. Do not pass
`--remote-readback` until the chair explicitly wants fresh live ref reads.
The infra edit resolver returned outside-root rc2 for the authorized OpenCAD
doc target; this is retained, not relabeled PASS or repaired by changing rules.
Recovery uses named forward commits/reverts and the established primary-first
sync tool, never force, credential changes or unknown cleanup.
