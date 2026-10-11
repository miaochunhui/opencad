# Document Input Integrity

## Contract

`cadcore.docmodel.load_blocking_problems(document)` is the strict admission
gate for a `Document` or L1 mapping. An empty result means the checks described
here passed, not that every CAD extension or physical model is valid. Validation
does not change the supplied document, invent identities, coerce coordinates,
choose between conflicting representations, or replace missing geometry.

`document_from_l1` remains a parser and migration entry point, not an implicit
strict geometry gate. Existing diagnostic workflows may parse an invalid
candidate and inspect its problems. Web mutation callers must parse, invoke
the admission gate, and reject before a durable commit. `document_to_l1` also
remains deterministic serialization rather than admission.

## Identity And Geometry

The document identity index includes `meta.doc_uuid`, sketches, features,
annotations, sheets, sketch entities/constraints/dimensions, and sheet
views/dimensions. UUID spellings are compared by their parsed UUID identity.
Cross-container, cross-kind, and cross-sheet duplicates are blocking. Reference
strings are not identity declarations. Missing or malformed identity-bearing
nodes and containers are rejected at admission.

One legacy exception remains: exactly one sketch dimension and one
`dimension`/`dimensional` annotation may share a UUID when both contain the same
exact-integer `value_nm` and string `text_key`, with identical types. Their
targets need not be identical: the legacy plate uses a sketch entity reference
and a feature named-shape reference for the same displayed dimension. A third
identity, mismatched mirror, or sheet-dimension mirror is not exempt.

Known coordinate/radius keys and point coordinates require exact Python `int`
values, never `bool`, null, strings, floats, nested lists, or non-finite values.
Existing nanometre range limits also apply. Point pairs must be 2D. Angles and
direction components remain finite numeric values; directions reject bool and
strings, and `closed` requires an actual bool.

Required geometry is checked for line, circle, arc, point, ellipse, polyline,
spline, ray, and xline. Nonpositive radii, zero-length lines/directions, collapsed
point sequences, and consecutive repeated points are rejected. A closed point
sequence needs at least three distinct points; an explicit repeated first
point at the end is permitted.

Accepted read-only geometry aliases include line `p1`/`p2`,
`start`/`end`, `start_nm`/`end_nm`, `p1_nm`/`p2_nm`; circle/arc
`center`/`center_nm`, `radius_nm`/`radius`/`r`; arc
`start_deg`/`a1_deg`, `end_deg`/`a2_deg`; and `points`/`points_nm`.
Multiple representations must agree. No alias is converted into a persisted
replacement or used to default absent geometry.

Relevant problem prefixes include `uuid_duplicate`, `uuid_missing`,
`uuid_bad`, `bad_container`, `bad_node`, `missing_meta`,
`geom_length_not_integer`, `geom_missing_field`, `geom_points_invalid`,
`geom_degenerate`, `geom_conflicting_fields`, `geom_direction_invalid`,
`geom_flag_invalid`, and the existing nanometre/angle problems.

## Compatibility And Limits

Missing legacy `schema_version` still migrates from version 0; explicit null
is rejected with `DocumentError(reason="bad_schema_version")`. Missing
top-level legacy scaffolding is filled by the existing migration, with
diagnostics. Missing geometry is not scaffolding.

The real plate fixture, native sketch/draw producers, vendor fields, and
deterministic roundtrips remain regression-tested. Unknown nonempty geometry
kinds retain generic value checks; this is not a complete validator for all
extension-specific geometry. UUID declarations inside arbitrary vendor,
organization, or embedded history subtrees are not indexed as top-level
document identities. This work does not establish production HTTP admission,
solver correctness, industrial CAD parity, or multi-tenant readiness.

## Evidence And Reproduction

Evidence parent:
`/3fs/research3fs/data/tmp/opencad-durable-20261011`.

- `document-integrity-red/result.json`: original defect reproduction,
  24 tests, 20 failed methods and 58 failure records.
- `document-integrity-alias-syntax-red/result.json`: intermediate syntax
  failure retained separately, subsequently corrected.
- `document-integrity-boundary-red/result.json`: three new tests genuinely
  expose missing-meta admission, null-schema coercion, and the radius-alias
  positivity gap; 33 tests, exactly three failed methods.
- `document-integrity-green-v4/result.json`: 92 tests PASS, consisting of
  33 new methods and 59 unchanged original docmodel/fixture regressions;
  no errors, failures, skips, or drift in the two recorded source files.
  Finished at `2026-10-11T02:52:47.773851+00:00`.

Run from the independent worktree, using a new external output directory:

```bash
sudo -n -u infra python3 -B oss-alternatives/packages/cadcore/tests/test_document_input_integrity.py --regressions --out /hf3fs-jd/data/tmp/opencad-durable-20261011/document-integrity-recheck
```

The runner records timestamps, actual test counts, failure details, log SHA,
the exact loaded docmodel/test paths and their hashes, and their before/after
drift. Its source binding is not an entire-repository closure.

The frozen `tools/project_store_independent_review.py` provides the separate
18-method HTTP/SQLite admission review. Its previous red run is historical.
The new main-source result is pending the chair's integration of this patch;
local module green evidence must not be relabeled as main HTTP green.
