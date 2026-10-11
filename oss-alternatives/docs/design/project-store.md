# Durable Project Store

## Contract

This module provides persistence, not authentication, geometry validation or
command execution. The gateway must authorize each read/write against the
stored owner before exposing a head or receipt. A database is the only head;
an in-memory Workspace is a request-scoped candidate, never authoritative.

```python
ProjectStore(root, *, timeout=5.0)
create_project(owner, *, title, request_id, payload) -> dict
list_projects(owner) -> list[dict]
get_project(project_id) -> dict
read_head(project_id) -> dict
read_snapshot(project_id, revision) -> dict
read_create_receipt(owner, request_id, *, title, payload) -> dict | None
read_receipt(project_id, request_id, *, request_payload) -> dict | None
commit(project_id, *, expected_revision, expected_sha,
       request_id, request_payload, payload, response) -> dict
close()
```

`payload` is exactly `{doc: str, undo: list[str], redo: list[str],
standard: str}`, or a serialized JSON object with those fields. Each doc/history
string must contain a JSON object. Duplicate keys, non-finite values, invalid
UTF-8, extra/missing fields and boolean revisions are rejected. CAD structural
invariants and standard IDs remain the gateway's responsibility. L1 text bytes
are preserved; snapshot SHA-256 covers the entire compact, key-sorted UTF-8
workspace JSON, including undo/redo and standard, not just the document.

Head/get_project returns:

```json
{
  "project_id": "generated UUID hex",
  "owner": "authorized user ID",
  "title": "project title",
  "revision": 1,
  "sha256": "64 lowercase hex characters",
  "snapshot": {"doc": "L1 JSON text", "undo": [], "redo": [], "standard": "gbt"},
  "created_at": "UTC ISO8601",
  "updated_at": "snapshot commit UTC ISO8601"
}
```

List returns the same metadata without `snapshot`, only for the requested
owner. Results are detached copies; changing them cannot mutate stored state.
Revision begins at 1 and advances even when snapshot bytes are unchanged.
`read_snapshot` reads immutable history and sets updated_at to that revision.

Receipt returns:

```json
{
  "project_id": "project ID",
  "request_id": "client request ID",
  "request_sha256": "canonical original request SHA-256",
  "revision": 2,
  "sha256": "committed full workspace SHA-256",
  "response": {"ok": true},
  "committed_at": "UTC ISO8601"
}
```

`request_payload` must be a complete JSON request object containing
`expected_revision` and `expected_sha`, exactly matching the commit arguments.
Include command, arguments and all other inputs that affect its semantics.
For an absent receipt the lookup returns None, not success or revision zero.
The lookup and transactional commit compare full canonical request bytes as
well as recording their SHA. A replay returns the original receipt/response,
before evaluating CAS or a newly computed candidate. Derived workspace or
response values on a retry do not replace the original committed result.

Creation is idempotent on `(owner, request_id)`, bound to title and the initial
workspace. Replays return the original revision-1 head even after later edits;
same ID with another body is a conflict. Login must not create projects.
The UI must use an explicit create action and preserve its initial body/ID
across retries; regenerated document UUIDs are different creation requests.

Errors expose `status` and `code`: InvalidPayload=400, ProjectNotFound=404,
RevisionConflict/IdempotencyConflict=409, StorageFailure=503, StoreCorrupt=500.
RevisionConflict also carries the actual `revision`/`sha256`. Storage errors
retain the SQLite/OSError cause. Failed/ambiguous transactions never acknowledge
success; lookup the original request ID to resolve uncertainty before retrying.

## Transaction And Recovery

Each method uses its own connection; mutations use `BEGIN IMMEDIATE`,
foreign keys, WAL and `synchronous=FULL`. The transaction inserts immutable
snapshot/history, advances the project head and writes the request/response
receipt together. COMMIT must succeed before a result can leave the method.
Triggers prohibit history/receipt overwrite/deletion and non-monotonic heads.
The `(revision, SHA)` CAS prevents A -> B -> A content from accepting an old
revision merely because its SHA matches again.

All data is in `root/projects.sqlite3` plus SQLite-owned WAL/SHM, with no
separate blob publication window. SQLite recovery after a process crash is the
only recovery source, not the old vault's SHA index or a release checkpoint.
Initializing a root syncs created parent entries and the database directory.
SQLite owns write/COMMIT sync; Python `os.fsync` is not a substitute for SQLite's
VFS sync. A lost response after COMMIT is resolved by receipt replay.

Root must be external to Git worktrees, private and on a durable local
filesystem. Known network/FUSE and temporary filesystems, linked components
and non-regular database files are rejected. No chmod, credential setup or
production path is performed here. The process trusts its private data
directory; same-UID hostile filesystem swaps are not a sandbox boundary.
WAL is a same-host design, not a multi-host database. Do not deploy the DB on
3FS; 3FS contains test logs and result evidence only.

## Gateway Ordering And Limits

1. Authorize owner and validate the complete request.
2. Lookup receipt before instantiating Workspace/executing action.
3. Load the exact authorized head and instantiate its full workspace.
4. Compute a candidate and response without externally publishing side effects.
5. Commit with expected revision/SHA and original request body.
6. Return the stored receipt response, attaching its authoritative revision/SHA.

Two concurrent computations may run before either receipt exists. CAS/receipt
uniqueness guarantees one database mutation, not exactly-once arbitrary file
side effects. The gateway must serialize/contain these operations, name private
staging files by request ID and only publish after confirming the receipt.
Export is a separate read-only operation on an authorized immutable revision.
Deletion, ownership transfer, title updates, quotas, pruning, multi-host
replication and external-side-effect outbox are not implemented in this layer.

## Executable Acceptance

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_project_store.py --evidence /hf3fs-jd/data/tmp/opencad-durable-20261011/acceptance-new --runtime-root /data/dev/opencad-durable-fixtures-20261011
```

The explicit local runtime-root holds only isolated test databases; logs,
counts, elapsed time and source fingerprints are external on 3FS. No production
server, HTTP POST, credentials or real user project is touched. Real SQLite
tests cover two competing connections, request replay, ABA, full-history
reopen, before-COMMIT crash rollback and after-COMMIT response loss. Fault
injection covers Python directory sync and SQLite COMMIT authorization failure,
paired with healthy controls and real SQLite query-only write failure.
These do not emulate power loss or prove the disk/controller honors sync.
No tests are mocked into geometric success, skipped or counted as full CAD
regression. The chair must separately run gateway/security/HTTP acceptance.
