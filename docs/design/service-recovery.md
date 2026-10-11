# Local Service Recovery

This operator tool coordinates the accounts and projects SQLite databases.
By default it operates only on independent children of
`/data/dev/opencad-private-fixtures`. An operator can explicitly authorize an
existing narrow local service parent using `--service-root-authorization`.
This enables recovery of real external Web-service data, not only demos.
The parameter is a tool capability, not production-operation approval.
This task does not deploy, modify live services or accept HA/industrial parity.

## Interface

Run as infra with bytecode disabled, from the reviewed source worktree:

```bash
sudo -n -u infra python3 -B tools/service_recovery.py --action backup --source-root /data/dev/opencad-private-fixtures/example/source --package-root /data/dev/opencad-private-fixtures/example/package --out /hf3fs-jd/data/tmp/example-backup
sudo -n -u infra python3 -B tools/service_recovery.py --action verify --package-root /data/dev/opencad-private-fixtures/example/package --out /hf3fs-jd/data/tmp/example-verify
sudo -n -u infra python3 -B tools/service_recovery.py --action restore --package-root /data/dev/opencad-private-fixtures/example/package --target-root /data/dev/opencad-private-fixtures/example/restored --out /hf3fs-jd/data/tmp/example-restore
sudo -n -u infra python3 -B tools/test_service_recovery.py --runtime-root /data/dev/opencad-private-fixtures/example-tests --out /hf3fs-jd/data/tmp/example-tests
```

For an explicitly authorized local service parent, pass the same reviewed
parent on **each** call (including verify). All source/package/target paths must
be strict descendants of that parent. For example, after the operator has
created and authorized `/data/tmp/opencad-recovery-scope`:

```bash
sudo -n -u infra python3 -B tools/service_recovery.py --action backup --service-root-authorization /data/tmp/opencad-recovery-scope --source-root /data/tmp/opencad-recovery-scope/example/source --package-root /data/tmp/opencad-recovery-scope/example/package --out /hf3fs-jd/data/tmp/example-authorized-backup
sudo -n -u infra python3 -B tools/service_recovery.py --action verify --service-root-authorization /data/tmp/opencad-recovery-scope --package-root /data/tmp/opencad-recovery-scope/example/package --out /hf3fs-jd/data/tmp/example-authorized-verify
sudo -n -u infra python3 -B tools/service_recovery.py --action restore --service-root-authorization /data/tmp/opencad-recovery-scope --package-root /data/tmp/opencad-recovery-scope/example/package --target-root /data/tmp/opencad-recovery-scope/example/restored --out /hf3fs-jd/data/tmp/example-authorized-restore
sudo -n -u infra python3 -B tools/test_service_recovery.py --runtime-root /data/dev/opencad-private-fixtures/example-default-tests --service-scope-runtime-root /data/tmp/opencad-recovery-scope/example-scope-tests --out /hf3fs-jd/data/tmp/example-scope-tests
```

Authorization cannot be empty/relative, contain `..`, name a missing parent,
traverse symlinks, cover a Git worktree or select a network/unsupported SQLite
filesystem. Filesystem/system umbrellas with fewer than three named components
(such as `/`, `/data`, `/data/tmp`) are rejected. A service root itself or a
directory nested beneath another service root cannot be the authorization
parent. No authorization is inferred from a package manifest: omission always
retains fixture-only access. Filesystem authorization is not identity-policy,
credential, permission or production-start authorization.

Every output directory must be absent and its parent must exist. Runtime and
package directories must be local, outside Git, not overlapping, not nested
inside another service root, and contain no symlink components. New files use exclusive creation; existing targets are
never adopted, cleaned or overwritten. A failed attempt needs a different new
target/output, after diagnosis. Preserve failed fixtures and original packages.
The known `/hf3fs-jd` alias is verified only for evidence, never SQLite.

The package has `accounts.sqlite3`, `projects.sqlite3`, `intent.json` and a
last-written `manifest.json`. The manifest format is
`opencad-service-recovery-v1`, with `complete=true`, `backup_id`, UTC `at`,
`source_root`, exact tool/dependency source fingerprints and fixed `lock_order`.
`databases.accounts` and `databases.projects` contain file name, byte size,
SHA-256, schema SHA, user/application versions, logical SHA and per-table counts.
`validation` includes product, external account count and owner mapping SHA.
New manifests record `authorization_parent` and `authorization_mode`; external
results record the current explicit argument and validated parent. The recovery
completion record marks entitlement reconciliation required before adoption.
No password, verifier, session token/hash or document content is written to
evidence. The database package itself contains sensitive account material and
must remain private; this is not an encrypted backup format.

External evidence contains exclusive numbered `journal-*.json` and
`result.json`: verdict, rc, first cause code/type/source coordinates and SQLite
error code/name when applicable, evidence errors, timestamps,
source fingerprints/drift, artifact metadata and completion marker.
The process prints `SERVICE_RECOVERY verdict=PASS|FAIL rc=N`.
Evidence write failure forces a failure exit and reports the original cause
plus the evidence exception type on stderr. Partial packages/roots are retained.
If final result durability fails after writing bytes, `terminal-failure.json`
is attempted without replacing `result.json`; this terminal failure and the
original process exit override any earlier PASS bytes. An unavailable evidence
filesystem is not accepted as a completed result.
A success marker is not a substitute for independently checking bytes/counts.

## Consistency And Verification

Backup first acquires auth `BEGIN IMMEDIATE`, then project `BEGIN IMMEDIATE`.
Source transactions are lock-only and rolled back, not committed. All gateway
operations spanning both databases must use this same order and hold the auth
guard through project commit; this tool does not patch or certify that gateway.
Independent read-only connections perform SQLite `backup` while both locks
remain held. Calling backup on the same write-transaction connection can
deadlock and is explicitly avoided. No live database is copied as raw bytes:
committed WAL content is included by SQLite backup.
Read-only transactions are pinned after both locks and kept until the writable
lock connections close, preventing backup's last-writer close from checkpointing
the source WAL into the main file. Source transactions never change business
rows; SQLite shared-memory lock bookkeeping is not byte-for-byte immutable.
If a WAL-mode source has no WAL yet, acquiring a write lock can create an empty
WAL sidecar. This is recorded separately from main-database or committed-WAL
byte drift; the tool never promises that absent auxiliary files remain absent.

Offline verification checks SQLite integrity and foreign keys, exact schema,
auth product `opencad`, auth schema/application IDs `1/0` and projects
`1/0x4F434144`. Auth currently has no nonzero application ID; this limitation
is explicit, not silently fabricated. It checks all snapshot canonical bytes
and SHA, contiguous history, complete request/response/create receipts and
canonical SHA, receipt bases and times, initial create responses, and exact
owner-to-local/external-account mapping. Snapshot workspace validation includes
all doc/undo/redo JSON strings but does not execute CAD geometry or prove every
industrial document invariant. A manifest hash is corruption detection, not a
signature or proof against an attacker able to rewrite the entire package.

Restore verifies the original package before creating a new root, copies via
SQLite backup, verifies the copy, then revokes **all existing sessions** in a
transaction before writing `recovery-complete.json`. Original packages remain
unchanged. Account IDs, disabled flags, external mappings, projects, history and
idempotent receipts remain intact. External authentication is not reconfigured.
Historical account privilege/enabled state is necessarily restored from the
snapshot, including historical enabled/admin flags. All sessions are revoked,
but restoring a snapshot does not reconstruct later entitlement changes.
Reconcile current operator/identity-provider entitlements before allowing new
login or adopting the restored service; this is a separate required policy,
not something the backup package can prove.

Directory components are anchored with held `O_NOFOLLOW` descriptors; file
sets, regularity/hardlinks and directory/database identity are checked. SQLite opens
through the held directory and checks database inode stability. This is not a
sandbox against a malicious process with the same UID mutating files during
SQLite opens (including an ABA replacement). Only trusted, exclusive service-data
ownership is supported. Stop/drain the restored instance before any adoption;
never start a service against a partial root or one without a verified completion
record. The existing gateway does not enforce this operator completion policy.

## Acceptance Boundary

Selftests use real local SQLite, WAL and separate spawned OS processes (not
agents). They retain original exits and counts and exercise competing writers,
timeout/failure, partial backup/restore, journal/result failures, byte and
semantic corruption, owner/external mapping, reopen, revocation, receipt replay,
source logical/physical drift and path/race rejection. The runner binds exact
tool/test/auth/store bytes before and after; skips are reported, not passes.
Explicit-scope tests create only new children of the authorized
`/data/tmp/opencad-recovery-scope` test parent; they check real CLI backup,
verify, restore and reopen, source/package drift, default/no-inheritance,
empty/outside scope, nested service, existing target and partial failure.
No existing production root is touched and no Web service is started.

Untested: production storage, crash/power-loss durability, malicious same-UID
file races, encrypted/offsite retention, large-data performance, CAD semantic
replay, later entitlement reconciliation, external identity-provider
availability and the concurrent gateway seat's final integration.
