# SQLite Acceptance Runtime Isolation

## Scope And Contract

`tools/acceptance.py` owns the regression-runtime gate. It does not change
configuration products, the gateway, SQLite product modules, production
storage, credentials, permissions or services. Evidence remains outside Git
on 3FS. Every acceptance process gets a fresh, short local `/data/tmp/oc-a-*`
root by default; `--runtime-root` requires an absent absolute `/data/tmp`
child, at most 80 encoded bytes.

`TMPDIR`, `TMP`, `TEMP`, `tempfile.tempdir`,
`OPENCAD_TEST_RUNTIME_ROOT` and the HTTP selftest workspace use that same
local root. Child logs bind the actual values, filesystem, SQLite connection
count and observed database paths. No inherited `TMPDIR` can silently put
ordinary temporary SQLite fixtures back on 3FS.

The runtime gate rejects symlinks, `..`, repositories, existing explicit
roots and non-local/non-persistent mounts. It checks the real existing
ancestors with `stat`, not just a path name or a declaration. An empty
explicit runtime declaration fails rather than selecting a fallback.

The CPython `sqlite3.connect` audit event checks each real database filename,
including file URIs and WAL/SHM/journal sidecars, before the native connection
opens. Disk databases must be within the declared runtime and on a persistent
local filesystem. Network URI authorities, 3FS paths, another local root,
symlinks and runtime/tempfile drift fail loudly. In-memory connections do not
require disk placement. Rejected attempts remain in the runtime binding:
catching a refusal inside a test cannot erase it or make the acceptance suite
successful.

This is an acceptance-process guard, not a hostile same-UID sandbox or an
arbitrary external/native program SQLite interceptor. Product roots and
standalone test runners outside this entry point retain their own contracts.
It does not certify production filesystems or native writers that bypass
CPython's SQLite audit event.

## Retained SIGBUS And Diagnosis

The original failure remains immutable:

```text
/hf3fs-jd/data/tmp/opencad-config-suppression-owner-core-20261012-v1/
20261011T223755361719Z/acceptance-smoke/
```

At 2026-10-12 06:38:30 +08 the original store child exited `-7` (SIGBUS);
the outer runner exited 1. Its log ends at
`test_27_document_survives_reopen`, after 26 earlier `ok` lines, with no
complete test-count marker. The marker error is a consequence, not the native
cause. The original four relevant dependency hashes remain available in the
saved source manifest.

The old acceptance entry set `TMPDIR` to its 3FS evidence directory while
validating a separate local `OPENCAD_TEST_RUNTIME_ROOT`. The original store
fixture uses `TemporaryDirectory` without an explicit directory, so its
4096-byte database, 86552-byte WAL and 32768-byte SHM were actually on
`fuse.hf3fs`. The declared local runtime contained no files. That mismatch is
a proven harness defect; it does not by itself establish the direct SIGBUS
cause.

The authorized one-shot diagnostic is explicitly **FORENSICS_NOT_PERFORMANCE**:

```text
/hf3fs-jd/data/tmp/opencad-sqlite-runtime-isolation-20261012-v2/forensics/
```

The original store test/product dependencies were byte-identical in both
arms. The same original `test_27` ran once on 3FS and once on ext4; both
original child exits were 0, with real SQLite-path observations and complete
one-test markers. SIGBUS did not reproduce. Python faulthandler was enabled,
but no native crash stack was generated. The direct old SIGBUS cause remains
**UNATTRIBUTED**; do not turn this result into permission to run SQLite on 3FS
or a performance comparison.

The retained database, WAL and SHM were copied with exclusive creation to a
new local root. SQLite opened only that copy using `mode=ro` and
`query_only=ON`, including WAL; `immutable=1` was deliberately not used because
it could ignore uncheckpointed WAL. `integrity_check` returned `ok`,
foreign-key violations were 0 and schema tables were nonempty. Original
three-file hashes were identical before/after. This does not prove every
CAD index row, transaction history or storage flush.

The new forensic v1 failure remains recorded: the first observer attempted
to treat SQLite's bytes-valued audit path as a `Path` directly and failed
before opening SQLite. The earlier missing-evidence-parent launch also
failed before an actor started. These preparation/observer failures are not
product SIGBUS evidence and are not reclassified by v2.

## Reproduction And Acceptance

Run only with `infra`, Python `-B`, absent evidence/runtime destinations and
this independent tree. No retries reuse or replace old evidence.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-sqlite-runtime-isolation-20261012/tools/test_sqlite_runtime_isolation.py --selftest --out /hf3fs-jd/data/tmp/opencad-sqlite-runtime-isolation-20261012-final-selftest-v1 --runtime-root /data/tmp/oc-sqlite-final-selftest-v1
sudo -n -u infra python3 -B /data/dev/wt-opencad-sqlite-runtime-isolation-20261012/tools/acceptance.py --stage smoke --out /hf3fs-jd/data/tmp/opencad-sqlite-runtime-isolation-20261012-final-smoke-v1
```

The repair-before negative is retained in
`opencad-sqlite-runtime-isolation-20261012-v2/before-red`: the original child
TMPDIR differed from its declared runtime, one test failed, original rc1.
This proves the real child contract can see the old placement defect.

The dedicated isolation controls run as a normal smoke/all/coupling child
before the existing bootstrap/HTTP/store/DXF stages. Controls include real
SQLite/URI positives, 3FS/no-create negatives, another local root, symlinks,
sidecars, false declarations, runtime drift and real CLI preflight failures.
Final acceptance requires every child original rc0, one exact count marker,
count reconciliation, complete local runtime binding, log SHA and stable
source files/HEAD/status before and after. A partial store log is not a pass.

The v4 isolated controls completed 24/24 with original rc0. The v4 real smoke
completed all six children with original rc0: 160 tests, 159 passed, one
original store-teardown skip, zero failures/errors and 15 real HTTP checks.
Store observed 36 actual local SQLite connections; DXF observed 13.
Its root was `/data/tmp/oc-a-g98ex053`, and file/HEAD/status drift was empty.
This precommit result remains bound to its dirty source snapshot, not to a
later clean commit. The source-bound `--verify-result RESULT --out NEW_DIR`
mode rereads all raw logs, markers, totals, paths and current source identity.

The final local smoke can close the repaired harness-placement gate only
after its complete result is read. It cannot erase the old SIGBUS failure,
attribute its native cause, inherit the separate config37/Web58 passes or
claim full industrial, configuration UI, GPU or production acceptance.
