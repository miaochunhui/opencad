# OpenCAD Authentication And Request Context

## Interface v1

These callable components do not expose routes or claim production readiness.
The separate secure gateway must enforce authentication and project ownership
before it opens any workspace or returns private data. Legacy `server.py` is
unchanged and remains a single-workspace development/demo interface.

`AuthController(data_root, *, allow_registration=False, external_adapter=None,
session_ttl_seconds=28800)` stores `opencad-auth.sqlite3` in the explicit root.
The operator owns that root; it must be outside the source tree. SQLite uses
FULL synchronous transactions. Accounts and sessions are independent of
FreeChat; no FreeChat key, ticket, database or secret is imported.

- `register(username, password) -> {"user_id": str, "admin": False}` requires
  the explicit CLI/config registration flag and is unavailable in external mode.
  Do not expose public registration merely because the operator provisioner
  uses that flag. Admin provisioning is local CLI-only.
- `login(username, password) -> SessionTicket`: attributes `token`,
  `expires_at` (UTC Unix seconds), and `principal`.
- `authenticate(token) -> Principal`: attributes `user_id` and `admin`;
  `as_dict()` includes only those two public fields.
- `logout(token) -> None`: durable revocation, including other controllers
  reading the same database; repeat logout for a known session is idempotent.
- `authorize(principal, *, owner_id, allow_admin=False) -> None`: rechecks
  live session, expiry, revocation, enabled account and current server-side role.
  Forged/replaced public identity fields do not grant access. Owner metadata
  must come from the project store, never from the requesting client.
- `get_preferences(user_id) -> {"locale": str}` and
  `set_preferences(user_id, *, locale) -> {"locale": str}` are internal
  components. The gateway must authenticate and use `principal.user_id`,
  not accept an arbitrary target user ID from the client.
- `close() -> None` disables subsequent operations for that controller.

`AuthError` has `code`, `status`, and sanitized `detail`. Invalid input is 400;
missing/invalid/expired/revoked/foreign sessions are 401; project access and
disabled registration are 403; duplicate registration is 409; password work
capacity is 429; configuration/storage/external adapter failure is 503.
Unexpected programming errors remain errors, not an anonymous fallback.

Local credentials use a fresh 16-byte salt and scrypt with N=32768, r=8, p=3,
32-byte output (schema v1 fixes those parameters). Raw passwords and 256-bit
random hex session tokens are never stored. Session lookup uses a
product-domain-separated SHA-256 digest. Password verification uses
`hmac.compare_digest`; unknown users perform the same KDF work. At most two
KDF jobs run concurrently per process; this is not a full distributed
brute-force/abuse rate-limit claim. Persistent sessions survive service restart
until expiry/revocation; copying or losing the auth database is outside that
durability claim. Account disable invalidates existing sessions.

The optional external adapter is configured server-side and must implement
`authenticate(username, password, *, product="opencad") -> ExternalIdentity`.
Its `configured` property must be exactly True. Identity has `user_id`,
`username`, `product` and boolean `admin`. A foreign product, untyped result,
failed provider, or collision with a local account is rejected; there is no
fallback to local passwords or anonymous access. The adapter's central logout,
account-disable and role-freshness policy needs its own integration acceptance:
these components issue separate OpenCAD sessions, not borrowed provider tickets.

## Fixed Request Locale

`CatalogContext(locale: str, *, dirs=None)` loads an immutable translation
snapshot. Empty, invalid, unavailable or corrupt input fails before binding;
explicit language fallback is still reported by `LanguageChange.degraded`.

```python
context = CatalogContext(locale, dirs=directories)
with context.bind():
    core_for_request = dict(core, i18n_catalog=context)
    workspace = Workspace(core_for_request, locale=context.locale, standard=standard,
                          out_root=project_output_root)
    # Implicit cadcore catalog.t/active_* use this context during the request.
```

The adapter supports `t`, `active_locale`, `active_catalog`, `active_chain`,
`stats`, `untranslated_keys`, `set_locale`, `default_dirs`, `available_locales`,
`load_catalog`, `scan_catalogs`, and `resolve_chain`.
`set_locale` accepts only the same language and (if supplied) same directories,
returns `changed=False`, and never broadcasts a global language change.
Preference changes create a new context on the next request, not a mutation
of an in-flight request. Do not reuse another user's context to construct a
workspace. Keep standard/document/history/geometry cache in the project store.

`catalog.bind(locale, *, dirs=None)` constructs and binds the adapter.
Without any binding, legacy catalog functions retain their old single-process
API. With a binding, implicit translations, counters and missing-key tracking
are context-local; no switch-and-restore global language strategy is used.
The JSON parse cache is process-wide shared data keyed by content SHA, not
mutable per-user preferences.

`request_context.bind(locale, *, principal, project_id, dirs=None)` yields a
frozen `RequestContext(principal, project_id, locale, catalog)` and binds both
request identity and catalog. `current()` refuses an unbound request.
This binding is propagation, not authorization: the gateway must call
`authenticate` and `authorize` before it enters. `capture(callback)` copies a
currently bound context for an explicit worker-thread submission; background
jobs must reauthorize before private I/O. Async tasks inherit the context at
task creation. Separate process/queue workers must pass and reauthorize their
identity explicitly. Capturing a request never extends session validity.

## Gateway Obligations And Acceptance

The secure entry owns HttpOnly/SameSite cookie policy, same-origin mutation
checks, Bearer parsing, no trusted private identity headers, no long-lived
token localStorage, project selection, error-to-HTTP mapping and route coverage.
It must apply owner/admin checks to read and write routes alike. Release
maintenance authority is separate and not granted merely by project ownership.
Registration must remain disabled unless explicitly permitted by operator
configuration. Read requests must not open/switch projects or clear history.

Project commits, expected revision/SHA, request ID idempotency, persistent
version heads and fsync/crash recovery belong to the separate store/gateway
implementation. Tests here demonstrate real local account/session storage,
negative authentication, two real legacy Workspace instances and isolated
request translations; they do not claim authenticated HTTP route coverage,
industrial parity, full multi-tenant deployment or storage-crash atomicity.

Owned tests support default unittest discovery and standalone invocation:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_access_control.py --evidence /hf3fs-jd/data/tmp/opencad-auth-20261011/acceptance-v1
```

That external evidence runner uses `tools/acceptance.py --suite` for the
authentication, request, new catalog and existing catalog suites. It records
exact counts, skips, child exit codes, logs, owned source hashes and unchanged
legacy server hash. A missing suite, any failure/skip or source drift is FAIL.
Evidence paths must be new; failures are not overwritten by a later run.
