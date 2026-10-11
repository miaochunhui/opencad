# 工程图席权限误调用审计

## 结论与授权边界

事件为 **OPEN_UNATTRIBUTED**，不是 PASS 或已恢复。全 tmp 权限修复误调用、
root 身份、脚本实际写语义已经确证；逐路径成功改动全集和原权限不可核清。
本轮没有补权限、写探针、改 ACL、修改产品/主树/生产或执行恢复。
主树部署暂停由主席掌握，工程图功能测试通过不能替代事件关闭。

本轮仅新增 `tools/drawing_permission_audit.py` 和本文件；随后获准的私有
工程图 reviewer 使用另一独占工具/文档，不与事件处置混算。

## 误用命令与实际目标

在 `/data/dev/infra` 执行：

```bash
sudo -n -u infra sudo -n bash ops/3fs-storage/fix-3fs-perms.sh /hf3fs-jd/data/tmp --check
```

脚本 `ops/3fs-storage/fix-3fs-perms.sh` SHA256：
`1b4e735b8ba06a031d6352462a9f369202182c02e5aa64319de2e25c6699a19c`。
第17行只识别首参数 `--check`，所以本调用 `CHECK=0`，
参数集合为 `["/hf3fs-jd/data/tmp", "--check"]`。第二项是否作为相对
目录继续处理没有执行记录，不能假说已到达；其词法目标是 cwd 下的 `--check`。
第一项目标当前解析为 `/3fs/research3fs/data/tmp`，device 78、
inode 632925。目标根当前2777、ctime为2026-09-18T10:58:21Z，
不是本次改动的证明，也不重建历史挂载/目录入口。

第25行先对非精确2777目录执行 `chmod 2777`，第26行再对非精确666
普通文件执行 `chmod 666`。随后统计、为十个账号执行 `mktemp`/`rm`
探针。第14行只有 `set -u`，没有 `set -e`，find/chmod错误被丢弃。
**正确首位 `--check` 也不是纯只读**，本审计不执行它。
目录特殊位、访问/删除权、ACL mask以及路径被并发替换后的实际对象
不能仅凭当前 mode 推断。不能用猜测的2775/755等模式递归恢复。

## 原始时间线

以下为2026-10-11 UTC，北京时间加8小时。记录时间分别为工具调用或回执，
不是逐路径 syscall 时间；原 exec session 为48011。

| UTC | 事实 |
| --- | --- |
| 02:41:27.726 | 误用命令发出 |
| 02:42:51.222 | pgrep显示3928762仍为带chmod的目录find |
| 02:43:10.047 | 该find当时无可见子进程，pgrep rc1 |
| 02:43:18.880 | 向3928756/3928762请求STOP |
| 02:43:22.372 | STOP命令rc0 |
| 02:43:38.424 | 再查find子进程，rc1 |
| 02:43:50.939 | shell/find KILL命令rc0 |
| 02:44:17.124 | 两个sudo父进程KILL命令rc0 |
| 02:44:58.181 | 又调用首位check，仅目标专属证据目录 |
| 02:45:02.709 | 该check rc1：非2777目录1、非666文件0，仅infra探针成功 |
| 02:45:20.686 | 又调用修复模式，仅目标专属证据目录 |
| 02:45:25.257 | 该调用rc0，聚合报告非2777/666均0、十账号探针成功 |
| 02:46:12.860 | ps确认原四PID全部不存在，rc1 |

原进程链：3928190 → 3928744 → 3928756 → 3928762。
没有终止另一check链538179/538181/538182。最后观测与STOP之间存在空窗，
**文件阶段只可称未观测到，不能称绝未发生**。后续两次调用也纳入事件：
专属目标 `/hf3fs-jd/data/tmp/opencad-drawing-web-20261011` 当前2777，
ctime 02:45:24Z，与第二次root修复日志一致；没有逐路径原mode或探针清单。

独立系统证据 `/var/log/auth.log`：
683753（北京时间10:41:28，USER=root、PWD及误命令）；
685446（10:43:20 STOP）；685981（10:43:50 KILL）；
686952（10:45:00专属check）；687218（10:45:24专属修复）。
均只读获取精确匹配行，未转存整份认证日志或凭据。
`/var/log/audit`当前不存在；journal存在，但本轮没有证明其留有历史chmod
syscall。用户身份直接读取auth.log被拒，获准身份链只读取上述行。

## 当前候选集合与覆盖

证据父目录：
`/hf3fs-jd/data/tmp/opencad-drawing-web-20261011/`。

第一轮 `permission-audit-scan-20261011T0317Z/result.json`：
03:14:21.763254Z开始，03:17:22.605499Z结束，rc0只代表审计完成。
读33,602目录/417,209 entries，有670条2777/ctime候选、43条其他mode
同期记录、198读取错误、103,727个待处理目录，4,744 symlink未跟随。
文件未stat，全tmp覆盖INCOMPLETE。670条均在3fs-mon族，
43条不得算chmod成功；新建/并发写也能改变ctime。

限定轮 `permission-audit-scoped-20261011T0340Z/result.json`：
实际03:24:18.655393Z开始、03:24:57.002562Z结束。目录名仅是标签，不是时间证据。
只扫3fs-mon族，读完当前11,933目录、27,909文件、39,952 entries，
111 symlink未跟随，零错误、零待处理，38.170903秒。
找到10,823条现2777且ctime位于保守窗口的目录候选，
其ctime为02:41:33Z至02:42:48Z；没有666文件/其他文件mode同期候选。
窗口按3FS秒级ctime向外取整为02:41:27Z至02:43:51Z。
当前遍历完成不等于历史路径覆盖完整、内容完整性验证或成功chmod证明。
两轮集合重叠，**不能相加**。

完整限定轮候选清单 `metadata-candidates.jsonl` SHA256：
`e889ebfa6338b05d3cc5b4a7e7b933ca123fae0c526ad8cc06ec3f127d223915`。
相对 `/3fs/research3fs/data/tmp/3fs-mon-repair-20261010` 的分组：

| 分组 | 候选目录 |
| --- | ---: |
| 根自身 | 1 |
| accept | 42 |
| api | 2 |
| apitests | 27 |
| auth | 1 |
| auth-r2 | 1 |
| browser-r2 | 968 |
| capacity | 1 |
| captests | 19 |
| census | 2 |
| censustests | 67 |
| dashboard-write-r2 | 133 |
| deploy | 20 |
| docs-r2 | 547 |
| errors-r2 | 1641 |
| frozen-commit-r3 | 20 |
| frozen-r3 | 173 |
| history-r2 | 125 |
| installer-safety-r2 | 322 |
| integrate-r2 | 63 |
| lifecycle-r2 | 99 |
| natural-r3 | 4683 |
| refresh-r2 | 30 |
| release-r2 | 1836 |

首轮候选清单SHA256：
`f72ed9bace6d862e02bf8c6e8d6d2e033d613b46a64939884c4ecefd054b426e`。
首轮 `unscanned-directories.jsonl` SHA256：
`d69cc7c5f66cf5a93582f03afe6e8eba9b2fcd6a09b0b6536156c1c8fa97d937`。
每个待处理项的后代未枚举，198错误路径另在首轮result，不是零影响。
原global ps截断仅暴露4条release-r2/.git/objects路径片段，
缺pid/完整argv，工具标为unattributed，不用其推全影响。

## 不可核清范围与下一步

- 没有操作前逐路径mode/ACL快照、成功chmod syscall日志或完整stderr。
  成功改变路径数为null，不是0，也不能以10,823候选冒充。
- 全tmp未扫部分、读取失败项、事件后删除/改名/再次chmod对象不可排除。
  symlink/挂载及并发路径替换可能改变历史作用范围，不承诺源树完全未受影响。
- 专属目录两次后续调用的探针创建/删除集合不完整；后来的正常测试写入
  也改变ctime。未采前态，不做猜测回退。
- 未发现可用权限前态清单；所见shared-index.before是Git内容而非权限。
  这不是“所有位置绝无备份”的证明。由主席协调各候选族owner提供可验前态
  或收取明确风险处置决定，禁止本席接管其测试/沙箱/权限。
- 脚本参数解析及check写探针尚未结构性修复；本轮无该脚本写权。
  未来只读检查不使用此helper，修脚本及部署放行另需独立授权和验收。

旧外置 `permission-check-incident.md` 保留原文及SHA
`6fabad2bbe71072cd5b09ece586521a8e0e63fa8a7eda86e8ea841ee492b01c1`。
其10:44:03为事后记时，不是事件开始；“check首位只查不改”建议不安全，
“目录阶段仍在”只能绑定最后观测，不能排除未观测文件阶段。本文件订正，
不重写原历史证据。

## 审计工具验收与入口

限定轮自检9/9通过，无skip/errors/failures，源/脚本/旧note零漂移。
自检不代表事故关闭。工具只读源/目标元数据，只新增外置证据；
遍历使用锚定dirfd及O_NOFOLLOW，不执行helper或权限setter。
首次限定轮启动曾因新增process_rows缩进错误rc1退出，未创建该轮out、
未开始扫描；修该行后才完成上述限定轮。原工具回执保留。

只读查看已落盘摘要，不再投扫描：

```bash
sudo -n -u infra python3 -B /data/dev/wt-opencad-drawings-20261011/tools/drawing_permission_audit.py --read-result /hf3fs-jd/data/tmp/opencad-drawing-web-20261011/permission-audit-scoped-20261011T0340Z/result.json
```

终判为 `AUDIT_RECORDED_NOT_INCIDENT_CLOSED / OPEN_UNATTRIBUTED`。
本轮没有推送、权限恢复或生产操作。
