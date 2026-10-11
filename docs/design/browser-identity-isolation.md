# Browser Identity Isolation

## Scope

This track owns only project-session.js, its dedicated browser evidence tests,
and the browser_identity_acceptance.py tool. Industrial panels, the coordinator,
server, gateway and adapters are read-only dependencies. This is a local
two-account desktop/mobile workflow, not all-site language scanning, production
multi-tenancy, industrial parity or GPU performance acceptance.

## Boundary

Explicit logout and a different identity require full-page replacement: existing
panel closures, documents, undo state, rendering buffers and unsaved drafts must
not become the next user's workspace. The project selector validates saved
project identifiers against the authenticated owner's list.

An expired session or failed read/download must shield the old workspace.
Uncertain writes retain their original request ID and drafts for receipt replay
by the same identity only. A transport epoch rejects late responses from a
previous boundary; reconnect verifies the live identity and project before
unshielding. Auth/project transaction ordering remains the gateway's fixed
auth-to-project order; this browser change adds no server transactions.

## Reproduction

Run as infra, with Python bytecode disabled. Both roots must be fresh and outside
source; SQLite and Chromium profiles live in the local runtime child.

```bash
sudo -n -u infra python3 -B tools/browser_identity_acceptance.py \
  --source-root /data/dev/wt-opencad-browser-switch-20261012 \
  --out /hf3fs-jd/data/tmp/opencad-browser-identity-rerun-20261012-v1 \
  --runtime-root /data/tmp/oc-bi-rerun-v1
```

The tool launches the original secure CLI with an exact loopback origin, binds
its READY marker, PID/startticks and argv, and receives its original graceful
exit. Temporary account passwords and session values remain in memory; CDP
input parameters are never logged. Owned Chromium profiles may be disposed;
failure evidence and local SQLite fixtures are retained.

The workflow verifies durable receipts, stale CAS without a receipt, user-only
project lists, distinct documents/history/drafts, persisted zh-CN/en locale,
real pixels and STEP downloads, real offline read shielding, real SQLite
revocation, wrong-identity recovery rejection and late response rejection.
Source files/modes, HEAD/status, loaded session asset and all output artifact
hashes bind the run. Synthetic offline controls test the evidence reader only,
not the product or an arbitrary external CLI.

Offline readback and synthetic negative controls are separate modes:

```bash
sudo -n -u infra python3 -B tools/browser_identity_acceptance.py \
  --verify-evidence /hf3fs-jd/data/tmp/opencad-browser-identity-commit-20261012-v1 \
  --out /hf3fs-jd/data/tmp/opencad-browser-identity-readback-rerun-v1
sudo -n -u infra python3 -B tools/browser_identity_acceptance.py --selftest \
  --out /hf3fs-jd/data/tmp/opencad-browser-identity-selftest-rerun-v1
```

Verification requires the exact current source/HEAD/status of its producer.
An older receipt is not silently rebound after a commit or tool change. Fresh
post-commit evidence is required to certify the committed source. Results bind
the source bytes/modes, complete workflow ledger, original CLI argv and
PID/startticks before/after, all artifacts and exact terminal marker. Browser
STEP bytes match that download's actual response hash and document/revision
headers; the independent same-version STEP DATA is equal. Export timestamps
are not mistaken for model drift.

## Observed Results And Limits

The original v3-before run returned rc 1 at
1440-offline_read_shields_workspace: a true disconnected-network read left
the old workspace visible. v8 subsequently returned rc 0 with 44/44 desktop
and mobile workflow checks; offline readback verified its exact receipt.
The legacy private browser compatibility run returned rc 0, 30/30.
These are dated intermediate snapshots, not acceptance of later edits.
Earlier fixture failures, including socket path length, undo's empty model,
operator mode, suspended-Promise observation, STEP timestamps and missing JSON
media type, remain in their original v1/v2/v4/v5/v6/v7 evidence roots.

The product shield is reversible: same-account recovery preserves input node
identity and drafts, while explicit logout discards them through page reload.
An uncertain write blocks logout rather than silently discarding its request.
Direct public-transport auth changes reload before a new identity can enter old
panel closures. Project selection is a bounded owner/project pair; no password
or token enters browser storage. Back/forward restored pages reload before use.
Only session-owned dialogs bypass the shield; new body children are shielded
while locked. Intentional request cancellation is not treated as network loss.

Real HTTP concurrent CAS accepts one of two same-base saves, rejects the other
without a receipt, and leaves the document unchanged. A delayed genuine read
succeeds in the positive control. After revocation it is rejected. A genuine
old mesh delayed while an independent HTTP actor edits geometry cannot overwrite
the refreshed new model after same-account recovery; the unsaved depth remains.
SQLite operator revocation touches only newly generated local fixture accounts.

This does not certify all 2D/industrial panel state, every language string,
cross-tab cookie replacement, service workers, arbitrary response streams,
physical network partitions or production multi-tenancy. The workflow checks
zh-CN/en persisted viewer locale, not all-site language scanning. DOM shielding
is UI isolation, not erasure of browser heap or a sandbox against same-origin
malicious script. Native server GPU admission stays disabled.
