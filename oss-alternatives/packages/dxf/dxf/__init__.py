"""dxf —— CAD（AutoCAD 平替）第一步的**最小可用内核**：DXF R12 ASCII 的解析 + 序列化 + 索引。

设计一句话
----------
**磁盘上的 `.dxf` 是唯一真源，`packages/vault` 只当索引与检索底座**；
本包的第一属性是**字节保真**：读进来再写出去，未修改的文档**逐字节等于输入**，
看不懂的实体 / XDATA / 代理对象**一字不动地带回去**。

四层（一层一个文件，互不越界）::

    dxf.model   —— Line/Tag/Entity/Section/Table/Layer/Document（数据模型，纯标准库）
    dxf.reader  —— 文件 → Document（坏输入抛带行号的 DxfError，绝不静默截断）
    dxf.writer  —— Document → 文件（未改 ⇒ 逐字节相同；改了 ⇒ 只重写被改的那些行）
    dxf.index   —— 图纸 → vault（一张图纸一个会话，一个实体一条消息；幂等 + 墓碑）

为什么这是第一步（依据）
------------------------
`oss-alternatives/docs/research/cad-prestudy.md` §6：真正该在第一版就做对的是
「**看不懂的实体原样保留**」—— 判据与 `vault` 的 A1/A2（逐字节 round-trip、二次导入幂等）
**同构**，可直接复用同一套尺子。本包不含 DWG 读写、不含几何求交、不含约束求解
（见 `oss-alternatives/docs/design/dxf-format-notes.md` 的"已知限制"）。

底座接口只准用 `oss-alternatives/docs/design/impl-interfaces.md` §2/§3 冻结的那些，
本包**不修改** `packages/vault/` 下任何文件（逐字节未改动有 sha256 对照为证）。

用法（任何目录都能跑，包内自行处理 `sys.path`）::

    from dxf.reader import read
    from dxf.writer import write
    from dxf import index as dxf_index
    from freechat_vault.store import Vault

    document = read("/tmp/demo-dxf/plan-a.dxf")
    write(document, "/tmp/out.dxf")                     # 未改 ⇒ 与输入逐字节相同

    with Vault("/tmp/dxf-vault") as vault:
        print(dxf_index.index_drawings(vault, "/tmp/demo-dxf").as_dict())
        print(dxf_index.layers(vault))

本模块用 PEP 562 的惰性 `__getattr__` 导出常用符号：`import dxf` 不会拖起整个包，
也不会与 `dxf.index` 的 `sys.path` 自举形成 import 环。
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "DxfError",
    "Document",
    "Section",
    "Entity",
    "Tag",
    "Line",
    "Table",
    "Layer",
    "read",
    "write",
    "dumps",
    "index_drawings",
    "scan_drawings",
    "load_drawings",
    "entities",
    "layers",
    "stats",
]

#: 导出名 → (子模块, 该模块里的属性名)
_LAZY = {
    "DxfError": ("dxf.model", "DxfError"),
    "Document": ("dxf.model", "Document"),
    "Section": ("dxf.model", "Section"),
    "Entity": ("dxf.model", "Entity"),
    "Tag": ("dxf.model", "Tag"),
    "Line": ("dxf.model", "Line"),
    "Table": ("dxf.model", "Table"),
    "Layer": ("dxf.model", "Layer"),
    "read": ("dxf.reader", "read"),
    "write": ("dxf.writer", "write"),
    "dumps": ("dxf.writer", "dumps"),
    "index_drawings": ("dxf.index", "index_drawings"),
    "scan_drawings": ("dxf.index", "scan_drawings"),
    "load_drawings": ("dxf.index", "load_drawings"),
    "entities": ("dxf.index", "entities"),
    "layers": ("dxf.index", "layers"),
    "stats": ("dxf.index", "stats"),
}


def __getattr__(name: str) -> Any:
    """PEP 562：按需把 `dxf.<name>` 解析成对应子模块里的对象。"""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError("module %r has no attribute %r" % (__name__, name))
    import importlib

    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value          # 缓存，下一次不再走这里
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY))
