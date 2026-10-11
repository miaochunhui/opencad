# Independent Durable Integration Review

## Scope

The independently written tool runs the real ProjectGateway, HTTP handler,
Workspace and SQLite ProjectStore in a new local fixture. It does not execute
production requests, alter the reviewed sources, change permissions or reuse
the store's own test helpers. Only this tool and this document belong to this
review seat. Identity implementation/security auditing belongs to the separate
security seat; this review only checks whether a revoked live session prevents
a durable mutation after CAD computation.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-durable-20261011/tools/project_store_independent_review.py --source-root /data/dev/opencad --runtime-root /data/dev/opencad-independent-review-runtime-20261011-v4 --out /hf3fs-jd/data/tmp/opencad-durable-20261011/independent-review-v4
```

Choose previously unused runtime/evidence directories. Existing paths are
rejected, never cleaned or overwritten. The tool only creates its own loopback
listener; it has no production URL option. The first runs used explicit
`--auth-root /data/dev/wt-opencad-auth-20261011` as a provisional dependency
binding while the chair integrated auth/catalog. It loads that exact source,
but all CAD/store/gateway dependencies come from source-root. Results record
`same_source=false`; this cannot be signed as main-tree integrated acceptance.
Run v3 used integrated main alone, with `same_source=true`.
Missing dependencies fail, never fall back to
infra or synthetic authentication. Password/session material is generated in
memory and is not reported. Each fixture owns its loopback server and project.

## Acceptance

The tool hashes source code/data and checks actual runtime module origins.
It records before/after source drift, named tests, failures, skips, log SHA,
elapsed time and UTC collection times. FAIL remains FAIL even if many other
checks passed. No retry or production certification is implied.

Tests independently cover malformed revision/SHA/request-ID rejection before
action, concurrent same-ID replay versus different-ID CAS competition,
receipt-before-action and cross-route request fingerprinting, full-workspace
ABA, standard/undo/redo/save/open history, real SQLite-denied COMMIT on all
mutation routes, invalid candidates, read/export/query non-mutation, revoked
session after action, actual committed-but-lost response and receipt readback.
Create-project replay and changed-body conflicts use real HTTP and deterministic
initial document IDs. Extra probes require malformed command containers to
produce a client error, reject duplicate UUIDs and non-integer geometry, and
prohibit acknowledgement of an export file deleted on request exit.

SQLite failures use its real transaction authorizer rather than replacing
commit with an unconditional exception. Lost-response injection happens only
after the actual store COMMIT, then reopens and reads the receipt; it is not
a physical power-loss test. HTTP concurrency synchronizes real CAD actions,
not mocked geometry. Query/export success must leave full head and all durable
rows unchanged. Schema includes doc, undo/redo and standard; actual geometry
validity and complete document invariants remain Workspace/gateway concerns.

## Boundaries

The store's atomic COMMIT does not make arbitrary external file side effects
exactly-once. Request-scoped temporary exports must not be returned as durable
paths after deletion. Export should use the authorized read-only route and
return bytes for that revision, or implement separately durable publication.
Same-ID concurrent actions can both compute before either receipt exists;
only one database mutation is promised. Serializing/isolating irreversible
side effects is still the gateway's job.

Source review must distinguish current defects from accepted limits:
authorization and project storage use separate databases; the live-session
check immediately before COMMIT is not a cross-database transaction lock.
This review tests a deterministic revocation-before-final-check window, not
all possible microsecond revoke/commit interleavings. Multi-host replication,
physical disk failure, pruning/quota, industrial CAD coverage and production
release readiness are outside scope.

Findings and frozen hashes belong to each external result/log. This document
does not pre-fill PASS, claim source stability before terminal readback, or
rewrite historical evidence after the chair changes source.

## Collected Verdict And Binding

At `2026-10-11T02:32:25.468031+00:00` (10:32:25 +08), run v3 was **FAIL**:
18 discovered/executed tests, 13 passing methods, 5 failing methods,
8 failure records (including subtests), 0 errors and 0 skips.
Source drift was empty, actual module origins matched main and
`same_source=true`. A passing transaction probe does not override these reds.
This is independent fixture evidence, not production acceptance.

Evidence: `/3fs/research3fs/data/tmp/opencad-durable-20261011/independent-review-v3/result.json`
and sibling `tests.log`. Log SHA-256:
`f6ae2e0181fd9367195520664dd66d624d801fa9cd8ca83695098c364206c68e`.
Run v1 retained the incomplete layer query fixture; v2 corrected that fixture
and exposed a dependent stale-CAS subtest. V3 isolates each schema candidate,
so successful bad input cannot invalidate another negative control.
Earlier evidence is historical and is not rewritten.

All following source paths are under `/data/dev/opencad/oss-alternatives/`:

| Source | SHA-256 |
| --- | --- |
| `apps/opencad/project_gateway.py` | `8185f0cc1e7fe3d1283da4856868e4bdd26efc3b707b9c3c19ad536f12f5a394` |
| `apps/opencad/project_store.py` | `3b74d494b2a25ded31af81f5212b57b3efc7b0c378a750cdf23ee78ab0fea76c` |
| `apps/opencad/secure_web.py` | `56a75b995c44e1f746554b26cfa9e012aae05630db9641fe2853c3e33068f391` |
| `apps/opencad/server.py` | `4f821cc7fe037cde502ea563e9d49a613578fedf37fbbeccc87541e1e82abd7c` |
| `packages/cadcore/cadcore/docmodel.py` | `b8a98ade1a52464e7889aa7005cfe8f4ca8b955f18a29c242b99fb18f4af8fe8` |

The external result contains the complete source hash list and runtime origins.
The source was changing between runs; no later source inherits this verdict.
The worktree scan independently passed at
`/3fs/research3fs/data/tmp/opencad-durable-20261011/independent-review-scan-v2/result.json`;
that hygiene check is not a green gateway verdict.

## Findings And Required Closure

1. **P1: a receipt acknowledges a deleted PDF.** Gateway lines 63-64 create a
   request-scoped temporary output directory; line 255 runs `export_pdf`;
   lines 296-300 commit its response and then delete the directory.
   With a valid CAS base, `command="export_pdf", args={"out":"review.pdf"}`
   returned HTTP 200, advanced revision 1 to 2 and stored a receipt, although
   the returned path no longer existed. Replay cannot repair that path.
   Reject file-writing commands in the mutation gateway (include the legacy
   `_DOC_PATH_ARGS` family), or implement separately durable publication.
   Authorized GET PDF/DXF bytes already pass read-only head/history tests.
   Closure: no false durable path acknowledgement; rejection leaves all rows
   unchanged, or durable bytes remain readable on replay/reopen.

2. **P1: invalid scalar coordinates are durably accepted.** Each independent
   `/api/open` candidate with `geom.x1` equal to `true`, `null` or `"1"` returned
   HTTP 200 and changed durable rows. `docmodel.py:771` skips these types as
   metadata; range checking also skips bool/null. Gateway line 269 trusts the
   incomplete load gate and commits. Validate known coordinate/radius keys
   through integer-nm rules, without rejecting legitimate boolean metadata.
   Closure: all three cases return 400/422 with head, snapshots and receipts
   unchanged; valid documents still round-trip.

3. **P1: duplicate entity UUIDs enter immutable history.** Duplicating one line
   in an otherwise valid `/api/open` candidate returned HTTP 200 and persisted
   both identical UUIDs. `check_invariants` reports `uuid_duplicate`, but
   `docmodel.py:885` does not include it in `LOAD_BLOCKING_PREFIXES`.
   Closure: same-container duplicates are rejected before COMMIT, while
   intentional cross-kind shared references remain valid.

4. **P2: malformed command containers become HTTP 500.** A versioned mutation
   with `command=[]` or `{}` reaches gateway line 219 set membership and raises
   `TypeError` before strict command validation. Durable rows remained unchanged.
   Closure: strict string command and mapping args validation yields HTTP 400
   without action, history or receipt changes.

5. **P2: invalid client document schema becomes HTTP 500.** Opening
   `doc='{"schema_version":1}'` raises `DocumentError` at gateway line 268;
   `handle` lines 115-131 does not map that client input error. HTTP returns
   `internal_error`; rows remain unchanged. Closure: 400/422 with a stable
   machine code for rejected input, retaining the internal root cause.
   Unexpected corrupt stored/computed history should remain fail-loud rather
   than being mislabeled as a client error.

All five findings belong to the chair's gateway/document integration; this
seat modified neither product. The core store CAS/receipt contract passed
independent malformed-base, same-ID concurrency, competing-ID CAS, ABA,
response-loss/reopen and real COMMIT-denial checks. Power-loss durability,
multi-process HTTP deployment and cross-database revocation races remain
explicitly unmeasured.
