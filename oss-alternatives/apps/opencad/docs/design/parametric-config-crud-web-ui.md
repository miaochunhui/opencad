# Atomic Configuration CRUD UI

## Interface

The existing parameter panel retains the current-model override form and adds a
separate named-configuration definition form. Effective expressions are not
configuration definitions. The editor reads `configuration_definitions`,
`configuration_suppressions` and the canonical L1 bound to the same private
project/revision/snapshot/document SHA.

Parameter rows explicitly choose inheritance or one unit-bearing expression.
Suppression rows choose inherit, live (`false`), or suppressed (`true`).
Inheritance omits the semantic key and restores `base_suppressed`; it never
reuses the last active flag. UUID and baseline bindings are read-only.
Version 1 definitions have parameters only, with no suppression edit or upgrade.

Each action makes one `POST /api/parametric`:

- Create: `create_configuration`, name, complete definition.
- Replace: `replace_configuration`, existing name, complete definition.
- Suppressions only: `set_suppressions`, existing name, complete suppression row.
  An unsubmitted parameter-definition change refuses this shortcut.
- Delete: `delete_configuration`, existing name, explicit replacement if active.

All include the current expected document SHA. The unchanged private session
alone supplies revision/snapshot SHA/request ID, serialization and original
receipt replay. There is no open/rebuild pair, panel-owned retry, fresh-ID
fallback, UUID rename or generic transaction/auth/storage change.

Selection is explicit: keep omits `configuration` and retains active overrides;
apply target passes its name and clears overrides; base passes `""` and clears
overrides. Null is never sent. Active deletion starts with no replacement and
requires a surviving named configuration or base plus visible confirmation.
Cancel changes neither saved definitions nor drafts; base itself is not deletable.

## Draft And Failure Boundary

Definition and model overrides have distinct dirty state and submit snapshots.
Compatible reads and save acknowledgements preserve both. A successful operation
clears only its unchanged submitted draft; another form's changed semantic base
blocks further writes rather than silently rebasing or erasing it.
Unknown results remain the session's exact original request; only the existing
manual receipt confirmation can resolve them. Confirmed-save/view failure never
resubmits. Changed source/base, CAS, invalid expressions and dependency rejection
retain draft input and the first error.

Owner/project checks and generation/read sequence gates fence late reads.
Unauthorized or changed identity clears this panel's private forms, definitions
and result metadata. This is not a site-wide draft-clearing or locale-scan claim.
All added labels are Simplified Chinese within the existing panel visual system.

## Independent Gate

The new standalone `tests/test_parametric_config_crud_browser.py` owns its fresh
external results and short local runtime. It imports existing CLI/Chromium/mesh
validators read-only, with reader-file source hashes. It does not invoke the
previous browser acceptance or copy any old PNG.

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_parametric_config_crud_browser.py --selftest --evidence-dir /hf3fs-jd/data/tmp/opencad-config-crud-controls-20261012-v1 --runtime-root /data/tmp/oc-cc-ctl-1012-v1
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_parametric_config_crud_browser.py --evidence-dir /hf3fs-jd/data/tmp/opencad-config-crud-browser-20261012-v1 --runtime-root /data/tmp/oc-cc-br-1012-v1
```

Every rerun requires fresh paths; failures and local generated fixtures remain.
Offline readback uses the same script with `--verify-evidence <producer root>`
and a fresh `--evidence-dir`; it starts neither kernel nor HTTP.
These commands describe gates, not predeclared successful results.

Nine mandatory workflows run with real Chromium inputs at 1440x900 and 390x844:
inactive creation with two drafts; replacement/selection/overrides; three-state
suppression; explicit active deletion/cancel; invalid-definition atomicity;
save/draft conflict; save/undo/redo/CLI restart/download; lost-response exact
receipt and late-read fences/CAS; logout and second-identity isolation.
The gate retains fresh screenshots, real mesh/STEP and FreeCAD STEP reimport,
actual HTTP request/CAS bodies, SQLite receipt counts, strict original CLI
READY/argv/PID/startticks/exit, source bytes/modes/HEAD/status, artifact hashes
and a saved/reread terminal candidate/completion marker. It requires exact
mandatory labels and rejects drift/incomplete evidence; parent rc must still
be independently received by the caller.

The browser records every completed response for parametric/state/mesh/STEP,
save/undo/redo and the forbidden open route. Independent non-auth HTTP actors
also retain response bytes (base64), byte count, SHA256 and the allowlisted
content-type/X-OpenCAD headers. Each row is rehashed and JSON status/ok checked;
successful operations bind their request to the response and SQLite receipt.
Authentication payloads, cookie/authorization headers and full transport
headers are intentionally excluded, not claimed as complete HTTP capture.
Exact lost-response replay requires two identical request bodies, identical
response bodies and unchanged revision/receipt count. Synthetic controls
mutate each response field, single-receipt increments, booleans and replay.

The original browser v1/v2/v3 FAIL roots remain outside Git. V1 exposed an
empty-project editor assumption; v2 exposed overwritten failure guidance;
v3 completed 17 workflows before a test-side reload/login-form replacement
race. The reader now waits for the current document's actual auth probe
status, then uses real login or project-selection controls. This does not
relax the expired-private-DOM or second-identity isolation requirements.
V4 added a real committed-POST/held-response/revocation boundary and found
that the outer error display overwrote the permanent expired-panel notice.
The expired shield now retains its notice; the original v4 FAIL is preserved.
During a pending POST both draft forms are locked, not silently accepting new
input. A late authenticated response followed by live revocation recheck must
clear this panel rather than render its saved private fields.

Production, GPU performance, arbitrary persistent topology, all-config BRep
validity, site-wide language coverage and distributed crash recovery are outside
this gate. The backend and earlier UI's original FAIL evidence remain unchanged.
