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
depending on infra `ops/oss-alt-web`, desktop packaging or `cad-parity/status`
evidence remain migration debt. Full discovery must report those failures;
do not delete tests or turn missing dependencies into successful skips.
The old `tools/cad3d/opencad_acceptance.py` is retained only as a browser
helper dependency; its wider infra/desktop release stages are not this
repository's release contract.

## License

First-party CAD, DXF and vault components are Apache-2.0 according to the
preserved `oss-alternatives/license-manifest.json`. See `LICENSE` and `NOTICE`.
That manifest is not a complete third-party dependency audit. Separately
installed geometry engines keep their own licenses and notices. No third-party
CAD source, standards body text or desktop runtime is bundled by bootstrap.
