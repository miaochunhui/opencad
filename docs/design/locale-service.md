# Locale Service Foundation

## Owned Source And Boundary

The foundation starts from `eb19211ca7ca4b9304678fe747026009234d1515`
in `/data/dev/wt-opencad-locale-foundation-20261012`. Its exclusive six files
are `web/js/locale-service.js`, `web/panel-i18n.json`, `web/index.html`,
`web/js/app.js` under `oss-alternatives/apps/opencad/`, the sibling
`tests/test_locale_service.py`, and this document.

The chair owns private-page asset routing and `private-locale.js`; separate
seats own parameter and session consumers. Only the foundation seat edits
`panel-i18n.json`. The existing session epoch, authentication, project, CAS,
GET/export and draft gates are unchanged. Surface, assembly, drawing and
render panel translation are separate future scopes. Workbench headings
do not establish whole-site translation or industrial parity.

## Public Contract

`window.OpenCADLocale` exposes exactly `ready`, `snapshot()`,
`update({locale, strings})`, `t(key, args)` and `bind(root)`.

`ready` is a Promise loading the same-origin public dictionary relative to
the service script URL. Invalid, empty, duplicate-key, mismatched key or
placeholder sets fail loudly, create an independent role alert and reject.
No credentials or server-selected project data are needed to load it.

`snapshot()` returns a frozen object:

```javascript
{
  schema: "opencad.panel-i18n.v1",
  status: "loading", // ready, unsupported, error
  locale: null,
  generation: 0,
  bound: false,
  available_locales: [],
  supported_locales: [],
  unsupported: [],
  error: null // or {code}
}
```

After a successful `ready`, the explicitly agreed public default is `en`;
`t` and `bind` work without a state event, but `bound` remains false.
Only an explicit accepted update changes `bound` to true. This flag means
language response binding, not independently verified user identity.
Available languages are read dynamically from actual `#lang-select` and
`#locale` options; supported languages are dictionary keys. There is no
three-language allowlist. Missing translations remain visibly unsupported,
and `t` rejects rather than silently returning English. The dictionary's
present locale set mirrors the repository catalog; adding a complete locale
requires dictionary data, not a service branch.

`update` accepts exactly a locale and a nonempty string map, copies/freezes
the strings and increments `generation`. An update arriving before package
loading is retained; `ready` does not overwrite it with the default. Accepted
state events must carry `ok:true`, `locale` and `strings`. Only these language
fields are extracted. Callers retain responsibility for their authentication,
epoch, project and late-response acceptance gates. DOM events are not a
security boundary and the service does not infer locale from users, projects,
document content or locale controls' selected value.

The 2D app calls `OpenCADLocale.update` only after a successful response has
been applied/rendered; a locale POST notifies after the accepted rendering.
It passes only locale and strings, no document, project, owner, receipt or user.
It does not introduce a stripped `opencad:state` event which older consumers
could mistake for a project change.
The private 3D viewer already publishes accepted state after its refresh gate.
Updates dispatch `opencad:locale` containing only the snapshot shape above.

`t` resolves `panel.*` only from the unique panel dictionary; other keys
resolve from the accepted server string map. Keys and named placeholders
are strict. Required and supplied argument sets must match exactly. Argument
values may be strings, finite numbers or booleans; objects, arrays, missing
arguments and extras fail. No key or argument fallback is provided.

`bind(root)` immediately applies the current locale and returns an idempotent
unsubscribe function. Multiple bindings of one root are independently counted.
Binding does not emit `opencad:locale`, avoiding reentrant locale consumers.
It changes only text leaf nodes and explicit attributes:

- `data-panel-i18n` changes `textContent`, never `innerHTML`.
- `data-panel-i18n-aria` changes `aria-label`.
- `data-panel-i18n-placeholder` changes `placeholder`.
- `data-panel-i18n-title` changes `title`.
- `data-panel-i18n-alt` changes `alt`.

Optional `data-panel-i18n-args` is a JSON object with named arguments. A text
binding on an element containing child elements, or on input/select/textarea,
script/style/canvas, is refused. Split form labels into an explicit text span;
do not bind the containing label. Every binding is validated before any
translation paint. Controls are never rebuilt and value, selection, focus,
disabled, hidden, inert, draft and project/CAD state are untouched.
Late-mounted nodes require a new `bind(root)`; the service deliberately does
not observe and recreate arbitrary panel content. Dynamic notices may call
`t` and maintain their own current key/arguments on `opencad:locale`.
The old `data-i18n*` app bindings never select `data-panel-i18n*`.

The chair's private consumer waits for DOM readiness and `ready`, then binds
`.industrial-workbench`. Its separate role alert/rethrow must not replace CAD
status. The service's own alert reports package/unsupported failures, not CAD
or session errors.

## Offline Acceptance

Use the committed new tree, not the frozen UI tree. All output directories
must be new paths below `/hf3fs-jd/data/tmp/`; no cleanup is performed.

```bash
sudo -n -u infra python3 -B \
  /data/dev/wt-opencad-locale-foundation-20261012/oss-alternatives/apps/opencad/tests/test_locale_service.py \
  --selftest \
  --source-root /data/dev/wt-opencad-locale-foundation-20261012 \
  --out /hf3fs-jd/data/tmp/opencad-locale-foundation-selftest-20261012-v1
```

The actual Node runtime executes the shipped service, not a copied service
oracle. A strict DOM fixture rejects unknown selectors and text writes which
would destroy child elements. It checks every shipped translation, the actual
catalog/selector collection, all five attributes, literals containing HTML,
late loading/binding, unsubscription, untouched draft/focus/shield controls,
unsupported/extended locale data, private-data-free events and the real app
bridge. Actual separate negative children must exit 1 with the exact causes
for missing keys/arguments, invalid argument/state/root and unsafe binding.
Package failure controls reject HTTP/load/malformed/empty/duplicate/key-set/
placeholder-set failures. These checks are not browser/HTTP/CAD measurements.

The unittest discovery entry requires the parent's `OPENCAD_EVIDENCE_ROOT`
and keeps its own Node script, child logs and ledger in that external gate.
The explicit CLI additionally binds the complete tracked file byte/mode set,
HEAD, clean status and physical source set, permitting only root `.git`
metadata and refusing nested/unknown/symlink artifacts. `--development`
is explicitly noncommitted development evidence and accepts only the six
owned changes relative to the base, never an integrated-release acceptance.
Normal clean source binding does not freeze integration to a repair allowlist.

Offline readback:

```bash
sudo -n -u infra python3 -B \
  /data/dev/wt-opencad-locale-foundation-20261012/oss-alternatives/apps/opencad/tests/test_locale_service.py \
  --verify /hf3fs-jd/data/tmp/opencad-locale-foundation-selftest-20261012-v1/result.json \
  --source-root /data/dev/wt-opencad-locale-foundation-20261012 \
  --out /hf3fs-jd/data/tmp/opencad-locale-foundation-readback-20261012-v1
```

The reader checks the exact child collection/argv/actual exits/terminal logs,
all artifact bytes/hashes and current complete source. The parent shell exit
must still be received independently; `actual_rc` in JSON is not a substitute.
New private-page real language-switch/browser acceptance follows integration
of the chair and panel consumers. Existing CRUD browser/all results are not
inherited as locale acceptance. Missing consumer key-map alignment remains
an integration dependency, not an excuse to make the foundation silently
fallback or to claim whole-site i18n is repaired.

`--controls <committed-selftest-result>` runs actual offline reader negative
CLIs on fresh copied evidence, retaining each child exit and first cause.
It rejects missing children/artifacts, false exits/counts/source SHA/modes,
foreign roots, corrupted or extra artifacts, and rehashed terminal/completion
logs. It never rewrites the original producer evidence.
