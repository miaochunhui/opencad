# Exact Loop Release Continuation

## Primary Repository And Published Snapshot

On 2026-10-12 at 03:53:31-03:53:33 Asia/Shanghai, actual explicit
synchronization and three independent final reads returned
`128070a78f617960987835061528a14dafc40002` for main. The parent process exit
was received as 0; `result.json` reports `SYNCED/PASS`, 833 reachable objects
scanned, no source drift and the exact two-commit linear tail. GitLab and
Gitee use the unchanged checked Git transport; GitHub accepted the original
blob/tree/commit identities before a non-forced main update.

Evidence is retained under
`/hf3fs-jd/data/tmp/opencad-research-docs-batch-plan-20261012-v1/` and
`/hf3fs-jd/data/tmp/opencad-research-docs-batch-sync-20261012-v1/`.
The earlier resumed 03:38 read was a plan, not a push.

Repository roles remain:

- Primary: r640HA GitLab, `ssh://git@192.168.1.181/infra/opencad.git`.
- Copy: Gitee, `git@gitee.com:sdfwefqaa/opencad.git`, current branch main.
- Copy: GitHub, `https://github.com/miaochunhui/opencad.git`, branch main.

Gitee's historical master is preserved. This is explicit synchronization,
not an installed automatic mirror, an atomic multi-service transaction or
a physical HA failover test. Existing credentials, permissions, host keys,
proxy configuration and production service have not been changed.
The GitHub PAT remains file-sourced, with no value in Git or evidence.

The clean publication worktree for that snapshot is
`/data/dev/wt-opencad-research-publication-batch-20261012`.
The primary local `/data/dev/opencad` tree retains its nine original
untracked private-entry assets and its older local main. Do not overwrite
those assets or force a checkout to make local main look current.

The research snapshot remains PARTIAL: 14 official sources, 70 facts,
10 unexecuted workflows and 25 offerings. Anchors and source/reference
closure do not prove full semantic entailment, workflow execution,
licensing completeness or industrial feature parity. These two commits
do not change the product runtime relative to `a6d1ad3`.

## Integrated Candidate And Acceptance Defects

The independent release worktree is
`/data/dev/wt-opencad-industrial-reliability-release-20261012`.
At the continuation checkpoint its HEAD is
`bfa18f033f101dab6b25ceb08443aafaeea2ac5c`, clean. Four exact UV-loop commits
were picked without conflicts; their nine net files have the same blob IDs
and modes as `af96616d3eda7b2c4a209342e2664acb63ec0068`.
That source's full/private results are retained, but are not acceptance of
the new release tree.

The research selftest hardening is integrated. On 2026-10-12 at
03:43:51 Asia/Shanghai its independent run returned 36/36 PASS, original
rc 0, four owned-file before/after hashes equal, unchanged HEAD and no
drift. Evidence:
`/hf3fs-jd/data/tmp/opencad-primary-research-selftest-20261012-v1/`.
The default ledger ownership gate is deliberately not relaxed for an
integration tree. This selftest certifies the offline validator only.
Independent review notes that its HEAD mutation control is not HEAD-only.

Two observed verifier defects prevent promoting a bare verifier PASS:

- GPU evidence readback accepted an incomplete three-CPU-sample ledger
  with absent full-source binding and empty artifact/CLI manifests at
  03:28:23 Asia/Shanghai. Original input and false-success output remain
  under `opencad-render-tool-integration-audit-20261012-v1`.
  GPU measurement admission and tool integration stay paused until
  the verifier rejects the original counterexample and independent
  negative controls. No new GPU performance claim is made.
- Exact-loop evidence readback checked nonempty/self-consistent counts,
  but not all mandatory workflow labels, and recorded rather than
  enforcing HEAD stability. Independent hardening must reconcile actual
  producer checks, HTTP requests, measurements and geometry/readback
  identities, with deletion/duplicate/renaming/HEAD-only controls.
  The original real 191 checks, 46 HTTP requests and 67 geometry checks
  remain preserved, not retrospectively rewritten.

The current GitHub API transport also refuses regular-file deletion.
The exact-loop history contains a legitimate deletion of an intermediate
document. An independently tested exact parent-tree/null-SHA implementation
is being prepared; no history rewrite, ref deletion, forced push, capacity
increase or automatic transport fallback is authorized.

## Immediate Acceptance And Operational Boundary

After the three bounded verifier/transport fixes are reviewed, freeze a
clean release source and obtain a new source fingerprint. Run the existing
`tools/acceptance.py --stage all` and the exact-loop private review against
that fingerprint with fresh external evidence and a new local runtime.
Receive each actual process exit, inspect counts/skips/log hashes and
source drift, then independently read back geometry and durable receipts.
Never reuse the older source's whole-tree verdict for this candidate.

Native tool capacity remains six leaders; the attempted seventh seat was
rejected. Each editing task has its own worktree and exclusive files.
The shared Flash pool is DENY/off, without nested agents or expansion.

The live private-deployment inspection is still FAIL: the legacy
anonymous server served state while private auth/projects/assembly routes
were absent. No cutover or restart is claimed. Required cutover conditions
include saved drawings, a separate local private root, legitimate account
provisioning, a trusted cross-host proxy-to-loopback chain and coordinated
recovery. Failure must not silently reopen anonymous legacy data.
Do not overwrite `/var/lib/oss-web/opencad`.

The long-term goal remains active. Its tool cannot edit the objective;
this continuation corrects the repository and immediate ordering:
primary-first source synchronization is established, exact-loop integration
awaits new-source acceptance, verifier defects are repaired before new
admission, and private production/GPU/industrial parity remain unaccepted.
Only use named forward commits/reverts for source recovery; never erase
remote history, delete unknown work or change existing credentials.
