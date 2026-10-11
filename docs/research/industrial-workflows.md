# Industrial Workflow Evidence

2026-10-12 快照；**NOT_EXECUTED**，不是厂商或 OpenCAD 实测报告。
机器可读的十条完整方案在 `official-sources.json.research_completion.workflows`。
这里提供人读的输入、顺序、输出、数值尺子和采购先决条件。

证据分层：`OFFICIAL_PROCEDURE` 是真实操作文档；`OFFICIAL_DESCRIPTION`
是厂商主体中的功能/转换描述，不能称为完成过的操作。除 `w-interference`
的操作顺序外，其他行是 **RESEARCHER_PROTOCOL**：本席根据已获证功能
制定的待执行方案，参数/容差/数学参考不是厂商承诺。每个方案仍须核实际
版本、已购 role/module、文件格式及命令可用性后才能投递。

## 真正的操作与转换证据

| ID / 官方主体 | 主体支持的 workflow | 未执行与未获证边界 |
| --- | --- | --- |
| `w-interference` / 2025 SOLIDWORKS Help `t_Detecting_Interferences.htm` | 安装版 Tools / Evaluate / Interference Detection，PropertyManager 选组件/选项，Calculate；Results 显示体积/组件，highlight、ignore/un-ignore、Excel 导出。[fact:s-interference-procedure] [fact:s-interference-volume] | 本席只读取内层 Help，未安装/运行软件。不给 xDesign 套命令，不由它推出配合 DoF、残差、孔壁精确算法或大装配性能。 |
| `w-pmi` / Siemens MBD 主体 | Convert to PMI 将 drawing views/objects 转为 model views/PMI；可选 drawing/sheet/view/annotation 范围，有转换 summary report。[fact:n-pmi-convert] | 是 functional workflow 描述，不是逐点击手册；模块许可/标准修订/semantic export 未核。 |
| 参数模板 / CATIA Mechanical 主体 | 官方 gearbox 示例修改 dimensions/ratios、实例化并用模板 checks 检查。[fact:c-knowledge] | 是示例而非命令手册/性能测试，不能计成已运行工业样件。 |
| 钣金展开 / CATIA Mechanical 主体 | 专用 sheet 应用的 unfolded view 与 bend order 描述。[fact:c-unfold] [fact:c-dfm-addons] | 未获证具体展开算法、K-factor 约定或角色购买清单。 |
| 关联图纸/BOM / CATIA Mechanical 主体 | 多视图、尺寸、关联 BOM 描述。[fact:c-drawing-bom] | 两个 SOLIDWORKS BOM 候选均是 Help 错误体，不接受为操作证据。BOM 层级/排除/编号须另读正确手册。 |
| Creative shape / CATIA Creative Design 主体 | sketch/model→subdivision variation；algorithm/visual scripting 用于 shape variants。[fact:c-creative-subdivision] [fact:c-creative-algorithm] | Creative 组合描述，不证明全角色购买、Class-A 连续性或已执行算法优化。 |
| 关联展开 / SOLIDWORKS 官方 xDesign 文章 | folded geometry→2D flat；文章明确 flat 与 folded geometry 保持更新关联。[fact:x-flat-associative] | 功能流程描述，不是 K-factor/容差或逐点击手册；捕获时间 02:13:44 +08:00。 |
| 结构 cut list / 同上 | model edges、2D sketches 或 curves→members→corner/end trim→fabrication cut list。[fact:x-frame-input] [fact:x-cutlist] | 只证 cut list，不升格为全装配 BOM 或数量实测。 |
| 模具 / 同上 | parting lines、shutoff/parting surfaces、splitting；draft/undercut 检查；core/cavity/insert 输出。[fact:x-mold-operations] | 几何功能流程，不是 CAM/post 或生产机器验收。 |
| 图纸与孔标注 / 同上 | Sheet 的 Generate Drawing Views (Beta) 生成视图；hole callout 从 cylindrical geometry 得出。[fact:x-drawing-beta] [fact:x-hole-callout] | 保留文章 Beta 标记；未证当前 GA、标准符合性或几何判定精度，不混入 Siemens PMI converter。 |

## 待执行矩阵

所有数值容差由测试规格明确制定，不借网页宣传补成精度承诺。
每项都要保存输入/版本、真实产物和实际失败，不能仅凭截图或进程终态。

| ID / 域 | 输入与执行次序 | 产物与正控 | 必须看得见的负控 / 购买边界 |
| --- | --- | --- | --- |
| `w-solid` / 参数实体 | 80×40×10 mm，四个直径6通孔；约束草图→拉伸/孔阵列→长度改100→重生成→保存重开 | 初始体积 `32000-360*pi mm3`，四孔和依赖持久，规格容差0.01 mm3 | 自交轮廓/超大孔不能假成功。V5 配置、xDesign included、Design modeling、Designcenter Standard 分别核，不假定通用命令。[fact:v5-mechanical] [fact:x-parametric] [fact:s-standard] [fact:n-standard] |
| `w-surface` / 曲面 | 两个相邻 patch、参考边界；建模→独立量 G0/G1/G2→改边界→round-trip/重开 | 输出采样坐标与位置/切线/曲率最大偏差，位置规格0.01 mm | 人为0.1 mm裂缝/切线断裂必须红。V5/CATIA advanced 与 xDesign subdivision 不等价，Designcenter 此细项未核。[fact:v5-shape] [fact:c-surface] [fact:x-freeform] |
| `w-interference` / 装配 | 两10 mm立方体，X偏移8及12；按上述官方命令算→读对/体积→导出 | 偏移8参考交叠200 mm3；偏移12无交叠；对名/选项/版本/表格完整 | ignored 不能被误报几何清洁。只绑定安装版操作文档，无 DoF/大装配外推。[fact:s-interference-volume] [fact:s-install] |
| `w-pmi` / 图纸PMI | 两页旧drawing、三尺寸、一datum/GDT、3D版本；选页转PMI→读report→全量转→改模型 | model views/PMI对象和summary，所选与全部转换 inventory 可对账 | 未选页不能被悄悄转换；断参考/只有可见标签必须红。Designcenter MBD module许可先核，CATIA公差role/xDesign drawing不是同转换器。[fact:n-pmi-convert] [fact:n-gdt] [fact:c-tolerance-role] [fact:x-drawing] |
| `w-sheet` / 钣金 | 厚1.5、内半径2、90°、本规格K=0.4；折弯→展开/顺序→折回→改厚→重开 | allowance参考 `pi/2*(2+0.4*1.5) mm`，厚度和版本绑定；另核 xDesign flat/folded 关联更新 | 自重叠/矛盾折弯规则不能导出假有效flat。CATIA专用、xDesign included、Design all-tier分开；NX具体命令未核。[fact:c-unfold] [fact:x-sheet] [fact:x-flat-associative] [fact:s-standard] |
| `w-structure` / 结构焊件 | 四成员框架、重复profile不同长度、稳定ID；edges/sketches/curves→成员/trim→改长→核cut list→重开 | 四成员 inventory，裁切长度/ID/数量与几何对账 | 重复ID/缺profile必须红，cut list 不冒充未证的完整BOM。CATIA白车身/复材设计/制造不能用焊件替身算完成。[fact:x-frame-input] [fact:x-cutlist] [fact:c-body-role] [fact:c-composite-design] [fact:c-composite-manufacture] |
| `w-tooling` / 工装CAM | draft cavity、0.8 mm wall、刀具/stock坐标、获准post；DFM→parting/shutoff/splitting与draft/undercut→core/cavity/insert→有许可才NC→离线去料/post核验 | 薄壁/draft记录、mold、获准CAM时toolpath/stock残差与post来源 | 无CAM许可/错post/刀具碰撞不能称生产可用，不操作生产机。V5 CAM组合、CATIA add-on、xDesign仅mold、Design NC概述不同。[fact:c-wall-draft] [fact:v5-cam] [fact:x-mold-operations] [fact:s-nc] |
| `w-cae` / 分析 | 悬臂100×10×1 mm，E=210000 N/mm2，P=1 N，I=10/12 mm4；固定/材料→三网格解→单位/反力/解析核 | `PL^3/(3EI)` 约1.904762 mm，反力平衡、收敛，solver/mesh来源 | 刚体未约束/单位混用必须红。Premium只证线性静力，Designcenter模拟模块另核；不推CFD/非线性。[fact:s-premium] [fact:n-cae] |
| `w-routing` / 电气流体 | 两connector三segment、直径/最小半径/障碍；logical→physical→移端→重生成 | connectivity、半径、segment IDs、总长、cut记录 | 断端/小半径/穿障碍必须红。建筑电气/流体role分开，Premium routing、Designcenter具体模块另核；不含EM/水力验证。[fact:c-electrical-role] [fact:c-fluid-role] [fact:s-premium] [fact:n-routing] |
| `w-revision` / PDM PLM | A/B零件、assembly指A、两测试user/nonmember；存A→改B→赋读改权限→并发/重开 | A不可变、装配参考明确、权限和conflict/recovery记录 | 非成员读取/只读写/陈旧覆盖必须红。必须另获测试账号授权，不能拿生产资源验证；厂商能力描述不是企业安全认证。[fact:x-revise] [fact:c-access] [fact:sc-roles] |

## 关闭条件

本席实际执行的厂商 workflow 数为 **0**。完整源 ledger 校验与负控可以
执行，但那是证据尺子的验收，不是上述十条工业测试通过。

研究尚未关闭：V5明确安装/运行与配置、CATIA全role页及许可、Connected与
当前Design商业映射、全部module合同范围、更多域的操作手册、所有厂商
交互服务器帧路径均未获证。未获证不是不存在，正控规格不是网页能力。
完整终态与原rc/count/source drift应回读新的外置`result.json`，不抄预期数。
