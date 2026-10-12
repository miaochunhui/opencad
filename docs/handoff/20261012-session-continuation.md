# OpenCAD 下一 Session 续作交接

快照日期：2026-10-12，Asia/Shanghai；首轮主席核盘窗口 14:01 至 14:07。
用户明确要求本轮落盘交接、下一 session 继续既定目标。本件是工作交接，
不是新的生产授权、产品全绿证明或工业全功能声明。

## 1. 结论与第一优先级

长期目标未完成、未取消。先修本轮已复现的参数语言绑定泄漏和登录区
翻译错误首因丢失，再整合全部面板语言，冻结新源码跑完整回归与真实
浏览器验收，及时具名提交、GitLab-first 同步三仓；然后继续工业能力、
官方调研、服务器 GPU 性能和私有 Web 生产切换。

不能从第一次仓库迁移、第一次鉴权或第一次配置 UI 重新做起：
这些已有受限范围的实现、验收和三仓发布。不能反向把未合入的语言
改动说成整仓全绿。这里的“整仓”指独立 OpenCAD 的完整登记回归，
不指 infra 中其他产品，不把 skip/NA 当 PASS。

## 2. 用户目标与 Goal 校正

用户要求开源的浏览器 2D/3D CAD 服务，3D 最优先，以 CATIA、
SolidWorks 全功能范围及工业可靠性为对标；兼顾 AutoCAD、NX/UG。
最终用户不安装独立客户端。本地 RTX 3080 可用于服务器渲染，
但浏览器 WebGL、软件 SwiftShader 和服务器 GPU 性能必须分开举证。
主仓放 r640HA GitLab，同时推送 Gitee 和 GitHub `miaochunhui/opencad`。
用户已授权保存当前图纸、重启 OpenCAD，并接受进程内 undo/redo 丢失；
仍必须先核具体目标与恢复路径，不能扩成任意其他服务重启授权。

2026-10-12 本轮 `get_goal` 返回原线程
`01a12460-f98d-7e60-924a-7000acf9ce65`，状态实际为 **paused**，
不是 active，也不是 complete。现有 `update_goal` 只允许 complete/blocked，
不能改 objective 或 resume；本轮没有关闭或假称已激活目标。
下一 session 检查载体，按用户“继续目标”的指令恢复可用目标机制；
若新线程无继承 goal，可依此前明确“使用目标”的授权建立同目标。
恢复自动推进必须以工具实际状态为准，本件不保证跨线程自动唤醒。

原 objective 的首次迁移、首次事务/API 和首次配置 UI 排序已过期。
以本件及 10 月 12 日最新验收交接校正，完整未关闭范围如下。

| 范围 | 当前定位与完成判据 |
| --- | --- |
| 仓库与交付 | 三仓角色和人工显式同步已实现；每笔新提交仍需扫描、实际 push/API 终态及三端 SHA 回读。自动镜像、物理 HA 未验。 |
| 官方竞品调研 | 已有官方主体账本和矩阵，仍 PARTIAL；逐产品/角色/版本/许可/Web 或安装边界、工业域工作流补齐，证据不足不推断缺失。 |
| 鉴权与项目隔离 | 有限私有 Web cookie/ACL/CSRF/注销、跨项目隔离已实现；完整生产身份、配额、滥用控制及真实 TLS/proxy 验收继续做。 |
| 持久事务与恢复 | 已有 expected_revision/SHA、request_id 回执、持久历史及受限崩溃恢复；各新增修改路径须验 CAS、幂等、存储故障原子性和确认后恢复。 |
| 工业参数化 | 带孔板及配置 CRUD/定义 API 闭环已有验收；全下游重建、表达式、拓扑持久引用、任意特征工作流仍需数值、BRep/STEP/mesh 同版本和失败回滚。 |
| 工业曲面/草图 | 已有受限精确曲面、裁剪环及私有导出；完整 NURBS、导引放样、缝合增厚、G0/G1/G2 及完整约束草图仍需逐工作流签收。 |
| 真实装配 | 已有受限零件/位姿/配合及私有 HTTP 流程；任意层级、拾面/轴、DoF/残差、BRep 碰撞、配置/BOM 和工业规模继续验。 |
| 关联工程图 | 已有受限关联视图、剖面/隐藏线及导出；完整标准/GD&T/MBD/BOM、模型联动和 PDF/DXF 独立读回继续验。 |
| 工业域补齐 | 钣金/焊件、工装/CAM、任意 CAD 仿真、电气/管路、企业审批/审计均不能被通用算子或单算例代替，按工艺/物理域/角色验。 |
| 服务器 RTX | 离屏 GPU PNG 及明确 CPU 回退的正确性已有验收；当前源码代表模型、端到端 p95/帧时/显存、并发准入和受控性能收益未完成。 |
| 整合上线 | 新源码完整回归零失败，跳过逐条保留；真实桌面/手机建模、语言与用户切换、存储恢复、生产资产绑定后再发布，保护图纸。 |

每项关闭须真实实现、正负控、源码/模型/产物绑定、独立审核、原进程
退出与现读判词/采集时间。矩阵承诺工作流未逐项签收前，不称
CATIA/SolidWorks 工业全功能；全部判据满足前不标 goal complete。

## 3. 真源、仓库与已验收基线

实际独立仓库 `/data/dev/opencad`，不是当前终端所在 `/data/dev/infra`。
本地 main 为 `1c67344`，现读 behind origin/main 10；有九件原未跟踪资产：
`project_gateway.py`、`secure_web.py`、两件对应测试、project-session CSS、
industrial-panels/project-session JS、private_web_acceptance/browser 工具。
都视为他人资产，不删除、不覆盖、不强行 pull/checkout。
origin 的缓存 ahead/behind 不是本轮新的网络回读。

已发布文档基线：`9c4abdac2906111d49f0c39a437ca4cef7abb9b3`，
干净树 `/data/dev/wt-opencad-definition-ui-release-docs-20261012`。
它在产品 `eb19211` 后只改两份文档，不能当新产品全量测试。
本件从 9c4abda 新建
`/data/dev/wt-opencad-session-handoff-20261012`、
`codex/session-handoff-20261012`，只改本文件，不合入语言代码。

最新完整产品基线：
`eb19211ca7ca4b9304678fe747026009234d1515`，
冻结树 `/data/dev/wt-opencad-definition-ui-integrated-20261012`。
2026-10-12 12:37:33.173004 完成 all：
2520 总项 = 2507 PASS + 13 原 skip，0 failure/error，15 HTTP checks，
六个 child rc0，source/HEAD 前后不变。
本轮读取原 result 的 totals/HEAD/drift；原父退出签收归上轮主席，
不从 JSON actual_rc 倒推本轮亲收。

对应证据都在 `/hf3fs-jd/data/tmp/`：

- `opencad-definition-ui-integrated-all-20261012-v1`：完整 all。
- `opencad-definition-ui-integrated-euler-all-readback-20261012-v1`：五项独立读回。
- `opencad-config-crud-final-browser-20261012-v1`：桌面/390px 共18工作流、28几何测量、514响应、88产物；捕获为 SwiftShader，不是 RTX。
- `opencad-config-crud-final-readback-20261012-v1`：浏览器18项读回。
- `opencad-definition-ui-integrated-three-repo-sync-20261012-v1`：eb19211 三仓发布。
- `opencad-definition-ui-release-docs-three-repo-sync-20261012-v1`：13:23:33.282953 SYNCED/PASS，三仓 main 为 9c4abda。
- `opencad-three-repo-resume-readback-20261012-v2`：13:44:09 至13:44:11 三端仍为9c4abda；PLANNED/PASS、tail=[]、all_three_synced=false，是只读回读，不是又 push。

13 skip：12项需缺失的可选 OCP/OCCT 路径，1项为实际模块存在时无需注入
的夹具 teardown 分支。未删测试、未新加 skip 制造全绿。
历史首因、FAIL 和运行中源码漂移记录全部保留，不追认。
完整证据/签收/摘要在 `docs/handoff/20261012-definition-ui-release.md`；
此前曲面、精确闭环、事务和仓库交接在同目录，按主题读取。

## 4. 六席暂停断点

本轮只复用上下文登记六席收尾，没有新增开发或嵌套派生。
固定八 leader 约定不变；下一 session 先核工具实际容量并披露，
能支持八席才按八个有价值独立写集派发，不冒称512席。
`/data/dev/infra/ops/agent-shared-pool/capabilities.json` 现读 DENY，
adoptions=[]，Flash 默认关闭。六席工具状态均 completed，仅表示
“暂停交接回报完成”，**不表示开发验收完成**。

下表路径都是 `/data/dev/` 下独立 worktree，各自同名 `codex/...` 分支。
非参数席 HEAD 都还是9c4abda，全部新修改未提交；原测试/日志仍留原处。

| 席 / worktree 后缀 | 独占写集与实际断点 |
| --- | --- |
| Heisenberg / `wt-opencad-parametric-locale-lifecycle-20261012` | parametric-panel.js、test_parametric_panel_locale.py、app内 docs/design/parametric-panel-locale.md；HEAD ec8035699f0c3fe2486d3da0f46d63bca629bc83，仅测试 dirty。36项/30 PASS/6 FAIL，rc1，首因 locale_bind_token_leak，产品修复未做。 |
| Lagrange / `wt-opencad-session-locale-r2-20261012` | project-session.js、test_project_session_locale.py、docs/design/project-session-locale.md；30场景/308断言 FAIL，rc1，加载首因被通用 snapshot 错误遮蔽，公开告警保留策略未实现。 |
| Plato / `wt-opencad-surfaces-locale-r2-20261012` | surfaces-panel.js、industrial-panels.js 仅surface段、test_surface_panel_locale.py、docs/design/surface-panel-locale.md；1537正控/11负控开发PASS，reader/9反例PASS；依赖HEAD强绑定、DCO及新浏览器仍欠。 |
| Laplace / `wt-opencad-assembly-locale-r2-20261012` | assembly-panel.js、test_assembly_panel_locale.py、docs/design/assembly-panel-locale.md；Node及9负控、unittest1、reader10记录/7负控已收；新快照、DCO、浏览器未收。 |
| Carson / `wt-opencad-drawing-locale-r2-20261012` | drawing-panel.js、test_drawing_panel_locale.py、docs/design/drawing-panel-locale.md；13:58:32 rc0，1309检查/14场景/4负控，真实373词典；新reader/9反例、DCO、整合浏览器未收。 |
| Planck / `wt-opencad-render-locale-r2-20261012` | render-panel.js、test_render_panel_locale.py、docs/design/render-panel-locale.md；17场景/573检查/6负控、reader/9反例及scan开发PASS；DCO、clean复验、discovery与浏览器未收，没有新GPU实验。 |

席内交接均已回读，文档追加会改变各树指纹，不能沿用追加前PASS验
当前树。各席均已亲收其已知在飞测试终态；主工具 wait也收到六席
completed。未做全机进程扫描，不保证其他线程无在飞作业。

关键原证据与续接：

- 参数红测：`/hf3fs-jd/data/tmp/new-task/parametric-lifecycle-red-20261012-r1/`；13:57:53.873180 rc1。该目录 `HANDOFF-20261012-1401.md` 自足，旧29项全过，新6项红，source_drift=[]。
- Session红测：`/hf3fs-jd/data/tmp/opencad-session-locale-r2-fix-green-20261012-v2/`；13:51:40.988222 rc1。`load-failure.stderr.log` 保留真实首因。
- Surface：`opencad-surfaces-locale-r2-20261012-{fix-green,reader,reader-controls}-v2`。
- Assembly：`opencad-assembly-locale-r2-20261012/precommit-{real,unittest,readback,controls}-v1`。
- Drawing：`opencad-drawing-locale-r2-20261012-frozen-catalog-v1`。
- Render：`opencad-render-locale-r2-20261012/{fix-green-dev,dev-readback,dev-controls,source-scan}-v2`。

## 5. 语言整合依赖与具体未修项

共享基础服务：`/data/dev/wt-opencad-locale-foundation-20261012`，
提交 `97b80e675f39e23faffcb8c0f6bb8706a8238f0b`。
唯一实际词典树：`/data/dev/wt-opencad-panel-catalog-r2-20261012`，
clean HEAD `373fe47a041cc33a7ba92e1d7f1257483a69964b`；
父链是97 -> `505ab8f85b24844c2e97084240d645d5fc739e67` ->373。
505是ec80356参数修改在97上的整合版本，不再同时 cherry-pick ec和505。
373只做实际消费者词典匹配与对应测试/文档；本轮现读六测试PASS，
这不签收参数生命周期、全站语言或浏览器。
基础另有合并顺序相反的606分支，禁止不审重复合入两个基础版本。

四件私有页接线仍未提交：
`/data/dev/wt-opencad-private-locale-page-20261012` 的secure_web.py、
web/js/private-locale.js、test_private_locale_page.py、
docs/design/private-locale-page.md。它的旧18测试是Node/显式夹具，
不等于真实基础服务/词典/用户恢复一体化验收；原旧树不覆盖。

第一项修复：参数 translatePanel 每次 bind 丢 release，需先解绑再即时
paint；ready/mount/service getter 拒绝要保留原对象、可观察首因。
加强 token_failure 先成功bind再失败的反例，不能只在后续循环测到泄漏。
第二项修复：session localeService 先报 session_locale_snapshot_invalid，
遮住 snapshot.error.code=locale_package_http_error；保留首因与key坐标。
第三项：shieldWorkspace 不得隐藏安全公开locale诊断，也不得让任意
role=alert泄露私人数据。参数交接中的专用旧attribute与session新建议
尚未统一；**不要把提议当已实现接口**。主席协调定义唯一严格契约：
body直挂叶子output、有限属性/固定安全code、禁止child/原detail，
正控含异步添加/锁定可见，负控含伪role/私人detail/未知属性/非叶子。
私有页、参数、基础告警与session分别改自己的文件，不跨写或宽豁免。

随后真实词典匹配所有key/args，缺键/参数失配fail-loud；语言事件只改
文案/ARIA，不重挂面板、重算、重复POST、改变身份/epoch/草稿/焦点/
revision/SHA/disabled/屏蔽状态。保留原server error、用户输入、型号/
单位/ID，不把原始协议数据强行翻译。语言本名选项“日本語”不是混用。
三语浏览器验收必须覆盖已有模型、未提交草稿、失败状态、恢复/注销/
换账号、2D/3D和桌面/手机；不以无假名或Node测试替代自然语言/生产验收。

## 6. 下个 Session 开工命令

先读适用AGENTS、memory-router和本件，核目标、Git与独占写集。
以下只读命令立即可跑，不重投已知红测：

```bash
sudo -n -u infra cat /data/dev/opencad/AGENTS.md
sudo -n -u infra git -C /data/dev/opencad status --short --branch
sudo -n -u infra git -C /data/dev/wt-opencad-panel-catalog-r2-20261012 status --short --branch
sudo -n -u infra cat /hf3fs-jd/data/tmp/opencad-session-locale-r2-fix-green-20261012-v2/load-failure.stderr.log
sudo -n -u infra git -C /data/dev/wt-opencad-parametric-locale-lifecycle-20261012 diff -- oss-alternatives/apps/opencad/tests/test_parametric_panel_locale.py
```

按本轮新首因修产品和严格告警后，在各独占树复跑其文档中的producer、
reader、实际reader反例与scan，收原parent rc，三件/四件具名DCO提交。
主席另建整合树，不直接改冻结373、eb19211、原local main或旧dirty树；
cherry-pick先审顺序和exact diff，保留session/industrial-panels接线边界。
新集成tree运行 `python3 -B tools/acceptance.py --stage all --out NEW_3FS_ROOT`，
以及真实浏览器/语言/私有安全、各工业模型数值回归和独立源码闭包读回。
NEW_3FS_ROOT必须新建；SQLite runtime走新短 `/data/tmp/oc-*` 本地目录，
不能用3FS TMPDIR绕过ProjectStore文件系统限制。此参数示意不是本轮运行。

可先离线复核已有产品基线，命令本轮未重复执行：

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-definition-ui-integrated-20261012/tools/gateway_publication_review.py --source-root /data/dev/wt-opencad-definition-ui-integrated-20261012 --commit eb19211ca7ca4b9304678fe747026009234d1515 --all-evidence /hf3fs-jd/data/tmp/opencad-definition-ui-integrated-all-20261012-v1 --baseline-evidence /hf3fs-jd/data/tmp/opencad-definition-integrated-all-20261012-v1 --publication-evidence /hf3fs-jd/data/tmp/opencad-definition-ui-integrated-three-repo-sync-20261012-v1 --out /hf3fs-jd/data/tmp/opencad-next-session-baseline-readback-20261012-v1
```

目录已存在就用新版本，不删除旧证据、假写parent_rc或改测试判词。

## 7. 三仓同步、生产与恢复边界

固定main：GitLab `ssh://git@192.168.1.181/infra/opencad.git` 主仓，
Gitee `git@gitee.com:sdfwefqaa/opencad.git`，GitHub `miaochunhui/opencad`。
GitLab用原严格host-key与既有infra SSH身份；GitHub经典PAT来源仅
`/home/mch2/.passwd_github_miaochunhui`，不读入prompt、输出、URL、argv、
配置或Git。旧凭据权限问题不在本轮修改，不能顺手换key/改known_hosts。
Gitee历史master保留，当前开发main；人工显式同步不是已安装自动镜像。

既有 `tools/repository_sync.py` 有plan/scan/sync。
GitHub HTTPS故障时已审 `tools/github_api_sync.py` 是显式精确对象传输，
不是自动fallback；plan先于sync，最多8个普通线性提交，每提交32个
常规文件、单文件1MiB，force=false。GitLab/Gitee先成功再更新GitHub，
失败保留partial，不盲重试、force或并发两个同步器。
本件后续实际提交/同步状态以Git和新证据为准，不预写“已push”。

2026-10-12 本轮systemd只读：
oss-opencad-web.service active/running，PID1605277，
启动2026-10-11 00:02:11 CST，ExecStart仍为infra旧server.py，
端口8766、out-root=/var/lib/oss-web/opencad。
所以独立仓9c4abda发布不等于opencad.ai4everyone.top已切新私有服务。
本轮没发生产HTTP写请求、没重启、没修改部署；
进程存在不等于线上功能/私有生产安全PASS，本轮公网内容未重验。
切换前重新检查当前图纸、保存和事务恢复，不复用旧空图纸SHA作保证。
TLS/proxy、共同备份/恢复、撤销配额、HA与工业规模按各design和handoff续作。

官方研究读取 `docs/research/competitive-matrix.md`、
official-sources.json、industrial-workflows.md及对应verify工具。
本轮只回读已有文档，没有重新联网调研；最新矩阵仍PARTIAL，
厂商工业workflow全部NOT_EXECUTED、parity NOT_ASSESSED。
官方主体事实不等于购买含所有role，也不定位交互服务器帧架构。
研究ownership门固定原研究树，不为整合绿而放宽或清未知文件。
GPU续接优先读最新private-render、render性能/准入工具与原证据，
不能照旧server-rendering.md的“未整合”历史段重做已经接通的有限链路；
当前源码性能和跨进程准入仍需重新实测，不继承旧小模型收益。

恢复：只对本任务具名提交新建git revert，再正常正向同步；
不reset/宽checkout、不清未知worktree/数据，不裸复制在线SQLite而漏WAL。
OpenCAD重启授权不等于可中断GitLab维护；GitLab故障先用原入口只读
核服务/HTTP/认证/refs，操作需要具体恢复授权和范围。
本件不是常驻自动复飞或发布守卫；跨session连续性仍需目标载体和
下一席实际读取/执行。所有未收项由下一session主席继续协调验收到终态。
