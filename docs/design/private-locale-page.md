# Private 3D Locale Wiring

The isolated source is based on `eb19211ca7ca4b9304678fe747026009234d1515`.
Only `secure_web.py`, `web/js/private-locale.js`,
`tests/test_private_locale_page.py` and this document belong to this change.
Authentication, durable transactions, CAD commands and production are unchanged.

## Contract

The private page loads `locale-service.js` before `project-session.js`.
Its deferred `private-locale.js` waits for both DOM and real catalog readiness,
then binds only `.industrial-workbench`. It neither remounts a panel nor sends
a request, writes a preference, changes an input or unlocks a private view.
The shared service owns `data-panel-i18n` and related attribute bindings;
existing `data-i18n` remains owned by the CAD viewer.

The 2D path never loads the 3D binder. When its existing index already contains
the unique locale-service tag, the private session adapter follows that tag;
otherwise both assets are inserted in that order. Duplicate locale-service
tags are rejected rather than initializing a second state service. The parser
recognizes absolute and relative source paths and real tags, not comments or
an exact attribute ordering. Deferred, asynchronous or unclosed service tags
are refused because they cannot establish the required initialization order.

The shared catalog must contain the six `panel.workbench` keys: `heading`,
`parametric`, `surfaces`, `assembly`, `drawing` and `render`. Their locale and
strings come from the accepted state event, not browser-language inference or
cross-account storage. Initial anonymous English remains an explicit unbound
presentation, not a user's authenticated language.

`OpenCADPrivateLocale.ready` preserves the original rejection and returns
the binding's unsubscribe function on success. A missing service, catalog,
workspace or translation is also reported through a separate visible alert
and the console. It must not overwrite the CAD operation status or silently
claim successful localization.

## Verification

The exact ten-case runner first returned rc 1 with ten failures against the
unchanged parent source at `2026-10-12T05:02:03.681611+00:00`.
Evidence is `/hf3fs-jd/data/tmp/opencad-private-locale-page-before-20261012-v1`.
It checks asset order and uniqueness, all five summary bindings, the workspace
accessible name, actual JS boot, late DOM readiness, catalog and binding
failure, absent workspace/service and unchanged draft/disabled/hidden state.
The JS executes under Node; the DOM and shared service are explicit doubles.
These are not browser or whole shared-catalog acceptance.
The unchanged ten-case run passed at `2026-10-12T05:04:18.780142+00:00`.
Four new 2D separation/ordering/duplicate-load cases were then added; all
fourteen passed without skips or source drift at
`2026-10-12T05:06:02.565401+00:00`, original rc 0.
Those results are separate evidence snapshots and do not rewrite before-red.
The relative-path integration check then exposed the limits of the original
literal-tag matcher; that version was not submitted or published. Four more
parser cases were added. All eighteen passed at
`2026-10-12T05:10:46.943272+00:00`, original rc 0, without skips or drift.
Static 3D captions use the explicit anonymous English presentation until the
accepted locale and shared catalog replace them.
Independent review then returned one failure and five errors because the
static-server double used an unresolved temporary-root alias, unlike the real
server's resolved root. That failure is retained under
`opencad-private-locale-page-aquinas-20261012-v1`. The fixture now uses an
explicit local runtime root, resolves it, checks actual file bytes and imported
source, and requires the precise `invalid_locale_service` error code. No product
static confinement was relaxed and no old failure is promoted to success.

Run the same source-bound checks with a new evidence directory:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_private_locale_page.py --out /hf3fs-jd/data/tmp/opencad-private-locale-page-next-20261012-v1
```

Before production adoption, integrate the separately owned shared service and
catalog, verify real static asset responses and repeat authenticated desktop
and mobile language switching with nonempty drafts and account revocation.
Whole-page and all-panel language completeness remain unaccepted until that
new-source run. This patch alone does not certify full-site localization,
industrial parity, GPU speed or a live deployment.

## Chair Resume 2026-10-12: strict public failure node

The continuation session replaced the private binding's failure `<p>` with the
single strict public locale diagnostic agreed by the chair and session seat:
one body-direct, visible `<output>` with exactly `role=alert`, `lang=en` and
`data-opencad-public-locale-error="private"`, no element children, and the
fixed text `private_locale_initialization_failed`. It never interpolates the
underlying error, path, project or user data into that node. The original
rejection remains observable through `OpenCADPrivateLocale.ready` and the
console keeps diagnosis; `role=alert` alone is never a shield exemption.

The Node fixture now asserts the exact attribute set, body parent, leaf shape,
fixed text and absence of known underlying failure strings, while retaining
the draft/disabled/hidden non-mutation assertions. The development run
`/hf3fs-jd/data/tmp/opencad-private-locale-chair-fix-20261012-v1` returned the
actual parent rc 0 with 18 tests, 0 failures and 0 errors. This remains a
Node/static-fixture result: the real shared catalog/foundation execution,
authenticated private HTTP flow, desktop/mobile browser switching, logout or
cross-account recovery, production deployment and industrial claims are still
not executed by this seat and must be done on the composed integration tree.
