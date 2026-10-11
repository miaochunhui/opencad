# Durable Confirmation And Revocation

The private gateway confirms project creation and mutation under
`AuthController.authorized_transaction`. The authorization database holds its
SQLite writer reservation while the project database commits its snapshot and
receipt. All participating operations use the lock order accounts, then projects.
Expensive CAD evaluation remains outside this short confirmation boundary.

If logout, expiry or account disable wins before this boundary, confirmation is
refused and no new snapshot or request receipt is stored. If confirmation wins,
revocation cannot commit until the project transaction finishes. A restored
request receipt is returned only after a fresh live authorization check.
These guarantees concern database confirmation, not when a remote browser
receives already authorized response bytes.

The generated-session actor in `tests/auth_boundary_actor.py` is an independent
SQLite process, not a simulated revocation result. Its path is restricted to the
explicit local test runtime. At the final store-entry scheduling point it either
commits revocation or measures `SQLITE_BUSY`. Unexpected storage errors fail.
The HTTP suite covers creation, ordinary editing and revocation before the final
guard, preserving exact stored head and receipt expectations.

Run as infra from the reviewed worktree:

```bash
python3 -B tools/private_web_acceptance.py --stage gateway --out /hf3fs-jd/data/tmp/opencad-gateway-revocation-new-run --runtime-root /data/dev/opencad-private-fixtures
```

Choose a never-existing evidence directory. Logs, hashes, original exit status,
test counts and source drift are external. The baseline on 2026-10-12 at
01:33:22 Asia/Shanghai failed two of 23 tests: late independent revocation
succeeded, yet both creation and editing still acknowledged success.
Evidence is `/hf3fs-jd/data/tmp/opencad-gateway-revocation-before-20261012-v1`.
This failure is not erased by a later passing run.

This work does not install an identity provider, deployment gate or automatic
backup, change production, or prove physical power-loss durability. Coordinated
recovery and restored-session revocation are separate acceptance requirements.
