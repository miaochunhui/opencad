# Assembly Panel Locale

## 写集与词典交接

基线 `9c4abdac2906111d49f0c39a437ca4cef7abb9b3`，独立树
`/data/dev/wt-opencad-assembly-locale-r2-20261012`，分支
`codex/assembly-locale-r2-20261012`。本席仅修改装配 JS、本测试及本文。
主席是唯一词典 owner；本席不修改 `panel-i18n.json`、industrial-panels、
其他 module、凭据、权限或生产，不 push，不启用 Flash。

以下是提交给主席的完整三语 keymap。每行 key 均加前缀
`panel.assembly.`；占位符精确集合单列在表后。
工业术语是候选译文，最终审定由主席负责。没有 JS 本地词典/defaults。

| Key Suffix | zh-CN | en | ja |
| --- | --- | --- | --- |
| title | 真实装配 | BRep Assembly | BRep アセンブリ |
| part | 独立零件 L1 文件 | Independent part L1 file | 独立部品 L1 ファイル |
| part_id | 零件 ID | Part ID | 部品 ID |
| component_id | 实例 ID | Instance ID | インスタンス ID |
| load | 加载零件 | Load part | 部品を読み込む |
| components | 层级实例 | Instance hierarchy | インスタンス階層 |
| pose | 局部位姿与父级 | Local pose and parent | ローカル姿勢と親 |
| apply | 应用位姿 | Apply pose | 姿勢を適用 |
| mate | 平面 / 轴配合 | Plane / axis mates | 平面 / 軸の拘束 |
| add_mate | 应用配合 | Apply mate | 拘束を適用 |
| solve | 求解配合 | Solve mates | 拘束を解く |
| save | 保存项目版本 | Save project revision | プロジェクトのリビジョンを保存 |
| download | 下载装配 JSON | Download assembly JSON | アセンブリ JSON をダウンロード |
| reopen | 重开装配 JSON | Reopen assembly JSON | アセンブリ JSON を再度開く |
| refresh | 刷新 | Refresh | 更新 |
| empty | 尚未加载独立零件 | No independent parts loaded | 独立部品は未読み込み |
| ready | 已同步 | Synchronized | 同期済み |
| busy | 处理中 | Working | 処理中 |
| bom | 物料清单 | Bill of materials | 部品表 |
| collision | 真实 BRep 碰撞与间隙 | BRep collisions and clearance | BRep の干渉とクリアランス |
| faces | 绑定零件 SHA 的面索引 | Face indices bound to part SHA | 部品 SHA に対応する面インデックス |
| separated | 分离 | Separated | 分離 |
| touching | 接触 | Touching | 接触 |
| penetrating | 相交 | Intersecting | 交差 |
| preview | 装配三角网格视图 | Assembly triangle mesh preview | アセンブリ三角形メッシュのプレビュー |
| component_json | 层级实例 JSON | Instance hierarchy JSON | インスタンス階層 JSON |
| mate_json | 配合 JSON | Mate JSON | 拘束 JSON |
| translation | 平移（mm） | Translation (mm) | 平行移動（mm） |
| solver | 配合求解残差与自由度 | Mate solver residuals and degrees of freedom | 拘束ソルバーの残差と自由度 |
| group | 组 | Group | グループ |
| current_revision | 已同步 · 当前项目版本 {revision} | Synchronized · Current project revision {revision} | 同期済み · 現在のプロジェクトリビジョン {revision} |
| metrics | {backend} · 实体数={solids} · 体积（nm3）={volume} | {backend} · Solids={solids} · Volume (nm3)={volume} | {backend} · ソリッド数={solids} · 体積（nm3）={volume} |
| error_detail | 错误：{detail} | Error: {detail} | エラー：{detail} |
| translation_invalid | 平移需要三个有界有限数值（mm） | Translation requires three bounded finite numbers (mm) | 平行移動には範囲内の有限数値が三つ必要です（mm） |
| part_file_required | 需要不超过 {limit} MiB 的独立零件 L1 文件 | An independent part L1 file of at most {limit} MiB is required | {limit} MiB 以下の独立部品 L1 ファイルが必要です |
| sync_required | 请先同步当前项目 | Synchronize the current project first | 先に現在のプロジェクトを同期してください |
| assembly_file_required | 需要不超过 {limit} MiB 的装配 JSON 文件 | An assembly JSON file of at most {limit} MiB is required | {limit} MiB 以下のアセンブリ JSON ファイルが必要です |
| component_unknown | 未知实例：{identifier} | Unknown instance: {identifier} | 不明なインスタンス：{identifier} |
| json_invalid | JSON 格式错误：{detail} | Invalid JSON: {detail} | JSON 形式が無効です：{detail} |
| webgl_required | 装配网格预览需要 WebGL | WebGL is required for the assembly mesh preview | アセンブリメッシュのプレビューには WebGL が必要です |

精确 args 集合：`current_revision={revision}`，
`metrics={backend,solids,volume}`，`error_detail={detail}`，
`part_file_required={limit}`，`assembly_file_required={limit}`，
`component_unknown={identifier}`，`json_invalid={detail}`。其余键不传 args。
字面竖线必须使用 `\|`，当前译文没有字面竖线。

## 契约与边界

依赖只读 foundation `97b80e675f39e23faffcb8c0f6bb8706a8238f0b` 的
`window.OpenCADLocale.ready/t(key,args)/bind(root)/snapshot()` 和
`opencad:locale`。绑定使用 `data-panel-i18n`、`-aria` 及
`data-panel-i18n-args`，参数集合严格相等；未知 key/status 必须报错。
mount 的 `options.labels` 不再覆盖唯一词典。

语言展示与 `render` 分离。切换只重绘叶文字/ARIA、碰撞状态和摘要，
不重建控件或树，不 select、不 draw、不发请求，不修改 state、
pose/mate 草稿、焦点、禁用态、solver/碰撞数据、会话、历史或 CAS。
组件/part/parent ID、BOM 内容、JSON、面索引、solver 原始诊断、
轴、mm/nm/nm3 和 FreeCAD-BRep 标识保持原数据。
raw 工程错误保留原 message/code；仅错误前缀及本地用户输入说明翻译。
JSON 解析首因保留为 `detail`，不按字符串替换翻译工程诊断。

## 验证阶段

已实现：完整真实装配 JS 的 Node DOM/WebGL 夹具修前红、修后绿，
selector 自证、独立失败子进程、源/日志/退出码绑定及 offline readback。
Node 对每个 mount 执行完整 JS，不抽函数、不复制产品实现；
WebGL 是明确桩，不是浏览器、硬件或真 BRep 验收。
断言计数含夹具机械检查，不冒称同数量独立测试。

最新测试直接读取主席
`/data/dev/wt-opencad-panel-catalog-r2-20261012/oss-alternatives/apps/opencad/web/panel-i18n.json`
及 foundation 真实服务。服务源码必须逐字节匹配 `97b80e`；JSON、
装配 JS 及 Node fixture 的读取路径/SHA、子进程 argv/原 rc、开始与结束
时刻、stdout/stderr、完整源字节/模式清单全部保留。证据中的 JSON 只是
原文件逐字节快照，Node 不以该快照替换实际读取路径。不会再从本文生成
运行时词典。旧 keymap 契约夹具只作历史开发证据，不追认为真实词典验收。
主席词典仍在开发、未冻结；整合之后必须在新源/新证据上再验。

正向覆盖三语静态文字/ARIA、组名、三种碰撞状态、空状态、版本/指标、
本地文件/平移/JSON 输入说明与 raw 错误；保留树/按钮 identity、选中项、
pose/mate 草稿、焦点/光标/滚动/禁用态、在途请求、mesh draw 计数及
state/BOM/solver/faces。合法 import/pose/mate/solve/checkpoint/reopen/export
经过真实 JS 事件路径，协议 body、digest 和原工程拒绝门保留。
语言事件不发请求、不 draw、不重建树；销毁解除监听和绑定。

9 个实际 Node 失败负控：未知 key、缺 args、多余 args、未知碰撞 status、
非法 selector、缺服务、包加载失败、移除 locale listener、locale 时重建树。
每个必须原 rc1 且命中准确首因；夹具自证失败原 rc2，不能伪装产品红。
7 个 reader 负控：缺子记录、假 rc、假源 SHA、坏日志、额外证据文件、
重新摘要后的假 terminal 与 completion。均运行实际 reader CLI 收原 rc1，
不修改原证据。日志/源漂移 fail-loud，不以孤立 PASS JSON 代验。

证据根 `/hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/`：
`before-red-v1` 是夹具数字 selector 漏洞，不是产品失败；
修后 `before-red-v2` 在 2026-10-12 13:47:31 +08:00 原 rc1，
旧 labels 覆盖被抓到。`fix-green-v1` 的正向成功，但判因器遗漏
`ERR_ASSERTION` 的详细 message；修后 `fix-green-v2` 原 rc0。
`readback-v1`、`reader-controls-v1` 已原 rc0，但只属于早期契约版。
`real-catalog-green-v1` 在 13:55:41 原 rc0，10 个子进程；
`real-catalog-unittest-v1` 在 13:55:48 原 rc0，独立仓内入口 1 项通过、
无 skip，内部再执行完整 Node 套件。最终提交源码必须用 fresh 证据复核。
此处不继承历史浏览器/all 验收，也不声明 auth、工业全功能或生产部署通过。

复跑命令（out 必须使用新目录，禁止清理旧证）：

```bash
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-assembly-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_assembly_panel_locale.py --selftest --out /hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/next-selftest
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-assembly-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_assembly_panel_locale.py --verify /hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/next-selftest/result.json --out /hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/next-readback
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-assembly-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_assembly_panel_locale.py --controls /hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/next-selftest/result.json --out /hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/next-reader-controls
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-assembly-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_assembly_panel_locale.py --regression --out /hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/next-unittest
```

`--source-root`、`--locale-root`、`--catalog-root` 可绑定整合树；正常默认
为本独立树/foundation/主席词典树。`--regression` 复用 `tools/acceptance.py`
的 exact suite/计数/来源门，运行时显式新建 `/data/tmp/oc-al-<时刻>/`，
证据只落 3FS。源码/证据完整扫描拒绝任务外改动和未知产物。
apply_patch 工具新建测试/文档属主实测 mch，已有 JS 为 infra；未擅改权限
或属主。所有终端命令及测试/证据落盘均经 `sudo -n -u infra -B`。

## 暂停交接 2026-10-12

用户要求本轮仅落盘交接、下个 session 续接。已停止新开发、新测试投递、
提交及生产操作；所有已投递进程均已收取原终态，无残余本席 RUNNING。
没有删除资源、证据、runtime 或未知文件，没有向主树提交或推送。

原任务：装配面板消费统一 locale，移除 defaults/本地 labels，保留 CAD
数据和操作状态。独立树与分支见首节；HEAD 仍为基线
`9c4abdac2906111d49f0c39a437ca4cef7abb9b3`，本席提交 SHA **尚无**。
当前仅三件在制：`web/js/assembly-panel.js` 为 M，
`tests/test_assembly_panel_locale.py` 和本文未跟踪，均在
`oss-alternatives/apps/opencad/` 下，本文除外（根 `docs/design/`）。
JS diff 为 +128/-67；新增件不会出现在未暂存 `git diff --stat` 中。
未暂存任何文件。禁止把独立树的新增件当作已经提交。

当前 JS SHA256：
`3b507fbb0f593a31f94d4454ac8b16c828fc9f6b207b5129b2432eba92c2d7b8`。
测试 SHA256：
`21cdcf2ee91b8d1db7f5cce07b49bd22cc63f4e4cda3d40fbd150aca03855756`。
本文因本次交接再次变更，不复用追加前的文档 SHA。
现读 clean 主席词典提交
`373fe47a041cc33a7ba92e1d7f1257483a69964b`，JSON SHA256
`8cc631501aa8960ef16b2319eebce2158db0425430c0104f31011329abe73f0f`，
与实际 Node 读取摘要一致。40 键无新增，四列表及精确 args 仍在本文。
真实 service SHA256
`83deaad7c93f6bca84750885bfc769106863f401f9101948cb2115fa52fb1f80`。
只从 foundation 读 service，不读其旧 JSON。

本席亲收的最终在飞证据均在上述证据根（时间为北京时间）：

- `precommit-real-v1`：13:57:31，外层 rc0；完整 Node 正向 33391 次断言、
  10 次源码执行、20 selectors，另 9 个独立负控各 rc1。断言含夹具检查。
- `precommit-unittest-v1`：13:57:38，外层 rc0；真实
  `tools/acceptance.py` 发现/执行 1 项，通过 1，skip/failure/error 均 0。
  stdout/stderr 在该根；runtime `/data/tmp/oc-al-055728297047`，SQLite 未调用。
- `precommit-readback-v1`：原 session 41199 收 rc0，13:58:45，
  `verified=10`；核完整 source/log/argv/exit/字节快照。
- `precommit-controls-v1`：原 session 89703 收 rc0，13:59:27，
  `reader_negatives=7`；每个变异副本实际 reader rc1，日志及首因保留。

未完成：最终源码评审、交接追加后的 fresh 完整 source 绑定验收、
仅三件 DCO 提交、主席整合后新验收。本文追加导致旧 full-source
readback 预期报漂移，不能把旧 PASS 追认为本交接版本通过。
没有已观测产品失败未处理；旧夹具/判因失败及修复证据均保留。
服务依赖、私有页加载顺序、浏览器、auth/session/undo/CAS 的真实端到端、
工业全功能、生产均未由 Node 桩证明。本独立树基线没有引入 foundation
文件或词典，运行面依赖主席整合，不在此扩写其他 module。
新增文件属主差异如上，仅如实记录，不擅改权限。

复飞先读本文、适用 AGENTS/skills 并核 Git 状态及主席词典 HEAD；
确认原三件未撞写后，下一条测试命令（**本轮未执行**）：

```bash
sudo -n -u infra -B python3 -B /data/dev/wt-opencad-assembly-locale-r2-20261012/oss-alternatives/apps/opencad/tests/test_assembly_panel_locale.py --selftest --source-root /data/dev/wt-opencad-assembly-locale-r2-20261012 --locale-root /data/dev/wt-opencad-locale-foundation-20261012 --catalog-root /data/dev/wt-opencad-panel-catalog-r2-20261012 --out /hf3fs-jd/data/tmp/opencad-assembly-locale-r2-20261012/resume-real-v1
```

之后按新的同源结果做 readback/controls/regression，检查精确三件 diff、
源码扫描与暂存集合，获准阶段再 DCO 收口，不 push，不改主树或生产。

## Chair Resume 2026-10-12

The continuation session ran the actual composed dependencies (foundation
`97b80e6`, actual catalog `373fe47`) against the still-dirty three-file seat:

```text
producer    /hf3fs-jd/data/tmp/opencad-assembly-locale-chair-fix-20261012-v1
            rc=0, children=10
readback    /hf3fs-jd/data/tmp/opencad-assembly-locale-chair-verify-20261012-v1
            rc=0, verified=10
controls    /hf3fs-jd/data/tmp/opencad-assembly-locale-chair-controls-20261012-v1
            rc=0, reader_negatives=7
regression  /hf3fs-jd/data/tmp/opencad-assembly-locale-chair-regression-20261012-v1
            rc=0, real discovery 1/1, 0 skipped/failed/errors
```

These are the actual received parent exits. The old development/PASS and FAIL
evidence is retained. The same four commands must be rerun after the three
owned files are committed; only then is the commit clean acceptance complete.
No browser, private HTTP/session, industrial parity, production or GPU claim
is made here.
