# Surface Panel Locale

## Scope

Base: `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`.
Tree: `/data/dev/wt-opencad-surfaces-locale-r2-20261012`.
Branch: `codex/surfaces-locale-r2-20261012`.
Only the surface consumer, surface labels/mount wiring in industrial-panels,
this document, and `tests/test_surface_panel_locale.py` are owned.
The chair alone owns the shared dictionary. No local runtime dictionary,
shared source edit, nested agent, push, production or permission change.

Foundation dependency:
`97b80e675f39e23faffcb8c0f6bb8706a8238f0b`, read-only tree
`/data/dev/wt-opencad-locale-foundation-20261012`.
Consumers use `OpenCADLocale.ready/snapshot/update/t/bind` and the
`opencad:locale` event. Static captions are leaf bindings; control ARIA
uses the same keys. Dynamic status keeps its key/args independently of
the raw error. Language changes only paint text and explicit attributes.
Recipe JSON, UV, thickness, inward, mesh, downloads, session gates and
dirty state are not language inputs. Mathematical symbols and metric
JSON remain raw. There are no select options or new placeholders here.

## Chair Keymap

All keys below have prefix `panel.surfaces.`. Empty args mean `{}`.
The table is a handoff specification, not a runtime fallback dictionary.

| Key Suffix | zh-CN | en | ja |
| --- | --- | --- | --- |
| heading | 精确曲面 | Exact surfaces | 精密サーフェス |
| limits | NURBS / 矩形裁剪 / 缝合 / 增厚。G0/G1/G2 为采样检查，不是全域证明。 | NURBS / rectangular trim / sew / thicken. Sampled G0/G1/G2, not a whole-domain proof. | NURBS / 矩形トリム / 縫合 / 厚み付け。G0/G1/G2 はサンプリング検査であり、全領域の証明ではありません。 |
| recipe | 曲面配方（整数 nm 坐标） | Exact recipe (coordinates in integer nm) | サーフェスレシピ（整数 nm 座標） |
| plane | 10 mm 平面 | 10 mm plane | 10 mm 平面 |
| cylinder | R5 四分之一圆柱面 | R5 quarter cylinder | R5 四分円柱面 |
| bounds | 裁剪 UV 范围 [u0,u1,v0,v1] | Trim UV [u0,u1,v0,v1] | トリム UV 範囲 [u0,u1,v0,v1] |
| thickness | 厚度（mm） | Thickness (mm) | 厚さ（mm） |
| inward | 向内 | Inward | 内向き |
| trim | 包裹裁剪 | Wrap trim | トリムで包む |
| thicken | 包裹增厚 | Wrap thicken | 厚み付けで包む |
| sew | 两平面接缝 | Two-plane seam | 2 平面の継ぎ目 |
| apply | 验证并保存候选 | Validate & save candidate | 候補を検証して保存 |
| reload | 重读已保存曲面 | Read saved surfaces | 保存済みサーフェスを再読込 |
| preview | 真实曲面网格预览 | Exact surface mesh preview | 実サーフェスメッシュのプレビュー |
| metrics | 曲面测量结果 | Surface measurements | サーフェス測定結果 |
| status | 曲面操作状态 | Surface operation status | サーフェス操作の状態 |
| empty | 尚未保存曲面。 | No saved surface. | 保存済みサーフェスはありません。 |
| ready | 精确内核结果已就绪。 | Exact kernel result ready. | 精密カーネルの結果が準備できました。 |
| downloaded | 已下载保存的精确曲面；未保存配方保持不变。 | Saved exact surface downloaded; unsaved recipe retained. | 保存済み精密サーフェスをダウンロードしました。未保存レシピは保持されています。 |
| download_step | 下载已保存 STEP | Download saved STEP | 保存済み STEP をダウンロード |
| download_brep | 下载已保存 BRep | Download saved BRep | 保存済み BRep をダウンロード |
| error | 曲面操作失败：{detail} | Surface operation failed: {detail} | サーフェス操作に失敗しました：{detail} |
| error_thickness | 厚度必须为正整数 nm：{detail} | Thickness must be a positive integer in nm: {detail} | 厚さは正の整数 nm が必要です：{detail} |
| error_webgl | 无法显示曲面网格，请检查 WebGL：{detail} | Cannot display the surface mesh; check WebGL: {detail} | サーフェスメッシュを表示できません。WebGL を確認してください：{detail} |
| error_mesh | 曲面网格数据无效：{detail} | Invalid surface mesh data: {detail} | サーフェスメッシュのデータが無効です：{detail} |
| error_busy | 曲面请求尚未完成：{detail} | A surface request is still in progress: {detail} | サーフェスの要求が処理中です：{detail} |
| error_export | 曲面导出失败，请核查回执和文件：{detail} | Surface export failed; check the receipt and file: {detail} | サーフェスのエクスポートに失敗しました。受領情報とファイルを確認してください：{detail} |

Args: `error`, `error_thickness`, `error_webgl`, `error_mesh`, `error_busy`,
`error_export` require exactly `{detail}`. All other keys have no args.

## Offline Evidence

The new Python CLI writes its actual Node fixture under `runtime/`, with
child logs and JSON under `data/tmp/`, in a fresh external `--out`.
It reads actual surface/industrial source and actual foundation service
and chair dictionary; no translated test dictionary is generated.
Strict DOM selectors and destructive text writes are rejected.
Before-red, fix-green and reader receipts are distinct runs.
Node/DOM checks are not browser, industrial, HTTP or whole-site acceptance.

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-surfaces-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_surface_panel_locale.py --selftest --foundation-root /data/dev/wt-opencad-panel-catalog-r2-20261012 --out /hf3fs-jd/data/tmp/opencad-surfaces-locale-r2-20261012-fix-green-v1
```

Use `--verify <producer-out>/result.json --parent-rc 0` with a new `--out`
for offline reader acceptance. The parent rc must be the actual received
exit, not a producer JSON assertion. Source/artifact hashes, child terminal
counts, negative causes and UTC times are reread. Missing dictionary keys
are an explicit chair integration dependency, never a silent fallback.

The earlier `test_industrial_surfaces.py` browser `PAGE` mounts the surface
consumer without loading the unified locale dependency. That legacy fixture
needs a separate chair-owned integration update; it is not modified here,
and its historical browser/industrial PASS is not inherited.

## Development Receipts

All original parent exits were received, not inferred from JSON.
Evidence roots below are children of `/hf3fs-jd/data/tmp/`.

| Run | Evidence suffix | Parent rc | Result |
| --- | --- | --- | --- |
| Before red | opencad-surfaces-locale-r2-20261012-before-red-v1 | 1 | surface_locale_bindings_missing |
| Dependency red | opencad-surfaces-locale-r2-20261012-catalog-dependency-red-v1 | 1 | locale_missing_key |
| Fix green | opencad-surfaces-locale-r2-20261012-fix-green-v1 | 0 | 1537 positive assertions, 8 actual negative children |
| Offline reader | opencad-surfaces-locale-r2-20261012-reader-v1 | 0 | 9 child terminals/argv/exits/times, source and artifacts reread |
| Reader controls | opencad-surfaces-locale-r2-20261012-reader-controls-v1 | 0 | 9 actual reader rejections; positive assertions not applicable |
| Repository scan | opencad-surfaces-locale-r2-20261012-source-scan-v1 | 0 | source hygiene, credential and runtime import checks |

The first green terminal is `2026-10-12T05:53:10.932534+00:00`.
Its read-only dictionary SHA256 is
`7590fc25fae6e0f38511408700730c28032a8d80efe09b1b1006ea2b1f99798a`;
service SHA256 is
`83deaad7c93f6bca84750885bfc769106863f401f9101948cb2115fa52fb1f80`.
The chair dictionary was not a final whole-tree freeze. These are development
receipts; later documentation/source edits require new producer/readback runs.
No browser, industrial geometry, full Web regression or production verification
was run for this locale change.

## 暂停交接 2026-10-12

现读时刻：`2026-10-12T14:02:06+08:00`。用户明确本轮仅落盘交接，
停止新开发、新测试投递及生产操作；不提交、不推送、不清理。
下一 session 继续本任务，不能将本暂停记为任务完成。

原任务：OpenCAD 曲面面板 `panel.surfaces.*` 三语 locale。
worktree `/data/dev/wt-opencad-surfaces-locale-r2-20261012`；
分支 `codex/surfaces-locale-r2-20261012`；
HEAD 仍为 `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`。
本席新提交 SHA：无。暂存区为空，以下四件为唯一未提交写集：

- `oss-alternatives/apps/opencad/web/js/surfaces-panel.js`：叶子 caption、
  ARIA、动态状态 key/args、原 error detail 保留、挂载前完整键校验、
  dispose 解绑；没有本地词典。SHA256
  `d4da9fa0d1f26aa00f82fe646757d5d19e5e70ee11460c7264e0985c391da725`。
- `oss-alternatives/apps/opencad/web/js/industrial-panels.js`：仅 surfaceLabels
  段及 surface 挂载接线；其余分支由机械边界核对未变。SHA256
  `392c430ead0812af0b7661b731d3b6310d5eb99b9299f4cadde97a6a84599b05`。
- `oss-alternatives/apps/opencad/tests/test_surface_panel_locale.py`：实际
  sourceNode 严格 DOM、11 个真实负控、独立 reader 及 9 个 reader 负控；
  runtime/data/tmp 全部外置。SHA256
  `d2d005cf504b2d5500c8f9f6eb56ea3002553ec2bb8121f61ab6cf1f65000a07`。
- 本文：27 键四列表及 args 契约、证据坐标、本暂停交接。

最新真实开发测试均已收到原始 parent rc=0，无未收在飞测试：

| 运行 | /hf3fs-jd/data/tmp 下目录 | UTC 终判 | 实测结果 |
| --- | --- | --- | --- |
| sourceNode v2 | opencad-surfaces-locale-r2-20261012-fix-green-v2 | 2026-10-12T05:56:59.560320+00:00 | PASS，1537 正控断言，11 负控 |
| reader v2 | opencad-surfaces-locale-r2-20261012-reader-v2 | 2026-10-12T05:57:39.926927+00:00 | PASS，逐件核源码、产物、12 子终态、argv、rc、时刻 |
| reader controls v2 | opencad-surfaces-locale-r2-20261012-reader-controls-v2 | 2026-10-12T05:57:48.025296+00:00 | PASS，9 个实际 reader 预期拒绝；checks=0 不表示实测零个正控 |

三个目录均有 `result.json` 及 `data/tmp/completion.log`；
producer 的实际 fixture 在 `runtime/surface-fixture.cjs`，逐子日志在
`data/tmp/<mode>.stdout.log`、`<mode>.stderr.log`。
result SHA256 依次为
`5565e6b9fa26b9e34cb375fa968cb50a23cc36c0ace782d3644ae805f75223bd`、
`9546ac2298f485c2e768739ed8b0941287ff905da1ea2f35adaecd97a1a2c6e6`、
`4a494e5146fc39839c70c473cdcc2ba836166c6725791a04c54fa6cf32b73191`。
v1 before-red、dependency-red、绿/reader/controls 和 source-scan
目录均保留；source scan 原 parent rc=0，专属 local runtime 为
`/data/tmp/oc-sl-r2-scan-20261012-v1`。未清理任何旧证据。

真实依赖为主席只读树 `/data/dev/wt-opencad-panel-catalog-r2-20261012`，
现读 clean DCO HEAD `373fe47a041cc33a7ba92e1d7f1257483a69964b`；
该树含 foundation 97、param 505ab8f 及 catalog 373。
当前词典 SHA256
`8cc631501aa8960ef16b2319eebce2158db0425430c0104f31011329abe73f0f`；
service 原字节 SHA256
`83deaad7c93f6bca84750885bfc769106863f401f9101948cb2115fa52fb1f80`。
主席已修 credential-shaped literal 并保留旧 FAIL，未改 scanner；
该修复不是本席写集或本席 scan 的证明。

未完成及边界：

- 暂停前尚未补依赖 HEAD/clean/blob 强绑定；现有验收器绑定依赖文件
  byte/mode/hash，不得宣称已绑定 dependency commit。
- 尚未形成四件具名 DCO 提交，尚未完成提交后最终 producer/reader/
  reader controls 与正常 scan 复验；此次交接文档改变源码指纹，
  v2 仅是其当时字节的开发 PASS，不是当前源码终验。
- 旧 `test_industrial_surfaces.py` browser PAGE 未加载统一语言服务，
  需要主席独立集成更新；本席未扩写该文件，也不继承旧 browser PASS。
- 未跑新浏览器、HTTP/工业几何、全 Web 回归、全站语言或生产验收。
  不称全站完成；共享词典仅主席 owner，禁止跨写、嵌套、push、
  生产及凭据/权限操作。旧主树未跟踪 industrial 文件未碰。

复飞先读适用 AGENTS、memory-router/shared-flash-pool、本交接及最新
Git/依赖状态。以下为下一次实际自测命令，本轮未执行；仅在继续授权
后使用全新目录，再收真实 parent rc 后运行 reader。若目录已存在，
另取新版本，禁止删除或覆盖。

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-surfaces-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_surface_panel_locale.py --selftest --foundation-root /data/dev/wt-opencad-panel-catalog-r2-20261012 --out /hf3fs-jd/data/tmp/opencad-surfaces-locale-r2-20261012-resume-v3
```

## Chair Resume 2026-10-12

The next session resumed with the current actual catalog as the combined
foundation/catalog root. The fresh producer, offline readback and reader
negative controls all returned their actual parent exit 0:

```text
producer  /hf3fs-jd/data/tmp/opencad-surfaces-locale-chair-fix-20261012-v1
          checks=1537 negatives=11
readback  /hf3fs-jd/data/tmp/opencad-surfaces-locale-chair-verify-20261012-v2
          checks=1537 negatives=11
controls  /hf3fs-jd/data/tmp/opencad-surfaces-locale-chair-controls-20261012-v2
          reader_negatives=9
```

The failed first readback attempt (passing the catalog directory instead of
the producer's file coordinate) is retained as
`opencad-surfaces-locale-chair-verify-20261012-v1`; it was a CLI-coordinate
error, not relaxed into a success. The evidence binds the source and the two
actual dependency files by path, bytes, mode and SHA256. The dependency
worktrees' commit/clean identity is still not separately recorded by this
seat's reader; that provenance gap and the legacy harness integration remain
for the chair's composed-tree acceptance. No browser, full Web, industrial
geometry or production claim is made by this locale run.
