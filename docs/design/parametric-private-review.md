# Independent Private Parametric Review

## Scope and Boundaries

`tools/parametric_private_review.py` reads a selected frozen source tree and runs
its real `ProjectGateway`, `secure_web`, authentication, durable project store,
Workspace, parameter adapter and FreeCAD. It never connects to an existing
service, never uses production accounts and cannot accept a remote endpoint.
Only its own ephemeral IPv4 loopback listener and fresh test database are used.
No source overlays, product patches, shared Git writes or nested agents occur.

SQLite WAL runtime uses a fresh directory below the external local
`/data/dev/opencad-private-fixtures/`, never 3FS. Evidence, HTTP-stage timings,
source hashes, complete failure tracebacks, STEP export and FreeCAD roundtrip
jobs remain below the external 3FS seat directory. Test-account passwords and
session cookies exist only in memory. HTTP audit records never serialize login
bodies, cookie headers or credential-derived hashes. The database contains only
the real authentication controller's salted verifier/session digest.

## Source Freeze Gate

`--source-root` defaults to the checkout containing this tool, not a hard-coded
main tree. Independent main-tree validation supplies an explicit source root.
The preflight binds app source/assets, cadcore/DXF/vault source and resource sets,
checks required real modules and reports route readiness. It does not report
product acceptance. Full execution requires the exact preflight source digest,
rechecks it before creating accounts/listening and rejects source drift after
the workflow. Imports must resolve to the selected source tree.
Loaded namespace packages must have a nonempty `__path__` wholly inside the
selected source. Each report separately binds the review tool's exact SHA.

During a product integration window run preflight or tool selftests. A real
workflow in that window requires the chair's explicit diagnostic authorization,
and any source drift still fails the outer verdict regardless of passing HTTP
checks. Final acceptance requires a stable, chair-approved source digest.
After product changes collect a fresh preflight; never silently substitute a
new digest to retry a failed batch.

```bash
sudo -n -u infra python3 -B tools/parametric_private_review.py --selftest
sudo -n -u infra python3 -B tools/parametric_private_review.py --preflight --source-root /data/dev/opencad
```

The full command uses `--source-root`, `--expect-source-sha` (full preflight
digest), optional `--out` evidence parent and `--runtime-root` local fixture
parent. An absent or mismatched digest refuses execution. Every run receives a
new timestamped evidence directory; old FAIL evidence is never overwritten or
reclassified.

## Real Workflow

The workflow creates two isolated local accounts through the real controller
and logs in through the real HTTP boundary. It checks the anonymous denial,
HTTP-only same-site session cookie, private project creation and parameter GET.
It then verifies:

1. Real `create_plate` creates the 40x20 mm hole plate with positive BRep volume,
   independent closed-mesh volume and actual dimensions/depth.
2. Exact receipt replay is byte-equivalent and makes no durable head/history/
   receipt change. A reused request ID with changed fields is rejected.
3. A second authenticated user cannot read or edit the owner's project.
4. `wide` configuration rebuilds 60 mm width, 30 mm hole center, radius 6 mm and
   boss/cut depth 12 mm. Candidate mesh/STEP and committed L1 share docSHA.
5. Authorized STEP download has exact revision/docSHA headers and is read back
   through real FreeCAD with an independently checked analytic volume.
6. Invalid radius, negative dimension, dependency cycle, executable expression
   and outside hole fail without any head, snapshot, history or receipt write.
7. Stale outer CAS and incorrect inner docSHA fail without a durable write.
8. Real undo and redo preserve exact canonical L1 while advancing project
   versions. Explicit save preserves the document.
9. Two simultaneous HTTP rebuilds at one frozen base have exactly one winner;
   only one receipt/head increment is allowed. Old receipt replay never rolls
   the current project head backward.
10. Closing/reopening the real service/controllers retains the exact document,
    version, parameters, mesh and receipt. Real `/api/open` and a subsequent
    complete rebuild retain the docSHA and honor history reset semantics.

Store anchors are read from the test project's real head and read-only SQLite
row counts; they do not rely on API self-report. The concurrency gate uses two
independent sockets and a synchronization barrier, not a sleep or process
existence. Tool selftests include independent mesh positive/open/nonfinite
negative controls, exact digest and source-difference checks, redaction,
concurrent audit accounting, failure-log double-fault first-cause preservation,
runtime namespace and path boundaries, cross-project/revision/document receipt
negative controls and the existing source-hygiene scanner's positive control.
The actual workflow scans its logs, STEP/job files and real database for the
in-memory test passwords/raw session tokens; only coordinates are reported.
All fixture state is retained in newly created external directories. Closing
the owned listener/controllers is not a production restart, and reopening them
does not reconstruct the Document on the test side.

The read-only `--compare-evidence PRIOR_DIR --source-root TREE` mode revalidates
the prior binding's complete digest and reports added/removed/changed paths.
It creates only a fresh external report and never invokes HTTP or CAD. A
`DRIFT` result exits 2; it is not a product acceptance run.

## Observed Evidence

Evidence parent:
`/hf3fs-jd/data/tmp/opencad-parametric-20261011/seat-private-review/`.

- `20261011T032012329019Z`: initial real authorized HTTP/DB/FreeCAD workflow,
  98 assertions PASS, zero source drift. This used the earlier 304-file binding,
  not the final wider binding.
- `20261011T032500087398Z`: 105 real behavior assertions passed, outer FAIL.
  The review's import gate incorrectly required `__file__` on the legitimate
  `cadcore.i18n` namespace. The tool was fixed and given local/foreign/empty
  namespace controls; this original FAIL remains unchanged.
- `20261011T032612063457Z`: 106 real behavior assertions passed, 45 runtime
  imports bound, 15 evidence/database files checked with no raw credentials.
  Outer FAIL because the authorized main integration window changed
  `secure_web.py`, `tests/test_project_gateway.py` and
  `web/js/project-session.js`. This is not a same-source product PASS.
- `20261011T032849978494Z`: read-only binding comparison reproduced those
  exact three changed paths, with no added/removed path.
- `20261011T033233854945Z`: a chair-authorized recheck against main HEAD
  `40dae2c` refused before importing product code, creating accounts or
  listening because the expected source digest changed after preflight.
  `acceptance=NOT_RUN`; the follow-up read-only comparison at
  `20261011T033256709137Z` identified only `tests/test_project_gateway.py`.

The initial measured 40 mm plate volume was `5997.876140340506 mm3`; the wide
60 mm configuration was `13042.83197364921 mm3`, and its downloaded STEP,
reimported through FreeCAD, was `13042.831973649163 mm3`. The independent closed
mesh integration agreed within the stated 0.5 percent tessellation tolerance.
Two simultaneous same-base requests yielded exactly HTTP 200/409. Final
wider-binding stable-source acceptance is still pending chair freeze; earlier
green observations do not reclassify either preserved FAIL.

The final report separates tool selftests, preflight readiness and real API
acceptance. Browser interaction is not this tool's scope; the separately frozen
adapter browser regression and chair private-browser integration cover that.
This workflow does not certify industrial parity, arbitrary persistent topology,
assemblies, surfacing, production authorization scale or network deployment.
