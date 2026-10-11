# Local Auth Revocation And Project Commit Guard

## Scope And API

Baseline is `941005d7c45a35f8cbbe8d2dc65c68093a790a3b`. OpenCAD accounts
and sessions remain product-bound; no FreeChat or production credentials,
permission changes or HTTP account-administration routes are introduced.

`AuthController.authorized_transaction(principal, *, owner_id,
allow_admin=False)` is a context manager yielding the live `Principal`, not
a connection. It opens the existing auth `BEGIN IMMEDIATE`, rechecks session
product, revocation, expiration, account enabled state, identity/role equality
and owner/admin permission, then keeps that same connection/transaction open
until the caller exits. `authorize` shares `_authorize_in_db(db, principal,
*, owner_id, allow_admin=False)` but closes its short transaction immediately.
Ordinary reads do not implicitly become caller-spanning transactions.

The owner must come from the project database, never a client field. The
gateway integration is owned by the chair, not this auth change:

```python
# Prepare candidate and preferences before taking the auth lock.
with auth.authorized_transaction(principal, owner_id=stored_owner) as live:
    receipt = store.commit(...)
# Return only the confirmed stored receipt after both contexts exit.
```

Use the guard at the final `store.commit` / `store.create_project` boundary
and before exposing a replay receipt. Candidate construction is outside the
guard. Auth must not be called again from inside the guard: no
authenticate/get_preferences/set_preferences or second auth controller.
Same-controller, same-thread reentry fails immediately with
`auth_transaction_active`/503. This is a misuse detector, not a general
cross-controller/global deadlock detector.

## Ordering And Operator Controls

The fixed lock order is **auth -> project**, also required by backup/recovery.
Never hold a project transaction while waiting for auth. Logout, disable and
revoke-all all serialize on the same auth SQLite writer lock:

- Revocation commits first: the final guard fails 401 and no project head,
  snapshot or receipt is written; an existing receipt cannot bypass the check.
- Guard wins first: project commit/create finishes before the competing auth
  writer can revoke. A saved receipt remains valid historical evidence, not
  permission for a revoked session to read it.
- Caller exceptions release/rollback auth without replacing the project first
  cause. Auth connection/begin/commit errors fail closed with their SQLite cause.

This is same-host revocation linearization, **not a two-database atomic commit**
or two-phase commit. Auth uses a five-second busy wait; a longer guard can
cause the competing operator to receive `auth_unavailable`/503, never false
revocation success. Session expiry is checked on guard entry, not retroactively
after a started authorized commit. Keep the guard short.

Operator APIs require an explicit server-side `operator_mode=True` controller:
`disable_user(user_id)`, `enable_user(user_id)`, `revoke_all_sessions(user_id)`.
They return user ID, current enabled/admin state and newly revoked session count.
Disabled accounts and their current OpenCAD sessions are changed atomically.
Re-enable never clears revoked flags or changes admin; for a legacy disabled
row it revokes remaining old sessions before enabling. Repeated enable of an
already enabled user does not revoke new sessions. Revoke-all affects current
OpenCAD sessions only; it does not prohibit a later fresh login.

Operator mode is trusted process configuration, not proof of human authority.
Do not expose these methods or this flag to untrusted HTTP callers. CLI
authority is the existing OS access to the explicit local auth root. No keys,
passwords or raw tokens are accepted as CLI account-control arguments:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/access_control.py --data-root APPROVED_LOCAL_AUTH_ROOT --disable-user USER_ID
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/access_control.py --data-root APPROVED_LOCAL_AUTH_ROOT --enable-user USER_ID
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/access_control.py --data-root APPROVED_LOCAL_AUTH_ROOT --revoke-all USER_ID
```

`--admin` remains provisioning-only and cannot accompany those actions.
These examples are not production authorization. Unknown targets fail 404;
invalid targets fail 400; non-operator API use fails 403.

## Real Acceptance And Retained First Cause

`tools/auth_revocation_acceptance.py` runs the real `test_access_control.py`
through the existing independent suite entry. Evidence is a new 3FS directory;
SQLite fixtures are retained in a new `/data/tmp` child on local storage.
Logs retain original child rc, exact counts, completion marker, log SHA and
full source manifest/drift. No fixture cleanup or mock authentication is used.

```bash
sudo -n -u infra python3 -B tools/auth_revocation_acceptance.py --out /hf3fs-jd/data/tmp/opencad-auth-revocation-20261012-v1 --runtime-root /data/tmp/opencad-auth-revocation-20261012-v1
```

The pre-fix control uses the old short `authorize` only when the guard is
absent. A real spawned process takes auth `BEGIN IMMEDIATE` with timeout zero
at the final save boundary; it commits revocation before the project save.
The test requires that writer to be blocked, so the old implementation is red.
Its retained evidence is
`/hf3fs-jd/data/tmp/opencad-auth-revocation-before-20261012-v1`: original rc1,
one test/one failure, zero source drift. The chair separately retained real
HTTP red controls in `opencad-gateway-revocation-before-20261012-v1`.
The first post-fix run `opencad-auth-revocation-20261012-v1` is also retained:
original rc1, 33 tests/29 passes/4 failures, zero source drift. Its first
cause was the test actor reading `sqlite_errorcode`, unavailable on this
Python 3.10 runtime; the compatibility fix recognizes the exact SQLite
`BEGIN IMMEDIATE` error `database is locked`, never arbitrary exceptions.

Post-fix tests cover logout/revoke-all/disable competition from independent
processes, prior-revocation no-receipt, guarded create/replay, forged/stale/
expired principal rejection, caller-error lock release, operator repetitions,
restart durability, legacy re-enable, product isolation and CLI execution.
Real SQLite trigger/COMMIT-authorizer failures test rollback and first-cause
preservation without replacing authentication or project storage with mocks.
Deadline failures are explicit deadlock-detector failures, never PASS.

This auth seat does not certify gateway integration, production TLS/proxy,
coordinated account/project restore, external-provider central revocation,
multi-host locking, power-loss recovery, distributed abuse/rate controls or
industrial parity. Existing per-process KDF capacity is unchanged; no rate-limit
feature is added to this narrowly scoped locking change.
