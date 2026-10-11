# Private Web Release Review

## Scope And Verdict

This is a read-only source/package review, not a deployment approval. The
reviewer never imports audited modules, opens databases, starts HTTP/browser
processes, provisions accounts, changes permissions or executes production.
Only new evidence and selftest fixtures are written to external 3FS tmp.
`verdict=PASS` means the declared static checks completed; `production_ready`
is always false. Missing files, unsafe nonproduction parameters and source
drift are real failures, not skips. Production blockers remain separate.

The chair owns the private entry and its integration. Review the main working
tree explicitly, including untracked private files, without adding them to
the reviewer's commit. An untracked runtime file or a working file differing
from HEAD produces `git_archive_verdict=FAIL`: a Git archive cannot reproduce
those reviewed working bytes. Integrate, re-review, then bind the actual release
package against the complete per-file hashes; this tool does not copy a package.

## Reproducible Static Acceptance

From the verify worktree, run as infra. Each invocation creates a new timestamped
evidence directory and prints its path. Do not delete or overwrite prior runs.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-bootstrap-verify-20261011/tools/private_release_review.py --selftest
sudo -n -u infra python3 -B /data/dev/wt-opencad-bootstrap-verify-20261011/tools/private_release_review.py --source-root /data/dev/opencad
sudo -n -u infra python3 -B /data/dev/wt-opencad-bootstrap-verify-20261011/tools/private_release_review.py --scan-owned
```

Evidence includes `result.json`, `verdict.log`, reviewer hash, source HEAD,
working-byte hashes, dependency edges, Git states, source drift, nonproduction
configuration and explicit production blockers. Only coordinates/hashes are
recorded, not source excerpts, request bodies, passwords, session tokens or DB
contents. The owned-content scan reuses the repository bootstrap scanner; it
does not claim an external secret scanner or production security audit.

Runtime selection starts from secure HTTP, gateway, local auth/store, legacy
Workspace (not its listener), solid commands, generated 3D page, index and
license notices. All cadcore runtime Python and data JSON are conservatively
included because Workspace/feature dispatch is dynamic. Tests and other
products are not in this runtime bundle. Literal imports recursively bind
local modules, package initializers and valid namespace packages; missing
cadcore/DXF/vault imports fail. The package map retains the migrated layout.
Tracked application module names are also local even when their file is
missing; they cannot be relabeled as an unassessed external dependency.
No mathcore dependency is assumed merely from the old monorepo name.
The language manifest binds each declared catalog and its default standard;
the default English catalog/GB profile and feature registry's MODULES cannot
disappear just because directory scanning would select fewer files.
Existing `ops/features/_quarantine` files are retained and SHA-bound because
feature membership is derived from their presence. They are not executed by
the live slice loader. Their unresolved imports are listed as
`quarantine_import_debt`, not silently removed or presented as live startup
failures. Moving a broken import into the live directory makes it fail the
same resolver; the selftest exercises both scopes.

Static resource checks cover HTML and generated-page literal script/link/src,
CSS URL/import and literal JS module/worker references. Project-session JS/CSS
injection, 2D app JS/CSS and 3D viewer/advanced JS/CSS must all exist. Local
references escaping `/web/` and external resource URLs fail. Computed imports,
generated FreeCAD script bodies, computed JS resource URLs, browser rendering,
route behavior and third-party ABI are NOT_ASSESSED. The result records dynamic
Python coordinates rather than claiming general static-analysis completeness.
New dynamic loading requires an explicit boundary review.

For the private 3D page specifically, an inert AST interpreter resolves only
constants, string joins/f-strings, fixed loops and string replacements. It
never imports or calls the application's `render`: the viewer page template
is read as AST and the timestamp is explicitly marked static. Unsupported
template operations fail. The result lists five ordered panel hosts, all five
JS/CSS pairs, project-session-first/coordinator-last script order, exported
mount/binding shapes and the actual same-origin CSP. The coordinator follows
all five panel scripts, but precedes the existing viewer/advanced scripts.
Inline scripts/styles
and external assets fail. Renderer data-URL images match `img-src data:`;
computed downloads/drawing SVG and real browser CSP remain runtime acceptance.
Parametric's independent autoboot does not wait on session-ready like the
coordinator: retain this timing debt and test login/project transitions, rather
than claiming a static mount check proves successful interactive behavior.

Acceptance sources are a separate binding: private HTTP/gateway tests,
private component/browser runners, viewer-controls helper and its local CDP
helper. The browser runner needs a separately supplied Chromium executable;
its home-cache default is not a deployable dependency. The component runner's
manually listed hashes do not bind the complete runtime closure, and it reports
skips without rejecting them. Neither is a substitute for this package review
or the independent repository-wide `tools/acceptance.py` regression.

## Nonproduction Configuration

The tool checks and records this profile without starting it: host
`127.0.0.1`, port `8767`, exact origin `http://127.0.0.1:8767`, and explicit
local external root `/data/dev/opencad-private-review-runtime`. A changed port
requires the same change in origin. It rejects public listeners, malformed or
different origins, linked roots, Git-contained roots, 3FS/FUSE/network/tmpfs
storage and temporary roots. Mount type is recorded, but disk durability,
ownership/access restrictions and capacity are not proven by a mount name.

The operator must separately authorize and prepare a private local root and
its `runtime` directory before running the recorded argv. No startup occurs in
this task. Keep `PYTHONDONTWRITEBYTECODE=1`; use the local runtime for subprocess
temporary files. Clear unrelated `PYTHONPATH`, `OPENCAD_CADCORE_ROOT`,
`OPENCAD_I18N_DIRS` and `CADCORE_PDF_BACKEND` overrides to prevent another
checkout or external backend from silently supplying release dependencies.
Pin any intentionally used FreeCAD executable via `CADCORE_SUBPROC_EXECUTABLE`
and separately accept its actual ABI/geometry. Do not borrow infra credentials.
The new render adapter requires `libEGL.so.1`, `libGLESv2.so.2` and accepted
NVIDIA/EGL device support only for separately authorized `--enable-gpu`.
This review leaves the CLI GPU flag absent; neither library loading nor GPU
work is performed. Static shader presence is not GPU performance evidence.

For isolated tests, `OPENCAD_TEST_RUNTIME_ROOT` is the explicit local fixture
root; test results/screenshots/logs remain on 3FS. It is not a secure server
CLI switch and does not override `--data-root`. Never set ProjectStore's root
to the 3FS evidence directory. Keep the store's rejection gate unchanged.

The CLI places `opencad-auth.sqlite3` in `<data-root>/accounts`, project WAL
DB/sidecars in `<data-root>/projects`, and request directories in
`<data-root>/runtime`. Local operator provisioning uses `access_control.py`
with `--data-root <data-root>/accounts --create-user <name>`; the password is
prompted, never an argv/environment/log literal. No registration HTTP route
or external identity provider is enabled by this profile. Admin provisioning
is operator-only, not permission to run a release maintenance endpoint.

## Production Preconditions And Recovery

1. Chair integrates reviewed entry/assets/tests and freezes a reproducible
   source package, Python version, first-party Apache manifest and LICENSE/
   NOTICE. Preserve FreeCAD/OCCT and any other third-party notices separately;
   first-party Apache does not relicense external libraries or Chromium.
2. Authorize a TLS reverse proxy with an exact HTTPS public origin. The current
   `BoundedServer` is an HTTP listener, not a TLS listener; an HTTPS origin flag
   only changes policy/cookies. Keep upstream on loopback and prevent external
   direct access. Verify proxy scheme/host/origin, Secure HttpOnly SameSite
   cookies, CSRF, framing limits, websocket assumptions and HTTPS-only browser
   behavior through the real proxy before exposing private data.
3. Accept local durable storage and service ownership. ProjectStore uses
   SQLite WAL/FULL sync on one host; no 3FS or multi-host shared WAL. Accept
   actual crash recovery and storage/controller sync behavior separately.
   Accounts and projects are independent databases, not one atomic DB.
4. Accept account disable/revocation, session lifetime, brute-force controls,
   connection/job exhaustion, disk quotas, export scope and log redaction.
   Two password KDF slots and 32 HTTP connection slots are bounded mechanisms,
   not distributed rate limits, resource quotas or industrial readiness.
5. Run frozen private security/gateway/browser and full independent regression
   against the intended release and actual FreeCAD subprocess backend. Include
   document/history reload, ownership negatives, stale revision/receipt replay,
   DXF/PDF/STEP download and desktop/mobile views. Retain every skip/failure.
6. Approve cutover, writer drain, health acceptance and recovery authority
   before changing production. `/api/release/*` is disabled even for admin;
   do not treat its HTTP 503 as a switch/rollback implementation.

Recovery is a prerequisite, not executed here. Retain the prior immutable
release and a coordinated accounts/projects backup at an approved writer-
quiescent point or via an accepted SQLite backup workflow. Never copy only a
live database file and discard its uncheckpointed WAL. Preserve the pairing
between stored owners and account IDs; restore into a new private local root,
not over the live root. Test integrity, foreign keys, schema compatibility,
heads/history SHA and receipt replay in isolation before a separately
authorized switch. Treat restored auth sessions as a security decision: a
backup may predate revocation; do not silently restore revoked access.

Code rollback alone is not a data rollback. Auth and project schemas currently
gate version 1; an older binary may reject a future database. Prove that the
prior release can read the selected recovery snapshot or define an accepted
forward recovery path. Do not delete, rewrite or prune real history as a
workaround. The earlier startup constructed storage before listener validation.
The current CLI reserves and validates the listener before opening storage and
drains admitted requests before closing databases. Actual invalid-origin,
bind-failure and drain tests are part of the integrated HTTP suite. This does
not prove physical crash recovery; operators must not infer READY or clean
rollback from process presence.

No automatic pre-commit/deployment hook is installed by this task. Connecting
these checks to an approved release gate remains a chair-owned prerequisite,
not a guarantee supplied by a manually runnable reviewer.

## Evidence And DCO Recheck

`--verify-evidence <result.json>` requires a successful static-review result
from the identical reviewer bytes, checks its terminal log, recomputes every
source hash/fingerprint and compares Git HEAD and tracked/untracked inventory.
It is a source recheck, not a second runtime test or production approval.

After the chair-approved owned two-path commit, `--commit-evidence
--review-evidence <review-result.json> --selftest-evidence <selftest-result.json>`
checks that HEAD changed exactly this tool and this document, committed bytes
match the owned content scan, both paths are clean, author DCO sign-off exists,
selftest counts close with every case PASS and the reviewed main-tree source
still matches. It only reads Git; it neither commits nor pushes. The evidence
contains the real commit SHA, scan, source binding and selftest coordinates.
