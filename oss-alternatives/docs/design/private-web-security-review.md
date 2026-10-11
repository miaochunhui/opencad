# Private Web Security Review

This is a finite independent review, not production security certification,
industrial completeness, or permission to deploy. Product files in the selected
source tree are read-only. The review owns only this document and
`tools/private_web_security_review.py`.

## Reproduce

Run as the repository owner. Use an explicitly local, service-owned test directory
for SQLite; never use production accounts, credentials, ports, or project files.
Evidence directories are new and exclusive, and must be under 3FS temporary
storage. The default source is the tree containing the review tool; an explicit
`--source-root` selects another read-only candidate without copying product
files into the worktree.

```bash
sudo -n -u infra python3 -B tools/private_web_security_review.py \
  --stage selftest --out /hf3fs-jd/data/tmp/opencad-auth-20261011/security/selftest-NEW
sudo -n -u infra python3 -B tools/private_web_security_review.py \
  --stage http --source-root /data/dev/opencad \
  --runtime-root /data/dev/opencad-private-fixtures \
  --out /hf3fs-jd/data/tmp/opencad-auth-20261011/security/http-NEW
sudo -n -u infra python3 -B tools/private_web_security_review.py \
  --stage browser --source-root /data/dev/opencad \
  --runtime-root /data/dev/opencad-private-fixtures --browser /path/to/chromium \
  --page 3d --width 390 \
  --out /hf3fs-jd/data/tmp/opencad-auth-20261011/security/browser-NEW
```

`OPENCAD_TEST_RUNTIME_ROOT` can supply the local runtime root. Raw session tokens,
passwords, Cookie headers, and request streams are not written to evidence.
`result.json` records counts, UTC times, runtime import origins, reviewed source
hashes and drift. `review.log` retains failing assertions and response status
sequences. Source drift, skips, missing prerequisites, or empty discovery are
not PASS. A candidate copy of the three directly reviewed product files is kept
as external evidence; it contains code, not runtime credentials.

## Scope And Initial Evidence

The HTTP review uses real AuthController, ProjectStore and ProjectGateway,
two independent users and real persistent sessions over a loopback HTTP server.
It checks project reads, exports and mutations for owner authorization; identity
forgery; preference isolation; expiration; CSRF/logout; duplicate Cookie headers
and names; GET/static framing; early POST rejection; TE/CL ambiguity; correct
alignment after fully consumed POST bodies; CSP and secure cookie attributes.

The real Chromium review checks HttpOnly visibility and actual CSP violations,
serialized concurrent edits, response loss after a real committed edit, blocking
of subsequent mutations, explicit manual replay of the exact request ID/base,
version conflicts and revoked sessions. Network loss is an explicit test wrapper
around a real received HTTP response. It does not replace auth, storage or CAD
execution with a mock. No automatic retries are performed by the review.

Initial HTTP evidence:
`/hf3fs-jd/data/tmp/opencad-auth-20261011/security/before-framing-v1/`.
17 tests: 13 PASS, 4 FAIL, 0 errors, 0 skips, 0 source drift.
The recorded candidate already rejected GET bodies and duplicate cookies.
The remaining first causes were:

- Early ambiguous Authorization returned `[401, 200]`, CSRF returned `[403, 200]`,
  and ambiguous project returned `[400, 200]`. The second request was embedded in
  the unconsumed first POST body. The HTTP boundary must close on early rejection
  or consume a fully validated body before reuse, not interpret it as a request.
- An empty `Transfer-Encoding` field returned 200. Presence, including an empty
  or duplicated field, must trigger the unsupported-framing rejection.

Exact source hashes and captured source coordinates are in that run's
`result.json` and `candidate/secure_web.py`; later candidate changes do not rewrite
the retained failure. Fix acceptance must use the unchanged negative cases in
a new evidence directory.

Browser evidence after correcting the review fixture's local socket path and
excluding read-only command queries from its mutation audit:
`/hf3fs-jd/data/tmp/opencad-auth-20261011/security/browser-v3/`.
6 tests: 5 PASS, 1 FAIL, 0 errors, 0 skips, 0 source drift. The remaining failure
is a real `/api/state` 401 after revocation while the active project's status
still says its edit was saved. Real serial edits, lost-response blocking,
exact receipt replay followed by reload without a second commit, and conflict
refresh passed. Earlier browser-v1 (socket path too long) and browser-v2
(read-only queries mixed into mutation counts) remain failed fixture evidence,
not product defects. They were not skipped or relabeled PASS.

The orthogonal download-401 check is also red in
`/hf3fs-jd/data/tmp/opencad-auth-20261011/security/browser-v4/`:
7 tests, 5 PASS, 2 FAIL, 0 errors, 0 skips, 0 drift. Both read and download return
real 401 responses without the required login-expiration notice. The recorded
JavaScript source handles the notice only in its mutation branch and returns
failed downloads directly. No authorization bypass was demonstrated by these
UI failures.

The 3D pointer review is recorded separately in
`/hf3fs-jd/data/tmp/opencad-auth-20261011/security/browser-3d-pointer-v5/`:
9 tests, 7 PASS, 2 FAIL, 0 errors, 0 skips, 0 drift. Opening the project,
confirming a pending receipt and refreshing a conflict all use real Chromium
pointer events with `elementFromPoint` hit validation, not DOM `.click()`.
The status output is static and does not cover the logout control at 390 and
1280 CSS pixels. The corrected CSS collision is not a transaction failure.
Exact receipt replay and reload still pass. Only the two 401-notice failures
remain in this browser run.

The expanded HTTP matrix is frozen in
`/hf3fs-jd/data/tmp/opencad-auth-20261011/security/http-expanded-before-v2/`:
22 tests, 16 PASS, 6 FAIL, 0 errors, 0 skips, 0 source drift. It adds HEAD-body
rejection, bodyless GET pipeline alignment, duplicate-cookie POST early
rejection, and empty/duplicate TE fields. Duplicate-cookie POST repeats the
same early-rejection first cause (`[401, 200]`); empty-TE POST returns 400 after
JSON parsing but leaves the connection open. These are additional manifestations
of the original framing findings, not six unrelated vulnerabilities.
The review tool's before/after SHA is identical in that result.

## Fix Acceptance

The unchanged frozen review tool is
`b986617ba7a50311e9a072a0d43051c85e1db51a715aae0304a3e483250f355f`.
Its five parser selftests passed in `security/reviewer-selftest-freeze-v5/`.
Source-bound independent fix acceptance completed on October 11, 2026:

- `security/after-framing-http-v4/result.json`: 22 PASS, 0 failures, 0 errors,
  0 skips, 0 drift; finished 02:49:23.229873 UTC (10:49:23.229873 UTC+08:00).
- `security/after-notice-browser-v7/result.json`: 9 PASS, 0 failures, 0 errors,
  0 skips, 0 drift; finished 02:49:25.140313 UTC (10:49:25.140313 UTC+08:00).
  This run serves the 3D page at 390 pixels and also hit-tests controls at 1280.

All relative evidence coordinates in this section are beneath
`/hf3fs-jd/data/tmp/opencad-auth-20261011/`.
Both fix runs used exactly these product bytes:

```text
secure_web.py        6a93919b42e3ff20b2a0807c45a8419fd4e88cf7b032fb4600d89ef6d0dab62f
project_gateway.py   5dd17e8a7079a10ae21e9c1a79ad84707e29b180ebd1a82948b54f080f4f9ce7
project-session.js   cd94998d8ce063353bc56cf27c736eb25d882405f9f1849d51368e58a97ab1f5
project-session.css  59015e9e5f845b0109e7655fe3a8a2cdaaf5a4e7f07f97d57302cfc7140bd31c
```

Earlier `chair-current-http-v3/` and `chair-current-browser-v6/` ran across the
chair's source update window. Both retain FAIL because two source files drifted;
the browser's nine passing assertions do not override that failed source binding.
The later fresh fix runs above have zero drift and do not overwrite prior logs.

The reported framing, read/download 401-notice, and pointer-obstruction findings
are closed for these exact candidates. This is finite review acceptance only.
An unconfirmed pending edit followed by session expiration still needs a
designed same-user re-login and original-receipt confirmation flow without losing
the pending request. It is an explicit unresolved boundary, not covered by the
successful active-session replay check. Subsequent industrial-route wiring or
source changes require fresh acceptance; no entire-repository PASS is claimed.

## Limits

This review does not assess TLS/proxy deployment, account recovery, rate limiting
across processes, external identity-provider integration, hostile local runtime
owners, every legacy UI route, browser storage after forced process termination,
or production interactions. Browser response-loss confirmation is covered only
while the original page retains its pending request. No complete multi-tenant
security or production release claim follows from these finite checks.
