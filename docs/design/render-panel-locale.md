# Render Panel Locale

## Scope And Key Handoff

The render UI seat starts at `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`
in `/data/dev/wt-opencad-render-locale-r2-20261012`, branch
`codex/render-locale-r2-20261012`. The exclusive write set is
`oss-alternatives/apps/opencad/web/js/render-panel.js`,
`oss-alternatives/apps/opencad/tests/test_render_panel_locale.py`, and this
document. The chair is the unique dictionary owner. No dictionary is copied
into the panel, and no other repository file is changed.

The real foundation is `/data/dev/wt-opencad-locale-foundation-20261012`
at `97b80e675f39e23faffcb8c0f6bb8706a8238f0b`. The panel uses `ready`,
`t(key, args)`, `bind(root)` and `snapshot()` with `opencad:locale`.
Unknown keys and missing/extra arguments are errors, never English fallback.

All keys below have the prefix `panel.render.`.
This is a handoff specification, not a runtime dictionary.

| Key Suffix | zh-CN | en | ja |
| --- | --- | --- | --- |
| title | 服务端离屏渲染 | Server Offscreen Render | サーバーオフスクリーン描画 |
| camera | 视角 | Camera | 視点 |
| camera.isometric | 等轴测 | Isometric | 等角投影 |
| camera.front | 前视 | Front | 正面 |
| camera.top | 俯视 | Top | 上面 |
| camera.right | 右视 | Right | 右面 |
| backend | 后端 | Backend | バックエンド |
| backend.gpu | GPU | GPU | GPU |
| backend.cpu | CPU | CPU | CPU |
| fallback | 允许明确 CPU 回退 | Allow explicit CPU fallback | 明示的な CPU フォールバックを許可 |
| submit | 渲染当前持久版本 | Render current persisted revision | 現在の永続化リビジョンを描画 |
| status.idle | 尚未调用服务端渲染 | Server render not requested | サーバー描画は未要求 |
| status.wait | 正在读取持久版本并渲染 | Reading persisted revision and rendering | 永続化リビジョンを取得して描画中 |
| image.alt | 服务端渲染 PNG | Server-rendered PNG | サーバー描画 PNG |
| status.result | {status} \| {backend} \| {renderer} \| 版本 {revision} | {status} \| {backend} \| {renderer} \| revision {revision} | {status} \| {backend} \| {renderer} \| リビジョン {revision} |
| status.fallback | CPU 回退：{reason} | CPU fallback: {reason} | CPU フォールバック: {reason} |
| status.failed | 渲染失败：{detail} | Render failed: {detail} | 描画失敗: {detail} |
| error.snapshot | 持久快照不可用 | Persisted snapshot unavailable | 永続化スナップショットを取得できません |
| error.request | 渲染请求失败 | Render request failed | 描画要求に失敗しました |
| error.response | 渲染响应版本或媒体类型不匹配 | Render response revision/media mismatch | 描画応答のリビジョンまたはメディア形式が不一致 |
| error.budget | 渲染 PNG 字节预算无效 | Render PNG byte budget invalid | 描画 PNG のバイト上限が不正 |
| error.magic | 渲染 PNG 文件签名无效 | Render PNG signature invalid | 描画 PNG のファイル署名が不正 |
| error.read | PNG 读取失败 | PNG read failed | PNG の読み取りに失敗しました |

Exact placeholder sets: `status.result={status,backend,renderer,revision}`,
`status.fallback={reason}`, `status.failed={detail}`. All other keys take
no arguments.

`status.result` arguments retain server status, backend, renderer/model and
revision values verbatim. `reason` and raw `detail` are not translated.
GPU/CPU model names, SHA, performance numbers and opaque server codes are not
language keys. Locally detected errors use `error.*`; switching locale
re-evaluates the explanation without changing the stored raw error.

## State Contract

Only leaf text and declared `data-panel-i18n`, `-aria`, `-title` and `-alt`
attributes are translated. A label containing a select/checkbox has a separate
text span; the containing label is never text-bound. No placeholder is added
to controls that do not have one. Camera/backend option values remain protocol
identifiers. Existing choices, automatic render-on-change behavior and
same-origin fetch options remain unchanged.

Locale handling retains the current status descriptor and argument values.
It does not dispatch render, click or change events, recreate controls, clear
an image, mutate camera/backend/fallback/disabled/busy, or touch private auth,
request/revision/SHA or any URL. PNG rendering continues to use FileReader
data URLs under the existing CSP, not a newly invented object URL path.
Translation failures have their own visible role alert and thrown/rejected
first cause; they must not be disguised as a successful render.

## Acceptance

The Python entry executes the whole shipped JS in real Node with a strict DOM
fixture, plus the exact real foundation service and chair dictionary. It does
not start a browser/server or run a renderer/GPU experiment. Evidence is new
external 3FS data; subprocess runtime is a new `/data/tmp` directory.
Complete physical/tracked file bytes, modes, HEAD, child argv, actual exits,
case/check counts, terminal markers and artifacts are bound and read back.
The independently received parent exit remains required for acceptance.

Integration of the chair's dictionary and the private-page script loading is
a separate dependency. Node results do not establish real browser, production,
industrial parity, private-auth end-to-end or RTX measurement.

The parser compares only the 23 render key rows against the real three-language
dictionary and checks named argument sets. Other namespaces are not compared
against this document. The full shipped locale service still validates its
entire dictionary by its own strict contract. Both read-only dependency HEADs
must descend from the foundation commit; the locale service bytes must match
that real API source. No exact-HEAD freeze rejects the chair's later catalog
commits. Integrated clean panel sources may descend from the requested base;
the three-file commit allowlist applies only to this seat's development mode.

Development evidence explicitly permits the chair's three owned dirty catalog
files, never arbitrary unknown files or changed locale service bytes. A dirty
catalog result is not release acceptance. Subsequent clean-source acceptance
needs fresh logs and a new source pin; old successful runs are not upgraded.

Reproducible clean-source commands after both trees are committed:

```bash
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-render-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_render_panel_locale.py --selftest --locale-root /data/dev/wt-opencad-locale-foundation-20261012 --catalog-root /data/dev/wt-opencad-panel-catalog-r2-20261012 --out /hf3fs-jd/data/tmp/opencad-render-locale-r2-20261012/committed-v1 --runtime /data/tmp/opencad-render-locale-r2-20261012-committed-v1
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-render-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_render_panel_locale.py --verify /hf3fs-jd/data/tmp/opencad-render-locale-r2-20261012/committed-v1/result.json --parent-rc 0 --locale-root /data/dev/wt-opencad-locale-foundation-20261012 --catalog-root /data/dev/wt-opencad-panel-catalog-r2-20261012 --out /hf3fs-jd/data/tmp/opencad-render-locale-r2-20261012/committed-readback-v1 --runtime /data/tmp/opencad-render-locale-r2-20261012-committed-readback-v1
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-render-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_render_panel_locale.py --controls /hf3fs-jd/data/tmp/opencad-render-locale-r2-20261012/committed-v1/result.json --parent-rc 0 --locale-root /data/dev/wt-opencad-locale-foundation-20261012 --catalog-root /data/dev/wt-opencad-panel-catalog-r2-20261012 --out /hf3fs-jd/data/tmp/opencad-render-locale-r2-20261012/committed-controls-v1 --runtime /data/tmp/opencad-render-locale-r2-20261012-committed-controls-v1
```

The `--parent-rc` value is the actually received producer exit, not a guessed
value or a value read from its JSON. Each output and runtime path must be new;
repeat with a new suffix, never remove previous evidence. `--foundation-root`
may explicitly bind a single real combined foundation/catalog worktree.
The CLI preserves failed children, original causes and syntax check logs.
Discovery uses the parent's `OPENCAD_EVIDENCE_ROOT` and
`OPENCAD_TEST_RUNTIME_ROOT`; `OPENCAD_RENDER_LOCALE_ROOT` and
`OPENCAD_RENDER_CATALOG_ROOT` select real dependency trees, not dictionary mocks.

The six Node negative children cover missing keys, missing/extra arguments,
unsafe nonleaf binding, a real full-panel JS locale-request mutation, and a
zero-exit/no-terminal child. The nine reader subprocess controls reject a
missing child, fake child exit, fake check count, source SHA drift, corrupt or
extra artifacts, rehashed terminal/completion lies and a nonzero original
parent exit. Original evidence is copied to new control directories, never
rewritten or cleaned up.

On 2026-10-12 the baseline full JS failed with
`render_title_not_locale_bound` after 24 fixture checks, original parent rc=1,
in `before-red-v1`. The first real dirty-catalog fix ran 17 cases/573 checks,
six controls and no skips, parent rc=0, in `fix-green-dev-v1`.
These are development coordinates, not a frozen release claim.
The unrelated infra routing initially refused a cross-repository absolute
target; the applicable in-root target mapping passed afterwards. No unsafe
path exception or permission change was made.

## 暂停交接：2026-10-12 14:02 +08:00

用户明确要求本轮只落盘交接，下个 session 继续原目标。状态为
PAUSED / 未提交 / 未整合，不是 DONE。本轮不再开发、不新投递、不做
生产操作、不向主树提交或 push，不清理资源、工作树或未知文件。

本席原任务是服务端渲染面板三语 UI，禁止 GPU/性能实验与生产修改。
工作树 `/data/dev/wt-opencad-render-locale-r2-20261012`，
分支 `codex/render-locale-r2-20261012`，
HEAD 仍为 `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`。
本席新增提交 SHA：无；此前只是准备提交，没有执行 add/commit。

独占且未提交的三件：

- `oss-alternatives/apps/opencad/web/js/render-panel.js`：叶节点与 aria/title/alt
  绑定、动态状态 key/args、独立翻译错误 alert；切语言不调用渲染。
- `oss-alternatives/apps/opencad/tests/test_render_panel_locale.py`：完整真实面板
  JS 与基础服务 Node 夹具、源/产物/时间/计数绑定、负控与原父 rc readback。
- `docs/design/render-panel-locale.md`：本件，包含四列三语 keymap 和交接。

keymap 仍为 `panel.render.*` 的 23 键，无新增键。占位符仍仅
`status.result={status,backend,renderer,revision}`、
`status.fallback={reason}`、`status.failed={detail}`。
主席是唯一词典 owner，本席没有改词典或造 JS 本地三语字典。

只读依赖现状：

- 基础服务 `/data/dev/wt-opencad-locale-foundation-20261012`，
  `97b80e675f39e23faffcb8c0f6bb8706a8238f0b`。
- 主席词典 `/data/dev/wt-opencad-panel-catalog-r2-20261012`，
  `373fe47a041cc33a7ba92e1d7f1257483a69964b`，本次现读 clean。
  它包含基础、parametric `505ab8f` 与主席词典提交，按祖先关系接受，
  不要求词典 HEAD 恰为基础 SHA；基础服务本身字节仍核原真源。

证据根 `/hf3fs-jd/data/tmp/opencad-render-locale-r2-20261012/`：

- `before-red-v1/positive.stderr.log`：完整修前 JS，
  `render_title_not_locale_bound`，24 个检查，原父 rc=1。
- `missing-catalog-v1/`：真实缺键 `locale_missing_key`，原父 rc=1，保留。
- `fix-green-dev-v2/`：2026-10-12 13:57:18 +08:00，
  17 场景、573 检查、6 个 Node 负控、0 skip，原父 rc=0。
  `result.json`、`completion.log`、每个 Node stdout/stderr 均保留。
- `dev-readback-v2/`：13:57:50 +08:00，独立 readback PASS，原父 rc=0；
  绑定上述生产者实际已收到的父 rc=0，而不是只读 JSON 自报。
- `dev-controls-v2/`：13:58:01 +08:00，9 个实际 reader 子进程全部按
  预期 rc=1 拒绝反例；控件总入口原父 rc=0。每例首因、argv 和日志保留。
- `source-scan-v2/acceptance.log`：仓库 `tools/acceptance.py --stage scan`
  PASS，原父 rc=0；无 scan 放宽、豁免或凭据修改。

Node 正控记录了 17 个真实夹具请求与 3 次 PNG 读取；语言切换前后
请求、控件、焦点、图像与夹具私人状态保持一致，不是 GPU 收益。
render namespace 前后 SHA-256 均为
`dc2c2140d0e7fdb474094397bcd84ee0f2aa46d4d56e065b388f01ebb26ddba4`。
上述测试及 reader 进程均已收到最终退出码，无本席进程待收终态。
runtime 均在 `/data/tmp/opencad-render-locale-r2-20261012-*`，不清理。

未完成与边界：

- 本席三件 DCO 提交尚未执行，工作树为 1 修改 + 2 未跟踪。
- 提交后不带 `--development` 的 clean-source Node/readback/controls 尚未跑。
- discovery 接入的实际 unittest 全链、真实浏览器、私人鉴权端到端、
  主席整合/加载入口与生产验收未跑；不以 Node/语法检查冒充这些。
- 主席曾报告词典 credential-shaped scan 失败，已修复并提交；原失败
  由主席保留。本席不改门，也不把它记成仍待修的阻断。
- 无已观测未定位的本席测试失败。当前阻断是用户暂停与未提交收口，
  不是词典缺键。旧开发结果不能升级为冻结/整合 PASS。
- 本交接写入改变文档字节，旧证据对旧源指纹仍有效，但不能拿旧
  readback 验收交接后的当前树；复飞必须新建证据目录。

下个 session 先读适用 AGENTS、memory-router 与本节，现查三树状态、
独占 diff 和词典 SHA。第一条重新取证命令如下，本轮未执行：

```bash
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-render-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_render_panel_locale.py --selftest --development --locale-root /data/dev/wt-opencad-locale-foundation-20261012 --catalog-root /data/dev/wt-opencad-panel-catalog-r2-20261012 --out /hf3fs-jd/data/tmp/opencad-render-locale-r2-20261012/resume-dev-v1 --runtime /data/tmp/opencad-render-locale-r2-20261012-resume-dev-v1
```

随后按实际父 rc 做新 readback/controls，核三件 diff 与凭据扫描，
只在本独立分支具名 DCO 提交；再执行上文全新 clean-source 三入口。
主树整合与 push 仍留主席授权，禁止自行执行。恢复路径为保留当前
三件与全部外置证据，不覆盖他人资产；若以后形成提交，按具名提交
由获授权 owner 恢复，绝不宽范围 reset/checkout 或清理。

## Chair Resume 2026-10-12

The next session reran the shipped render panel against the actual foundation
and chair catalog, receiving the following actual parent exits:

```text
producer  /hf3fs-jd/data/tmp/opencad-render-locale-chair-fix-20261012-v1
          rc=0, cases=17 checks=573 negatives=6 skips=0
readback  /hf3fs-jd/data/tmp/opencad-render-locale-chair-verify-20261012-v1
          rc=0
controls  /hf3fs-jd/data/tmp/opencad-render-locale-chair-controls-20261012-v1
          rc=0, nine actual reader negatives
```

The earlier development run and its source binding remain historical. The
current run still uses the actual service/catalog and keeps the original
render-once/no-request/no-draw assertions, raw backend detail and error-state
promises. After committing the three owned paths, the three commands are to be
rerun without `--development`; the real unittest discovery remains a separate
required receipt. No browser, production, RTX/GPU performance or integrated
private-auth claim is made by this seat.
