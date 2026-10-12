# Shared Panel Catalog

The chair owns only `web/panel-i18n.json`, the new
`tests/test_panel_catalog.py` under `oss-alternatives/apps/opencad`, and this
document. Base foundation is `97b80e6`; the parametric consumer `ec80356` is
net-picked into this isolated tree as `505ab8f` for actual-data acceptance.
Do not pick the consumer twice when assembling a release.

The catalog is the unique product translation source for panel copy. This
revision aligns the parametric namespace with its actual consumer request,
removes unused previous key names and corrects named arguments from `detail`
to `message`. It adds the surface, assembly, drawing and render requests, plus
the session translation-failure key. Consumers never receive English fallback
for missing keys or arguments. Protocol IDs, units, user drafts and original
engineering diagnostics are not translated data.

The session password caption uses the explicit `password_label` key. The old
key shape was correctly refused by the existing credential-literal scanner
at `2026-10-12T05:55` (scan original rc 1), because an English caption looked
like a credential assignment. The scanner is unchanged; the UI key and its
consumer contract are renamed, without changing password input names or
authentication. Evidence: `opencad-panel-catalog-chair-scan-20261012-v1`.

## Independent Contract And Controls

The parametric request table is an independent key and placeholder oracle,
not a runtime dictionary. Both missing and extra keys are rejected. The
new test loads the shipped JSON for all existing parametric behavioral
controls, rather than their previous documented-request double.

Positive controls require nonempty exact namespaces, duplicate-free JSON,
matching locale key sets and matching named placeholders. Negative controls
reject duplicate JSON keys, a removed key, an extra obsolete key, identical
but incorrect placeholders in all languages, empty inputs, Japanese kana in
Simplified Chinese, and Chinese/Japanese copy in English. These codepoint
checks do not certify every Simplified Chinese character or natural-language
quality. Full Traditional Chinese contamination and real-browser review
remain separate acceptance obligations.

The CLI binds the physical source, HEAD and status before/after and retains
Node product, fixture, real-catalog and log bytes outside the source tree.
The existing 29 parametric controls run with actual catalog strings, including
drafts, selection, focused inputs, busy gates, private clearing, saved-version
bindings and unchanged original diagnostics. This is still a strict DOM
double, not a browser or the real shared service's integration acceptance.
Surface, assembly, drawing, render and session consumers need their own
new-source tests after integration.

## Observed Evidence

At `2026-10-12T05:47:46.846956+00:00` the new CLI returned original rc 1:
six tests, two failures and one error. Actual panel mounting first failed on
`panel.parametric.panel_aria`; the independent namespace diff also found
obsolete keys and incorrect placeholders.
Evidence: `/hf3fs-jd/data/tmp/opencad-real-panel-catalog-before-20261012-v1`.
That failure is retained, not reclassified after fixing the dictionary.

The corrected parametric catalog returned original rc 0 at
`2026-10-12T05:50:49.279559+00:00`, six tests with no failures/errors and all
29 embedded real-catalog controls passing.
Evidence: `opencad-real-panel-catalog-param-fixed-20261012-v1`.
After expanding the other namespaces, the same CLI returned original rc 0
at `2026-10-12T05:53:25.491682+00:00`, again six tests and no failures/errors.
Evidence: `opencad-real-panel-catalog-expanded-20261012-v1`.
These are dirty-source development checkpoints, not a committed release,
whole-site language closure, production deployment or industrial parity.

From the chosen source tree, with a new evidence directory:

```bash
sudo -n -u infra python3 -B oss-alternatives/apps/opencad/tests/test_panel_catalog.py --out /hf3fs-jd/data/tmp/opencad-panel-catalog-next-20261012-v1
```

Full discovery automatically runs the tests using its external
`OPENCAD_EVIDENCE_ROOT`. Missing consumer/test dependencies fail loudly.
After consumer integration, add their versioned keymap contracts to the same
bidirectional checks; a current static list must not be sold as whole-site
coverage. No production, credential, permissions or remote ref changes are
part of this catalog revision. Recovery is a named forward revert, preserving
all external evidence and other owners' worktrees.
