# Drawing Panel Locale

## Scope And Handoff

Base: `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`.
Worktree: `/data/dev/wt-opencad-drawing-locale-r2-20261012`.
Branch: `codex/drawing-locale-r2-20261012`.
Exclusive files: `oss-alternatives/apps/opencad/web/js/drawing-panel.js`,
`oss-alternatives/apps/opencad/tests/test_drawing_panel_locale.py`, and this
document. The chair is the only dictionary owner. No dictionary, other panel,
CAD backend, credential, production or remote ref is modified by this seat.

## Dictionary Owner Keymap

These are handoff specifications, not a runtime dictionary or fallback.
Prefix every suffix below with `panel.drawing.`. All rows require no arguments
except `binding.version` (`revision`, `sha`) and `status.downloaded` (`format`).
The three languages must have identical keys and exact named argument sets.

| Key Suffix | zh-CN | en | ja |
| --- | --- | --- | --- |
| heading | 关联工程图 | Associative drawing | 関連図面 |
| kicker | 模型与图纸 · 同源版本 | Model and drawing · Same source version | モデルと図面 · 同一ソース版 |
| scope | 真实实体投影与剖切。完整 GD&T / MBD 和 GB / ISO 符合性未验收。 | Real solid projections and sections. Full GD&T / MBD and GB / ISO compliance are not verified. | 実ソリッドの投影と断面。完全な GD&T / MBD と GB / ISO 適合性は未検証です。 |
| definition | 工程图定义 JSON | Drawing definition JSON | 図面定義 JSON |
| save | 保存定义 | Save definition | 定義を保存 |
| refresh | 刷新关联图 | Refresh associative drawing | 関連図面を更新 |
| preview_aria | 真实关联投影视图 | Real associative projection views | 実形状に関連する投影视図 |
| table_scroll_aria | 关联尺寸，可横向滚动 | Associative dimensions, scroll horizontally | 関連寸法、横方向にスクロール可能 |
| table_aria | 关联实测尺寸 | Associative measured dimensions | 関連する実測寸法 |
| dimension | 尺寸 | Dimension | 寸法 |
| measured_value | 实测值 | Measured value | 実測値 |
| model_reference | 模型引用 | Model reference | モデル参照 |
| download_svg | 下载 SVG | Download SVG | SVG をダウンロード |
| download_pdf | 下载 PDF | Download PDF | PDF をダウンロード |
| download_dxf | 下载 DXF | Download DXF | DXF をダウンロード |
| status.unread | 尚未读取 | Not read yet | 未読み込み |
| binding.undefined | 尚未保存工程图定义 | Drawing definition not saved yet | 図面定義はまだ保存されていません |
| status.define_first | 先保存投影、剖面与尺寸引用定义 | Save projection, section and dimension reference definitions first | まず投影、断面、寸法参照の定義を保存してください |
| binding.version | 项目版本 {revision} · 模型 SHA {sha} | Project revision {revision} · Model SHA {sha} | プロジェクト版 {revision} · モデル SHA {sha} |
| status.recomputed | 关联图已重算，视图、尺寸与模型版本一致 | Drawing recomputed; views, dimensions and model version agree | 関連図面を再計算しました。視図、寸法、モデル版が一致しています |
| binding.checking | 正在核对当前模型 | Checking the current model | 現在のモデルを確認中 |
| status.recomputing | 正在重算真实投影与剖面 | Recomputing real projections and sections | 実形状の投影と断面を再計算中 |
| binding.unavailable | 当前工程图不可用 | Current drawing unavailable | 現在の図面は利用できません |
| binding.save_unconfirmed | 保存结果尚未确认 | Save result not confirmed yet | 保存結果はまだ確認されていません |
| error.save_prefix | 定义未确认保存： | Definition save not confirmed: | 定義の保存を確認できません： |
| error.prefix | 工程图错误： | Drawing error: | 図面エラー： |
| error.request_unconfirmed | 工程图请求未被确认 | Drawing request not acknowledged | 図面リクエストが確認されていません |
| error.svg_parse | 工程图 SVG 无法解析 | Drawing SVG cannot be parsed | 図面 SVG を解析できません |
| error.svg_node | 工程图含不支持的 SVG 节点 | Drawing contains an unsupported SVG node | 図面に未対応の SVG ノードが含まれています |
| error.svg_attribute | 工程图含不安全的 SVG 属性 | Drawing contains an unsafe SVG attribute | 図面に安全でない SVG 属性が含まれています |
| error.binding | 工程图版本或模型绑定不完整 | Drawing version or model binding is incomplete | 図面版またはモデルの関連付けが不完全です |
| error.export_failed | 导出失败 | Export failed | エクスポートに失敗しました |
| binding.export_changed | 模型已变化，请刷新后导出 | Model changed; refresh before exporting | モデルが変更されました。更新してからエクスポートしてください |
| error.export_version | 导出与当前预览不同版本，已拒绝下载 | Export and preview versions differ; download refused | エクスポートと現在のプレビューの版が異なるため、ダウンロードを拒否しました |
| error.export_read_changed | 读取导出期间模型已变化，已拒绝下载 | Model changed while reading export; download refused | エクスポートの読み取り中にモデルが変更されたため、ダウンロードを拒否しました |
| error.export_empty | 导出文件为空 | Export file is empty | エクスポートファイルが空です |
| status.downloaded | 已下载同版本 {format} | Downloaded same-version {format} | 同一版の {format} をダウンロードしました |
| binding.model_changed | 模型已变化，等待重算当前版本 | Model changed; waiting to recompute current version | モデルが変更されました。現在の版の再計算を待機中 |

## State Boundary

Locale changes paint existing leaf text and explicit ARIA attributes only.
They must not mount, render geometry, refresh, request CAD data, modify editor
bytes or selection, change dirty/pending/generation/current/refreshQueued,
replace preview/table/control nodes, or relax export SHA/version checks.
Model titles, UUIDs, JSON reference definitions, raw backend error code/detail,
dimension IDs/references, SHA, format and standards/units remain literal.
The public locale dependency is the exact foundation `97b80e675f39e23faffcb8c0f6bb8706a8238f0b`;
its files remain read-only. No runtime fallback translation is introduced.

`mount` retains its existing controller and adds `ready`, a rejecting Promise
for locale binding and the first drawing read. Missing service rejects before
mount; package/binding failures retain a separate role alert and their original
code, not a translated CAD status. The chair must load the service before this
consumer. Legacy standalone drawing harnesses lacking the service need explicit
locale wiring by their owner; this seat does not alter those harnesses.

The viewer publishes `opencad:state` even after a language-only request.
Drawing compares complete accepted `[project_id, revision, sha256]` metadata
against its latest model binding. Identical language-bearing state does not
queue a drawing refresh, clear current geometry or cancel a download.
Incomplete metadata and actual project/revision/SHA changes retain the original
refresh, queued-generation and export rejection checks. This is presentation
deduplication, not a new authentication or CAS boundary.

## Offline Entry

Run actual source plus the chair's current actual catalog:

```bash
sudo -n -u infra -B python3 -B \
  oss-alternatives/apps/opencad/tests/test_drawing_panel_locale.py \
  --selftest --dictionary-development \
  --dictionary-root /data/dev/wt-opencad-panel-catalog-r2-20261012 \
  --out /hf3fs-jd/data/tmp/opencad-drawing-locale-r2-committed-selftest-20261012-v1
```

Each run requires a new external directory and never cleans old evidence.
For dirty owned source only, explicitly add `--development`; this is not
committed-source or integrated-release acceptance. `--dictionary-development`
allows only the chair's named catalog/test/document dirty paths; all committed
and physical files, bytes and executable modes remain bound before/after.
The service root defaults to the exact read-only foundation worktree.
The default catalog is that service's package; explicitly supply the chair's
catalog when its new drawing keys are needed.

`--verify <producer-result.json>` replaces `--selftest` to perform offline
readback. `--controls <producer-result.json>` runs actual failing reader CLIs
against new copied evidence for missing children, fake child exit/count/source,
foreign source roots, extra/corrupt artifacts, and rehashed terminal/completion
logs. Supply identical roots and development flags to reproduce the binding.
Reader evidence has its own fresh `--out`. The parent shell's observed original
exit must be received separately; JSON `actual_rc` cannot certify its parent.

The optional `--contract` uses these documented requests in memory only.
It never writes a catalog and is explicitly not actual-catalog acceptance.
Normal unittest discovery requires external `OPENCAD_EVIDENCE_ROOT`, with
`OPENCAD_LOCALE_FOUNDATION_ROOT` / `OPENCAD_LOCALE_DICTIONARY_ROOT` identifying
the actual dependencies. No Python bytecode, source-tree test data or server
database is produced.

## Acceptance Status

New external evidence roots, all under `/hf3fs-jd/data/tmp/`:

- `opencad-drawing-locale-r2-20261012-before-red-v1`: actual base source rejects
  the newly required locale-readiness assertion, original parent exit 1.
- `opencad-drawing-locale-r2-20261012-fix-green-v1`: fixture sent an incomplete
  state notification, correctly rejected as `locale_unaccepted_state`; original
  parent exit 1 retained. The product boundary was not loosened.
- `opencad-drawing-locale-r2-20261012-state-before-red-v1`: 230 checks reached,
  same-model language state wrongly triggered a second `/api/drawing`; original
  parent exit 1, source/log snapshots retained.
- `opencad-drawing-locale-r2-20261012-state-fix-green-v1`: protocol-only PASS,
  1190 checks / 14 scenarios / 4 negative children, original parent exit 0.
- `opencad-drawing-locale-r2-20261012-real-catalog-v2`: actual service/catalog
  PASS, 1309 checks / 14 scenarios / 4 negative children, original parent exit 0.
- `opencad-drawing-locale-r2-20261012-readback-v2` and
  `opencad-drawing-locale-r2-20261012-reader-controls-v2`: original parent exit 1,
  `current_source_drift`, because the chair's unfrozen full source changed.
  These are not reclassified as accepted reader runs.

These counts are Node assertions, not independent browser/unit test counts.
They cover unchanged controls/selection/drafts/raw values, actual render and
held save/refresh/download callbacks, same-head state locale notifications,
newer queued model winning, raw parser/backend detail, SVG allowlist rejection,
version/empty-binding checks, late readiness, destroy and strict missing/extra
key/argument failures. Full committed-source rerun/readback belongs to its own
new evidence snapshot; earlier dirty-source evidence is not promoted.
No browser, private HTTP, CAD kernel, production or industrial full-function
acceptance is claimed by this locale seat.

## Paused Handoff, 2026-10-12 14:01 +08:00

Status: PAUSED by explicit user request, not DONE. Stop development, new test
dispatch, production actions and commits for this session. Preserve all files,
resources and original evidence. No new agents or Flash workers were started.
Every test launched by this seat has returned its actual terminal exit.

Original task: localize the associative drawing panel with the shared service,
preserving drafts, definitions, controls, selection, views/dimensions, revision,
SHA, CAS and download lifetime across language switches.
Worktree and branch remain the Scope And Handoff values above.
HEAD is still the original base `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`;
this seat has made no commit, staged no paths, and made no push or production
change. Current exclusive dirty set is exactly:

- Modified `oss-alternatives/apps/opencad/web/js/drawing-panel.js`: shared
  leaf/ARIA bindings, localized status/error prefixes, literal raw details,
  readiness/destroy handling and same-private-head locale-state deduplication.
- New `oss-alternatives/apps/opencad/tests/test_drawing_panel_locale.py`: real
  Node panel/service execution, full source/artifact closure, strict argument
  children and offline reader/control entry points.
- New `docs/design/drawing-panel-locale.md`: 38-key three-language handoff,
  named placeholder contract, scope, evidence and this pause record.

The actual dictionary is now the chair's clean local DCO
`373fe47a041cc33a7ba92e1d7f1257483a69964b` in
`/data/dev/wt-opencad-panel-catalog-r2-20261012`.
Read service source from `97b80e675f39e23faffcb8c0f6bb8706a8238f0b` in
`/data/dev/wt-opencad-locale-foundation-20261012`, but do not use its old package
as the drawing dictionary. No new keys are pending beyond the 38-row table.

Latest actual test:
`/hf3fs-jd/data/tmp/opencad-drawing-locale-r2-20261012-frozen-catalog-v1/`.
It finished at `2026-10-12T05:58:32.108312+00:00` (13:58:32 +08:00),
original parent exit 0, PASS, `contract=False`, 1309 Node assertions,
14 scenarios, 4 actual rejecting child controls (each original exit 1).
`result.json`, `completion.log`, `drawing-fixture.cjs` and every child's
`*.stdout.log` / `*.stderr.log` remain there. Full source_before/source_after
were equal; root set SHA was
`26f4eabea5fec3ec89e1ab7a8248f5cb493993f4468aa3b63960dc24a1504a9d`.
This handoff is a later documentation edit and therefore requires a fresh
source-binding run; do not accept that older fingerprint as current source.

Unfinished: no successful frozen-catalog offline readback or nine-reader
tamper-control batch yet; no committed-source rerun; no named three-file DCO
commit. Earlier reader runs failed loudly on unfrozen chair source drift and
remain FAIL. Full browser/HTTP/CAD/industrial and final integration acceptance
are not measured here. Legacy standalone drawing harnesses without the locale
service remain an integration dependency for their owner, not bypassed here.

Next session must reread applicable rules, this handoff and current Git state.
The next actual test command, not launched by this paused session, is:

```bash
sudo -n -u infra -B python3 -B \
  /data/dev/wt-opencad-drawing-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_drawing_panel_locale.py \
  --selftest --development \
  --source-root /data/dev/wt-opencad-drawing-locale-r2-20261012 \
  --dictionary-root /data/dev/wt-opencad-panel-catalog-r2-20261012 \
  --out /hf3fs-jd/data/tmp/opencad-drawing-locale-r2-20261012-resume-source-v1
```

That output must be new; never clear an existing directory to reuse the name.
After receiving the real parent exit, use the same roots and `--development`
with `--verify` and `--controls` on its result and separate fresh outputs.
Only after review and completed verification, and after the pause is lifted,
stage/commit the three named owned paths with DCO in this independent tree.
Never commit to the shared main tree, push, change credentials or operate
production without the chair's separate explicit scope.

## Chair Resume 2026-10-12

The continuation session resumed the paused seat and completed the missing
offline chain with the actual chair catalog. The received parent exits were:

```text
producer  /hf3fs-jd/data/tmp/opencad-drawing-locale-chair-fix-20261012-v1
          rc=0, checks=1309, scenarios=14, negative_children=4
readback  /hf3fs-jd/data/tmp/opencad-drawing-locale-chair-verify-20261012-v1
          rc=0, checks=1309, scenarios=14
controls  /hf3fs-jd/data/tmp/opencad-drawing-locale-chair-controls-20261012-v1
          rc=0, nine actual rejecting reader CLIs
```

The earlier reader failure caused by an unfrozen chair source remains a FAIL
and is not promoted. The current evidence executes the shipped panel against
the actual clean catalog, verifies its exact 38-key namespace, raw details and
same-model state deduplication. The three owned paths are ready for their DCO
commit; after that commit the same commands must be repeated without
`--development` for clean acceptance. This remains synthetic Node evidence,
not a browser, private HTTP, CAD kernel, industrial-parity or production
claim.
