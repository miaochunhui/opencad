"""cadcore —— 开源 CAD 替代品的**几何/文档内核**（L1 真源 + 内核 + 族算子 + 出图）。

设计一句话
----------
**L1 文本（`document_to_l1` 的 JSON）是唯一真源**，内存形态、图纸实体、DXF/SVG 产物
全部由它派生；任何"操作报成功但真源没变 / 产物没变"的调用都属于缺陷（B6）。

分层（一层一类文件，读侧口径只许有**一处**）::

    cadcore.docmodel        —— L1 文档模型：字段三分类（冻结字段 / 自家扩展键 / 未知字段保留）
    cadcore.kernel.*        —— 几何内核（occt / nano / subproc），投影与质量属性
    cadcore.sketch.*        —— 草图实体与约束求解
    cadcore.ops.*           —— 族算子（装配 / 出图 / 标注 / 组织 / 精度 …），每个 op 落 L1
    cadcore.ops.features.*  —— 功能片（补族模块缺口的那一格，与族模块共用同一套公开 API）
    cadcore.draw.*          —— 图纸装配（视图落位、尺寸、标题栏）与实体流
    cadcore.io.*            —— DXF / DWG / STEP 读写
    cadcore.i18n.*          —— 语言包与标准档

为什么本文件必须在（**不是**可有可无的装饰）
--------------------------------------------
本目录此前**没有** `__init__.py`，于是 `cadcore` 是一个**命名空间包**：`__path__` 由 `sys.path`
里所有含 `cadcore/` 的条目按顺序拼成。而本仓多处会在 import 期往 `sys.path` 插条目
（`ops/output.py`、`io/dxf_bridge.py`、`ops/hatch_measure_extra.py` 等，用于"直接跑脚本"的自举），
一旦 `packages/` 排到了 `packages/cadcore/` 前面，`cadcore.__path__[0]` 就会变成**项目目录**
（`packages/cadcore/`，里面有 `tests/`、`README.md`，**没有** `cadcore/docmodel.py`）——
`import cadcore` 仍能靠第二段路径侥幸成功，但任何按 `__path__[0]` 定位包内文件的读侧
（缺陷尺子、静态消费者扫描、打包器）都会读空并**静默报 0**。

放上本文件后 `cadcore` 成为**常规包**：Python 的查找器一旦在某个 `sys.path` 条目上找到带
`__init__.py` 的 `cadcore/`，立即采用它、不再拼命名空间段 ⇒ `__path__` 恒等于本包目录、
与 `sys.path` 顺序无关。这也与同目录下 `dxf` / `engine` / `notes` / `sheets` 四个兄弟包一致
（它们都有 `__init__.py`，只有 `cadcore` 与 `mathcore` 漏了）。

本文件**只放包级元信息，不 import 任何子模块** —— 保持 `import cadcore` 轻量且不成环。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
