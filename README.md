# OpenCAD

OpenCAD is a browser-based 2D drafting and 3D solid-modeling project.
The product is Web-only: users access a service, not a desktop installer.
Existing features are not a claim of CATIA/SolidWorks industrial parity,
large-assembly performance, or production multi-tenant security.

## Development

Python 3.10+ is required. Basic drafting uses Python's standard library.
Real 3D operations require an available, separately installed FreeCAD backend;
missing backend capabilities must fail visibly, not become mock geometry.
No dependency installation or production restart is performed by bootstrap.

From this repository:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/server.py --port 0 --out-root /hf3fs-jd/data/tmp/opencad-dev-workspace
sudo -n -u infra python3 -B tools/acceptance.py --stage smoke
sudo -n -u infra python3 -B tools/acceptance.py --stage all
```

Read the printed local port and open `/web/3d.html` for 3D or `/` for drafting.
The startup command is a development server, not a public deployment recipe.
Do not expose the baseline single workspace to untrusted users. Project
authorization, per-user preferences, durable commits, conflict handling and
crash recovery need separate acceptance before production use.

## Independent Migration

The original `oss-alternatives/` layout is retained for relative imports.
Only OpenCAD, cadcore, DXF, the vault store/FCX dependency and explicitly needed
CAD test helpers are migrated. No other application, desktop distribution,
credential, build output, screenshot or historical acceptance result is copied.
`mathcore` is not needed by the current CAD runtime import closure.

```bash
sudo -n -u infra python3 -B tools/bootstrap_from_infra.py --phase freeze --plan /hf3fs-jd/data/tmp/opencad-bootstrap-s7/source-plan.json
sudo -n -u infra python3 -B tools/bootstrap_from_infra.py --phase apply --plan /hf3fs-jd/data/tmp/opencad-bootstrap-s7/source-plan.json --out /hf3fs-jd/data/tmp/opencad-bootstrap-s7/migration
sudo -n -u infra python3 -B tools/bootstrap_from_infra.py --phase verify --plan tools/bootstrap-source-manifest.json
```

Freeze and evidence paths must be new. Bootstrap checks frozen SHA-256 values
before and after copying. It rejects every existing same-name migration target,
even identical files, but permits unrelated new files in the target repository.
It never overwrites, rolls back or cleans a partial migration.
`tools/bootstrap-source-manifest.json` records original working-tree bytes,
not a promise that the source matches the old infra Git HEAD. Later changes
have their own commits and acceptance fingerprint; they do not rewrite this
immutable provenance.

`tools/acceptance.py` is the independent regression entry point.
Smoke is a bounded, explicit subset, not full regression. Historical tests
depending on `cad-parity/status` have independent source-binding and real
operation-effect contracts instead of reading old "implemented" verdicts.
The unchanged G17 output tests use the supplementary original output
specification, not historical acceptance results. Supplement bytes are frozen
in `tools/bootstrap-supplement-manifest.json`; initial provenance is unchanged.
`--stage coupling` reproduces these migration boundaries and negative controls.
`all` discovers every current cadcore, OpenCAD Web and DXF unittest, plus
bootstrap negative controls and the real 15-check HTTP selftest. No tests are
excluded and no skips are added. Existing optional-backend and fixture skips
are reported individually, not counted as passes. Wider infra orchestrators
and desktop packaging remain unported and are not covered by this entry point.
The server's selftest creates an external temporary output root and explicitly
passes it to its builder; the original 15 HTTP checks and CLI test stay intact.
Do not delete tests or turn missing dependencies into successful skips.
The old `tools/cad3d/opencad_acceptance.py` is retained only as a browser
helper dependency; its wider infra/desktop release stages are not this
repository's release contract.

Evidence, ordinary test temporary files and exported CAD files remain under
3FS. SQLite WAL tests are the narrow local-storage exception: the runner
provides `OPENCAD_TEST_RUNTIME_ROOT`, defaulting to a new timestamped directory
under `/data/tmp/opencad-test-runtime`, outside every source tree. Override
with `--runtime-root` (a new local directory) or the same environment variable
(a local parent directory). Its path and filesystem type are recorded in each
run. Do not pass 3FS `TMPDIR` to ProjectStore or weaken its filesystem checks.
The runner does not change permissions or automatically remove these roots.
New gateway tests are included automatically by Web discovery once integrated;
the bootstrap branch alone does not certify that unmerged security work.

## License

First-party CAD, DXF and vault components are Apache-2.0 according to the
preserved `oss-alternatives/license-manifest.json`. See `LICENSE` and `NOTICE`.
That manifest is not a complete third-party dependency audit. Separately
installed geometry engines keep their own licenses and notices. No third-party
CAD source, standards body text or desktop runtime is bundled by bootstrap.
