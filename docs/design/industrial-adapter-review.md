# Industrial Adapter Boundary Review

Independent review only. The tool owns no product source or shared index.
Scratch data, failures and true FreeCAD jobs stay outside the repository.
Do not infer industrial parity from either module or private HTTP scope.

## Review Interface

`tools/industrial_adapter_review.py --source-root <frozen checkout> --scope modules`
loads the selected source as read-only and verifies real module origins.
An optional `--expect-head` must equal the chair's frozen HEAD before and after.
Every required source SHA is recorded on both sides; any drift invalidates the
run rather than weakening the geometry or mutation assertions. Missing modules
fail without skips. No existing package test suite is rerun.

The tool checks new orthogonal boundaries: a two-circle annular solid driven by
one depth parameter; independent mass/mesh volume and candidate byte binding;
legal JSON whitespace or SHA mismatch; measured-mesh provenance and mass
tampering; same part-name reference bytes or SHA tampering; mutable surface
candidate documentation; and mixed-domain drawing refusal.

Mixed-domain negative controls use actual far-away geometry, not arbitrary
metadata names. A surface spans x=30..40 mm. A real referenced annular assembly
is placed at x=50 mm. Their presence beside a normal feature solid must either
be rendered through an explicit supported domain or be rejected. In this
current one-solid drawing profile the tool expects rejection. Hashing the full
document while drawing only its local feature solid is not a whole-model
acceptance. A real top-view hole is the orthogonal positive HLR control.

## Candidate Boundary

`cadcore.parametric.PreparedRebuild` exposes `verify_integrity()` for its
Document, canonical L1, replay recipe and measured mesh. This binds consistency,
not trust against an attacker who owns server code and can recompute hashes.
The caller must additionally check the authoritative base under its CAS lock.

The surface candidate currently carries a mutable Document; the assembly
candidate carries an L1 string and mutable response. Frozen dataclasses alone
do not seal nested data. A gateway must normalize candidate bytes, verify that
its response digest and measured geometry refer to those exact bytes, then
perform authorized durable CAS and maintain both histories. Do not silently
accept tampered response mass/mesh solely because the base SHA still matches.

Assembly preview source uses per-instance real vertices/triangles. Its bbox is
only camera normalization, not a proxy geometry generator. Associative drawing
currently rebuilds the solid feature tree through STEP/FreeCAD HLR; it does not
yet consume industrial surface or real-assembly extension namespaces.

## Acceptance Status

Results are read from each unique external `result.json`, `tests.log`,
`observations.json`, `command.log` and source hashes. A clean module run returns
`MODULE_BOUNDARIES_PASS_GATEWAY_PENDING`, never full Web PASS. The chair's new
candidate publication routes were subsequently frozen and reviewed below.
No production requests, product edits, nested delegation or push is allowed.

### Observed Module Run

First evidence:
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T030346701259Z-65a106e594bf424c84f3ad2877d89175`.
Its `result.json` records 8 tests: 6 passed, 2 failed, no errors/skips,
and zero source drift. The failures are retained; this is not acceptance.

The true annulus rebuild measures 2001.194520336698 mm3, agreeing with
`91 * pi * 7`; independent signed-triangle integration gives
2000.365237449438 mm3. Legal L1 whitespace, wrong SHA, mesh provenance,
measured volume, and same-name assembly part byte/hash changes are rejected.
The real top-view HLR includes the 3 mm-radius hole.

Two cross-domain counterexamples reproduce at
`oss-alternatives/packages/cadcore/cadcore/draw/associative.py:269` and `:286`:
the full L1 is hashed, but only feature geometry is exported to STEP/HLR.
A real surface at x=30..40 mm and a real assembly at x=40..60 mm are omitted;
both drawings accept and report only the annulus bbox x=-10..10 mm.
These are demonstrated module-boundary violations, not claims about a
gateway route that has not yet been wired/frozen.

The targeted follow-up evidence is
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T030829266036Z-342029fba7744b72af0ed07f61c80058`.
It ran only four new test methods, not the earlier eight: two methods passed;
two methods failed across four surface/assembly subtests, no errors/skips.
Its source anchors again record zero drift. Both runs bind HEAD
`7ccf8fa48cf73cb2b2a8bb03ee0776fbcc4a9a6f`.

Default Workspace mesh (`server.py:459`) accepted both mixed domains and
replaced the prior mesh cache with local feature-only geometry bound to the
whole document SHA. Default STEP (`server.py:511`) created actual STEP files;
the real FreeCAD worker reread them and measured x=-10..10 mm only.
Document and histories stayed unchanged on STEP acceptance, but misleading
artifacts existed. Separate industrial panel reads passed with actual
surface/assembly triangles at their remote coordinates, retaining document,
undo, redo and the prior cache. Nested parametric Document mutation was
rejected as `candidate_changed`.

The module negative controls demonstrate the unsafe behavior of an unguarded
default/full-model entry. They do not mandate modifying Workspace if a single
gateway admission reliably rejects every corresponding public route and
legacy alias. A future explicit feature-only operation can be supported with
an honest domain scope; it is not a whole-model success.

### Admission Recommendation

Use one gateway admission function with a server-owned action/capability
mapping. Validate persisted domain schemas first and derive the set of active
geometry domains from validated state, not unknown extra keys or user labels.
`features`, `x_industrial_surfaces`, and `industrial_assembly_v1` are separate
sources; saving them together is not a Boolean, assembly, or surface rebuild.
An invalid recognized namespace must fail loudly even when another domain
could otherwise be drawn.

Allow authorized state/document reads, durable L1 save/reopen, and explicit
domain-panel reads to coexist. Each preview must identify its domain and
bind actual vertices/triangles, canonical document SHA, and project revision.
Assembly JSON export is a domain document export, not a whole-model STEP.

Refuse a whole-model operation if its backend does not consume every active
domain. Until explicit dispatch/composition exists, this includes default
mesh, STEP downloads/exports, full-model mass/clearance, and associative
drawing/view/section/dimension/export. PDF/DXF exports explicitly scoped to
existing 2D sheet entities can remain available, but must not be advertised
as whole-model industrial projections. A missing supported domain cannot
produce successful empty geometry or a hash-bound partial result.

Check admission on GET and POST, legacy command aliases and downloads, before
cache lookup, candidate measurement, output creation, or durable CAS. Recheck
the authoritative document/version under the CAS lock. Refusal must preserve
project revision, document, undo, redo, existing cache and output bytes.
Feature-parametric preparation may remain a labeled feature-only service;
its response cannot be presented as validating industrial extra geometry.

The review tool adds targeted default Workspace mesh/STEP negative controls
for each industrial domain and a separate-panel coexistence positive control.
STEP omission is measured by reading the actual exported STEP with the real
FreeCAD drawing worker, not inferred from its header or filename. Exact
`--case` selection permits only newly added cases to run while preserving
the earlier failed evidence without an unchanged rerun.

Acceptance after gateway wiring must include these distinct negative controls:

- Mixed solid+surface and solid+assembly full-model mesh/download/drawing must
  refuse before cache lookup or artifact creation unless all domains rebuild.
- Surface+assembly independent reads must succeed with their actual remote
  vertices and unchanged document/history/cache; read coexistence is legal.
- Changed candidate Document, legal L1 byte suffix, wrong document/recipe SHA
  and measured mass/mesh changes must not reach CAS or a durable receipt.
- A recognized but malformed persisted industrial namespace must refuse,
  not be treated as absent or replaced by the healthy feature domain.
- An explicitly scoped unsupported cross-domain part/drawing reference must
  refuse; domain-scoped exports must identify exactly what was consumed.

Current targeted reproduction from any working directory:

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-surfaces-20261011/tools/industrial_adapter_review.py --source-root /data/dev/opencad --scope modules --case test_workspace_mixed_mesh_refused_preserves_document_histories_cache --case test_workspace_mixed_step_refused_without_artifact_or_state_change --case test_independent_domain_reads_coexist_and_keep_real_geometry --case test_parametric_nested_document_tamper_rejected
```

Use only after a deliberate source/probe change or for an independent audit;
do not automatically retry the retained failed run. The result records exact
selected cases, test methods and failing subtests separately through its log.

The planned Web follow-up must exercise real private Workspace/project
transactions: corrupted candidate doc/bytes/hash or response mesh cannot be
committed; cross-domain drawing must refuse unsupported geometry; failures keep
head, cache and undo/redo unchanged. Do not pretend a review-only local checker
is the product's enforcement. Existing 30 surface and 17 adapter cases are not
repeated or counted again.

## Frozen Gateway Review

Source HEAD `40dae2c9d413f27b6f69dc30e87a4c9798165692` was checked before and
after every run below; all source hash comparisons show zero drift.
Sampling is on 2026-10-11, Asia/Shanghai (+08:00). A private ephemeral localhost
HTTP server uses the real AuthController, ProjectStore, ProjectGateway and
FreeCAD; no production endpoint or product source is changed. Authentication
values are generated in memory, not logged or copied into review evidence.
SQLite runtime is external local ext storage, separately recorded.

### Passed Boundaries

At 11:24:14..11:24:23, three test methods passed with no errors/skips:
full-model surface/assembly rejection for mesh, STEP, PDF/DXF, drawing and
exports, render, clearance, parametric and editing command; incompatible
surface/assembly/part writes and mixed open rejection; real parametric
candidate legal-byte/hash/mesh mutation rejection as `candidate_changed`.
Every refused mutation leaves the exact durable head, both histories and
absence of its receipt unchanged.

Evidence:
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T032414804735Z-0c55dea09cd64621a62195c1fe3aa9b7`.

At 11:26:35..11:26:38, the assembly-domain positive test passed: real mesh
vertices reach x=60 mm, document download bytes/hash agree, ordinary save
preserves the snapshot, and undo/redo restores exact domain/history bytes.
Evidence:
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T032635823421Z-d85c3ce3ecb745a28133d318bc649682`.
Its first attempt is preserved below: the review HTTP helper parsed the
JSON document download as a dict; raw-byte selection fixed the fixture.
Only that corrected positive test was rerun.

### Open Findings

1. Candidate handoff integrity is optional at `project_gateway.py:223`.
   Surface and assembly candidates have no verifier. Real prepare calls
   followed by nested doc/response-SHA/mesh mutations each return HTTP 200,
   advance revision 1 to 2 and create a durable receipt. Changed document
   bytes can disagree with the measurement response SHA. The mutation is
   deliberately injected at the internal handoff after actual FreeCAD
   measurement, not asserted to be a directly exposed remote injection API.
   Fix the common contract: a mandatory verifier with creation-time doc and
   response fingerprints, canonical bytes and measured-domain binding,
   invoked before document/history assignment. Do not recompute a fresh
   trusted hash from an already changed candidate at admission.
2. Single-domain open validates domain count, not contents, at
   `project_gateway.py:157` and `:446`. Ordinary authorized POST /api/open with
   `x_industrial_surfaces={"schema":1,"recipe":{"op":"nurbs"}}`, or an empty
   malformed assembly, returns 200 and durably saves revision 2. They pass
   generic docmodel diagnostics because those diagnostics only preserve
   extra data. Validate each recognized domain using its real schema API
   on candidate/open/history admission; preserve the first domain error
   and reject before clearing history or confirming a receipt.
3. Lower-risk contract mismatch: `project_gateway.py:378` read-only command
   fast path bypasses `_domain`. Surface project precision_status returns
   200 with unchanged durable head, despite the announced command policy
   allowing only undo/redo. No geometry mutation or unauthorized project
   access is demonstrated. Either enforce the policy before this branch or
   explicitly admit these non-geometric reads with truthful domain scope.

At 11:23:37..11:23:40, two methods yield five failing subtests, no errors:
three real surface candidate mutations and two malformed-domain HTTP opens.
Evidence:
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T032337699535Z-8b3d2f070d7a421586a8f03333712937`.

At 11:25:44..11:25:48, three methods yield four failing subtests (three
assembly mutations and the command policy mismatch) and one fixture error
in the positive download-byte check. The failure evidence is preserved:
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T032544368993Z-9650352ae4fd4019b30c0e57fa2fcc2f`.
The fixture correction does not alter or erase any product negative assertion.

### Reverification

The gateway scope contains only the newly added review boundaries:

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-surfaces-20261011/tools/industrial_adapter_review.py --source-root /data/dev/opencad --scope gateway
```

Use a chair-provided `--expect-head` for the next frozen source and exact
`--case` selection for targeted fixes. Do not automatically retry retained
negative failures before the source or fixture changes. A PASS for a selected
subset is not a whole review PASS. The two higher-impact findings remain open;
the implemented domain matrix does not establish combined-domain geometry,
all-feature Web parity, or full manufacturing/drawing compliance.

`--scope gateway --readback-result <result.json>` can be repeated to verify
retained evidence against current HEAD and every reviewed source hash without
rerunning tests. It checks both unittest and command summaries and scans the
two owned deliverables with the existing repository credential scanner.
`READBACK_MATCH_WITH_OPEN_FINDINGS` means the old failures still bind the
current source, not product acceptance. New evidence records the original
test times, result/log hashes and current owned-file hashes.

### Current Delivery Readback

On 2026-10-11 at 11:36:58 +08:00, readback found HEAD still at the same
commit but `oss-alternatives/apps/opencad/secure_web.py` changed since the
11:23..11:26 test evidence. The gateway and candidate adapters retained their
measured hashes. The current HTTP source is therefore not certified by those
old PASS results; no unchanged negative test was retried. This is
`INVALID_SOURCE_DRIFT`, not an inference that the old findings were fixed.
The file is a chair-owned untracked integration asset and was not modified
or restored by this review.

Retained first-cause evidence:
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T033658386789Z-45dd84599c0f43c0863942905d2332e1/result.json`.
The earlier unspecialized error is also retained at
`/hf3fs-jd/data/tmp/opencad-industrial-adapter-review-20261011/seat-surfaces/20261011T033518155354Z-09aef9f9947141bfbdd54b3175b69300/result.json`.
The tool now names every changed path and classifies this failure separately.
Both owned delivery files are credential-scanned before named DCO submission.
The next product acceptance requires a chair-frozen current source and
deliberate negative-control revalidation; committing this review does not
certify combined geometry or unresolved product behavior.
