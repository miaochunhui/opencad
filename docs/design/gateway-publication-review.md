# Gateway Publication Review

独立只读发布审计，写集仅本页、`tools/gateway_publication_review.py` 与
`tools/test_gateway_publication_review.py`。
不运行产品、不部署、不读取PAT/secret/配置内容，不fetch/push，不改权限、
凭据或生产，不安装、派生、换路由、自动重试或清理。只有本次fresh 3FS
证据根允许写入；未接常驻发布门。

## 验收判据

1. all原证据：完整manifest摘要及首尾source SHA/空drift；受管计划全体原child rc
   和日志末行、逐日志SHA、完整counts JSON、unittest footer、runtime
   origin。测试数不使用历史常数：静态读取冻结测试源码的类/继承及已支持的
   `load_tests` 选择规则，不import、不执行产品；discovered身份与原日志逐项
   start/唯一terminal身份双向相等，再核discovered/counts/footer/totals。
   多行测试说明或stdout仍须有唯一terminal；重复/遗漏/错名拒绝。
   HTTP数由冻结server的具名check声明与实际aggregate marker/结果相等；
   原HTTP日志未记录逐项成功结果，明确不声称逐项body核验。
   skips逐件标签/测试名/原因与实际baseline集合相等，无固定13常数。
2. 当前源/提交：完整文件bytes/SHA/Git mode双向差集为空，干净HEAD等于全SHA；
   独立读取Git tree/blob，重算blob对象ID及全部文件bytes/SHA/mode。不能将未
   提交的产品源绑定旧HEAD。Git只读设 `GIT_OPTIONAL_LOCKS=0`。
3. 发布记录：SYNCED/PASS、三角色全SHA/时刻与具名readback文件一致，
   GitLab/Gitee原查询rc/stdout与main相符，GitHub GET HTTP200；
   api-plan/history/partial/api-partial与commands逐件对账，独立核可达
   对象数、history原child rc、Git线性父链尾、两次push准确目标/原rc、
   primary-first查询及API写顺序、HTTP退出状态、唯一main PATCH与
   最终commit GET。日志未保存HTTP payload/响应主体，不声称已核其字节。
4. 本席独占三件development ownership与口令形态扫描，前后hash/HEAD
   状态相同；此门不修改原research四件guard，也不拿研究证据scope替代
   开发写集。证据输出不得与输入根重叠。
5. 可选真实补验：固定三端main，GitLab/Gitee用既有严格SSH identity，
   GitHub仅公开API GET；原main行/公开响应主体、hash及逐端时刻外置。
   每次请求30秒预算；失败立即保留，不改路由。是顺序快照，不是全局原子。

源/输入/HEAD/工具漂移为FAIL。空输入、PLANNED、旧SHA、网页壳、进程
存在或单独rc0不能签收。旧all与sync父进程原rc未保存在这些输入中，工具
明确null/NOT_RECORDED，不由PASS倒推；新版all有actual_rc时按实录核验。
主席亲收rc与本工具原rc分开报告。
不宣称部署、TLS/proxy、恢复、权限全覆盖、工业parity、大装配或GPU性能。

## 动态闭包与文档后继

`--source-root/--commit` 是发布候选；`--all-source-root/--all-commit`
独立指定实际all的冻结源，默认等于发布候选。先核all原source全集与冻结
Git bytes/mode，再核发布候选自身Git bytes/mode，不将executor新HEAD
或新工具的自测计入产品测试数。

不同HEAD只支持线性纯文档后继，必须逐次声明 `--allow-doc-path` 的确切
`docs/… .md` 路径（无glob）。声明集合必须等于真实bytes/mode变化集合，
每个中间commit也只能改这些路径；文档不能删除或改可执行mode。产品改动、
增删文件、mode变异、产品先改再revert均拒绝。报告保留all_commit和
publication_commit，文档后继 `whole_source_all_accepted=false`，不虚接
整树all PASS。

当前文档树已继续提交时，可用 `--publication-commit` 指定原sync记录的目标，
必须位于已验证的all冻结点/文档线性尾。当前HEAD仍严格pin/clean/bytes/mode
核验；报告明确 `current_source_head_published=false`，不将新文档HEAD
宣称已发布。该参数不能绑定产品后继或任意Git分支。

静态发现只支持可审阅的当前源码形态：普通TestCase声明/继承、unittest
suite包装、具名TestCase/addTests及模块字面量None条件，以及真实UI的
all/browser构造器。未知动态hook/filter/条件必须FAIL，不能根据观察到
的日志反向裁剪发现集合。UI外委测试只沿原Web日志明确marker中的路径读取
result/tests.log，核原source、时刻、逐项身份/footer/terminal后并入Web，
不猜目录、不把同一结果重复计入。

自测只变异内存输入与本次新建 `TemporaryDirectory` 再生夹具；其cleanup
仅删除该夹具，不删除未知资源。所有原3FS FAIL/log/result保留不改。
本工具仍是离线consumer，不是常驻发布闸或可信远端payload公证。

## SQLite 六步契约

不接受报告自选子集。静态解析且hash绑定冻结 `tools/acceptance.py` 中all
命令构造与实际执行循环：旧入口的五child与带SQLiteRuntimeGuard的新入口
六child分别要求完整有序集合；新SQLite负控必须在bootstrap前。未知构造、
多余child、遗漏child、缩选argv均拒绝。原计划及acceptance源SHA随结果保存。

新版要求source_before/after完整相等，source_head/status首尾pin且clean、
原actual_rc0。每child必须有唯一 `INDEPENDENT_RUNTIME_BINDING`，
与step.sqlite_runtime及counts内嵌runtime逐字一致；TMPDIR/tempfile/root
同指短本地runtime，rejected为空。sqlite_opens与实际路径集合非空/零
相互一致，cadcore/DXF不得无观测；每个数据库与WAL/SHM/journal路径在
runtime内、无symlink，原文件系统与现存祖先stat相等。可能已被夹具清理的
数据库不要求仍存在，不伪称重新打开数据库或拦截原生外部writer。
旧入口缺少这些字段时明确sqlite_runtime_guarded=false，不补造证据。
任何未知INDEPENDENT marker或旧报告夹带新版runtime marker都拒绝。
SQLite负控自身的嵌套INDEPENDENT_STEP按受管test源码A.child调用集合和
对应原environment.log核完整marker/原rc/counts/环境/runtime，不直接
忽略、不能缺失或重复，也不重复计入产品totals。

自测中的完整六child fixture仅证明reviewer闭包，不是新产品all PASS；
4b3fb4b的真实六child all另待原终态和授权现读，不继承83e计数。

## 边界与保留失败

主席冻结树 `/data/dev/wt-opencad-gateway-release-20261012`，
真实HEAD `67e0562c5137fa39defc2a0773ddf155dac573d6`，只读不编辑。
本席publication树从研究提交 `3098a7502bbcbc94e9a22ee7ff8b23d1dba9d793`
独立建立，分支 `codex/publication-review-20261012`。研究树原两件未
跟踪publication文件原样保留，不删除或修改research ownership guard。
主席research v2的额外两文件write-scope FAIL不归因于官方事实错误。

原证据统一位于 `/hf3fs-jd/data/tmp/`：
`opencad-gateway-release-all-20261012-v1`、原skip基线
`opencad-private-release-all-20261012-v1`、
`opencad-gateway-release-sync-20261012-v1`。
主席plan-v1在2026-10-12 02:37:22 +08:00因填错SHA被
HEAD_NOT_PINNED拒绝，before mutation；原失败不覆盖。

本席首轮审计器 `opencad-gateway-publication-review-20261012-v1/result.json`
于2026-10-12 02:41:31.216141 +08:00原rc1/checks0：
`SKIP_LOG_BINDING:cadcore`。修前研究树工具305-307行只识单行；
原cadcore.log 364-365及1921-1922行是unittest测试名/说明分行格式，
不是产品bug。修后精确绑定测试名、可选说明行、原reason，并加单行/
分行正控、错名/错reason/跨测试/重复负控；不改日志或原失败结果。

## 复跑入口

`--out` 每次必须fresh目录。下面仅回读已执行阶段，不重新投递all/sync。

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-publication-review-20261012/tools/gateway_publication_review.py --source-root /data/dev/wt-opencad-gateway-release-20261012 --commit 67e0562c5137fa39defc2a0773ddf155dac573d6 --all-evidence /hf3fs-jd/data/tmp/opencad-gateway-release-all-20261012-v1 --baseline-evidence /hf3fs-jd/data/tmp/opencad-private-release-all-20261012-v1 --publication-evidence /hf3fs-jd/data/tmp/opencad-gateway-release-sync-20261012-v1 --selftest --remote-readback --out /hf3fs-jd/data/tmp/opencad-gateway-publication-review-20261012-next
```

结果为 `result.json`、`review.log`、`selftest.json`；启用live再有
`remote-readback.json`、两端main原行及GitHub公开响应主体。
正控使用真实all/sync，负控仅内存变异；不修改原证据、夹具或未知资源。
未运行的边界不能从这次复核继承。

## 已实测回读

离线v2于2026-10-12 02:53:10.599326 +08:00原rc0/PASS，
5项主检查、34/34正负控。live v3于02:54:17.585235 +08:00原rc0/PASS，
6项主检查、34/34；独立GitLab 02:54:16.268366/Gitee
02:54:16.932233原rc均0，GitHub 02:54:17.480610公开GET200。
三端均为 `67e0562c5137fa39defc2a0773ddf155dac573d6`，原行/主体
分别留在v3 `gitlab-main.txt`、`gitee-main.txt`、`github-main.json`，
`remote-readback.json`逐端绑定hash/时刻。

五步原child rc均0，实际2341 tests/2328 passed/13原skip/15 HTTP、
零failure/error；388件当前源/Git tree/all的完整manifest摘要均为
`54df095506689528920ba31439f75aa177b9b2c8507a466df5f5cc72222c0f6b`。
sync 183条命令、24次API写记录（3 commit POST、1 main PATCH）、
801对象/3 exact tail与primary-first对账。all/source/input/HEAD/tool
无漂移。此为实读原证据，不是产品复跑；v2/v3对应当时工具hash。
主席sync父进程原rc0由主席亲收，本工具输入中未保存该进程rc。

v4于2026-10-12 02:56:16.889230 +08:00原rc0/PASS，6项主检查、
36/36正负控（新增primary-first及exact commit负控）；两件口令形态
命中0，source/input/reviewer drift均为空。v4不重复网络或产品测试。

## 单提交尾链自测修复

2026-10-12 09:11:41 +08:00原receipt回读rc1/SELFTEST_FAILED：
`opencad-config-repair-release-receipt-final-readback-20261012-v1/selftest.json`
仅`transport-tail-order`失败，35项通过。反转单元素`[d0a2c009…]`没有
改变输入，首因不是同步失败；真实publication/transport正控仍通过。
原FAIL、同步账与8d/7a/d0冻结树保留不改。

完整transport自测对多tail反转、单tail改变一个合法SHA字符；执行前必须
证明变异非空，继续要求`TRANSPORT_EXACT_TAIL`拒绝，不绕过selftest。
尾链校验提取为`review_transport_tail`单真源，完整transport仍必调用；
另以已存在的真实Git单提交/双提交线性链核正控、错SHA与双tail倒序负控。
这些仅为离线consumer控制项，不是再次push、远端查询或产品all运行。

## 本轮保留失败

旧数值审计器在
`/hf3fs-jd/data/tmp/opencad-surface-private-release-all-fermat-review-20261012-v1/result.json`
于2026-10-12 07:37:49.780011 +08:00原rc1：
`TOTALS_MISMATCH`，旧工具333行。原result SHA为
`e79e4345b4d7ea5c546820e4c6d9167837fb0d4e87a46700e74155f9577a1d0a`。
这是旧2341/2328硬编码拒绝新真实2396/2383，不能将原FAIL补成PASS。

修复开发自测v1于07:47:33.837981 +08:00原rc1，13测试、1failure/
3errors，共同首因为Web静态发现误计 `HTTP_SOURCE=None` 下未装载的
SidecarHTTPTests。诊断证据
`opencad-dynamic-release-closure-inventory-diagnosis-20261012-v1` 原rc1，
明确两项missing；修复按源码load_tests字面量条件选择，不硬编码排除名称。
修后自测v2于07:53:07.161826 +08:00原rc0，15测试、28负控，三种drift
为空；后续负控补充后的数字必须从对应新result现读，不继承v2。

本轮publication-v1于07:55:30.610878 +08:00原rc1 `HEAD_NOT_PINNED`：
当前文档树已由889推进到具名docs后继bec25d3，不能以旧SHA假pin。原FAIL
保留；后续回读使用现读bec HEAD与明确889 publication_commit分绑定。

SQLite兼容自测v5于08:00:59.646611 +08:00原rc1：旧HTTP argv使用
producer.resolve后的3FS挂载别名，计划未resolve而误拒；修计划取值为同义
真实路径。v6于08:02:32.016111 +08:00原rc1，新增六步夹具缺CAD origin；
v7于08:04:18.945650 +08:00原rc1，夹具文件系统常数与现读stat不同。
三次开发失败保留，不归因于产品；只修夹具/计划语义，不削弱原判据。

自测v8于08:05:36.171529 +08:00原rc0，20tests/52negative controls，
零failure/error/skip、输入/源/SQLite参考源/工具drift为空：
`/hf3fs-jd/data/tmp/opencad-dynamic-release-closure-selftest-20261012-v8/result.json`。
六步fixture、实际dbf178受管stage plan及路径负控属于reviewer自测，
不计作工业产品test，不签收当前仍RUNNING的4b3fb4b all。

新增嵌套子进程对账后的selftest-v9于08:09:08.039828 +08:00原rc0，
21tests/58negative controls，零failure/error/skip，四类drift为空：
`/hf3fs-jd/data/tmp/opencad-dynamic-release-closure-selftest-20261012-v9/result.json`。
本轮all-v2审计于08:07:21.239374 +08:00原rc0/6checks/22selftests；
publication-v3于08:07:22.937770 +08:00原rc0/7checks/36selftests，
是修改嵌套marker逻辑前的离线证据，仍按当时executor hash绑定。
所有FAIL和开发证据原样保留；最终提交后的复核以新外置result为准。

4b3fb4b真实all于08:10:37.190994 +08:00 FAIL/actual_rc1，
2394tests/2312passed/13skip/1failure/68errors/15HTTP，Web失败后未执行DXF。
原result SHA为
`60707e4850e130dfadf2974e6acd6b65940a54958231f5b106b31e83c97d93bd`。
实际reviewer CLI负控在
`/hf3fs-jd/data/tmp/opencad-dynamic-release-closure-config-fail-20261012-v1/result.json`
于08:12:56.755578 +08:00原rc1/ALL_NOT_PASS，不能改记PASS。
source-bound selftest-v10于08:13:09.770312 +08:00原rc0，
22tests/60negative controls，绑定该原FAIL的HEAD、全集、5实际log和原rc；
即使仅伪改顶层PASS/删除error，也因六步计划缺DXF拒绝。证据：
`/hf3fs-jd/data/tmp/opencad-dynamic-release-closure-selftest-20261012-v10/result.json`。
新完整六步真实PASS仍未取得，需Lovelace修复夹具后另收新冻结账；
本次负控不重跑产品、不删除/改写原失败。

本轮冻结all是83e66d1、all-v2原result完成于北京时间07:04:34，
2396tests/2383passed/13skip/15HTTP。889e885后继仅
`docs/handoff/20261012-surface-private-release.md`；这些数值是该输入
实例，不是工具未来阈值。下列入口只回读原件，不运行all/sync或live网络：

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-dynamic-release-closure-20261012/tools/gateway_publication_review.py --source-root /data/dev/wt-opencad-surface-private-release-20261012 --commit 83e66d1c98918a5197c71e6bf30436513ba06efc --all-evidence /hf3fs-jd/data/tmp/opencad-surface-private-release-all-20261012-v2 --baseline-evidence /hf3fs-jd/data/tmp/opencad-surface-private-release-all-20261012-v1 --selftest --out /hf3fs-jd/data/tmp/opencad-dynamic-release-closure-all-20261012-next
sudo -n -u infra python3 -B /data/dev/wt-opencad-dynamic-release-closure-20261012/tools/gateway_publication_review.py --source-root /data/dev/wt-opencad-surface-release-docs-20261012 --commit bec25d3828b493347070f2e3943117eaef090ccc --all-source-root /data/dev/wt-opencad-surface-private-release-20261012 --all-commit 83e66d1c98918a5197c71e6bf30436513ba06efc --allow-doc-path docs/handoff/20261012-surface-private-release.md --all-evidence /hf3fs-jd/data/tmp/opencad-surface-private-release-all-20261012-v2 --baseline-evidence /hf3fs-jd/data/tmp/opencad-surface-private-release-all-20261012-v1 --publication-evidence /hf3fs-jd/data/tmp/opencad-surface-private-release-docs-sync-20261012-v1 --publication-commit 889e885a4a3935ee593b9e18afef04faff60e294 --selftest --out /hf3fs-jd/data/tmp/opencad-dynamic-release-closure-publication-20261012-next
sudo -n -u infra python3 -B /data/dev/wt-opencad-dynamic-release-closure-20261012/tools/test_gateway_publication_review.py --source-root /data/dev/wt-opencad-surface-private-release-20261012 --expect-head 83e66d1c98918a5197c71e6bf30436513ba06efc --sqlite-source-root /data/dev/wt-opencad-sqlite-runtime-isolation-20261012 --failed-source-root /data/dev/wt-opencad-config-integration-20261012 --failed-all-evidence /hf3fs-jd/data/tmp/opencad-config-integration-all-20261012-v1 --all-evidence /hf3fs-jd/data/tmp/opencad-surface-private-release-all-20261012-v2 --baseline-evidence /hf3fs-jd/data/tmp/opencad-surface-private-release-all-20261012-v1 --original-failure /hf3fs-jd/data/tmp/opencad-surface-private-release-all-fermat-review-20261012-v1/result.json --out /hf3fs-jd/data/tmp/opencad-dynamic-release-closure-selftest-20261012-next
```
