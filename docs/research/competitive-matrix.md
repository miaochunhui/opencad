# Official CAD Competitive Matrix

研究快照：2026-10-12；状态 **PARTIAL**，industrial parity **NOT_ASSESSED**。
基线 `941005d7c45a35f8cbbe8d2dc65c68093a790a3b`。
本轮逐项事实见 `official-sources.json.research_completion`；每条均有官方
canonical URL、主体 anchor、范围及描述/操作证据级别。独立验证结果提供
实际字节、SHA256、原采集时间及解码后的 block 索引。历史采集没有改成今天重抓。

本页不把无证据称为“缺失”，也不把产品系列覆盖称为一个席位全部包含。
`NOT_VERIFIED` 是证据不足；`NOT_EXECUTED` 是本席没有实际运行厂商软件。
详细输入、步骤、产物、正负控及购买先决条件见 `industrial-workflows.md`。

## 运行与购买边界

“浏览器原生”仅表示主体明确 browser/web-based；不定位几何计算或交互光栅化。
“连接桌面”是安装客户端加平台服务；SaaS、云部署、网页启动器并不自动等于
浏览器 CAD。“可选串流”是官方部署描述，不是服务器逐帧路径的实测。
以下全部产品的 interactive server-frame topology 都为 **NOT_VERIFIED**。

| 产品 / 角色 | 真实主体支持的运行方式 | 购买边界与未核范围 |
| --- | --- | --- |
| CATIA V5 | 本轮页面没有明确安装/浏览器运行说明，NOT_VERIFIED；Connected Engineering 与转用 3DEXPERIENCE CATIA 分开介绍。[fact:v5-connect] | 模块化 Part/Assembly/Drafting/Surfacing，配置清单与实际许可未核。V5 Web-based Learning 是学习，不是 CAD 浏览器执行。[fact:v5-modules] |
| 3DEXPERIENCE CATIA online Creative Design 组合 | 主体明确 browser、无需安装/配置，仅绑定 Creative 组合。[fact:c-creative-browser] | subdivision 与 algorithm/visual scripting 描述；具体 included role/app 与报价未核，不能替代 Mechanical/V5 或全部 role。[fact:c-creative-subdivision] [fact:c-creative-algorithm] |
| 3DEXPERIENCE CATIA Mechanical Design & Engineering | 云平台机械组合的客户端方式 NOT_VERIFIED，不用 cloud URL 推断。[fact:c-dfm-addons] | 专用应用和 add-on 分开，不能全装进一个 role。[fact:c-role-packaging] |
| CATIA 3D Innovator | 明确 web-based 设计/协作。[fact:c-innovator-web] | 独立 role，具体 app/SKU/报价未核。[fact:c-role-packaging] |
| CATIA 3D Shape Designer | 明确 Web browser Applications，设计/自由形状/图案。[fact:c-shape-web] | 只适用于这张 role 卡，不推及全 CATIA。 |
| CATIA 3D Tolerancing & Annotation Designer | NOT_VERIFIED | 独立 MBD/公差 role；标准修订、语义交换与许可未核。[fact:c-tolerance-role] |
| CATIA Airframe Composites Designer / Manufacturing Engineer | 各自 NOT_VERIFIED | 设计与可制造性分别是不同角色，不合并许可。[fact:c-composite-design] [fact:c-composite-manufacture] |
| CATIA Airframe Fastener Engineer / Body Structure Designer | 各自 NOT_VERIFIED | 紧固装配与白车身结构分别有角色描述，不等同泛用 frame 工具。[fact:c-fastener-role] [fact:c-body-role] |
| CATIA Building and Civil 3D Electrical Engineer / Fluid Engineer | 各自 NOT_VERIFIED | 建筑土木电气与流体各有角色；不是全行业电气/流体全集。[fact:c-electrical-role] [fact:c-fluid-role] |
| SOLIDWORKS Design Standard | 安装客户端；2026 Installation Manager 及 Windows 先决条件。[fact:s-install] | 本次 named-user store offer：零件/装配/图纸和焊件/模具/钣金表格；NC 编程描述不证明具体 CAM 模块、轴数或 post。[fact:s-standard] [fact:s-nc] |
| SOLIDWORKS Design Professional | 同上安装客户端 | Standard 加智能库、渲染/动画、成本和 CAD 标准检查；不是所有仿真模块。[fact:s-professional] |
| SOLIDWORKS Design Premium | 同上安装客户端 | Professional 加电气/管线、线性静力、时间运动、曲面展平；非线性/CFD/full Simulation inclusion 未核。[fact:s-premium] |
| SOLIDWORKS Design with Cloud Services | 本地安装、license-key desktop，与 xDesign 分离。[fact:x-desktop-separation] | 不把 named-user store 的所有条目套到这个 machine-license 产品；安装以已购产品为准。[fact:s-purchased] |
| SOLIDWORKS Connected | 2025 精确安装主题明确 native app、Launcher background service、Windows System Tray。[fact:sc-installed] | 管理员赋 role 后获得 named-user 许可，角色决定应用访问。旧 Connected 与当前 Design SKU 商业映射仍未核，不直接画等号。[fact:sc-roles] |
| SOLIDWORKS xDesign | 明确全浏览器、无需安装。[fact:x-browser] | 季/年单用户同特征 offer；官方产品经理文章描述原分售能力整合，与 store 交叉核对，不合并整个 SOLIDWORKS 系列。本机原生 SW 文件仍是 multi-CAD 协作与有限特征编辑，不是完整原生 feature tree 等价。[fact:x-purchase] [fact:x-integrated-offer] [fact:x-native-files] |
| xDesign 内的 3D Render / xStudio | 云计算照片级 job 描述，不用于判定交互 viewport | FAQ 有包含的 role、135 credits 及额外购买；不将 job 计费推成服务器帧架构。[fact:x-render] |
| Designcenter X Essentials | 明确轻量浏览器 CAD/CAM/CAE。[fact:n-essentials] | essential tier，不推成 Standard/Advanced/Premium；该描述没有证明完整工业工作流。 |
| Designcenter X Standard | X MCAD 正文明确仍是 desktop application，可选 streaming。[fact:n-desktop] | 典型零件/装配；额外模块与 VBL 核心席先决条件分开。[fact:n-standard] [fact:n-tokens] |
| Designcenter X Advanced | 同上，不把 X 品牌整体扁平化为 Essentials Web | 卡片列模板/验证/渲染/3D annotation；具体附加模块仍需许可核验。[fact:n-advanced] |
| Designcenter X Premium | 同上 | 卡片包括 Standard/Advanced 与 flexible PCB 集成；不是全部 add-on 自动包含。[fact:n-premium] |
| Designcenter on-premise Standard/Advanced/Premium | 部署页分 on-premise、AWS/Azure streamed service、X SaaS；精确安装先决条件未核。[fact:n-deployment] | perpetual/subscription/rental 是选择，不是每档全部模块的报价。[fact:n-purchase] |
| Designcenter X VBL | 附加许可机制，不是另一种 Web runtime | Standard/Advanced/Premium core 加共享 token 预算，使用不得超过总量，退出释放。[fact:n-tokens] [fact:n-token-concurrency] |

CATIA 页面宣传 173 results，但本轮只读静态主体中的 20 张可见卡。
上述角色仅为被检查的子集，不能声称已研究“每个 role”。未逐页、未购许可、
未读所有应用手册。页面标题中的版本广告与历史 NX 更名文本均没有被用于
推测具体发布日、服务可用性或服务器架构。

## 工业域矩阵

这里的“能力”仅指官方主体描述；表格不声称 OpenCAD 已具备或厂商案例已执行。
每个单元使用特定产品/组合，不能拼出一个虚构的全包商业席位。

| 工业域 | CATIA V5 / 3DEXPERIENCE CATIA | SOLIDWORKS Design / xDesign | NX / Designcenter | 可验证收口 |
| --- | --- | --- | --- | --- |
| 实体/参数 | V5 solid/hybrid；机械知识模板。[fact:v5-mechanical] [fact:c-knowledge] | xDesign 参数零件/装配；Design 模型表格。[fact:x-parametric] [fact:s-standard] | Standard 典型零件/装配。[fact:n-standard] | `w-solid`，参数变更、体积与重开；模板只是描述示例。 |
| 曲面/造型 | V5 shape 与 advanced surfaces；Shape Designer Web role；Creative 组合 subdivision/visual scripting。[fact:v5-shape] [fact:c-surface] [fact:c-shape-web] [fact:c-creative-subdivision] [fact:c-creative-algorithm] | xDesign subdivision 与 Premium flattening 是不同范围。[fact:x-freeform] [fact:s-premium] | 本轮详细曲面工具主体未取证，NOT_VERIFIED | `w-surface`，位置/切线/曲率数值，不以渲染平滑冒充连续性。 |
| 装配/运动/干涉 | 机械运动与紧固角色描述。[fact:c-motion] [fact:c-fastener-role] | 安装版干涉检查操作原文，xDesign 仅参数装配描述。[fact:s-interference-procedure] [fact:x-parametric] | Standard 装配描述。[fact:n-standard] | `w-interference`，交叠体积/组件名/导出；DoF、残差、大装配另需证据。 |
| 图纸/PMI/GD&T/BOM | 关联多视图/尺寸/BOM、3D annotations 与独立公差角色。[fact:c-drawing-bom] [fact:c-pmi] [fact:c-tolerance-role] | xDesign drawings/MBD、几何 hole callout，文章中的 Generate Drawing Views 明标 Beta，不推为当前 GA；Design 图纸，BOM 猜测 Help 不收。[fact:x-drawing] [fact:x-hole-callout] [fact:x-drawing-beta] [fact:s-standard] | GDT 与 Convert to PMI/报告。[fact:n-gdt] [fact:n-pmi-convert] | `w-pmi`，语义对象/转换范围/报告；未核标准一致性。 |
| 钣金 | 专用 app 的展开和折弯顺序。[fact:c-unfold] [fact:c-dfm-addons] | xDesign folded geometry 与 2D flat 的关联描述；Design all-tier 描述。[fact:x-sheet] [fact:x-flat-associative] [fact:s-standard] | 本轮钣金具体特征/档位未核，NOT_VERIFIED | `w-sheet`，明确厚度/半径/展平约定，坏折弯负控。 |
| 结构/焊件/复材 | 白车身、复材设计/制造角色分开。[fact:c-body-role] [fact:c-composite-design] [fact:c-composite-manufacture] | xDesign edges/sketches/curves→members→trim→cut list；Design 焊件。[fact:x-frame-input] [fact:x-cutlist] [fact:s-standard] | 本轮详细结构工具未核，NOT_VERIFIED | `w-structure`，成员/裁切/ID/cut list；cut list 不等于完整装配 BOM，复材不被焊件替代。 |
| 工装/模具/CAM | 模块化 V5 CNC；DFM wall/draft 不等于已认证 NC。[fact:v5-cam] [fact:c-wall-draft] | xDesign parting/shutoff/splitting、draft/undercut 与 core/cavity/insert；Design NC 简介不证明 post。[fact:x-mold-operations] [fact:s-nc] | Essentials “essential CAM”不证明机器/post 全范围。[fact:n-essentials] | `w-tooling`，几何/刀具/stock/post 独立购买与离线核验。 |
| CAE | add-on 分析/专用仿真，不是一个机械角色全包。[fact:c-dfm-addons] | Premium 线性静力/时间运动，不外推 CFD/非线性。[fact:s-premium] | CAD-integrated simulation 与 Essentials CAE 简介。[fact:n-cae] [fact:n-essentials] | `w-cae`，单位、解析解、反力、网格收敛、刚体负控。 |
| 电气/流体/机电 | V5 systems；建筑电气/流体是特定角色。[fact:v5-system] [fact:c-electrical-role] [fact:c-fluid-role] | Premium 电缆/管道；不是全电磁/水力仿真。[fact:s-premium] | routing 信息链及 Premium flexible PCB。[fact:n-routing] [fact:n-premium] | `w-routing`，连接性/半径/障碍物；具体模块许可先核。 |
| PDM/PLM/协作 | 部件级读写描述，不是生产权限验收。[fact:c-access] | xDesign revision 与 Connected role access。[fact:x-revise] [fact:sc-roles] | X 由 Siemens 管理 backups/updates，企业事务未测。[fact:n-desktop] | `w-revision`，版本绑定/并发/权限/恢复，必须另获测试账号授权。 |

## 证据归位与复验

本轮证据根：
`/hf3fs-jd/data/tmp/opencad-official-research-complete-20261012-v1`。
`official/` 是新公开 HTML；`inspect-*/article.json` 为解析主体；`fetch-logs/`
保留原传输终态。旧两轮源与失败仍留原目录，JSON 原字段语义不变。

旧 CATIA family 与 Design store 的 budget lock 是 **历史拒绝记录**，不再
作为本轮有效资源事实。本轮 01:41:43 +08:00 现读未见持锁者，之后本席新
部署/V5取件实际完成；这不授权抢锁或清理其他资源。原拒绝 rc=2 保留。
首次空账本验证 `ledger-empty-v1/result.json` 原 rc=1 也保留。

本轮所有 `web.run` 搜索/open 的可见工具返回均为空，没有可引用 source IDs。
本席先前错误“恢复”汇报均已撤回，不入 facts；续接搜索/open 仍为空。
主席提供的 URL 只作定位，不能移植其工具引用。fallback 现读 download
规则后才使用现有 approved recipe，单次串行、1500k、tries=1，不安装、
不改凭据/权限/生产、不清理远端。Hash 是真实捕获的完整正文及传输绑定，
不是厂商签名的校验源；源由官方 HTTPS URL、原日志与语义主体共同核验。

三个 HTTP 成功的 Help 非主体/错误主体单独保留：Connected 猜测 2026
路径缺 article，两个 BOM 猜测主题不存在。另有主席给的 CATIA V5 Success
Path：02:14:29 +08:00 捕获 41569 bytes，传输 rc=0，02:15:35 +08:00
主体检查 rc=1（无 main/article），原正文及失败保留，没有新增 V5 运行事实。
`rc=0` 没有升格为有效事实。新增 Creative 与 xDesign 官方文章分别捕获于
02:12:29 / 02:13:44 +08:00，每 fact 均由正文 block 复核，不取发布日期广告。
旧 Onshape/AutoCAD Web/FreeCAD/OCCT 源仍可在原 ledger 查到，本轮未重新
核其运行边界，也没有用重复市场介绍计为新增 workflow。

独立复验（`--out` 每次使用新目录）。默认 ledger 模式的 ownership 门只允许
原研究树 `/data/dev/wt-opencad-research-complete-20261012`，继续按固定基线
核独占写集，不提供放宽或绕过参数。整合树/其他 worktree 会明确 FAIL；
不能将此模式当通用发布门，也不能为消除 FAIL 清理他人的 untracked 文件。
`--selftest` 可在独立树运行，但不认证该树符合默认 ownership 门。

```bash
sudo -n -u infra python3 -B tools/official_research_verify.py --selftest --out /hf3fs-jd/data/tmp/opencad-official-research-complete-20261012-v1/selftest-next
sudo -n -u infra python3 -B tools/official_research_verify.py --out /hf3fs-jd/data/tmp/opencad-official-research-complete-20261012-v1/ledger-next
```

工具核正文/anchor、捕获时刻、日志/bytes/SHA、引用闭包、十域、正负控、
历史账本不变、独占写集与全工作树前后指纹。不执行网络、购买或厂商 CAD。
anchor 存在与 fact ID 闭包只是字节/引用一致性，不证明整条 statement 的
语义蕴涵，也不自动证明 role/license、runtime、版本与流程归属；仍须人工
逐项审核。删空 remaining 或选择 COMPLETE 不能替代完整研究验收。
`--selftest` 单独输出四件 owned 源的完整 before/after SHA、工具 SHA、
Git HEAD/绑定首尾及 drift；未读件保留 null 与异常，漂移或异常必须 FAIL。
已完成控制及首因不会被后续证据读取失败覆盖，输出目录使用独占新建，
原失败不重写。绑定负控仅改本次新外置夹具的字节，保留预期 FAIL 结果；
原 33 controls 不删减。CLI 原退出仍需调用方亲收，JSON 的 actual_rc
不是独立进程签收，源码绑定也不是厂商正文签名或语义/产品认证。
正文筛选优先单一内容 `article`，多卡产品页保留 `main`；排除导航 role、
breadcrumb 与文章 footer。旧 `inspect-*/article.json` 原样保留；其外围
文字不能采事实，新的 ledger 在筛选后的主体重新给出 anchor block 索引。
`selftest-body-boundary-before/result.json` 原 rc=1、25/29，四个正文边界
负控揭露修前缺口；它不是网络失败或产品失败，后续修复不改原结果。
首次修复把多卡页面误判为单篇文章：`ledger-supplement-precommit-v1`
原 rc=1，首因 `catia-roles-readback:wrong-subject`，实读14源/70facts/10方案，
drift=[]；`selftest-supplement-v1` 原 rc=1、30/31，同样保留。
随后修正单篇/多卡判别并新增多卡与 role 卡负控，不删事实迁就筛选器。
校验 PASS 只等于账本一致；研究仍 PARTIAL，所有工业 workflows
NOT_EXECUTED，OpenCAD full parity NOT_ASSESSED。该工具未接常驻发布门，
不能声称已有全流程结构性保证。
