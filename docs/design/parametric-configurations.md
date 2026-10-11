# Parametric Feature Configurations

This extension prepares real FreeCAD solids, not field-only configuration metadata.
The five owned files change parameter evaluation, its Web adapter and their tests.
Document, kernel replay, gateway, authorization, durable store and UI are unchanged.

## Persisted Schema

Version 1 remains readable and the existing `create_plate` still creates version 1.
Version 2 retains the same expression parameters, dimension/feature bindings,
checks, guards, active configuration and explicit overrides. It adds two required
bounded tables:

```json
{
  "version": 2,
  "configurations": {
    "wide": {"width": "60 * mm"},
    "solid": {},
    "wide_solid": {"width": "60 * mm"}
  },
  "feature_bindings": {
    "plate_boss": {"target_uuid": "existing-boss-uuid", "base_suppressed": false},
    "hole_cut": {"target_uuid": "existing-cut-uuid", "base_suppressed": false}
  },
  "configuration_suppressions": {
    "wide": {},
    "solid": {"hole_cut": true},
    "wide_solid": {"hole_cut": true}
  }
}
```

This excerpt additionally requires the existing nonempty parameters and bindings.
Each configuration must have a suppression table, including an explicitly empty
one. Every suppression key resolves one declared semantic feature binding and
one unique stored UUID. Values are strictly booleans, never numeric expressions,
integers, strings or null. Unknown fields, names, UUIDs and duplicate targets fail.

Suppression is computed from `base_suppressed` each time, not from the last active
configuration. Unmapped features retain their stored flags. An omitted semantic
key restores its declared baseline, including a true baseline. `configuration=""`
restores base parameters and base suppression and clears previous overrides.
Omitting configuration retains the persisted active selection/overrides.
The saved feature flags must match that selection; inconsistent persisted input
is rejected, not repaired silently.

Every declared configuration and the base state are checked for missing,
self/forward and inactive feature dependencies. There is no automatic child
suppression, feature reordering or replacement of an input UUID. No configuration
may leave zero live features. A live edge/face-index consumer after a suppressed
feature is rejected as `configuration_topology_unsupported`, rather than guessing
that an old index still means the same topology.

## Web Contract

Use the existing authenticated `/api/parametric` route and gateway CAS envelope.
New `action="create_configurable_plate"` creates the real driven plate/cut with
the version 2 configurations above. Existing `action="create_plate"` is unchanged.
Both require an empty project and its exact `expected_document_sha256`.
`action="rebuild", configuration="solid"` removes the cut from the real BRep;
`"wide_solid"` combines dimensions and suppression; `""` restores the pierced base.
No POST field directly edits a stored suppression UUID or topology index.

GET and candidate responses add `parametric_version`, `feature_bindings` (each
entry has target UUID, baseline and effective boolean), and
`configuration_suppressions`. Existing configuration names, dimension inputs and
feature order remain available. The chair owns UI wiring; no UI change is claimed.

Core `feature_configuration(document, evaluation, require_current=True)` is a
read-only persisted-state validator. `prepare_rebuild` checks persisted state,
deep-copies, applies selected flags, binds all dimensions/depths, solves every
constrained sketch, validates guards and measures the complete live replay.
Even suppressed features retain valid parameters and solved profiles. A suppressed
hole does not disable hole containment/design checks.

The existing candidate seal covers L1, configuration response, mesh and STEP
metadata. The adapter does not install anything; the existing gateway owns CAS,
durable history/receipt publication, explicit save, undo/redo and reopen.
Failures retain their original cause and external scratch; they do not publish
a candidate, alter a current project or silently substitute a previous shape.

## Acceptance And Limits

Run both owned standalone suites with fresh external evidence:

```bash
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_parametric.py --evidence-dir /hf3fs-jd/data/tmp/opencad-config-suppression-owner-core-20261012-v2
sudo -n -u infra env PYTHONDONTWRITEBYTECODE=1 OPENCAD_TEST_RUNTIME_ROOT=/data/tmp/opencad-config-suppression-owner-web-20261012-v2 python3 -B oss-alternatives/apps/opencad/tests/test_industrial_parametric.py --evidence-dir /hf3fs-jd/data/tmp/opencad-config-suppression-owner-web-20261012-v2
```

Core tests require actual FreeCAD, measure four configuration transitions,
independently integrate closed meshes, export/reimport STEP and save/reopen L1.
An additional live downstream mirror retains its UUID while its driven offset,
complete BRep bounding box, volume and independently imported STEP follow the
selected dimensions and suppression. This proves full replay for that supported
modifier, not stable edge/face naming for arbitrary downstream operations.
Analytic pierced volume is `(width * 20 - pi * (width / 10)^2) * depth` mm3;
solid volume is `width * 20 * depth`. BRep/STEP tolerance is `1e-5 mm3`,
mesh tolerance 0.5 percent. DoF/residual are solver measurements for the two
fully constrained fixture sketches, not proof about all industrial models.

The Web suite additionally uses real auth, gateway, local SQLite and HTTP, checking
owner access, configuration switch, exact receipt replay, stale CAS and failed
input preservation, save, undo/redo and controller restart from durable data.
Its own local listener and fixtures are not production; all evidence is external.
The existing small HTTP fixture remains a separate candidate-contract test.
Both standalone results bind all five owned files plus the complete source
manifest, modes, HEAD and dirty status before and after. Empty/skipped suites or
source drift fail. Result `actual_rc` is the intended CLI exit status; the caller
must still collect the original process exit and compare it, not trust that field
alone. Failures preserve the first failing test and traceback or explicit gate
reason. Older results remain unchanged and do not certify later test bytes.

The optional broader `--acceptance-smoke` attempt is retained as FAIL:
`/hf3fs-jd/data/tmp/opencad-config-suppression-owner-core-20261012-v1/20261011T223755361719Z/`.
Its 36 targeted tests passed, but the original parent exit was 1 because the
independent `test_store.py` child ended with -7 (SIGBUS) at
`test_27_document_survives_reopen`. That legacy fixture uses `TemporaryDirectory`
under smoke's 3FS/FUSE `TMPDIR`, despite the separately declared local runtime.
This establishes a storage-location mismatch, not a proven SIGBUS root cause.
The unrelated store test and shared acceptance tool are outside these five
owned files and remain unchanged. No broad smoke/full-regression PASS is claimed;
the targeted suites must obtain their own fresh final-source results.

Unaccepted: arbitrary persistent topology naming, face-attached sketches,
configuration-driven assembly variants, arbitrary cross-sketch constraints,
guide lofts, large-model solver scaling, full industrial parity, UI interaction,
production deployment or distributed crash-atomicity. A passing owned suite is
not an inherited whole-source regression for this new source.
