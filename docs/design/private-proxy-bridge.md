# Private Proxy Bridge Candidate

## Scope And Existing Boundary

This is a local acceptance tool and an offline deployment candidate, not an
installed service or production cutover. Product/auth/gateway code is unchanged.
The legacy anonymous deployment and `/var/lib/oss-web/opencad` must not be used
as fallback or overwritten.

Read-only inspection found edge Caddy running as root on 192.168.1.183/184,
terminating public TLS and forwarding HTTP across hosts. The secure CLI rejects
non-loopback listeners. The candidate resolves that routing gap as:

`public TLS -> existing edge Caddy -> private remote Unix socket -> existing
infra SSH reverse StreamLocal forwarding -> local peer-bound Unix HTTP bridge
-> pinned secure CLI at 127.0.0.1`.

The original strict infra SSH route to the edges is retained. A previous
unchanged-identity local SSH probe to infra@192.168.1.30 returned rc 255,
Permission denied (publickey,password). No replacement login/key is created.
The edge sshd policy inspection allowed StreamLocal forwarding, but this does
not prove authorized_keys restrictions, actual tunnel traffic, or live cutover.

## Trust And Failure Boundary

The bridge listens only on a Unix socket in an existing, infra-owned private
parent (no group/other permission bits). It creates mode 0600 with umask 0177.
Linux SO_PEERCRED must match the explicitly expected local SSH-client UID.
This does not isolate hostile root or a compromised same-UID account.

Before any cookie/body is forwarded, the bridge checks exact secure CLI argv,
Python interpreter, PID/start time, process-owned loopback listener inode,
accepted TCP socket ownership, source HEAD and whole tracked-source plus owned
candidate-file hashes/status. It repeats binding checks after the response.
There is no retry, alternate upstream, auto-repin, or legacy fallback.
Restarting the CLI invalidates the old pin. A fresh explicit pin is required.

Only the literal HTTPS authority is accepted as Host. Direct bridge requests
with Forwarded/X-Forwarded, X-Real-IP or URL-rewrite headers are refused;
the reviewed Caddy candidate strips them before forwarding. They confer no trust.
Connection tokens may only name close/keep-alive, never identity/CSRF fields. Original
cookie/bearer identity, duplicate identity fields, Origin and
X-OpenCAD-Request reach the original secure handler; that handler remains the
authentication/CSRF authority. Public TLS is Caddy's responsibility, SSH
encrypts the cross-host hop, and local HTTP is restricted to loopback.
The Caddy candidate requires strict SNI/Host matching and an exact host/port
authority matcher before Host normalization. Its global `servers` option must
be merged/reviewed against all existing sites; it must never be installed as
an unreviewed second global block. A blank 200 from an unmatched Host is not
accepted as a rejection.

Optional `--read-only` is a deployment-wide bridge policy, not a new user role:
GET/HEAD and exact login/logout POST only. All other POST requests, including
read-like rendering/check endpoints, are refused. Requests/responses are capped
at 16 MiB; streaming, WebSockets and very large exports are not supported.
Capacity is 32 admitted Unix connections; excess connections close fail-closed.
Responses are buffered and verified before delivery, not streamed.

## Conditions Before Any Separate Cutover

- Independently verify the existing strict SSH authentication and host key.
  No new credentials, login method or automatic host-key update is permitted.
- Separately authorize creation of a unique edge parent owned infra, mode 0700;
  sshd must permit StreamLocal forwarding with socket mode 0600, no unlink.
- Existing Caddy must run as root or the approved same UID and be able to reach
  that parent. Do not relax parent/socket permissions to make routing work.
- Verify public certificate, DNS/FRP routing, existing Caddy site conflict and
  original public authority. Do not start a second conflicting public listener.
- Start the reviewed secure CLI with a separate private data root, HTTPS origin
  and a clean frozen source; provision legitimate accounts separately.
- Verify local/remote socket absence before starting. Never unlink old sockets.
  The SSH command exits on forwarding failure and does not reconnect itself.
- Plan and review migration, backup/restore and user-drawing preservation before
  traffic changes. This candidate performs neither migration nor DB restore.

Templates are embedded in the single bridge tool and emitted only into a fresh
external plan as `Caddyfile` and `opencad-private-bridge.service`.
The earlier untracked deploy candidates are preserved, not dependencies or
members of this three-file delivery.
The service has Restart=no, no privilege escalation, and a single permitted
socket-parent write path. Neither template is installed by the tool.

## Offline Plan And Local Acceptance

All commands use the original infra identity and Python bytecode is disabled.
Obtain the actual clean source HEAD, secure CLI PID/port and independently
approved private roots; placeholders below are not accepted evidence.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-private-bridge-20261012/tools/private_proxy_bridge.py --action plan --source-root /data/dev/wt-opencad-private-bridge-20261012 --expect-head REVIEWED_40_HEX_HEAD --socket /data/tmp/APPROVED_NEW_CHILD/bridge.sock --upstream-pid ACTUAL_SECURE_PID --upstream-port ACTUAL_LOOPBACK_PORT --data-root /data/tmp/APPROVED_PRIVATE_ROOT --public-origin https://opencad.ai4everyone.top --edge 192.168.1.183 --remote-socket /home/infra/opencad-private-bridge/APPROVED_NEW_CHILD/bridge.sock --out /hf3fs-jd/data/tmp/APPROVED_NEW_PLAN
sudo -n -u infra python3 -B /data/dev/wt-opencad-private-bridge-20261012/tools/test_private_proxy_bridge.py --evidence-dir /hf3fs-jd/data/tmp/APPROVED_NEW_TEST --runtime-root /data/tmp/APPROVED_NEW_TEST
sudo -n -u infra python3 -B /data/dev/wt-opencad-private-bridge-20261012/tools/test_private_proxy_bridge.py --verify-evidence /hf3fs-jd/data/tmp/APPROVED_COMPLETED_TEST --evidence-dir /hf3fs-jd/data/tmp/APPROVED_NEW_READBACK
```

Plan emits reviewed SSH argv and candidate templates, never executes SSH or
changes services. Existing output is refused. Failure keeps result FAIL and
first cause, never promotes PLANNED to deployed.

The local acceptance launches the real secure CLI, real Unix bridge and real
Caddy TLS proxy using an existing pinned local image. Its only generated
passwords/session tokens are in memory; its fixture TLS private key stays in
the unique local runtime, not Git or evidence. It logs request labels/status
and response hashes, not credentials or headers. It stops only its own
processes/container and unlinks only its own identified socket. Runtime/3FS
evidence remains. Missing prerequisites and failures are retained, fail-fast.

Local TLS/HTTP PASS is not remote SSH tunnel PASS, public TLS/DNS acceptance,
power-loss recovery, HA, coordinated storage recovery or production readiness.
Bridge outage must produce 502, never legacy state. Bridge restart restores
traffic only with the same validated pin; secure CLI restart requires a new
pin and retained private project state. Rollback of traffic is a separately
authorized reviewed Caddy configuration restore, not automatic anonymous
fallback. No rollback/reload is performed here.

## Dated Local Evidence And Remaining Gate

On 2026-10-12 at 06:18:47-06:19:02 Asia/Shanghai, the pre-commit local
suite returned original rc 0, 8/8 tests, zero skips/failures/errors,
23 named checks and 57 real HTTP requests. Before/after source HEAD and
file binding are equal. Evidence:
`/hf3fs-jd/data/tmp/opencad-private-bridge-all-20261012-v3/`.
Its separate offline readback returned original rc 0 with 28 artifact/ledger
checks at `opencad-private-bridge-readback-20261012-v3`.
This snapshot binds base HEAD `78871af28e4ea3cbec359b1dd4ce4b27a8385688`
plus the recorded uncommitted three-file bytes, not a subsequent commit.

The network fixture uses the actual BridgeServer implementation with the
original secure CLI and a real pinned Caddy container. It does not subclass
the gateway or fake HTTP responses. The bridge's clean-source CLI startup
is not claimed passed: the two retained earlier untracked deploy candidates
keep this worktree dirty, and the real CLI correctly refuses it. The suite
tests that refusal. The internal local fixture permits a stable dirty
development snapshot; the operator CLI does not. Offline late-read failure
injection is isolated to the plan/result writer, not the HTTP chain.

Two first failures remain immutable: `opencad-private-bridge-auth-20261012-v1`
returned rc 1 because Unix socket creation yielded 0700 instead of required
0600; creation umask 0177 repairs it without chmod. The fresh auth v2 passed.
`opencad-private-bridge-all-20261012-v1` returned rc 1 because an unmatched
TLS Host got blank HTTP 200 from Caddy. Strict SNI/Host plus exact authority
guard now gives 421. All v2 passed 6 methods/54 requests, and v3 adds HEAD,
identity/Origin negatives and fail-loud final-read controls. Earlier failures
are not rewritten or counted as passes.

Review-ready source is not deployment-ready. Before separate authorization,
verify actual edge SSH StreamLocal traffic using the existing login/key,
review Caddy global-option impact across all current sites, validate public
TLS/DNS/FRP routing, adopt a clean reviewed source, and approve private-root
account/data migration and coordinated backup/recovery. The local recovery
test is orderly process stop/restart and new-pin reopen, not power-loss or
database restoration. Neither remote forwarding nor production cutover ran.
