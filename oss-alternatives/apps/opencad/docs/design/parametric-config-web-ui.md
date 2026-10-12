# Parametric Configuration Web UI

## Contract And Scope

The private parameter panel uses the existing authenticated project transport.
It does not create request IDs or bypass revision/SHA CAS. The transport alone
owns pending requests and receipt replay. All visible labels in this panel are
Simplified Chinese; this is not an all-site locale scan.

`POST /api/parametric` accepts only `create_plate`, `create_configurable_plate`
and `rebuild`, with `expected_document_sha256`, optional `configuration`,
`overrides` and `dimensions`. The private transport supplies the outer project
revision, snapshot SHA and request ID. Version 1 creation remains available;
version 2 creation includes wide/solid/wide_solid configurations.

The panel selects a declared configuration, previews its semantic feature flags,
edits explicit parameter/dimension overrides, and can prepare removal of explicit
overrides. Nothing is saved until complete FreeCAD rebuild succeeds. The empty
configuration resets the baseline and clears previous overrides. Omission retains
the active configuration and its overrides. Feature UUIDs are never rebound.

Configuration-definition creation/deletion and suppression-definition editing
are not supported by the parametric mutation API at the 6596f022 baseline.
The existing document-open API could save a definition separately from a later
rebuild, but that is two independently acknowledged mutations and clears history.
This task must not silently represent that as an atomic configuration operation.
Its use awaits the chair's explicit scope decision. No new API is guessed.

## Draft And Identity Boundary

Background updates do not replace dirty fields. Same-version explicit readback
preserves the same input values. A changed saved version blocks draft submission
until an explicit, confirmed discard/reload. Compatible metadata-only receipts
can advance the base without deleting drafts. Model-changing receipts cannot
silently rebase them. The panel does not store private drafts or credentials in
browser storage.

Every accepted view binds project/revision/snapshot SHA, canonical document SHA,
mesh and STEP. A confirmed mutation followed by a view failure is not submitted
again. A lost response remains owned by the session's original receipt replay.
The user may inspect/discard the retained form only after checking the saved head.

Readback checks the live account before and after document reads. Authentication
failure or account/project mismatch clears private parameter DOM and fences late
responses with a generation token. Transient read failure keeps draft values in
memory, hides the unverified form and blocks edits. Same-account recovery is
explicit. The panel does not claim to clear other panels or the global mesh;
the shared session adapter owns the site-wide isolation boundary.

## Independent Acceptance

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_parametric_config_browser.py --evidence-dir /hf3fs-jd/data/tmp/opencad-config-ui-commit-browser-20261012-v1 --runtime-root /data/tmp/oc-cui-cmt-1012-v1
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_parametric_config_browser.py --selftest --discovery-check --evidence-dir /hf3fs-jd/data/tmp/opencad-config-ui-commit-controls-20261012-v1 --runtime-root /data/tmp/oc-cui-cctl-1012-v1
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_parametric_config_browser.py --verify-evidence /hf3fs-jd/data/tmp/opencad-config-ui-commit-browser-20261012-v1 --evidence-dir /hf3fs-jd/data/tmp/opencad-config-ui-commit-readback-20261012-v1
```

These are fresh committed-source acceptance coordinates, not a statement that
those results already exist. Every repeated run needs a new evidence/runtime root.
Unittest discovery runs only the new control suite, with no old product jobs.

The standalone runner uses the exact source-tree secure CLI, two temporary local
accounts, real SQLite, Chromium click input and software WebGL. It binds READY,
PID/startticks, argv, normal CLI exit, the complete source/HEAD/status/mode manifest,
the four owned files, checks, screenshots and artifact hashes. Password/cookie
values stay in memory; evidence is external. It retains first failure and does
not automatically retry. Runtime is deliberately short for Chromium Unix sockets.

It covers real configurations and override reset, BRep/independent mesh volume,
same-version graphics, save/undo/redo, normal CLI restart, actual STEP download and
independent FreeCAD STEP reimport, rejected edits, lost-response replay with drafts,
delayed-read positive/expired-session negative and a second account's empty project.
Negative controls reject geometry identity mismatch, empty checks, HEAD drift and
PASS with a retained first failure. The caller must separately collect the original
process exit; the report's intended exit is not proof of that parent exit.
The source-defined HTTP/1.1 handler idle timeout is 15 seconds. Normal CLI exit
is observed for at most 18 seconds, with 0.1 second polls and thread-count records.
Needing a kill or receiving a nonzero exit remains a failure, not a restart pass.
Profiles and all failed fixtures remain in the task's own local runtime child.

The final PASS is written only after a separately saved terminal candidate has
been read back and validated against the current source and all mandatory artifacts.
Offline readback checks the same artifact set, byte/size hashes, CLI scope/READY,
check log, closed-mesh volume, document/display identities and download witnesses.
It also requires the exact completion marker and unchanged terminal candidate.

## Terminal Failure Controls

The acceptance, selftest and offline-readback entries retain the first failure.
Later source reads, CLI stop, log close, evidence traversal and artifact reads
are recorded as `secondary_failures`, with stderr preserved. An unavailable
source is `null`; drift is also `null` when the comparison cannot be collected,
never an empty manifest or a claimed zero drift. Completed checks/tests and
failure/error counts survive finalization failures. Offline readback separately
records the original ledger count and `verified_checks`; an unreadable original
ledger is `null`, not zero verified checks.

Source/evidence traversal reports I/O errors rather than silently omitting a
directory. Partial artifact hashes remain visible with
`artifact_scan_complete=false`. A final report is independently read back before
publication; terminal I/O failure produces FAIL and original exit 1. If the
evidence filesystem itself cannot accept a FAIL report, stderr retains the
structured failure and no successful completion marker is claimed.

```bash
sudo -n -u infra python3 -B tools/test_parametric_config_terminal.py --discovery-check --evidence-dir /hf3fs-jd/data/tmp/opencad-config-browser-terminal-committed-20261012-v1 --runtime-root /data/tmp/oc-cbt-commit-1012-v1
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_parametric_config_browser.py --selftest --discovery-check --evidence-dir /hf3fs-jd/data/tmp/opencad-config-browser-terminal-committed-controls-20261012-v1 --runtime-root /data/tmp/oc-cbt-cctl-1012-v1
```

The separate discoverable suite exercises real child-CLI terminal paths with
explicit offline workload/I/O faults. It does not start a browser, authenticate,
serve HTTP, or certify new UI/product behavior. Each child retains actual exit,
stdout/stderr and result. It covers first-failure plus late failure, late failure
alone, log close, artifact read, stop, traversal, and successful verification
followed by an unavailable final source. The original 15 controls remain intact.
An offline valid-candidate positive control checks three source captures and
byte-identical candidate/final reports with a stable completion timestamp. Its
synthetic ledger is not an execution of the 42 browser workflows.
The baseline red evidence is
`/hf3fs-jd/data/tmp/opencad-config-browser-terminal-before-20261012-v1`;
the first repair attempt's runner-stream failure is retained separately under
`opencad-config-browser-terminal-after-20261012-v1`. Neither is converted to PASS.
These changes invalidate current-source browser binding for a subsequent
candidate; earlier 42-check browser evidence remains evidence of its old source,
not a newly executed browser gate for this terminal-only repair.

## Retained Development Failures

Each original attempt remains under the corresponding
`/hf3fs-jd/data/tmp/opencad-config-ui-browser-20261012-vN/`:

- v1: initial UI allowed a click before current project binding was ready.
  The panel now disables unbound controls; the runner waits for a bound project.
- v2: the fixture dispatched a non-bubbling synthetic select change.
  The runner now selects configurations using real Chromium keyboard input.
- v3: the fixture omitted JSON content type on save; the service correctly refused it.
  Save now uses a real panel button with the existing strict API contract.
- v4: the fixture's ten-second shutdown bound was shorter than the fifteen-second
  HTTP idle drain. That attempt retains the original forced exit -9.
- v5: the delayed-response fixture returned its deliberately pending promise to
  CDP. The corrected fixture schedules it without blocking the releasing CDP call.

The subsequent v6 run completed 38 real browser checks with original parent rc 0,
but later test/validator changes require fresh source-bound acceptance. None of
these preserved failures is converted to PASS or evidence of an industrial gate.
The v7 source also completed 38 checks and offline source/artifact readback with
original exits 0. The final fixture additionally exercises a visible in-panel
two-step confirmation, both cancel (retaining drafts) and accept (explicit discard).
That additional fixture source requires its own subsequent frozen run.
The v8 attempt retained a CDP timeout while a native confirmation dialog blocked
the sequential input response. Confirmation now uses a nonblocking labelled panel
group with real click targets; the confirmation requirement is not removed.
The v9 source completed 40 browser checks and offline readback with original exits
0, but is not acceptance of subsequent runner/document changes. The final runner
also selects zh-CN by its real catalog position and checks this panel's labels,
not arbitrary user-defined names or all site text.
The precommit-browser v1 retained original rc 1 with 35 completed checks and no
source drift: the mobile STEP fixture observed the filename before Chromium
finished writing its bytes. The final file was 19038 bytes and matched the HTTP
download witness, but the early read was empty. The runner now requires Chromium's
download-completed event and exact byte/hash binding before FreeCAD reimport.
An incomplete-download negative control rejects premature reads. Screenshots now
capture the whole actual page rather than only the scrolled form viewport, so the
rendered 3D canvas and the parameter panel are both retained without DOM alteration.

Not accepted: arbitrary configuration definition CRUD, arbitrary topology naming,
distributed crash atomicity, the shared adapter's complete user-switch behavior,
production deployment, whole-tree regression or industrial parity. GPU is not used.
The historical candidate-browser fixture has no live auth endpoint; it cannot
certify this private-service panel. Its optional scenario is not rerun or silently
treated as green. No anonymous-auth fallback is added to accommodate that fixture.
