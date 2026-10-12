# Terminal Repair And Three-Repository Release

## Source And Repository Roles

Collection date: 2026-10-12, Asia/Shanghai.
GitLab on r640HA is the primary:
`ssh://git@192.168.1.181/infra/opencad.git`.
Gitee `sdfwefqaa/opencad` and GitHub `miaochunhui/opencad` are explicit copies
of main, not independent development authorities or installed automatic mirrors.
The existing GitHub credential is read by the scoped synchronizer from the
user-specified file; no value, permission or identity method was changed.
The earlier credential readability risk remains unresolved.

The original `/data/dev/opencad` main remains `1c67344`, with nine protected
untracked files. Do not force-update or delete those assets. The clean frozen
integration is `60a0e9cb97950a321bf1c8a6f76dd581056b5cbc` in
`/data/dev/wt-opencad-terminal-repair-integration-20261012`.
Two named infra DCO commits follow 74a8a6c:

- `2c3aa2443c7d664b187d0ec17efc7d7b766269da`, picked from a1b74c8.
- `60a0e9cb97950a321bf1c8a6f76dd581056b5cbc`, picked from 0f81d01.

Only `tools/test_sqlite_discovery_fixtures.py`,
`tools/test_sqlite_discovery_terminal.py` and
`docs/design/sqlite-runtime-isolation.md` changed. Product files under
oss-alternatives have zero diff from 74a8a6c. These fixes preserve the
chronological first cause, subsequent failures, completed counts and null
unobserved source/log fields. They do not establish physical-disk durability
or attribute the historical SIGBUS.

## New Frozen Acceptance And Actual Publication

All times below are on 2026-10-12 in Asia/Shanghai. Original process exits
were received, not inferred from a JSON actual_rc field.

The new runtime selftest returned rc 0 at 09:43:55, 8/8 controls.
The terminal parent session 26341 returned rc 0 at 09:44:00, 21/21 controls.
There are two positive and nineteen expected-failure terminal actors; the
runtime probes comprise two positives and six expected refusals.
Evidence roots:

- `/hf3fs-jd/data/tmp/opencad-terminal-repair-integration-runtime-20261012-v1/`
- `/hf3fs-jd/data/tmp/opencad-terminal-repair-integration-controls-20261012-v1/`

Euler's independent offline readback returned original rc 0 at 09:53:42,
six checks, 421 committed byte/mode bindings, exact three-file write scope
and zero source/input drift. It checked actor logs, first/secondary errors,
null and partial counts, and retained partial publication bytes.
The result is
`/hf3fs-jd/data/tmp/opencad-terminal-repair-euler-review-20261012-v1/readback/result.json`.
SHA256: `f916ea02b7762b1385d4f67cce33c8d164187c657b491c2372865bd96f269c4c`.
The external one-off audit file is not a versioned reusable delivery or a
replacement for the repository's accepted verification tools.

The new all parent session 30289 returned original rc 0 at 09:57:11.
All six children completed with original rc 0: SQLite controls 24,
bootstrap controls 20, HTTP checks 15, core 1761 (1748 passes and 13 original
skips), Web 589 and DXF 56. Totals: 2450 tests, 2437 passes, 13 skips,
zero failures/errors, plus 15 HTTP checks. Source and HEAD before/after
are unchanged and clean, pinned to 60a0e9c.
Evidence: `/hf3fs-jd/data/tmp/opencad-terminal-repair-integration-all-20261012-v1/`.
Twelve skips concern the absent OCP/OCCT path; the other is an unnecessary
fixture teardown under the actual module. None is counted as passing.
The earlier 4b3 failure and unattributed SIGBUS evidence remain unchanged.

The chair's source-bound consumer session 70054 returned original rc 0 at
09:58:14, six checks and 22/22 controls, verifying all 421 committed files,
exact six-child stage plan, test identities, logs, runtime and original skips.
Fermat's independent consumer completed at 10:00:14 with the same bounded
readback; its original process receipt belongs to that reviewer.
Evidence: `opencad-terminal-repair-all-chair-readback-20261012-v1/` and
`opencad-terminal-repair-all-fermat-review-20261012-v1/`.
The consumer was the clean already-reviewed 69efa18 tree, not the held
596fa36 successor. Its known static-loop scrutiny limitation remains; the
actual complete logs and source were independently read in this release.

The plan parent session 3482 returned original rc 0 at 09:58:45, with all
three prior refs 74a8a6c, 1140 scanned objects and the exact two-commit tail.
The actual sync parent session 66576 returned original rc 0 at 10:01:08,
`SYNCED/PASS`. GitLab and Gitee original Git push exits were 0; GitHub accepted
the existing blob/tree/commit identities before its non-forced main update.
Final main readbacks:

- GitLab: 10:01:06.420771, 60a0e9c.
- Gitee: 10:01:07.053588, 60a0e9c.
- GitHub: 10:01:07.714739, 60a0e9c.

Evidence: `opencad-terminal-repair-three-repo-plan-20261012-v1/` and
`opencad-terminal-repair-three-repo-sync-20261012-v1/`.
Postpublication consumer session 84072 returned original rc 0 at 10:02:27,
eight checks and 40/40 controls, exact tail/object receipts, primary-first
ordering and fresh live three main refs. Evidence:
`opencad-terminal-repair-publication-readback-20261012-v1/`.
This is sequential source publication, not globally atomic mirroring,
physical HA failover or production deployment.

This handoff and the repository status update form a documentation-only
successor in `/data/dev/wt-opencad-terminal-repair-release-docs-20261012`.
They are not part of the recorded 60a0e9c all/sync. Verify exact Markdown-only
relation and synchronize that successor separately; do not relabel it as a
new whole-source all.

## Current Follow-On Work And Recovery

The long-term industrial Web CAD goal remains active. The goal tool has no
objective-edit operation; this versioned handoff corrects the repository
roles, completed release and remaining ordering without shrinking the goal.
No production service, current drawing, credentials or permission changed.

The historical native capacity was six; this resumed tool can communicate
with five named leaders. Restoring Lovelace failed with "agent thread limit
reached"; that unavailable slot is not claimed as active. Flash is DENY/off,
without private pools or nested delegation.

- Fermat's atomic definition API/response repair is c6fdd67531bc40c72982c3f912a73fa4366cc559.
  Anscombe independently accepted the bounded response workflow: core 27,
  HTTP 22 and twelve actual CLI refusals. Original source-bound HTTP execution
  recorded 134 responses; this is measured output, not the completeness
  threshold. The earlier missing-tail and wrong-known-test HOLDs are closed
  within that workflow, with original failed controls retained.
- A newly identified integration blocker remains in c6 source(): its historical
  three-file repair scope rejects a real 60a-plus-UI tree. Fermat has a new
  independent task to separate repair ownership from generic current committed
  source/runtime binding, with before-red and Git/dirty/mode/untracked negatives.
  Do not delete the guard or widen a frozen allowlist to make integration pass.
- Darwin's CRUD hardening c1121623c8b538cdb67f5158d3ed7eabef4186de closes the
  original sentinel, terminal and undo/redo-response issues. Aquinas independently
  passed 22 controls and bound the prior browser's 18 workflows, 28 geometry
  witnesses, 514 responses and 88 artifacts. A new P2 HOLD remains: history
  receipt.revision is not checked equal to the after revision. Darwin is assigned
  a new independent test/doc-only repair, missing/wrong revision negatives and
  preserved old browser-source provenance, without inheriting new browser runs.
- Euler's larger publication reviewer 596fa36 remains HOLD. Aquinas ran 31
  controls/83 negatives, then found two P2s: an early return before commands=[]
  can evade static execution reachability; the new test runner's final executor
  binding can discard terminal results. Euler is assigned a new isolated
  successor. Keep the already-published 69 single-tail fix when integrating.
- Official research remains PARTIAL. This resumed web search returned no
  visible source IDs. A chair message incorrectly described remembered URLs
  as successful search results and was immediately withdrawn before adoption.
  Those URLs are candidates only; no recovered-source or date claim was accepted.
  New official capture must use the existing approved, rate-limited route.
- RTX representative production benefit, native assembly STEP hierarchy,
  industrial domain parity and the private-production cutover remain unaccepted.
  Offline rendering controls or historical small-model runs do not open a
  new-source GPU execution/performance gate.

After the API source-binding and CRUD revision repairs receive independent
review, integrate in a new clean tree: bbdd API, fce response capture, c6 flow
binding, its new integration-source repair, bcad UI, c112 hardening and its new
revision repair. Text overlap with the current gateway was read-only predicted
to merge; preserve its surface download/auth/project-transaction changes.
Run fresh complete all, core/HTTP/response controls, real desktop/mobile CRUD
browser, STEP readback and final source/receipt closure before publication.
Old owner results are not acceptance of the integrated tree.

Rollback uses named forward git revert commits and the reviewed primary-first
synchronizer. Never force, delete remote refs, rewrite history, clear unknown
assets or change keys. Use fresh external evidence directories and retain
partial failures. Code synchronization alone does not switch the live service.
