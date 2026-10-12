# Parametric Panel Locale

## Contract And Ownership

Base: eb19211ca7ca4b9304678fe747026009234d1515. This seat owns only
`web/js/parametric-panel.js`, `tests/test_parametric_panel_locale.py` and this
document under `oss-alternatives/apps/opencad/`. The shared locale service and
`panel-i18n.json` belong to Fermat. No dictionary is embedded in the product JS.

The approved service is `window.OpenCADLocale`: `ready`, `snapshot()`,
`update({locale, strings})`, `t(key, args)` and `bind(root)`. The panel never
calls `update`. It waits for readiness and listens to `opencad:locale`.
Static leaf text uses `data-panel-i18n`; attribute bindings use
`data-panel-i18n-aria/placeholder/title/alt`. Parameterized messages use `t`
with the exact named arguments below, without inventing a shared argument
attribute. Translation does not render, fetch, retry, commit or refresh.
It preserves node identity, drafts, selected values, dirty/busy state,
receipts and the existing permanent/temporary shield policy.

## Dictionary Request

Every suffix below is prefixed with `panel.parametric.`. Each locale must
contain every key and exactly the same named placeholders. User configuration
names, expressions, UUIDs, numeric witnesses and original error code/details
are data, not translation keys. This table is a request for the shared
dictionary, not a second runtime dictionary.

| Key Suffix | zh-CN | en | ja |
| --- | --- | --- | --- |
| panel_aria | 参数化重建 | Parametric rebuild | パラメトリック再構築 |
| title | 参数化配置 | Parametric configurations | パラメトリック構成 |
| loading | 正在读取项目参数… | Reading project parameters… | プロジェクトのパラメーターを読み込み中… |
| configuration | 配置 | Configuration | 構成 |
| configuration_aria | 参数配置 | Parameter configuration | パラメーター構成 |
| base | 基础配置 | Base configuration | 基本構成 |
| expressions | 参数表达式 | Parameter expressions | パラメーター式 |
| dimensions | 驱动尺寸 | Driving dimensions | 駆動寸法 |
| expression_aria | {name} 表达式 | {name} expression | {name} の式 |
| dimension_aria | {name} 驱动尺寸 | {name} driving dimension | {name} の駆動寸法 |
| feature_suppression | 特征抑制 | Feature suppression | フィーチャー抑制 |
| v1_preview | 此模型为 v1，仅支持尺寸配置。 | This v1 model supports dimension configurations only. | この v1 モデルは寸法構成のみ対応しています。 |
| hole_cut | 孔切除 | Hole cut | 穴の切り取り |
| plate_boss | 板实体 | Plate solid | プレートソリッド |
| suppressed | 已抑制 | Suppressed | 抑制済み |
| live | 参与重建 | Included in rebuild | 再構築に含める |
| preview_help | 显示所选配置的抑制定义；完整重建后才改变已保存的实体。 | Shows the selected suppression definition; saved solids change only after a full rebuild. | 選択した抑制定義を表示します。保存済みソリッドは完全再構築後にのみ変更されます。 |
| rebuild | 完整重建并保存 | Rebuild fully and save | 完全再構築して保存 |
| reset_overrides | 清除显式覆盖草稿 | Clear explicit override drafts | 明示的な上書きの下書きを消去 |
| discard_reload | 丢弃草稿并重读 | Discard drafts and reread | 下書きを破棄して再読み込み |
| discard_aria | 确认丢弃本面板草稿 | Confirm discarding this panel's drafts | このパネルの下書きの破棄を確認 |
| confirm_discard | 确认丢弃草稿 | Confirm discarding drafts | 下書きの破棄を確認 |
| keep_drafts | 保留草稿 | Keep drafts | 下書きを保持 |
| discard_help | 只丢弃本面板未提交草稿，不删除已保存配置或图纸。 | Discards only this panel's unsubmitted drafts, not saved configurations or drawings. | このパネルの未送信の下書きのみ破棄し、保存済み構成や図面は削除しません。 |
| create_v2 | 创建真实配置板 | Create a configurable plate | 構成可能なプレートを作成 |
| create_v1 | 创建真实带孔板（v1） | Create a plate with a hole (v1) | 穴付きプレートを作成（v1） |
| reload | 重读已保存参数 | Reread saved parameters | 保存済みパラメーターを再読み込み |
| save | 确认保存当前模型 | Confirm saving the current model | 現在のモデルの保存を確認 |
| definition_aria | 配置定义编辑器 | Configuration definition editor | 構成定義エディター |
| definition_title | 配置定义 | Configuration definitions | 構成定義 |
| definition_target_aria | 编辑配置定义 | Edit configuration definition | 構成定義を編集 |
| definition_name_aria | 新配置名称 | New configuration name | 新しい構成名 |
| definition_new | 新建配置草稿 | New configuration draft | 新しい構成の下書き |
| selection_aria | 提交后的活动配置 | Active configuration after submission | 送信後のアクティブな構成 |
| selection_keep | 保留当前选择及 overrides | Keep current selection and overrides | 現在の選択と上書きを保持 |
| selection_target | 应用编辑目标并清除 overrides | Apply the edited target and clear overrides | 編集対象を適用し上書きを消去 |
| selection_base | 回基础并清除 overrides | Return to base and clear overrides | 基本構成に戻り上書きを消去 |
| definition_parameters | 配置参数定义 | Configuration parameter definitions | 構成のパラメーター定義 |
| explicit_aria | {name} 显式定义 | {name} explicit definition | {name} の明示的な定義 |
| definition_expression_aria | {name} 配置表达式 | {name} configuration expression | {name} の構成式 |
| explicit_help | 显式定义（未勾选继承基础） | Explicit definition (unchecked inherits base) | 明示的な定義（未選択の場合は基本構成を継承） |
| definition_suppressions | 配置抑制定义 | Configuration suppression definitions | 構成の抑制定義 |
| suppression_aria | {name} 抑制定义 | {name} suppression definition | {name} の抑制定義 |
| inherit_live | 继承基础（参与） | Inherit base (included) | 基本構成を継承（含める） |
| inherit_suppressed | 继承基础（抑制） | Inherit base (suppressed) | 基本構成を継承（抑制） |
| suppress | 抑制 | Suppress | 抑制 |
| v1_definition | v1 不支持抑制定义，不会自动升级。 | v1 does not support suppression definitions and will not be upgraded automatically. | v1 は抑制定義に対応しておらず、自動的に更新されません。 |
| create | 创建配置 | Create configuration | 構成を作成 |
| replace | 完整替换定义 | Replace the entire definition | 定義全体を置換 |
| save_suppressions | 仅保存抑制定义 | Save suppression definitions only | 抑制定義のみ保存 |
| delete | 删除已保存配置 | Delete saved configuration | 保存済み構成を削除 |
| delete_aria | 确认删除配置 | Confirm deleting configuration | 構成の削除を確認 |
| replacement_aria | 删除后的替代配置 | Replacement configuration after deletion | 削除後の代替構成 |
| confirm_delete | 确认删除配置 | Confirm deleting configuration | 構成の削除を確認 |
| cancel_delete | 取消删除 | Cancel deletion | 削除をキャンセル |
| delete_help | 删除活动配置必须明确选择替代或基础；此操作不会删除图纸。 | Deleting the active configuration requires an explicit replacement or base selection; drawings are not deleted. | アクティブな構成の削除には代替構成または基本構成の明示的な選択が必要です。図面は削除されません。 |
| discard_definition | 丢弃定义草稿 | Discard definition draft | 定義の下書きを破棄 |
| replacement_unselected | 请选择；非活动配置可保留当前选择 | Select a replacement; an inactive configuration may keep the current selection | 代替構成を選択してください。非アクティブな構成では現在の選択を保持できます |
| replacement_base | 基础配置（清除 overrides） | Base configuration (clear overrides) | 基本構成（上書きを消去） |
| replacement_named | {name}（清除 overrides） | {name} (clear overrides) | {name}（上書きを消去） |
| definition_create_help | 新配置初始继承基础。定义与当前模型的覆盖草稿独立。 | A new configuration initially inherits base. Its definition is independent of model override drafts. | 新しい構成は最初に基本構成を継承します。定義はモデルの上書きの下書きとは独立しています。 |
| definition_replace_help | 完整替换会替换全部参数和抑制定义，不做浅合并。 | Full replacement replaces every parameter and suppression definition, without a shallow merge. | 完全置換はすべてのパラメーターと抑制定義を置換し、浅いマージは行いません。 |
| definition_switch_blocked | 请先保存或明确丢弃定义草稿，再切换编辑目标。 | Save or explicitly discard the definition draft before switching targets. | 編集対象を切り替える前に定義の下書きを保存するか明示的に破棄してください。 |
| definition_dirty | 请先保存或明确丢弃定义草稿。 | Save or explicitly discard the definition draft first. | 先に定義の下書きを保存するか明示的に破棄してください。 |
| definition_delete_dirty | 请先保存或明确丢弃定义草稿，删除不会提交草稿。 | Save or explicitly discard the definition draft first; deletion does not submit drafts. | 先に定義の下書きを保存するか明示的に破棄してください。削除では下書きは送信されません。 |
| reset_ready | 已准备清除显式覆盖；完整重建成功后才持久保存。 | Override clearing is prepared; it is persisted only after a successful full rebuild. | 明示的な上書きの消去を準備しました。完全再構築の成功後にのみ永続保存されます。 |
| preserved | 已核对同一保存版本，未提交草稿仍保留。 | The saved version is verified; unsubmitted drafts are retained. | 保存バージョンを確認しました。未送信の下書きは保持されています。 |
| enabled_help | 表达式需显式单位，如 60 * mm。切换配置清除旧覆盖；完整重建才保存。 | Expressions require explicit units, such as 60 * mm. Switching clears old overrides; only a full rebuild saves. | 式には 60 * mm のような明示的な単位が必要です。構成の切り替えで古い上書きを消去し、完全再構築でのみ保存します。 |
| blank_help | 仅空白项目可创建配置板，不会替换已有图纸。 | A configurable plate can be created only in a blank project; existing drawings are not replaced. | 構成可能なプレートは空のプロジェクトでのみ作成でき、既存の図面は置換しません。 |
| other_draft_stale | 已保存版本改变了另一份草稿的依据。草稿保留，请明确丢弃对应草稿后重读。 | The saved version changed another draft's base. The draft is retained; explicitly discard it before rereading. | 保存バージョンが別の下書きの基準を変更しました。下書きは保持されています。該当する下書きを明示的に破棄して再読み込みしてください。 |
| draft_stale | 保存版本已变化。草稿仍保留，不能直接提交；请检查后明确丢弃草稿并重读。 | The saved version changed. Drafts are retained and cannot be submitted directly; inspect, explicitly discard, and reread. | 保存バージョンが変更されました。下書きは保持されていますが直接送信できません。確認後、明示的に破棄して再読み込みしてください。 |
| identity_changed | 账号已变化，已清除本页私有参数。请重新打开项目。 | The account changed; this page's private parameters were cleared. Reopen the project. | アカウントが変更されたため、このページの非公開パラメーターを消去しました。プロジェクトを開き直してください。 |
| project_changed | 项目已变化，已清除旧项目参数。请重新打开项目。 | The project changed; old project parameters were cleared. Reopen the project. | プロジェクトが変更されたため、以前のパラメーターを消去しました。プロジェクトを開き直してください。 |
| session_expired | 登录已失效，已清除本页私有参数。请重新登录。 | The session expired; this page's private parameters were cleared. Sign in again. | セッションが失効したため、このページの非公開パラメーターを消去しました。再度ログインしてください。 |
| read_unverified | 参数未能验证，草稿保留但暂停编辑。请核对保存结果后重读：{message} | Parameters could not be verified. Drafts are retained but editing is paused; check the save result and reread: {message} | パラメーターを確認できませんでした。下書きは保持していますが編集を停止しています。保存結果を確認して再読み込みしてください：{message} |
| saved_unverified | 编辑已保存，但视图未确认。不会再次提交；请核对保存结果后重读。 {message} | The edit was saved but the view is unverified. It will not be resubmitted; check the save result and reread. {message} | 編集は保存されましたが表示を確認できません。再送信はしません。保存結果を確認して再読み込みしてください。{message} |
| edit_unverified | 编辑未确认或被拒绝，草稿仍保留；请核对保存结果后重读。 {message} | The edit is unconfirmed or rejected. Drafts are retained; check the save result and reread. {message} | 編集は未確認または拒否されました。下書きは保持しています。保存結果を確認して再読み込みしてください。{message} |
| saved_other_draft | 编辑已保存，另一份草稿仍保留但依据已变化。请检查并明确丢弃对应草稿。 | The edit was saved; another draft is retained but its base changed. Inspect and explicitly discard that draft. | 編集は保存されました。別の下書きは保持されていますが基準が変更されました。確認して該当する下書きを明示的に破棄してください。 |
| artifacts_verified | 真实 BRep、网格和 STEP 已核对同一保存版本。 | BRep, mesh and STEP are verified against the same saved version. | BRep、メッシュ、STEP が同じ保存バージョンと一致することを確認しました。 |
| rebuild_result | 重建已保存 \| 体积 {volume} mm³ \| STEP {bytes} bytes \| {sha} | Rebuild saved \| Volume {volume} mm³ \| STEP {bytes} bytes \| {sha} | 再構築を保存済み \| 体積 {volume} mm³ \| STEP {bytes} bytes \| {sha} |
| model_saved | 当前已保存模型已确认；未提交的配置草稿仍保留。 | The saved model is confirmed; unsubmitted configuration drafts are retained. | 保存済みモデルを確認しました。未送信の構成の下書きは保持されています。 |
| error | 参数操作失败：{message} | Parameter operation failed: {message} | パラメーター操作に失敗しました：{message} |
| definition_unsaved_error | 参数定义草稿未保存，请使用完整替换，不能仅保存抑制。详情：{message} | The parameter definition draft is unsaved. Replace the full definition instead of saving suppression alone. Details: {message} | パラメーター定義の下書きが未保存です。抑制のみ保存せず、定義全体を置換してください。詳細：{message} |
| replacement_required_error | 删除活动配置必须明确选择替代配置或基础。详情：{message} | Deleting the active configuration requires an explicit replacement or base selection. Details: {message} | アクティブな構成の削除には代替構成または基本構成の明示的な選択が必要です。詳細：{message} |
| no_configuration_error | 没有可删除的配置。详情：{message} | No configuration is available to delete. Details: {message} | 削除できる構成がありません。詳細：{message} |

## Acceptance Boundary

The new owned CLI executes the complete panel JS in Node/vm with a strict typed
DOM and an approved-contract locale double. Unknown selectors, keys and
placeholders fail loudly. It records the real child exit, logs and product/
instrumented/runner hashes, source before/after, the chronological first cause
and terminal counts. Before-red is from the frozen base product, not a copied
old browser result. This does not certify the shared service, the real browser,
every panel, production deployment, or GPU performance.

Locale arguments live in a panel-owned WeakMap, not DOM attributes or storage.
Text binding is only attached to leaf nodes; the configuration label retains
its select as a sibling of the translated span. `shieldKey` first invokes the
original shield, then translates its notice. Missing shield translations
therefore fail loudly after private inputs and actions have been cleared.
Cleared result/definition notices lose both text and their bindings, so a
later locale event cannot resurrect stale volume, hash or error details.
The error's original `message`, `code`, `detail` and HTTP `status` are retained;
the translated error hint interpolates that unchanged message as data.

The shared service is not present in base eb19211. Integration must install
Fermat's service and complete dictionary before booting this panel. The existing
CRUD `private_clear_probe` is a separate owned fixture without this service or
the new DOM attribute methods. Its owner must integrate a strict unified double
before rerunning full discovery; do not remove its negative controls or treat
the old 18-workflow browser result as acceptance of the new source.

## New Evidence And Reproduction

Actual before-red used the exact committed eb19211 JS:
`/hf3fs-jd/data/tmp/opencad-parametric-panel-locale-before-20261012-v1/result.json`.
The parent returned 1, its real Node child returned 1, and the first cause was
`locale_title_untranslated`, with zero source drift. A preceding path preflight
returned 2 before creating evidence: the authorized 3FS alias resolved to
`/3fs/research3fs/data/tmp`; the test now resolves both the supplied path and
allowed root, without broadening the allowed directory.

The v2 development controls retained 22/23 and original rc 1:
`opencad-parametric-panel-locale-controls-20261012-v2`. Its first cause was
`locale_replaced_document_or_receipt_binding`: the create-mode fixture took
references before its own setup render. References now precede only the
tested locale switch. No product assertion was relaxed. Development v4 ran
27/27 with original parent 70981 rc 0 and zero drift; it remains dirty-source
evidence, not a claim that the resulting commit was already tested.

Run from the chosen frozen integration with fresh directories:

```bash
sudo -n -u infra python3 -B \
  /data/dev/wt-opencad-parametric-panel-locale-20261012/oss-alternatives/apps/opencad/tests/test_parametric_panel_locale.py \
  --selftest --require-clean \
  --out /hf3fs-jd/data/tmp/opencad-parametric-panel-locale-committed-controls-20261012-v1 \
  --runtime-root /data/tmp/oc-ppl-cc-1012-v1
sudo -n -u infra python3 -B \
  /data/dev/wt-opencad-parametric-panel-locale-20261012/oss-alternatives/apps/opencad/tests/test_parametric_panel_locale.py \
  --discovery --require-clean \
  --out /hf3fs-jd/data/tmp/opencad-parametric-panel-locale-committed-discovery-20261012-v1 \
  --runtime-root /data/tmp/oc-ppl-cd-1012-v1
```

These committed runs must be received after the commit; the paths above are
planned reproduction entries, not predeclared PASS. The CLI records complete
tracked/untracked source bytes/modes and HEAD/status before/after, all real Node
argv/exit/log bindings, a hashed result/log completion marker, chronological
first/secondary failures and null unpublished hashes. All fixtures are local
and retained. No remote, production, GPU, credential or permission operation
is performed; new live browser/shared-service acceptance remains separate.

## Chair Resume 2026-10-12: lifecycle and observable boot

The next session resumed this paused seat under the existing user authorization
and kept the original failed evidence. The product now owns one live locale
binding at a time: before every rebind it calls the previous binding's release,
then binds and immediately repaints the panel-owned dynamic bindings. A failed
rebind releases the new token if one was already returned and leaves no token
behind for the next event. Panel drafts, node identity, focus, revision/SHA and
shield state are not changed by that release/bind path.

Boot is now an observable promise exposed as `window.opencadParametric.ready`.
The rejection is the original error object (ready rejection, service getter
failure or mount failure all retain identity); the consumer receives it and
there is no unhandled rejection. Any such failure creates at most one body
direct public alert with the fixed text
`parametric_locale_initialization_failed`. It contains no original message,
code, detail, status, project data or user value. A later boot clears that same
alert on success and mounts once. The alert uses the chair's strict public
locale diagnostic shape: one `output[role=alert][lang=en]` with exactly the
`data-opencad-public-locale-error="parametric"` marker, zero element children,
no hidden/style, and no other attributes. The session shield may exempt only
this fully checked shape; `role=alert` alone is never an exemption.

The first resumed full run deliberately remains recorded as FAIL:
`/hf3fs-jd/data/tmp/opencad-parametric-locale-chair-fix-20261012-v1`
had 36 tests / 31 passed / parent rc 1. Four failures were the test probe
reading uninitialised mount fields from `api.state()` after a boot failure, and
one was a test accounting assertion counting failed bind attempts as live
tokens. Neither failure was hidden. The probe now returns null drafts before
mount; the accounting check now requires exactly one live token and compares
actual successful token events. The v2 run is retained as a second FAIL at
35/36 with the narrowed first cause. The v3 run returned parent rc 0:

```
out=/hf3fs-jd/data/tmp/opencad-parametric-locale-chair-fix-20261012-v3
runtime=/data/tmp/oc-param-chair-fix-20261012-v3
OPENCAD_PARAMETRIC_PANEL_LOCALE verdict=PASS tests=36 passed=36 actual_rc=0
```

The real Node children, logs, argv, product/instrumented/runner hashes and
source before/after are retained there. This is still synthetic Node/locale
evidence: it does not by itself prove the actual shared catalog, the real
private session shield, a browser, or production. Integration must rerun this
suite against the integrated driver and perform a separate real-catalog key/arg
audit and browser acceptance. The three owned paths remain the product JS, this
suite and this document.
