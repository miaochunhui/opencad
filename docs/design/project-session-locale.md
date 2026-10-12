# Project Session Locale R2

## Ownership And Dictionary Handoff

Base: `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`.
Worktree: `/data/dev/wt-opencad-session-locale-r2-20261012`.
Branch: `codex/session-locale-r2-20261012`.
Only this document, `oss-alternatives/apps/opencad/web/js/project-session.js`
and `oss-alternatives/apps/opencad/tests/test_project_session_locale.py` belong
to this seat. The old dirty tree is read-only other-owner property.
The chair is the sole production dictionary owner. This table is a handoff,
not another executable dictionary.

Read-only foundation: `97b80e675f39e23faffcb8c0f6bb8706a8238f0b`.
API: `OpenCADLocale.ready`, `snapshot()`, `update({locale, strings})`,
`t(key, args)`, `bind(root)`; event `opencad:locale`.
Bindings: `data-panel-i18n`, `data-panel-i18n-aria`,
`data-panel-i18n-placeholder`, `data-panel-i18n-title`,
`data-panel-i18n-alt`; named arguments use `data-panel-i18n-args`.

All suffixes below have prefix `panel.session.`. Unlisted arguments mean `{}`.
Named argument sets: `view_unverified={detail}`, `logout_unconfirmed={detail}`,
`locale_unavailable={code}`. Every locale must have the same exact argument set.
`detail` is the original exception/server detail as plain text, not a
translation key; the adapter does not translate or rewrite it. Account,
password and project-name input values, and project titles, are user data.
No credential values are logged or included in locale events.

| Key Suffix | zh-CN | en | ja |
| --- | --- | --- | --- |
| heading | OpenCAD 私有项目 | OpenCAD private projects | OpenCAD プライベートプロジェクト |
| explanation | 登录后选择项目。每次编辑会保存为持久版本，其他用户无法访问你的图纸。 | Sign in and choose a project. Each edit is saved as a persistent version. Other users cannot access your drawings. | ログインしてプロジェクトを選択してください。編集は永続的なバージョンとして保存され、他のユーザーは図面にアクセスできません。 |
| account | 账号 | Account | アカウント |
| password_label | 密码 | Password | パスワード |
| login | 登录 | Sign in | ログイン |
| logout | 退出登录 | Sign out | ログアウト |
| project | 项目 | Project | プロジェクト |
| open_project | 打开项目 | Open project | プロジェクトを開く |
| new_project_name | 新项目名称 | New project name | 新しいプロジェクト名 |
| create_project | 创建项目 | Create project | プロジェクトを作成 |
| project_name_required | 请输入项目名称。 | Enter a project name. | プロジェクト名を入力してください。 |
| request_failed | 请求失败，请检查连接和账号状态。 | Request failed. Check your connection and account. | リクエストに失敗しました。接続とアカウントを確認してください。 |
| project_unavailable | 项目不可用，请检查当前账号和项目。 | Project unavailable. Check the current account and project. | プロジェクトを利用できません。現在のアカウントとプロジェクトを確認してください。 |
| confirm_edit | 确认上一笔编辑 | Confirm previous edit | 直前の編集結果を確認 |
| refresh | 刷新最新版本 | Refresh latest version | 最新バージョンを更新 |
| login_again | 重新登录 | Sign in again | 再ログイン |
| recovery_heading | 恢复原账号登录 | Restore the original account | 元のアカウントで再ログイン |
| recovery_explanation | 未确认的编辑保留原请求编号。登录成功后，请手动确认上一笔编辑，不会自动重复保存。 | The unconfirmed edit retains its original request ID. After signing in, confirm it manually. It will not be saved again automatically. | 未確認の編集は元のリクエスト ID を保持します。ログイン後に手動で結果を確認してください。自動で再保存されることはありません。 |
| original_account | 原账号 | Original account | 元のアカウント |
| restore_login | 恢复登录 | Restore sign-in | ログインを復元 |
| later_login | 保留页面，稍后登录 | Keep this page; sign in later | このページを保持して後でログイン |
| wrong_account | 账号与当前项目不一致。请以原账号登录，原编辑仍保留。 | This account does not match the current project. Sign in with the original account. The edit is retained. | このアカウントは現在のプロジェクトと一致しません。元のアカウントでログインしてください。編集は保持されています。 |
| expired | 登录已失效，请重新登录。 | Your session expired. Sign in again. | セッションの有効期限が切れました。再ログインしてください。 |
| expired_pending | 登录已失效，请以原账号重新登录，再确认上一笔编辑。原请求仍保留在此页面。 | Your session expired. Sign in with the original account, then confirm the previous edit. Its request is retained on this page. | セッションの有効期限が切れました。元のアカウントでログインし、直前の編集結果を確認してください。元のリクエストはこのページに保持されています。 |
| view_checking | 编辑已确认保存，正在核对最新视图。未提交的表单保留在此页。 | The edit is confirmed saved. Checking the latest view. Unsubmitted forms remain on this page. | 編集の保存を確認しました。最新の表示を確認中です。未送信のフォームはこのページに保持されています。 |
| view_verified | 编辑已确认保存，最新视图已核对。未提交表单仍保留，请检查后再保存。 | The edit is confirmed saved and the latest view is verified. Unsubmitted forms remain; review them before saving. | 編集の保存と最新の表示を確認しました。未送信のフォームは保持されています。確認してから保存してください。 |
| view_unverified | 编辑已确认保存，但视图未验证：{detail}。请核对最新视图，不会再次提交原请求。 | The edit is confirmed saved, but the view is not verified: {detail}. Check the latest view. The original request will not be submitted again. | 編集の保存は確認しましたが、表示は未確認です：{detail}。最新の表示を確認してください。元のリクエストは再送信されません。 |
| original_login_required | 请先以原账号重新登录，未确认的编辑不会自动重放。 | Sign in with the original account first. Unconfirmed edits will not replay automatically. | 先に元のアカウントで再ログインしてください。未確認の編集は自動で再実行されません。 |
| previous_pending | 上一笔编辑尚未确认，请先确认保存结果。 | The previous edit is unconfirmed. Confirm its save result first. | 直前の編集結果は未確認です。先に保存結果を確認してください。 |
| project_changed | 项目已有新版本，请刷新后检查图纸，再重新编辑。 | The project has a newer version. Refresh and review the drawing before editing again. | プロジェクトに新しいバージョンがあります。更新して図面を確認してから再編集してください。 |
| connection_pending | 连接中断，编辑是否保存尚未确认。请确认上一笔编辑，勿重复操作。 | Connection lost; the save result is unconfirmed. Confirm the previous edit. Do not repeat it. | 接続が切れ、保存結果は未確認です。直前の編集結果を確認し、同じ操作を繰り返さないでください。 |
| connection_shielded | 连接中断，旧工作区已屏蔽。请核对最新版本后恢复，未提交表单仍保留。 | Connection lost. The old workspace is hidden. Verify the latest version before restoring it. Unsubmitted forms remain. | 接続が切れました。以前の作業領域は非表示です。最新バージョンを確認してから復元してください。未送信のフォームは保持されています。 |
| response_pending | 响应损坏，保存结果尚未确认，请确认上一笔编辑。 | The response is invalid and the save result is unconfirmed. Confirm the previous edit. | 応答が無効で保存結果は未確認です。直前の編集結果を確認してください。 |
| response_shielded | 响应损坏，旧工作区已屏蔽。请核对最新版本后恢复。 | The response is invalid. The old workspace is hidden. Verify the latest version before restoring it. | 応答が無効です。以前の作業領域は非表示です。最新バージョンを確認してから復元してください。 |
| edit_saved | 编辑已保存。 | Edit saved. | 編集を保存しました。 |
| read_refused | 读取被拒绝，旧工作区已屏蔽。请核对当前账号和项目。 | Read refused. The old workspace is hidden. Check the current account and project. | 読み取りが拒否されました。以前の作業領域は非表示です。現在のアカウントとプロジェクトを確認してください。 |
| save_unconfirmed | 服务未确认保存结果，请确认上一笔编辑。 | The service did not confirm the save result. Confirm the previous edit. | サービスから保存結果を確認できません。直前の編集結果を確認してください。 |
| version_conflict | 项目版本冲突，请刷新并检查最新图纸。 | Project version conflict. Refresh and review the latest drawing. | プロジェクトのバージョンが競合しています。更新して最新の図面を確認してください。 |
| edit_refused | 编辑被拒绝，图纸未修改。 | Edit refused. The drawing was not changed. | 編集が拒否されました。図面は変更されていません。 |
| export_failed | 导出失败，请检查项目状态。 | Export failed. Check the project state. | エクスポートに失敗しました。プロジェクトの状態を確認してください。 |
| local_file_required | 请通过文件导入打开图纸，私有项目不接受服务器文件路径。 | Import a local file to open a drawing. Private projects do not accept server file paths. | ローカルファイルをインポートして図面を開いてください。プライベートプロジェクトではサーバーのファイルパスは使用できません。 |
| confirm_before_switch | 请先确认上一笔编辑，再切换登录。 | Confirm the previous edit before switching accounts. | アカウントを切り替える前に直前の編集結果を確認してください。 |
| switch_unconfirmed | 登录切换未完成，旧工作区仍屏蔽。请恢复原账号或重试退出。 | The account switch is unconfirmed. The old workspace remains hidden. Restore the original account or try signing out again. | アカウントの切り替えは未確認です。以前の作業領域は非表示のままです。元のアカウントを復元するか、再度ログアウトしてください。 |
| previous_not_submitted | 上一笔编辑未提交，请刷新并检查最新图纸。 | The previous edit was not submitted. Refresh and review the latest drawing. | 直前の編集は送信されませんでした。更新して最新の図面を確認してください。 |
| still_unconfirmed | 保存结果仍未确认，请保留此页面。 | The save result is still unconfirmed. Keep this page open. | 保存結果はまだ未確認です。このページを開いたままにしてください。 |
| confirm_failed | 无法确认保存结果，请保留此页面，待连接恢复后再次确认。 | Cannot confirm the save result. Keep this page open and confirm again when the connection returns. | 保存結果を確認できません。このページを開いたままにし、接続の復旧後に再度確認してください。 |
| login_restored | 原账号登录已恢复。请手动确认上一笔编辑，尚未自动重放。 | Sign-in with the original account is restored. Confirm the previous edit manually. It has not replayed automatically. | 元のアカウントへのログインを復元しました。直前の編集結果を手動で確認してください。自動再実行はされていません。 |
| confirm_before_logout | 请先确认上一笔编辑，再退出登录。 | Confirm the previous edit before signing out. | ログアウトする前に直前の編集結果を確認してください。 |
| logout_unconfirmed | 退出未确认，旧工作区仍屏蔽：{detail} | Sign-out is unconfirmed. The old workspace remains hidden: {detail} | ログアウトは未確認です。以前の作業領域は非表示のままです：{detail} |
| locale_unavailable | 登录和项目区翻译不可用：{code} | Session translations unavailable: {code} | ログインとプロジェクト領域の翻訳を利用できません：{code} |

## Safety And Acceptance

Public copy uses the foundation's explicit English default before any
authenticated preference. It never reads a locale from local/session storage.
Copy events may repaint recovery notices while locked; they never change
input values, selection, identity, epoch, pending, base, queue, disabled/hidden
controls, focus or workspace shield. Response-driven updates require current
identity, project and epoch before and after body/ready consumption.

Missing keys/arguments, invalid snapshots and readiness rejection remain
visible diagnostics carrying the exact first error code and key coordinate.
English boot text is an explicit readiness/error fallback, not a dictionary.
Failure diagnostics never interpolate private values into logs/events.

Acceptance must retain fresh before-red, fix-green, selftest and readback
evidence under `/hf3fs-jd/data/tmp/`, with local runtime under `/data/tmp/`.
Node executes the real product JS, source/HEAD/byte/mode are bound, actual
child exits/counts and completion markers are checked, and reader mutations
must be rejected. No synthetic test proves production deployment.
Only verified terminal acceptance permits a three-path DCO commit; no push.

## Paused Handoff 2026-10-12

Status: **PAUSED / NOT ACCEPTED**, collected at `2026-10-12T14:02:08+08:00`.
The user requested handoff only; no further development, test dispatch,
production action, commit or push is authorized in this turn.

- Original task: take over login/project/recovery locale using the unified
  foundation API, preserving original user data/error detail and strict
  session boundaries. This is one of the chair's actual six native seats,
  not an additional worker; no nested agents or Flash calls were made.
- Worktree/branch are stated above. Current HEAD remains the published base
  `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`; **no task commit exists**.
- Exclusive dirty set is exactly SCRIPT/TEST/DOC stated above. JS is modified;
  test and document are untracked. JS tracked diff is 256 insertions and
  66 deletions. No dictionary/private/parametric/service files were edited.
- Old `/data/dev/wt-opencad-project-session-locale-20261012` remains read-only,
  HEAD `eb19211ca7ca4b9304678fe747026009234d1515`, with its original dirty
  JS and two untracked test/document assets. Nothing was reverted or cleaned.
- The four-column table above has 50 keys. The chair is sole dictionary owner.
  Named sets remain `view_unverified/detail`, `logout_unconfirmed/detail`,
  `locale_unavailable/code`; all other rows require exactly `{}`.

### Actual Evidence

All four test invocations returned original parent process exit **1**, and
all have terminal `completion.log` plus `result.json`. None is accepted green.
Each Node child has separate `<case>.stdout.log` and `<case>.stderr.log`.
The full results bind complete physical source bytes/modes, HEAD, foundation,
fixture and original child exits; synthetic DOM/HTTP are explicitly disclosed.

| Evidence directory under /hf3fs-jd/data/tmp/ | UTC finish | Observed result |
| --- | --- | --- |
| opencad-session-locale-r2-before-red-20261012-v1 | 2026-10-12T05:48:25.891669+00:00 | FAIL; 1 case, 0 checks; fixture selector did not support h1. Not product-red evidence. |
| opencad-session-locale-r2-before-red-20261012-v2 | 2026-10-12T05:48:41.902164+00:00 | FAIL; 1 case, 1 check; real baseline title stayed Chinese rather than the explicit anonymous English default. Product-red evidence. |
| opencad-session-locale-r2-fix-green-20261012-v1 | 2026-10-12T05:50:21.086568+00:00 | FAIL; 30 cases, 159 checks; several Node children exited 0 without terminal due to missing select default value. Producer rejected missing terminals. |
| opencad-session-locale-r2-fix-green-20261012-v2 | 2026-10-12T05:51:40.988222+00:00 | FAIL; 30 cases, 308 checks; load-failure exact-first-cause assertion failed. |

The word `fix-green` in directory names is only an intended phase, **not a
PASS claim**. Check counts include strict fixture assertions and are not
independent tests. No browser, production HTTP, full Web regression or
industrial parity acceptance was run this round.
Local runtime directories are `/data/tmp/oc-sl-r2-red-v1`,
`/data/tmp/oc-sl-r2-red-v2`, `/data/tmp/oc-sl-r2-green-v1`,
`/data/tmp/oc-sl-r2-green-v2`; all retained.
Old Chromium artifacts are historical only, not inherited acceptance.

All issued test exec sessions have been collected as terminal failures.
The earlier broad read-only artifact search (exec session 34309) was also
collected, rc=2 from permission-denied unrelated directories; it is not a
test and no permissions were changed. No known task process remains in flight.

### Blockers And Resume

1. Confirmed product/diagnostic blocker: `localeService()` maps an error-status
   snapshot to `session_locale_snapshot_invalid` before surfacing
   `snapshot.error.code=locale_package_http_error`. Preserve the real first
   code and its key coordinate; do not relax the load-failure assertion.
   Reproduction log:
   `/hf3fs-jd/data/tmp/opencad-session-locale-r2-fix-green-20261012-v2/load-failure.stderr.log`.
2. Newly assigned P2 **not implemented**: `shieldWorkspace()` still hides
   body-mounted private/parametric/foundation public translation alerts.
   Proposed strict contract is body-direct, leaf
   `output[role="alert"][data-opencad-public-locale-error="private"]` or the
   same attribute value `parametric`, with only approved fixed code text,
   restricted attributes and no children/private detail. The code allowlist,
   attribute allowlist and exact implementation are **still pending**.
   The chair/private and parametric owners must align their nodes; this seat
   must not edit their source. Arbitrary role=alert is never an exemption.
3. Foundation already uses leaf `output[role="alert"][data-opencad-locale-alert]`.
   Separately validate its exact finite error codes or its exact
   `locale_unsupported: ` text matching current snapshot unsupported codes.
   No arbitrary error message/detail may pass. Add locked-visible positives
   and fake-role/private-detail/unknown-attribute/nonleaf negatives, including
   asynchronously appended alerts. Existing service is read-only.
4. The latest JS/test changes remain unaccepted. Reader CLI and twelve copied
   evidence negative controls are implemented but **not executed**.
   Fixture early-exit protection was added after v1; add a dedicated actual
   negative child to prove it. Further review must check original-detail
   bindings, anonymous/recovery lifecycle, stale activation/recovery and
   strict auth gates; directory/test names alone do not establish coverage.
5. Actual unique dictionary alignment and integrated private-page/browser
   acceptance are still owed to the chair. Current Node catalog comes from
   this handoff table, not the production dictionary.
6. Before any DCO commit: fresh full green, actual readback and reader controls,
   secret scan of all three paths, final diff/source freeze and precise staged
   set. Do not submit current FAIL as an accepted commit. No push/production.

Next session's first safe executable command is a read-only failure read:

```bash
sudo -n -u infra -B cat /hf3fs-jd/data/tmp/opencad-session-locale-r2-fix-green-20261012-v2/load-failure.stderr.log
```

Read applicable AGENTS and memory-router/shared-flash-pool again, inspect this
worktree/status and the immutable old tree, then fix the diagnosed cause and
implement the strict P2 contract via apply_patch in the three owned files.
Only after those fixes, the next fresh targeted test command is:

```bash
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-session-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_project_session_locale.py --selftest --development --case load-failure --out /hf3fs-jd/data/tmp/opencad-session-locale-r2-load-cause-20261012-v3 --runtime-root /data/tmp/oc-sl-r2-load-v3
```

Do not run this unchanged as an automatic retry, and do not reuse/clean any
existing evidence directory. The handoff append changes source bindings:
old producer artifacts remain historical and cannot pass current readback.
Captured pre-handoff JS SHA256:
`21d7c2f659b3c5f4f902884bafd189c3166994fbddd66b4bad684cf6f85c8535`;
test SHA256:
`04b12f496115692e51ffdbe3622e87c88eaa0efc2d0a955a10735d127498f4b1`.

## Chair Resume 2026-10-12

The next session resumed this seat after the user's continuation instruction.
The paused section above and all original failure evidence remain historical.

The first-cause defect is closed in `localeService()`: when the shared
snapshot is in `error` state and carries a code, that underlying code is
surfaced before the generic `session_locale_snapshot_invalid` fallback. The
public diagnostic stores only a sanitised identifier-shaped code and a
`panel.session.*` key coordinate; arbitrary exception text cannot become a
public error coordinate. A fresh single-case reproduction first showed
`actual=session_locale_snapshot_invalid`; after the fix the same case returned
parent rc 0 with five checks.

The shield now has one strict exemption for cross-owner public locale
diagnostics. A node must be a body-direct, visible, childless `output` with
`role=alert`, `lang=en`, and exactly three attributes: those two and
`data-opencad-public-locale-error`. The owner is only `foundation`,
`parametric`, or `private`; the text must exactly equal one of the finite
codes: foundation `locale_package_http_error`, `locale_package_load_failed`,
`locale_unsupported`; parametric
`parametric_locale_initialization_failed`; private
`private_locale_initialization_failed`. Anything else, including any other
`role=alert`, an extra attribute, an element child, private text, a nested
node, a wrong role/language/code, or a hidden node, is never exempt. A
MutationObserver re-runs the shield while locked, so asynchronously appended
nodes are checked too. The dedicated `public-alert-contract` child passes 99
checks covering all five accepted codes, locked/asynchronous insertion,
repeated shielding, and the negative shapes.

The Node catalog is no longer copied from this document. The test now reads
the actual clean `panel-i18n.json` from the catalog tree, binds its HEAD,
clean status and file hash in producer/readback evidence, and keeps this
document's request table as the independent key/placeholder oracle. The
foundation binding likewise pins a clean descendant of the foundation commit
and its actual service bytes. Applying this to the actual catalog exposed a
real product mismatch: the catalog calls the password key
`panel.session.password_label`, while the panel briefly used
`panel.session.password`. The product now uses the actual key. All 50
session keys and their placeholder sets match both the request table and the
actual catalog.

The full 31-case actual-catalog run returned parent rc 0 with 416 Node checks
at `/hf3fs-jd/data/tmp/opencad-session-locale-chair-actual-catalog-20261012-v2`
(runtime `/data/tmp/oc-sl-chair-actual-v2`). This is dirty-source development
evidence, not the post-commit clean acceptance; offline readback, the
twelve-copy reader controls, the post-commit reruns and all browser/private
HTTP/production evidence are intentionally not claimed here. Integration must
rerun this suite against the final composed tree and the actual shield nodes
owned by foundation, private and parametric. No nested agent, Flash worker,
push or production operation was used.
