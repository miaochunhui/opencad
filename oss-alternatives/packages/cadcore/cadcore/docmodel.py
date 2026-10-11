#!/usr/bin/env python3
"""A5 L1 文档模型：文本真源 + 迁移链 + 不变量 + 纯函数式重建。

L1（`document_to_l1` 产出的规范化 JSON 文本）是**唯一权威**：二进制缓存、内核形状、图纸投影
全都可以删光重建，重建的输入只有这份文本。因此本模块的四条硬口径是：

1. **同一文档两次序列化逐字节相同**：键序排序（`sort_keys`）、缩进固定、换行固定 `"\\n"`、
   `ensure_ascii=False` + UTF-8、浮点只允许有限值且 `-0.0` 归一为 `0.0`、禁止非字符串键
   （避免 `json` 把 int 键静默转成 str 而改变文档形状）。
2. **浮点不是真源**：长度一律整数纳米（`*_nm` 与 `geom` 的坐标/半径/直径），浮点只允许出现在
   角度（`*_deg`）这类"非长度"字段；小数毫米进文档前必须过 `units.mm_to_nm()`。
3. **引用一律 `(uuid, sub_shape_name)`**：禁止下标引用（`0` / `[0]` 形），因为草图和特征的顺序会变，
   位置型引用会在编辑后静默指向别的几何。
4. **禁静默丢字段**：`migrate()` 单调升版、**禁跳版**（版本链上任何一步缺失即报错，不"猜一个"），
   未知字段**原样保留**并记进 `diagnostics`（机器可读条目，随文档一起落盘）。

重建（`recompute`）是纯函数：不改入参、不写盘；内核缺席抛出的 `DependencyMissing` **必须被捕获成
diagnostics 条目**并把 `ok` 置 `False`（"没算"与"没失败"绝不同形，见 AGENTS.md E2.3）。

自检入口（文件自带，可直接跑）：

    python3 oss-alternatives/packages/cadcore/cadcore/docmodel.py --selftest
    python3 oss-alternatives/packages/cadcore/cadcore/docmodel.py --hash <l1.json>   # 跨进程同一输入同一 sha256

中文只出现在 docstring 里：`tools/cad/i18n_gate.py` 把"非 docstring 的含汉字字符串常量"计为
`hardcoded_literals`，面向用户的文案一律走语言包（`cadcore.i18n.catalog`）。
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import sys
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

if __package__ not in (None, ""):  # 正常路径：作为包模块导入
    from .errors import CadError, DependencyMissing, DocumentError
    from .i18n.keys import HAN_RE, KEY_RE, is_text_key
    from .sketch.constraints import nm_in_range
    from .units import unit_scale_nm
else:  # pragma: no cover - 以脚本方式直接运行（自带自检入口）时补包上下文
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from cadcore.errors import CadError, DependencyMissing, DocumentError
    from cadcore.i18n.keys import HAN_RE, KEY_RE, is_text_key
    from cadcore.sketch.constraints import nm_in_range
    from cadcore.units import unit_scale_nm

__all__ = [
    "SCHEMA_VERSION", "APP_VERSION", "DEFAULT_UNIT", "DEFAULT_STANDARD_PROFILE", "DEFAULT_TITLE_KEY",
    "DIAGNOSTICS_KEY", "Document", "RebuildResult",
    "new_document", "document_to_l1", "document_from_l1", "migrate",
    "save_l1", "load_l1", "check_invariants", "recompute", "selftest",
    "LOAD_BLOCKING_PREFIXES", "load_blocking_problems", "range_problems", "text_encoding_problems",
]

#: L1 真源当前版本。**只增不改**：任何破坏兼容的改动都必须走 `_MIGRATIONS` 的新一步。
SCHEMA_VERSION = 1

#: `meta.app_version` 的缺省值（写进新文档；读取时不校验，方便老文件打开）。
APP_VERSION = "0.1.0"

DEFAULT_UNIT = "mm"
DEFAULT_STANDARD_PROFILE = "iso"
DEFAULT_TITLE_KEY = "doc.untitled"

#: 迁移/读取诊断在 L1 文本里的顶层键（**空即不写**，保证干净文档逐字节稳定）。
DIAGNOSTICS_KEY = "diagnostics"

#: **L1 冻结字段**（有 `Document` 槽位的那些）：不在此列的一律**原样保留**并记
#: `unknown_field_preserved` —— 但"不在此列"要减掉下面的自家扩展键，见 `PRODUCT_EXTENSION_KEYS`。
_KNOWN_TOP_LEVEL = frozenset({
    "schema_version", "meta", "unit", "standard_profile", "projection_angle",
    "sketches", "features", "annotations", "sheets", DIAGNOSTICS_KEY,
})

#: 本仓**自家扩展键**登记表：**节点类型 → 该节点上由本仓写侧写出的扩展键**。**唯一真源**。
#:
#: 为什么需要这张表：`extra`（文档级）与 `sk["extra"]`（草图级）同时承担两件事 —— ①保留外来文件的
#: 未知字段；②承载本仓各族模块的扩展表（图层表/块表/插入记录、装配表、出图账、精度表…，各族模块
#: docstring 写明落点就在 `extra`）。读侧审计若不认后者，**自家 save 出去的文档 open 回来就凭空多出
#: `unknown_field_preserved`**：L1 文本不再逐字节稳定（`save → open → save` 字节漂移），那条伪诊断
#: 还会被写回文件里越积越多。这是 B6 缺陷 RC4，与 RC3（`geom.points`）同类：
#: **写侧发得出去的形态，读侧必须认下来**。
#:
#: 字段三分类，别再混：
#:   ① L1 冻结字段      → `_KNOWN_TOP_LEVEL` / `_NODE_FIELDS` 的字面量（有 `Document` 槽位）；
#:   ② 自家扩展键        → 本表（住 `extra` 或扩展节点里，是随 L1 往返存活的**文档数据**）；
#:   ③ 真未知外来字段    → 原样保留 + 记 `unknown_field_preserved`（`x_` 前缀仍按厂商扩展豁免）。
#:
#: ⚠️ `root` 一档与 `_KNOWN_TOP_LEVEL` **必须不相交**：`to_payload()` 只回写"不在 `_KNOWN_TOP_LEVEL`"
#: 的 `extra` 键，而 `Document` 没有这些键的槽位 ⇒ 把一个根级扩展键塞进 `_KNOWN_TOP_LEVEL` 会让它
#: **静默丢数据**。这条由 `tests/test_roundtrip_extension_namespaces.py` 现证（不靠人记得）。
#:
#: 维护规则（**每条都要有真生产者**）：新增一条时，同步在上面的测试 `PRODUCERS` 里加一条真生产者探针
#: ——否则"登记了但写侧根本写不出来"（幽灵条目）与"写侧写了但没登记"（漏登记）都没人看得见。
#: 本表**只登记有生产者探针的键**；没有探针的族是显式欠账，不靠猜着填。
PRODUCT_EXTENSION_KEYS: dict[str, frozenset[str]] = {
    # 文档根（`Document.extra[...]`）——各族模块的记账/设置表落点
    "root": frozenset({
        # 图层表 / 块表 / 插入记录 / 组 / 外部参照（`ops/organization.py` + `ops/features/organization__*.py`）
        "organization",
        # 图档级"当前层"设置（`ops/organization.py: current_layer`）
        "current_layer",
        # 出图账：plot_jobs / plotters / style_tables / publications / exports（`ops/output.py`）
        "output",
        # 出图扩展账：样式映射 / 导出清单（`ops/output_extra.py`）
        "output_extra",
        # 装配表：components / mates（`ops/sw_assembly.py`）
        "sw_assembly",
        # 精度表：正交/极轴/捕捉/栅格模式（`ops/precision_ops.py`）
        "precision",
        # 极轴捕捉结果表（`ops/features/precision_ops__w26.py`）
        "precision_polar_snap",
        # 坐标输入历史（`ops/features/precision_ops__w28.py`）
        "coord_input",
        # 撤销栈（`ops/edit_ops.py` 的 `HISTORY_KEY`；文档形态落 `extra`）
        "edit_ops_history",
        # 标注样式族（`ops/annotation.py`）的六张表：样式定义 / 样式替代 / 文字编辑 /
        # 文字样式 / 引线样式 / 表格样式。写侧落在 L1 文档**根级**（`ops.annotation._doc_bucket`），
        # 读侧不认 ⇒ 自家 save→open 凭空多出 `unknown_field_preserved`，且同一条伪诊断会逐轮
        # 写回文件里越积越多（B6 缺陷 RC-S4，与 RC4/RC3 同类：**写侧发得出去的形态，读侧必须认下来**）。
        "dim_styles",
        "dim_overrides",
        "dim_edits",
        "text_styles",
        "mleader_styles",
        "table_styles",
    }),
    # 草图节点：族模块对草图形态用草图顶层键，`sk["extra"]` 是草图级扩展容器
    "sketch": frozenset({
        # 草图级扩展容器本身（`ops/draw_ops_extra.py` 等把表挂在 `sk["extra"][<ns>]`）
        "extra",
        "organization",
        "current_layer",
        "precision",
        # 栅格显示记录（与 `doc.meta["precision_grid"]` 同键；草图形态落草图顶层）
        "precision_grid",
        "precision_polar_snap",
        # 填充/测量表（`ops/hatch_measure.py: _HM_KEY`）
        "hatch_measure",
        # 基准/阵列表（`ops/sw_pattern_refgeom.py`，草图 dict 形态）
        "sw_pattern_refgeom",
        # 撤销栈（草图形态落草图顶层 `HISTORY_KEY`）
        "edit_ops_history",
    }),
    # `meta` 节点：`ops/precision_ops.py` 的**公开契约**栅格显示记录（`GRID_DISPLAY_KEY`）
    # + `sketch/session.py` 的草图会话状态（当前草图 / 基准面；`SESSION_KEY`）
    "meta": frozenset({"precision_grid", "sketch_session"}),
    # `sheet` 节点：`ops/output.py: layout_create` 的布局记录 —— 与 `draw/sheet.py` 的图纸记录
    # 同容器、不同形态：写侧两种形态都发得出去，读侧两种都要认
    "sheet": frozenset({"name", "kind", "active", "tilemode"}),
    # Public sw_features producers; round-trip probes cover every registered key.
    "feature": frozenset({"sketch_uuid"}),
    "params": frozenset({"end_condition", "merge", "profile_min_dim_nm",
                          "edited", "edit_count", "edit_log", "name", "suppressed", "reorder_to",
                          "path_points_nm", "profile_uuids", "offsets_nm", "ruled",
                          "size_nm", "edge_indices", "thickness_nm", "face_indices", "inward",
                          "translations_nm", "fuse", "normal_axis", "offset_nm", "keep_original",
                          "step_text", "sketch_uuid"}),
}

_CONTAINER_KEYS = ("sketches", "features", "annotations", "sheets")

#: 各节点已知字段（未知字段保留 + 记诊断；`x_` 前缀视为厂商扩展，不报）。
_NODE_FIELDS: dict[str, frozenset[str]] = {
    "root": _KNOWN_TOP_LEVEL,
    "meta": frozenset({"doc_uuid", "created_at", "app_version", "title_key"}),
    "sketch": frozenset({"uuid", "plane", "entities", "constraints", "dimensions", "solved"}),
    "entity": frozenset({"uuid", "kind", "layer", "construction", "geom"}),
    "constraint": frozenset({"uuid", "kind", "refs", "value_nm"}),
    "dimension": frozenset({"uuid", "kind", "refs", "value_nm", "text_key"}),
    "solved": frozenset({"dof", "converged", "cache"}),
    "feature": frozenset({"uuid", "kind", "inputs", "params", "suppressed"}),
    "params": frozenset({"distance_nm", "symmetric", "angle_deg", "op", "axis", "angle"}),
    "annotation": frozenset({"uuid", "target", "kind", "value_nm", "text_key", "literal", "args",
                             # 顶层 `text`：`/api/open` 与手写 L1 的字面量口径（`ops.annotation.add_text`
                             # 走 `args["text"]`）。两种形态都是自家写侧发得出去的，读侧必须都认，
                             # 否则自家 save→open 会凭 judge 出一条 `unknown_field_preserved` 伪诊断。
                             "text"}),
    "sheet": frozenset({"uuid", "standard_profile", "size", "scale", "views", "dimensions",
                        "title_block"}),
    "view": frozenset({"uuid", "direction", "at_nm", "hidden", "cache"}),
    "geom": frozenset({"x1", "y1", "x2", "y2", "cx", "cy", "r", "start_deg", "end_deg",
                       "points", "points_nm", "closed"}),
}

#: 读侧审计认下来的字段 = 上面的冻结字段 ∪ 本仓写侧真写得出来的扩展键（`PRODUCT_EXTENSION_KEYS`）。
#: **真正未知**的字段仍照旧报 `unknown_field_preserved`（阴性对照见 `tests/test_docmodel.py` 与
#: `tests/test_roundtrip_extension_namespaces.py`）。
for _kind, _extension_keys in PRODUCT_EXTENSION_KEYS.items():
    _NODE_FIELDS[_kind] = _NODE_FIELDS[_kind] | _extension_keys
del _kind, _extension_keys


def annotation_text(node: Any) -> str:
    """标注节点 → 可显示文本（**写侧 ↔ 读侧唯一口径**）；没有文本 ⇒ `""`（不编空文本）。

    为什么必须有这一个函数：标注的字面量有**两种写侧形态**，读侧只认一种就会把产品自己
    写出来的文本静默丢掉 —— 操作报 `ok=true`、文档也真的变了，但任何产物里一个字节都没有
    （B6 缺陷 RC-S2：最危险的一类 phantom，"用户以为做成了"）：

      ① `ops.annotation.add_text/add_mtext(literal=True)`：字面量落 `args["text"]`
         （同时把 `literal` 落进 `args`，使文本不变量尺子认它是"显式字面量"）；
      ② `/api/open` 与手写 L1 口径：字面量落节点**顶层** `text` + 顶层 `literal`。

    取文本顺序：`literal` 为真时先看顶层 `text`、再看 `args["text"]`；非字面量（或没有字面量）
    回落 `text_key` —— 真源存的就是 key，翻译在语言层。整个仓库里"标注文本怎么取"只此一处，
    `draw.sheet.annotation_texts` 与 `io.dxf_bridge.annotation_text_lines` 都走它。
    """
    if not isinstance(node, Mapping):
        return ""
    args = node.get("args")
    if not isinstance(args, Mapping):
        args = {}
    if bool(node.get("literal")) or bool(args.get("literal")):
        for holder in (node, args):
            value = holder.get("text")
            if isinstance(value, str) and value:
                return value
    for holder in (node, args):
        key = holder.get("text_key")
        if isinstance(key, str) and key:
            return key
    return ""

#: `geom` 里的**点串**字段名：写入侧（`sketch/primitives.add_polyline`）用 `points`，
#: 图纸/投影面用 `points_nm`。两个名字都是**本仓自己的**真源字段，读侧必须都认、同一把尺子量；
#: 只认一个的后果实测过：自家写出的 `geom.points` 被判 `unknown_field_preserved` 伪诊断，
#: 于是 L1 文本不再逐字节稳定 —— undo/redo 与 save/open 往返全部"结构对不上"（B6 缺陷 RC3）。
_GEOM_POINT_KEYS: frozenset[str] = frozenset({"points", "points_nm"})

#: 节点类型 → 子节点类型（未知字段审计用；`None` = 自由映射，不审计键名）。
_CHILD_KINDS: dict[str, dict[str, str]] = {
    "root": {"meta": "meta", "sketches": "sketch", "features": "feature",
             "annotations": "annotation", "sheets": "sheet"},
    "sketch": {"entities": "entity", "constraints": "constraint", "dimensions": "dimension",
               "solved": "solved"},
    "entity": {"geom": "geom"},
    "feature": {"params": "params"},
    "sheet": {"views": "view", "title_block": ""},
    "meta": {}, "constraint": {}, "dimension": {}, "solved": {}, "params": {}, "annotation": {},
    "view": {}, "geom": {},
}

#: 引用型字段：值必须是 `[[uuid, sub_shape_name], ...]`。
_REF_KEYS = ("refs", "target")

#: uuid 列表字段：值是 `[uuid, ...]`（**不是**下标列表）。
_UUID_LIST_KEYS = ("inputs", "dimensions")

#: i18n key 形状与汉字区间的**别名**：判据本体唯一真源在 `cadcore.i18n.keys`（写侧/读侧同源）。
#: 保留这两个名字是因为本包多处注释以它们指代"key 形状口径"（`ops/*` 的 `_KEY_RE` 副本注释）。
_KEY_LIKE_RE = KEY_RE
_HAN_RE = HAN_RE
_INDEX_REF_RE = re.compile(r"^\[?\s*\d+\s*\]?$")
_BRACKET_INDEX_RE = re.compile(r"\[\s*\d+\s*\]")
_TEXT_FIELD_KEYS = ("text", "label", "caption", "literal_text")


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------


@dataclass
class Document:
    """L1 文档的内存形态（**不是**真源：真源是 `document_to_l1` 的文本）。

    `extra` 装未知顶层字段、`diagnostics` 装迁移/读取诊断 —— 两者都必须能被再序列化回去，
    否则"打开旧文件再保存"就会静默丢东西。
    """

    schema_version: int = SCHEMA_VERSION
    meta: dict[str, Any] = field(default_factory=dict)
    unit: str = DEFAULT_UNIT
    standard_profile: str = DEFAULT_STANDARD_PROFILE
    projection_angle: str | None = None
    sketches: list[Any] = field(default_factory=list)
    features: list[Any] = field(default_factory=list)
    annotations: list[Any] = field(default_factory=list)
    sheets: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """内存形态 → L1 载荷（未知字段与诊断一并带上；已知字段永不被 `extra` 覆盖）。"""
        payload: dict[str, Any] = {}
        for key, value in self.extra.items():
            if key not in _KNOWN_TOP_LEVEL:
                payload[key] = value
        payload.update({
            "schema_version": int(self.schema_version),
            "meta": dict(self.meta),
            "unit": str(self.unit),
            "standard_profile": str(self.standard_profile),
            "projection_angle": self.projection_angle,
            "sketches": list(self.sketches),
            "features": list(self.features),
            "annotations": list(self.annotations),
            "sheets": list(self.sheets),
        })
        if self.diagnostics:
            payload[DIAGNOSTICS_KEY] = [dict(item) for item in self.diagnostics]
        return payload

    def as_dict(self) -> dict[str, Any]:
        """别名（`store._as_plain` 按 `as_dict()` 取机读形态）。"""
        return self.to_payload()


@dataclass
class RebuildResult:
    """`recompute()` 的返回值：**数据**，不是副作用（可直接进判定行/日志）。"""

    ok: bool
    shape_refs: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    kernel: str = ""
    kernel_version: str = ""

    def errors(self) -> list[dict[str, Any]]:
        return [item for item in self.diagnostics if item.get("severity") == "error"]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": bool(self.ok), "kernel": self.kernel, "kernel_version": self.kernel_version,
                "shape_refs": [dict(item) for item in self.shape_refs],
                "diagnostics": [dict(item) for item in self.diagnostics],
                "shape_count": len(self.shape_refs), "error_count": len(self.errors())}


# --------------------------------------------------------------------------------------
# 诊断条目
# --------------------------------------------------------------------------------------


def _diag(severity: str, code: str, **details: Any) -> dict[str, Any]:
    """机读诊断条目：`severity` + `code` 固定，其余进 `details`（**排序靠 `_diag_sort_key`**）。"""
    entry: dict[str, Any] = {"severity": str(severity), "code": str(code)}
    for key, value in details.items():
        entry[str(key)] = value
    return entry


def _diag_key(entry: Mapping[str, Any]) -> str:
    try:
        return json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - default=str 之后基本不可达
        return repr(sorted(entry.items(), key=lambda kv: kv[0]))


def _dedupe_diags(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """去重 + 稳定排序（诊断顺序不能引入不确定性，否则两次序列化会不一样）。"""
    unique: dict[str, dict[str, Any]] = {}
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        entry = {str(key): value for key, value in item.items()}
        unique.setdefault(_diag_key(entry), entry)
    return [unique[key] for key in sorted(unique)]


# --------------------------------------------------------------------------------------
# 规范化（确定性序列化的唯一入口）
# --------------------------------------------------------------------------------------


def _canonical(value: Any, path: str = "$") -> Any:
    """任意值 → 可确定性序列化的 JSON 值（**拒绝**静默破坏形状的输入）。

    规则：`str/bool/int` 原样；有限 `float` 原样（`-0.0` → `0.0`）；非有限浮点报错（JSON 里
    `NaN`/`Infinity` 不是标准 JSON，读回来会和写出去不一样）；`Mapping` 的键必须是 `str`
    （`json` 会把 int 键静默转成 str，那等于悄悄改文档）；序列 → list；dataclass / `as_dict()`
    对象按映射展开；其余类型报错。
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DocumentError("non-finite numbers are not allowed in L1",
                                reason="non_finite_number", path=path, value=repr(value))
        return 0.0 if value == 0.0 else value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DocumentError("L1 object keys must be strings",
                                    reason="non_string_key", path=path, key_type=type(key).__name__)
            out[key] = _canonical(item, f"{path}.{key}")
        return out
    if isinstance(value, (list, tuple)):
        return [_canonical(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, (set, frozenset)):
        raise DocumentError("sets are not order-stable; use a list in L1",
                            reason="unordered_value", path=path, value_type=type(value).__name__)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _canonical(dataclasses.asdict(value), path)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return _canonical(as_dict(), path)
    raise DocumentError("unsupported value type in L1",
                        reason="unsupported_value", path=path, value_type=type(value).__name__)


def _as_payload(doc: Any) -> dict[str, Any]:
    """`Document` / 映射 → 规范化载荷（**深拷贝**：`recompute` 的纯函数性靠它）。"""
    if isinstance(doc, Document):
        return _canonical(doc.to_payload())
    if isinstance(doc, Mapping):
        return _canonical(doc)
    raise DocumentError("document must be a Document or a mapping",
                        reason="bad_document", value_type=type(doc).__name__)


# --------------------------------------------------------------------------------------
# 新建 / 序列化 / 读入
# --------------------------------------------------------------------------------------


def new_document(*, unit: str = DEFAULT_UNIT, standard_profile: str = DEFAULT_STANDARD_PROFILE,
                 projection_angle: str | None = None, title_key: str = DEFAULT_TITLE_KEY) -> Document:
    """新建空文档：只填 L1 顶层骨架，**不猜投影角**（`projection_angle` 与单位/标准档三轴互不联动）。

    `unit` 立即过 `units.unit_scale_nm()` 校验（未知单位报错，不静默按 mm 处理）。
    """
    unit_scale_nm(unit)
    if projection_angle is not None and not str(projection_angle).strip():
        raise DocumentError("projection_angle must be a non-empty string or None",
                            reason="bad_projection_angle")
    meta = {
        "doc_uuid": str(_uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app_version": APP_VERSION,
        "title_key": str(title_key or DEFAULT_TITLE_KEY),
    }
    return Document(schema_version=SCHEMA_VERSION, meta=meta, unit=str(unit),
                    standard_profile=str(standard_profile),
                    projection_angle=None if projection_angle is None else str(projection_angle),
                    sketches=[], features=[], annotations=[], sheets=[])


def document_to_l1(doc: Any) -> str:
    """文档 → L1 文本（**唯一权威形态**）：键序排序 / 缩进 2 / 换行 `"\\n"` / `ensure_ascii=False`。

    同一文档调用两次逐字节相同（这是可复跑的判定行，见 `--selftest` 的 `sha256_a/sha256_b`）。
    本函数**不做字段校验**（只拒绝无法确定性序列化的值），校验是 `check_invariants` 的职责 ——
    出图/存档不该因为一条不变量就被挡住。
    """
    payload = _as_payload(doc)
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
    return text + "\n"


def document_from_l1(text: str | bytes) -> Document:
    """L1 文本 → 文档。读入即跑迁移链（旧 schema 文件必须能打开）。"""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8")
    if not isinstance(text, str):
        raise DocumentError("L1 text must be str or bytes", reason="bad_l1_text",
                            value_type=type(text).__name__)
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DocumentError("L1 text is not valid JSON", reason="bad_json",
                            line=exc.lineno, column=exc.colno) from exc
    if not isinstance(raw, Mapping):
        raise DocumentError("L1 document must be a JSON object", reason="bad_document",
                            value_type=type(raw).__name__)
    return _document_from_payload(migrate(raw))


def _document_from_payload(payload: Mapping[str, Any]) -> Document:
    """规范化载荷 → `Document`（**不发明 uuid**：缺 `meta.doc_uuid` 就报错）。"""
    meta = payload.get("meta")
    if not isinstance(meta, Mapping):
        raise DocumentError("document.meta must be an object", reason="missing_meta")
    doc_uuid = meta.get("doc_uuid")
    if not isinstance(doc_uuid, str) or not doc_uuid.strip():
        raise DocumentError("document.meta.doc_uuid is required; refusing to invent one",
                            reason="missing_doc_uuid")
    unit = payload.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise DocumentError("document.unit is required", reason="missing_unit")
    try:
        unit_scale_nm(unit)
    except CadError as exc:
        raise DocumentError("unknown length unit", reason="unknown_unit", unit=unit) from exc
    profile = payload.get("standard_profile")
    if not isinstance(profile, str) or not profile.strip():
        raise DocumentError("document.standard_profile is required", reason="missing_standard_profile")
    containers: dict[str, list[Any]] = {}
    for key in _CONTAINER_KEYS:
        value = payload.get(key)
        if not isinstance(value, list):
            raise DocumentError("document.%s must be a list" % key, reason="bad_container", key=key,
                                value_type=type(value).__name__)
        containers[key] = value
    diagnostics = payload.get(DIAGNOSTICS_KEY)
    extra = {key: value for key, value in payload.items() if key not in _KNOWN_TOP_LEVEL}
    return Document(
        schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
        meta={str(key): value for key, value in meta.items()},
        unit=str(unit),
        standard_profile=str(profile),
        projection_angle=payload.get("projection_angle"),
        sketches=containers["sketches"], features=containers["features"],
        annotations=containers["annotations"], sheets=containers["sheets"],
        extra=extra,
        diagnostics=_dedupe_diags(diagnostics if isinstance(diagnostics, list) else []),
    )


# --------------------------------------------------------------------------------------
# 迁移链（单调 · 禁跳版 · 禁静默丢字段）
# --------------------------------------------------------------------------------------


def _migration_0_to_1(payload: dict[str, Any], diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    """legacy（无 `schema_version`，视作 0）→ 1：只补顶层骨架，**不发明 uuid**。"""
    diagnostics.append(_diag("info", "legacy_version_stamped", path="schema_version", **{"from": 0}))
    out = dict(payload)
    out["schema_version"] = 1
    return out


#: 版本链：`_MIGRATIONS[n]` 必须把 `schema_version=n` 变成 `n+1`（缺一步即"跳版"，直接报错）。
_MIGRATIONS: dict[int, Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]]] = {
    0: _migration_0_to_1,
}


def _fill_known_fields(payload: dict[str, Any], diagnostics: list[dict[str, Any]]) -> None:
    """补齐缺失的**顶层已知字段**（补的是骨架不是数据），每一项都记诊断 —— 不静默。"""
    if "meta" not in payload:
        payload["meta"] = {}
        diagnostics.append(_diag("warning", "field_filled", path="meta", value={}))
    for key, default in (("unit", DEFAULT_UNIT),
                         ("standard_profile", DEFAULT_STANDARD_PROFILE),
                         ("projection_angle", None)):
        if key not in payload:
            payload[key] = default
            diagnostics.append(_diag("warning", "field_filled", path=key, value=default))
    for key in _CONTAINER_KEYS:
        if key not in payload:
            payload[key] = []
            diagnostics.append(_diag("warning", "field_filled", path=key, value=[]))


def _audit_unknown_fields(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """未知字段审计：返回诊断清单，**不改动载荷**（未知字段原样留着，随文档一起存回去）。"""
    found: list[dict[str, Any]] = []

    def walk(node: Any, kind: str, path: str) -> None:
        if not isinstance(node, Mapping):
            return
        known = _NODE_FIELDS.get(kind)
        children = _CHILD_KINDS.get(kind, {})
        if known is not None:
            for key in node:
                if not isinstance(key, str) or key in known or key.startswith("x_"):
                    continue
                found.append(_diag("warning", "unknown_field_preserved", path=f"{path}.{key}",
                                   node_kind=kind))
        for key, child_kind in children.items():
            if key not in node:
                continue
            child = node[key]
            child_path = f"{path}.{key}"
            if not child_kind:
                continue
            if isinstance(child, Mapping):
                walk(child, child_kind, child_path)
            elif isinstance(child, (list, tuple)):
                for index, item in enumerate(child):
                    walk(item, child_kind, f"{child_path}[{index}]")

    walk(payload, "root", "$")
    return found


def migrate(payload: dict[str, Any], *, target: int = SCHEMA_VERSION) -> dict[str, Any]:
    """版本迁移：**单调升版、禁跳版、禁静默丢字段**。

    - 未知字段**原样保留**，每个记一条 `unknown_field_preserved` 诊断；
    - 版本链缺一步就抛 `DocumentError(reason="no_migration_step")`，绝不跨版"猜"；
    - `target > SCHEMA_VERSION` ⇒ `target_too_new`；`target < 当前版本` ⇒ `downgrade_unsupported`
      （单调 = 只升不降）。诊断写回返回值的 `diagnostics` 键（空则不留该键，保证干净文档稳定）。
    """
    if not isinstance(payload, Mapping):
        raise DocumentError("payload must be a mapping", reason="bad_document",
                            value_type=type(payload).__name__)
    if isinstance(target, bool) or not isinstance(target, int):
        raise DocumentError("target must be an int", reason="bad_target", target=repr(target))
    if target > SCHEMA_VERSION:
        raise DocumentError("target schema is newer than this build",
                            reason="target_too_new", target=target, known=SCHEMA_VERSION)
    data = _canonical(payload)
    diagnostics: list[dict[str, Any]] = []
    existing = data.get(DIAGNOSTICS_KEY)
    if isinstance(existing, list):
        diagnostics.extend(item for item in existing if isinstance(item, Mapping))
    version = data.get("schema_version", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise DocumentError("schema_version must be a non-negative int", reason="bad_schema_version",
                            schema_version=repr(data.get("schema_version")))
    if version > SCHEMA_VERSION:
        raise DocumentError("document schema is newer than this build",
                            reason="schema_too_new", schema_version=version, known=SCHEMA_VERSION)
    if target < version:
        raise DocumentError("downgrade is not supported; migration is monotonic",
                            reason="downgrade_unsupported", schema_version=version, target=target)
    data["schema_version"] = version
    while version < target:
        step = _MIGRATIONS.get(version)
        if step is None:  # 禁跳版：链上缺一步就停在这里报红，不许"就近"升到 target
            raise DocumentError("no migration step registered for this version",
                                reason="no_migration_step", schema_version=version, target=target,
                                registered=sorted(_MIGRATIONS))
        before = version
        data = step(data, diagnostics)
        produced = data.get("schema_version")
        if isinstance(produced, bool) or not isinstance(produced, int) or produced != before + 1:
            raise DocumentError("migration step must advance the version by exactly one",
                                reason="bad_migration_step", **{"from": before}, produced=repr(produced))
        version = produced
    _fill_known_fields(data, diagnostics)
    diagnostics.extend(_audit_unknown_fields(data))
    data.pop(DIAGNOSTICS_KEY, None)
    if diagnostics:
        data[DIAGNOSTICS_KEY] = _dedupe_diags(diagnostics)
    return data


# --------------------------------------------------------------------------------------
# 落盘 / 读盘
# --------------------------------------------------------------------------------------


def save_l1(doc: Any, path: str) -> str:
    """写 L1 文本真源（UTF-8、换行固定 `"\\n"`、同目录临时文件 + 原子替换），返回路径。"""
    target = os.fsdecode(os.fspath(path))
    text = document_to_l1(doc)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = "%s.tmp-%d" % (target, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):  # pragma: no cover - 失败路径清理
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return target


def load_l1(path: str) -> Document:
    """读 L1 文本真源（等价于 `read + document_from_l1`；读入即跑迁移链）。"""
    with open(os.fsdecode(os.fspath(path)), "r", encoding="utf-8") as handle:
        return document_from_l1(handle.read())


# --------------------------------------------------------------------------------------
# 不变量（返回违规清单，空 = 合规）
# --------------------------------------------------------------------------------------


def _path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else str(key)


def _is_text_key(value: Any) -> bool:
    """i18n key 形状：点分 ASCII 标识符，且不含空白/汉字（人话文案就是违规）。

    判据本体在 `cadcore.i18n.keys.is_text_key`（唯一真源）—— 写侧 `add_dimension` 用同一条，
    免得"写侧发得出去、读侧判它违规"。
    """
    return is_text_key(value)


def _uuid_ok(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _in_product_extension_table(path: str) -> bool:
    """路径是否落在**自家族模块的扩展表**里（见 `PRODUCT_EXTENSION_KEYS`）。

    这类子树有自己的 schema（例：装配组件行 `{"refs": {"origin": {"point_nm": …, "dir": …}}}`
    的 `refs` 是**命名参考几何表**），不是 L1 节点的 `[uuid, sub_shape_name]` 引用列表。
    B6 缺陷：不区分就会把自家写侧发得出去、也能原样装载回来的表格判成 `refs_not_a_list`
    （装配族模块自带自检即因此常红），属"读侧不认写侧"那一类。
    只对**引用形状**（`refs`/`target`/`_UUID_LIST_KEYS`）放宽；`*_nm`/`geom`/`title_key` 照旧查
    —— 扩展数据同样是文档数据。
    """
    if not path.startswith("$."):
        return False
    head = path[2:].split(".", 1)[0]
    if head.startswith("sketches["):
        if "." not in path[2:]:
            return False
        tail = path[2:].split(".", 1)[1]
        segment = tail.split(".", 1)[0].split("[", 1)[0]
        return segment == "extra" or segment in PRODUCT_EXTENSION_KEYS["sketch"]
    return head.split("[", 1)[0] in PRODUCT_EXTENSION_KEYS["root"]


def _iter_nodes(node: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    """深度优先遍历（映射/列表），产出 `(路径, 值)`；顺序即文档顺序（确定性）。"""
    yield path, node
    if isinstance(node, Mapping):
        for key, value in node.items():
            yield from _iter_nodes(value, _path(path, str(key)))
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            yield from _iter_nodes(item, f"{path}[{index}]")


def _check_nm_value(value: Any, path: str, key: str, problems: list[str]) -> None:
    if value is None:
        if key != "value_nm":
            problems.append("nm_is_null:%s" % path)
        return
    if isinstance(value, bool):
        problems.append("nm_is_bool:%s" % path)
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        problems.append("nm_not_integer:%s value=%r" % (path, value))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_nm_value(item, f"{path}[{index}]", key, problems)
        return
    problems.append("nm_not_a_number:%s type=%s" % (path, type(value).__name__))


def _check_geom(value: Any, path: str, problems: list[str]) -> None:
    if not isinstance(value, Mapping):
        problems.append("geom_not_mapping:%s type=%s" % (path, type(value).__name__))
        return
    if not value:
        problems.append("geom_empty:%s" % path)
    for key, item in value.items():
        child = _path(path, str(key))
        if key in _GEOM_LENGTH_KEYS:
            if type(item) is not int:
                problems.append("geom_length_not_integer:%s value=%r" % (child, item))
            continue
        if key in _GEOM_PAIR_KEYS:
            _check_geom_point(item, child, problems)
            continue
        if key in _GEOM_POINT_KEYS or key == "samples":
            if not isinstance(item, (list, tuple)):
                problems.append("geom_points_invalid:%s expected point list" % child)
            else:
                for index, point in enumerate(item):
                    _check_geom_point(point, "%s[%d]" % (child, index), problems)
            continue
        if key in ("dx", "dy"):
            if isinstance(item, bool) or not isinstance(item, (int, float)) or (
                    isinstance(item, float) and not math.isfinite(item)):
                problems.append("geom_direction_invalid:%s" % child)
            continue
        if key == "closed":
            if type(item) is not bool:
                problems.append("geom_flag_invalid:%s expected bool" % child)
            continue
        if str(key).endswith("_deg"):
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                problems.append("angle_not_a_number:%s type=%s" % (child, type(item).__name__))
            elif isinstance(item, float) and not math.isfinite(item):
                problems.append("angle_not_finite:%s value=%r" % (child, item))
            continue
        if str(key).endswith("_nm"):
            _check_nm_value(item, child, str(key), problems)
            continue
        if item is None or isinstance(item, (bool, str)):
            continue  # 非长度型元数据（如 closed）不在这里判
        if isinstance(item, float):
            problems.append("geom_length_not_integer:%s value=%r" % (child, item))
        elif not isinstance(item, (int, list, tuple)):
            problems.append("geom_value_unsupported:%s type=%s" % (child, type(item).__name__))


def _check_geom_point(value: Any, path: str, problems: list[str]) -> None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        problems.append("geom_points_invalid:%s expected 2D point" % path)
        return
    for index, coordinate in enumerate(value):
        if type(coordinate) is not int:
            child = "%s[%d]" % (path, index)
            if isinstance(coordinate, (list, tuple)) or (
                    isinstance(coordinate, int) and not isinstance(coordinate, bool)):
                problems.append("geom_length_not_integer:%s value=%r" % (child, coordinate))
            else:
                # Keep the public nm_* codes without recursively accepting nested points.
                _check_nm_value(coordinate, child, "coordinate", problems)


def _entity_geometry_problems(entity: Mapping[str, Any], path: str) -> list[str]:
    """Validate required native/legacy geometry without manufacturing defaults."""
    problems: list[str] = []
    geom = entity.get("geom")
    if not isinstance(geom, Mapping):
        return ["geom_not_mapping:%s.geom" % path]
    kind = entity.get("kind")
    # Aliases are read-only views of the same geometry, not defaults or conversions.
    canonical = dict(geom)
    aliases = {
        "p1": ("start", "start_nm", "p1_nm"),
        "p2": ("end", "end_nm", "p2_nm"),
        "center": ("center_nm",),
        "radius_nm": ("radius", "r"),
        "start_deg": ("a1_deg",), "end_deg": ("a2_deg",),
    }
    for key, names in aliases.items():
        values = [geom[name] for name in (key, *names) if name in geom]
        if values:
            canonical[key] = values[0]
            if any(value != values[0] for value in values[1:]):
                problems.append("geom_conflicting_fields:%s.geom.%s" % (path, key))
    if "cx" in geom and "cy" in geom:
        center = [geom["cx"], geom["cy"]]
        if "center" in canonical and canonical["center"] != center:
            problems.append("geom_conflicting_fields:%s.geom.center" % path)
    if all(key in geom for key in ("x1", "y1", "x2", "y2")):
        for key, point in (("p1", [geom["x1"], geom["y1"]]),
                           ("p2", [geom["x2"], geom["y2"]])):
            if key in canonical and canonical[key] != point:
                problems.append("geom_conflicting_fields:%s.geom.%s" % (path, key))
    required = {
        "line": (("x1", "y1", "x2", "y2"), ("p1", "p2")),
        "circle": (("cx", "cy", "radius_nm"), ("center", "radius_nm")),
        "arc": (("cx", "cy", "radius_nm", "start_deg", "end_deg"),
                ("center", "radius_nm", "start_deg", "end_deg")),
        "point": (("x", "y"),),
        "ellipse": (("cx", "cy", "rx", "ry"),),
        "polyline": (("points",), ("points_nm",)),
        "spline": (("points",), ("points_nm",)),
        "ray": (("px", "py", "dx", "dy"),),
        "xline": (("px", "py", "dx", "dy"),),
    }
    if not isinstance(kind, str) or not kind.strip():
        return ["geom_missing_field:%s.kind" % path]
    alternatives = required.get(kind)
    if alternatives and not any(all(key in canonical for key in fields) for fields in alternatives):
        problems.append("geom_missing_field:%s.geom kind=%s required=%r" % (path, kind, alternatives))
    for key in ("r", "radius", "rx", "ry", "radius_nm"):
        if type(geom.get(key)) is int and geom[key] <= 0:
            problems.append("geom_degenerate:%s.geom.%s must be positive" % (path, key))
    if kind == "line":
        if all(type(geom.get(key)) is int for key in ("x1", "y1", "x2", "y2")):
            if (geom["x1"], geom["y1"]) == (geom["x2"], geom["y2"]):
                problems.append("geom_degenerate:%s.geom zero-length line" % path)
        if "p1" in canonical and "p2" in canonical and canonical["p1"] == canonical["p2"]:
            problems.append("geom_degenerate:%s.geom zero-length line" % path)
    if kind in ("ray", "xline") and geom.get("dx") == geom.get("dy") == 0:
        problems.append("geom_degenerate:%s.geom zero direction" % path)
    if kind in ("polyline", "spline"):
        for key in _GEOM_POINT_KEYS:
            points = geom.get(key)
            if not isinstance(points, (list, tuple)):
                continue
            valid = [point for point in points if isinstance(point, (list, tuple))
                     and len(point) == 2 and all(type(value) is int for value in point)]
            minimum = 3 if geom.get("closed") is True else 2
            if len(points) < minimum or len(valid) != len(points):
                problems.append("geom_points_invalid:%s.geom.%s" % (path, key))
            elif len({tuple(point) for point in valid}) < minimum:
                problems.append("geom_degenerate:%s.geom.%s collapsed points" % (path, key))
            elif any(first == second for first, second in zip(valid, valid[1:])):
                problems.append("geom_degenerate:%s.geom.%s repeated segment" % (path, key))
        if "points" in geom and "points_nm" in geom and geom["points"] != geom["points_nm"]:
            problems.append("geom_conflicting_fields:%s.geom.points" % path)
    return problems


def _is_single_ref(value: Any) -> bool:
    """`target` 是**单个** `[uuid, sub_shape_name]`（§C），`refs` 是它的**列表**。"""
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and isinstance(value[0], str) and isinstance(value[1], str))


def _check_refs(value: Any, path: str, problems: list[str]) -> None:
    if not isinstance(value, (list, tuple)):
        problems.append("refs_not_a_list:%s type=%s" % (path, type(value).__name__))
        return
    entries = [value] if _is_single_ref(value) else list(value)
    for index, ref in enumerate(entries):
        child = path if _is_single_ref(value) else f"{path}[{index}]"
        if not isinstance(ref, (list, tuple)) or len(ref) != 2:
            problems.append("ref_not_pair:%s expected=[uuid,sub_shape_name]" % child)
            continue
        target, name = ref[0], ref[1]
        if not _uuid_ok(target):
            problems.append("ref_bad_uuid:%s value=%r" % (child, target))
        if not isinstance(name, str) or not name:
            problems.append("ref_bad_sub_shape:%s value=%r" % (child, name))
            continue
        if _INDEX_REF_RE.match(name) or _BRACKET_INDEX_RE.search(name):
            problems.append("ref_index_used:%s value=%r" % (child, name))


def _check_text(node: Any, path: str, problems: list[str]) -> None:
    if not isinstance(node, Mapping):
        return
    literal = bool(node.get("literal"))
    if "title_block" in node and isinstance(node["title_block"], Mapping):
        for field, value in node["title_block"].items():
            if not _is_text_key(value):
                problems.append("text_not_key:%s value=%r" % (_path(path, "title_block." + str(field)),
                                                             value))
    for key in _TEXT_FIELD_KEYS:
        if key in node and isinstance(node[key], str) and not literal and not _is_text_key(node[key]):
            problems.append("text_not_key:%s value=%r" % (_path(path, key), node[key]))
    if "text_key" in node:
        value = node["text_key"]
        if value is None:
            if node.get("kind") in ("dimensional", "dimension", "gdt") and not literal:
                problems.append("text_key_missing:%s" % path)
        elif isinstance(value, str):
            if not _is_text_key(value):
                problems.append("text_not_key:%s value=%r" % (_path(path, "text_key"), value))
        else:
            problems.append("text_key_not_string:%s type=%s" % (_path(path, "text_key"),
                                                                type(value).__name__))


def check_invariants(doc: Any) -> list[str]:
    """不变量体检：返回违规清单（**空 = 合规**）。四类判据：

    1. 长度一律整数纳米：`*_nm` 与 `geom` 的坐标/半径不得是浮点（`*_deg` 角度除外）；
    2. 引用一律 `(uuid, sub_shape_name)`：`refs`/`target` 必须是二元组且 uuid 合法；
    3. 禁下标引用：子形状名不得是 `"0"` / `"[0]"` 这类位置引用；
    4. 文本一律存 key：`meta.title_key` / `text_key` / `title_block` 的值必须是 i18n key 形状。

    另外查文档身份唯一性；仅保留草图尺寸与 dimension 注记的一对一致镜像。
    """
    payload = _as_payload(doc)
    problems: list[str] = []
    unit = payload.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        problems.append("unit_missing:%s" % repr(unit))
    else:
        try:
            unit_scale_nm(unit)
        except CadError:
            problems.append("unit_unknown:%r" % unit)
    for path, node in _iter_nodes(payload):
        extension = _in_product_extension_table(path)
        if isinstance(node, Mapping):
            for key, value in node.items():
                child = _path(path, str(key))
                if str(key).endswith("_nm"):
                    _check_nm_value(value, child, str(key), problems)
                elif key == "geom":
                    _check_geom(value, child, problems)
                elif key in _REF_KEYS:
                    if not extension:  # 族模块扩展表有自己的字段 schema（见 _in_product_extension_table）
                        _check_refs(value, child, problems)
                elif key in _UUID_LIST_KEYS and not extension and isinstance(value, (list, tuple)):
                    for index, item in enumerate(value):
                        if isinstance(item, str) and not _uuid_ok(item):
                            problems.append("uuid_list_bad_ref:%s[%d] value=%r" % (child, index, item))
                elif key == "uuid":
                    if not _uuid_ok(value):
                        problems.append("uuid_bad:%s value=%r" % (child, value))
                elif key == "title_key":
                    if not _is_text_key(value):
                        problems.append("text_not_key:%s value=%r" % (child, value))
            _check_text(node, path, problems)
    problems.extend(_duplicate_uuid_problems(payload))
    return problems


#: 几何里的**长度**字段（整数纳米）：量程判据用，与 `_check_geom` 的类型判据同一批字段。
_GEOM_LENGTH_KEYS: frozenset[str] = frozenset({
    "x1", "y1", "x2", "y2", "cx", "cy", "r", "x", "y", "px", "py", "rx", "ry",
    "radius", "radius_nm",
})
_GEOM_PAIR_KEYS: frozenset[str] = frozenset({
    "p1", "p2", "center", "start", "end", "p1_nm", "p2_nm",
    "start_nm", "end_nm", "center_nm", "point_nm", "at_nm",
})

#: L1 **装载闸**的判据集：命中这些前缀的不变量 = 真源里的**值/类型/引用形状非法**（入参边界），
#: 装载即拒收。为什么要有这张表、而不直接拿 `check_invariants` 的全部结论拒收：产品自己的写侧会产出
#: 空子形状名（rectangle 的约束、radial 尺寸的 refs），`ref_bad_sub_shape` 属于**写侧与检查器的口径分歧**，
#: 拿它拒收会让产品自己 save 出去、能打开的文档打不开 —— 那是另一个问题，不该由装载闸承担。
LOAD_BLOCKING_PREFIXES: tuple[str, ...] = (
    # 长度/几何：真源是整数纳米，类型错或存不下都不许进
    "nm_is_null", "nm_is_bool", "nm_not_integer", "nm_not_a_number", "nm_out_of_range",
    "geom_not_mapping", "geom_empty", "geom_length_not_integer", "geom_value_unsupported",
    "geom_missing_field", "geom_degenerate", "geom_points_invalid", "geom_conflicting_fields",
    "geom_direction_invalid", "geom_flag_invalid",
    # 角度：必须是有限数
    "angle_not_a_number", "angle_not_finite",
    # 引用：必须是 (uuid, sub_shape_name) 二元组、uuid 合法、禁下标引用
    "refs_not_a_list", "ref_not_pair", "ref_bad_uuid", "ref_index_used",
    # 文本：真源存 i18n key（`literal=True` 的自由文本由 check_invariants 自己放行）
    "text_not_key", "text_key_not_string", "text_key_missing",
    # 文本必须可编码成 UTF-8（孤立代理对会让 state/save/导出当场崩）
    "text_not_utf8",
    # 标识与单位
    "uuid_bad", "uuid_duplicate", "unit_missing", "unit_unknown",
)


def _check_nm_range(value: Any, path: str, problems: list[str]) -> None:
    """长度字段 → **可存量程**体检（`nm_in_range`）。

    与 `_check_nm_value` 的分工：那边判"是不是整数纳米"（类型），这边判"存不存得下"（量程）。
    两条都得有 —— 309 位整数在类型上完全合法，但落进真源后 `float()` 通路（导出/变换/捕捉）全部溢出。
    """
    if value is None or isinstance(value, bool):
        return  # 类型问题由 check_invariants 报，这里不重复报
    if isinstance(value, int):
        if not nm_in_range(value):
            problems.append("nm_out_of_range:%s value=%r" % (path, value))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_nm_range(item, "%s[%d]" % (path, index), problems)


def range_problems(doc: Any) -> list[str]:
    """真源里所有长度字段的**量程**违规清单（空 = 全在 `|v| <= 2**53` 内）。

    遍历口径与 `check_invariants` 同一棵树：`*_nm` 键（标量 / 点表）与 `geom` 的长度字段。
    """
    payload = _as_payload(doc)
    problems: list[str] = []
    for path, node in _iter_nodes(payload):
        if not isinstance(node, Mapping):
            continue
        for key, value in node.items():
            child = _path(path, str(key))
            if str(key).endswith("_nm"):
                _check_nm_range(value, child, problems)
            elif key == "geom" and isinstance(value, Mapping):
                for geom_key, geom_value in value.items():
                    if geom_key in (_GEOM_LENGTH_KEYS | _GEOM_POINT_KEYS | _GEOM_PAIR_KEYS) or geom_key == "samples":
                        _check_nm_range(geom_value, _path(child, str(geom_key)), problems)
    return problems


def load_blocking_problems(doc: Any) -> list[str]:
    """**装载闸**：返回必须拒收的入参边界问题（空 = 可装载）。

    = `check_invariants` 里命中 `LOAD_BLOCKING_PREFIXES` 的那些 + `range_problems`（量程）
    + `text_encoding_problems`（可编码性，孤立代理对）。
    动机（B6 实测缺陷 RC1b/RC9/RC16）：`/api/open` 原样收下非法真源就装进工作区 —— 非数值/布尔/越界长度
    静默落库（`nm_is_bool` / `nm_not_a_number` / `out_of_range_persisted`），孤立代理对则让 `/api/state`
    的 UTF-8 编码当场崩（兜底 `internal_error`，文档从此读不出来）。判据必须与"产品自己的不变量"同源，
    所以这里只做**筛选**，不另立一套规则。
    """
    problems = [item for item in check_invariants(doc)
                if str(item).split(":", 1)[0] in LOAD_BLOCKING_PREFIXES]
    problems.extend(range_problems(doc))
    problems.extend(text_encoding_problems(doc))
    problems.extend(_input_structure_problems(_as_payload(doc)))
    return sorted(set(problems))


def text_encoding_problems(doc: Any) -> list[str]:
    """真源里所有字符串（含键名）必须能编码成 UTF-8（空 = 合规）。

    为什么单列一条：`json.loads` 允许 `"\\ud800"` 这样的**孤立代理对**——它在 Python 里是合法 `str`，
    但 `str.encode("utf-8")` 抛 `UnicodeEncodeError`。L1 真源最终要落 UTF-8 文件/响应体，
    所以"能不能编码"是装载期就该查的值约束，不是等到 save/state 才炸的运行时意外（B6 缺陷 RC16）。
    """
    payload = _as_payload(doc)
    problems: list[str] = []

    def check(value: Any, path: str) -> None:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                problems.append("text_not_utf8:%s value=%r" % (path, value))
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                child = _path(path, str(key))
                check(key, child + "<key>")
                check(item, child)
            return
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                check(item, "%s[%d]" % (path, index))

    check(payload, "$")
    return problems


def _identity_nodes(payload: Mapping[str, Any]) -> Iterable[tuple[str, str, Mapping[str, Any]]]:
    meta = payload.get("meta")
    if isinstance(meta, Mapping):
        yield "meta", "document", {"uuid": meta.get("doc_uuid")}
    def rows(holder, key):
        value = holder.get(key)
        return value if isinstance(value, (list, tuple)) else ()
    for key in ("sketches", "features", "annotations", "sheets"):
        for index, node in enumerate(rows(payload, key)):
            if not isinstance(node, Mapping):
                continue
            path = "%s[%d]" % (key, index)
            yield path, key, node
            children = ("entities", "constraints", "dimensions") if key == "sketches" else (
                ("views", "dimensions") if key == "sheets" else ())
            for child in children:
                for child_index, item in enumerate(rows(node, child)):
                    if isinstance(item, Mapping):
                        yield "%s.%s[%d]" % (path, child, child_index), child, item


def _dimension_mirror(first: tuple, second: tuple) -> bool:
    (first_path, first_kind, first_node), (second_path, second_kind, second_node) = first, second
    if {first_kind, second_kind} != {"dimensions", "annotations"}:
        return False
    annotation = first_node if first_kind == "annotations" else second_node
    dimension = second_node if first_kind == "annotations" else first_node
    dimension_path = second_path if first_kind == "annotations" else first_path
    return (dimension_path.startswith("sketches[")
            and annotation.get("kind") in ("dimension", "dimensional")
            and type(dimension.get("value_nm")) is int
            and isinstance(dimension.get("text_key"), str)
            and all(key in annotation and key in dimension
                    and type(annotation[key]) is type(dimension[key])
                    and annotation[key] == dimension[key] for key in ("value_nm", "text_key")))


def _duplicate_uuid_problems(payload: Mapping[str, Any]) -> list[str]:
    """One document identity index; references are not additional identities."""
    problems: list[str] = []
    seen: dict[str, list[tuple]] = {}
    for path, kind, node in _identity_nodes(payload):
        value = node.get("uuid")
        if not _uuid_ok(value):
            continue
        identity = str(_uuid.UUID(value))
        previous = seen.setdefault(identity, [])
        current = (path, kind, node)
        if previous and not (len(previous) == 1 and _dimension_mirror(previous[0], current)):
            problems.append("uuid_duplicate:%s duplicates %s value=%r" % (path, previous[0][0], value))
        previous.append(current)
    return problems


def _input_structure_problems(payload: Mapping[str, Any]) -> list[str]:
    """Strict load-only structural checks; advisory parsing stays compatible."""
    problems: list[str] = []
    if not isinstance(payload.get("meta"), Mapping):
        problems.append("missing_meta:meta expected object with doc_uuid")
    for path, _, node in _identity_nodes(payload):
        value = node.get("uuid")
        if value is None:
            problems.append("uuid_missing:%s" % path)
        elif not _uuid_ok(value):
            problems.append("uuid_bad:%s.uuid value=%r" % (path, value))
    for key in _CONTAINER_KEYS:
        nodes = payload.get(key, [])
        if not isinstance(nodes, list):
            problems.append("bad_container:%s expected list" % key)
            continue
        for index, node in enumerate(nodes):
            path = "%s[%d]" % (key, index)
            if not isinstance(node, Mapping):
                problems.append("bad_node:%s expected object" % path)
                continue
            children = ("entities", "constraints", "dimensions") if key == "sketches" else (
                ("views", "dimensions") if key == "sheets" else ())
            for child in children:
                values = node.get(child, [])
                if not isinstance(values, list):
                    problems.append("bad_container:%s.%s expected list" % (path, child))
                    continue
                for child_index, item in enumerate(values):
                    child_path = "%s.%s[%d]" % (path, child, child_index)
                    if key == "sheets" and child == "dimensions" and isinstance(item, str):
                        continue  # Legacy sheets reference annotations by UUID.
                    if not isinstance(item, Mapping):
                        problems.append("bad_node:%s expected object" % child_path)
                    elif child == "entities":
                        problems.extend(_entity_geometry_problems(item, child_path))
    return problems


# --------------------------------------------------------------------------------------
# 纯函数式重建
# --------------------------------------------------------------------------------------


def _shape_ref(shape: Any, *, feature_uuid: str, sketch_uuid: str, sub_shape_name: str) -> dict[str, Any]:
    """内核形状 → 机读 shape_ref（`(uuid, sub_shape_name)` 形态，含可核对的元数据）。"""
    edges = getattr(shape, "edges", None)
    return {
        "feature_uuid": str(feature_uuid),
        "sketch_uuid": str(sketch_uuid),
        "sub_shape_name": str(sub_shape_name),
        "shape_ref": str(getattr(shape, "name", "") or ""),
        "kind": str(getattr(shape, "kind", "") or ""),
        "depth_nm": int(getattr(shape, "depth_nm", -1) or 0),
        "edge_count": len(edges) if isinstance(edges, (list, tuple)) else -1,
    }


def _kernel_capabilities(kernel: Any) -> tuple[frozenset[str], str]:
    probe = getattr(kernel, "capabilities", None)
    if not callable(probe):
        return frozenset(), "capabilities() missing"
    try:
        return frozenset(str(item) for item in probe()), ""
    except Exception as exc:  # noqa: BLE001 - 能力探测失败要留证，不能假装有能力
        return frozenset(), "%s: %s" % (type(exc).__name__, exc)


def recompute(doc: Any, kernel: Any) -> RebuildResult:
    """纯函数式重建：**不改入参、不写盘**，返回 `shape_refs` + `diagnostics`。

    - 内核缺席（`DependencyMissing`，含"没有草图"/"没有实体特征"/"后端不支持该能力"）一律捕获成
      `diagnostics` 条目并把 `ok` 置 `False` —— 整个调用**不崩**；
    - 几何层错误（`CadError`/`GeometryError`）同样进 diagnostics，且不中断其它特征的重建；
    - 结果确定性：`shape_refs` 按 `(sketch_uuid, feature_uuid)` 排序，诊断去重排序。
    """
    diagnostics: list[dict[str, Any]] = []
    shape_refs: list[dict[str, Any]] = []
    payload = _as_payload(doc)  # 深拷贝 ⇒ 纯函数式（内核拿到的是快照，改不动调用方的文档）
    if kernel is None:
        diagnostics.append(_diag("error", "kernel_missing", detail="no kernel adapter supplied"))
        return RebuildResult(False, [], _dedupe_diags(diagnostics))
    kernel_name = str(getattr(kernel, "name", "") or "")
    kernel_version = str(getattr(kernel, "version", "") or "")
    probe = getattr(kernel, "available", None)
    if callable(probe):
        try:
            usable = bool(probe())
            why_unavailable = "" if usable else "available() is False"
        except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理，原因是证据
            usable, why_unavailable = False, "%s: %s" % (type(exc).__name__, exc)
        if not usable:
            diagnostics.append(_diag("error", "kernel_unavailable", kernel=kernel_name,
                                     detail=why_unavailable))
            return RebuildResult(False, [], _dedupe_diags(diagnostics), kernel_name, kernel_version)
    caps, why = _kernel_capabilities(kernel)
    if why:
        diagnostics.append(_diag("warning", "capabilities_unknown", kernel=kernel_name, detail=why))
    sketches = [item for item in payload.get("sketches") or [] if isinstance(item, Mapping)]
    features = [item for item in payload.get("features") or []
                if isinstance(item, Mapping) and not bool(item.get("suppressed"))]
    if not sketches:
        diagnostics.append(_diag("error", "no_sketch", detail="document has no sketch to rebuild"))
    elif not features:
        diagnostics.append(_diag("error", "no_solid_feature",
                                 detail="document has no unsuppressed feature"))
    by_uuid = {str(item.get("uuid") or ""): item for item in sketches}
    for feature in features:
        feature_uuid = str(feature.get("uuid") or "")
        inputs = [str(item) for item in feature.get("inputs") or [] if isinstance(item, str)]
        sketch = next((by_uuid[item] for item in inputs if item in by_uuid), None)
        if sketch is None and not sketches:
            diagnostics.append(_diag("error", "no_sketch", feature_uuid=feature_uuid,
                                     detail="document has no sketch to fall back to"))
            continue
        if sketch is None:
            sketch = sketches[0]
            diagnostics.append(_diag("warning", "feature_input_unresolved", feature_uuid=feature_uuid,
                                     inputs=inputs, fallback_sketch=str(sketch.get("uuid") or "")))
        sketch_uuid = str(sketch.get("uuid") or "")
        kind = str(feature.get("kind") or "").strip().lower()
        params = feature.get("params") if isinstance(feature.get("params"), Mapping) else {}
        try:
            if kind == "extrude":
                shape = kernel.extrude(sketch, distance_nm=int(params.get("distance_nm") or 0),
                                       symmetric=bool(params.get("symmetric")))
            elif kind == "revolve":
                shape = kernel.revolve(sketch, axis=str(params.get("axis") or "x"),
                                       angle_deg=float(params.get("angle_deg") or 0.0))
            elif kind == "boolean":
                diagnostics.append(_diag("error", "boolean_needs_shape_inputs", feature_uuid=feature_uuid,
                                         detail="boolean inputs are shapes, not sketches"))
                continue
            else:
                diagnostics.append(_diag("error", "feature_kind_unknown", feature_uuid=feature_uuid,
                                         kind=kind))
                continue
        except DependencyMissing as exc:
            diagnostics.append(_diag("error", "dependency_missing", feature_uuid=feature_uuid,
                                     kind=kind, reason=exc.reason, detail=exc.message,
                                     kernel=kernel_name, capability=str(exc.details.get("capability", ""))))
            continue
        except CadError as exc:
            diagnostics.append(_diag("error", "geometry_error", feature_uuid=feature_uuid, kind=kind,
                                     reason=exc.reason, detail=exc.message))
            continue
        except Exception as exc:  # noqa: BLE001 - 未预期异常要留证并继续，不能吞掉
            diagnostics.append(_diag("error", "kernel_raised", feature_uuid=feature_uuid, kind=kind,
                                     detail="%s: %s" % (type(exc).__name__, exc)))
            continue
        shape_refs.append(_shape_ref(shape, feature_uuid=feature_uuid, sketch_uuid=sketch_uuid,
                                     sub_shape_name=kind))
        if "mass" in caps:
            mass_probe = getattr(kernel, "mass_properties", None)
            if callable(mass_probe):
                try:
                    mass = mass_probe(shape)
                    mass_dict = mass.as_dict() if hasattr(mass, "as_dict") else mass
                    shape_refs[-1]["mass"] = _canonical(mass_dict, "$.mass")
                except Exception as exc:  # noqa: BLE001 - 质量属性缺席是降级，不是失败
                    diagnostics.append(_diag("warning", "mass_unavailable", feature_uuid=feature_uuid,
                                             detail="%s: %s" % (type(exc).__name__, exc)))
    shape_refs.sort(key=lambda item: (item.get("sketch_uuid", ""), item.get("feature_uuid", ""),
                                      item.get("sub_shape_name", "")))
    deduped = _dedupe_diags(diagnostics)
    ok = not any(item.get("severity") == "error" for item in deduped)
    return RebuildResult(ok, shape_refs, deduped, kernel_name, kernel_version)


# --------------------------------------------------------------------------------------
# 自检（文件内自带入口：python3 <本文件> --selftest）
# --------------------------------------------------------------------------------------


def _fixture_path() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "tests", "data", "plate.json")


def _sample_document() -> Document:
    """自检用最小文档（一草图一拉伸）：**不依赖夹具文件**，保证阴性对照随时可跑。"""
    sketch_uuid = "a11ce000-0000-4000-8000-0000000000a0"
    doc = new_document()
    doc.sketches = [{
        "uuid": sketch_uuid, "plane": "XY",
        "entities": [{"uuid": "a11ce000-0000-4000-8000-0000000000a1", "kind": "line", "layer": "0",
                      "construction": False,
                      "geom": {"x1": 0, "y1": 0, "x2": 1000000, "y2": 0}}],
        "constraints": [], "dimensions": [],
        "solved": {"dof": -1, "converged": False, "cache": True},
    }]
    doc.features = [{"uuid": "a11ce000-0000-4000-8000-0000000000a2", "kind": "extrude",
                     "inputs": [sketch_uuid],
                     "params": {"distance_nm": 1000000, "symmetric": False, "angle_deg": "0",
                                "op": "new"},
                     "suppressed": False}]
    return doc


def _broken_kernel() -> Any:
    """阴性对照内核：任何几何操作都抛 `DependencyMissing`（证明降级信号真被捕获）。"""
    class _Broken:
        name = "broken"
        version = "0.0.0"

        def available(self) -> bool:
            return True

        def capabilities(self) -> frozenset[str]:
            return frozenset({"sketch", "extrude"})

        def extrude(self, profile: Any, *, distance_nm: int, symmetric: bool = False) -> Any:
            raise DependencyMissing("backend is unavailable for this capability",
                                    reason="capability_missing", capability="extrude")

    return _Broken()


def _real_kernel() -> Any:
    from .kernel.base import get_kernel
    return get_kernel("nano")


def selftest() -> int:
    """自带自检：确定性双跑 sha256 / 迁移 / 不变量阴阳对照 / 降级捕获 / 落盘往返。"""
    import hashlib
    import tempfile

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> bool:
        checks.append((name, bool(passed), detail))
        return bool(passed)

    doc = new_document()
    check("new_document_schema", doc.schema_version == SCHEMA_VERSION, "schema=%r" % doc.schema_version)
    check("new_document_uuid", _uuid_ok(doc.meta.get("doc_uuid")), "uuid=%s" % doc.meta.get("doc_uuid"))
    check("new_document_empty", not (doc.sketches or doc.features or doc.annotations or doc.sheets))

    text_a = document_to_l1(doc)
    text_b = document_to_l1(doc)
    sha_a = hashlib.sha256(text_a.encode("utf-8")).hexdigest()
    sha_b = hashlib.sha256(text_b.encode("utf-8")).hexdigest()
    check("byte_identical", text_a == text_b and sha_a == sha_b, "sha256=%s" % sha_a)
    check("newline_fixed", text_a.endswith("\n") and "\r" not in text_a)
    check("keys_sorted", list(json.loads(text_a)) == sorted(json.loads(text_a)))
    check("roundtrip_stable", document_to_l1(document_from_l1(text_a)) == text_a)

    legacy = {"meta": {"doc_uuid": "a11ce000-0000-4000-8000-0000000000ff", "title_key": "doc.untitled"},
              "legacy_only_field": {"kept": [1, 2, 3]}}
    migrated = migrate(legacy)
    check("legacy_migrated", migrated.get("schema_version") == SCHEMA_VERSION)
    check("legacy_unknown_kept", migrated.get("legacy_only_field") == {"kept": [1, 2, 3]})
    codes = {item.get("code") for item in migrated.get(DIAGNOSTICS_KEY, [])}
    check("legacy_diag", "legacy_version_stamped" in codes and "unknown_field_preserved" in codes,
          "codes=%s" % sorted(codes))
    check("unknown_field_survives_roundtrip",
          document_from_l1(document_to_l1(migrated)).extra.get("legacy_only_field") == {"kept": [1, 2, 3]})
    try:
        migrate({"schema_version": 2, "meta": {"doc_uuid": "x"}})
        check("no_downgrade", False, "schema_too_new not raised")
    except DocumentError as exc:
        check("no_downgrade", exc.reason == "schema_too_new", "reason=%s" % exc.reason)
    saved = dict(_MIGRATIONS)
    try:
        def _skipping(payload: dict[str, Any], _diags: list[dict[str, Any]]) -> dict[str, Any]:
            out = dict(payload)
            out["schema_version"] = 2  # 一次跳两版：必须被拒
            return out

        _MIGRATIONS[0] = _skipping
        try:
            migrate({"meta": {"doc_uuid": "x"}}, target=1)
            check("no_version_skip", False, "bad_migration_step not raised")
        except DocumentError as exc:
            check("no_version_skip", exc.reason == "bad_migration_step", "reason=%s" % exc.reason)
    finally:
        _MIGRATIONS.clear()
        _MIGRATIONS.update(saved)

    fixture = _fixture_path()
    if os.path.isfile(fixture):
        with open(fixture, "r", encoding="utf-8") as handle:
            raw = handle.read()
        model = document_from_l1(raw)
        check("fixture_loads", model.schema_version == SCHEMA_VERSION and len(model.sketches) == 1)
        check("fixture_migrate_clean", migrate(json.loads(raw)).get(DIAGNOSTICS_KEY) is None)
        problems = check_invariants(model)
        check("fixture_invariants", problems == [], "problems=%s" % problems[:3])
        mutated = json.loads(raw)
        mutated["sketches"][0]["entities"][0]["geom"]["x1"] = 0.5
        mutated["sketches"][0]["constraints"][0]["refs"][0][1] = "0"
        mutated["sketches"][0]["dimensions"][0]["text_key"] = "Width is 120 mm"
        bad = check_invariants(mutated)
        check("negative_nm_float", any(item.startswith("geom_length_not_integer") for item in bad))
        check("negative_index_ref", any(item.startswith("ref_index_used") for item in bad))
        check("negative_literal_text", any(item.startswith("text_not_key") for item in bad))
    else:
        check("fixture_present", False, "missing %s" % fixture)

    sample = _sample_document()
    result = recompute(sample, _broken_kernel())
    check("dependency_missing_captured",
          result.ok is False and any(item.get("code") == "dependency_missing"
                                     for item in result.diagnostics),
          "diag=%s" % result.diagnostics)
    check("null_kernel_captured", recompute(sample, None).ok is False)
    check("sample_invariants", check_invariants(sample) == [], "problems=%s"
          % check_invariants(sample)[:3])
    try:
        kernel = _real_kernel()
    except Exception as exc:  # noqa: BLE001 - 内核缺席是 NA，不是自检失败
        check("nano_kernel_na", True, "NA: %s: %s" % (type(exc).__name__, exc))
    else:
        real = recompute(document_from_l1(raw) if os.path.isfile(fixture) else sample, kernel)
        check("nano_rebuild_ok", real.ok is True and len(real.shape_refs) >= 1,
              "shape_refs=%d diag=%s" % (len(real.shape_refs), real.diagnostics[:2]))
        check("nano_sample_rebuild_ok", recompute(sample, kernel).ok is True)
        check("nano_rebuild_pure", document_to_l1(doc) == text_a)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "nested", "doc.l1.json")
        returned = save_l1(doc, path)
        check("save_returns_path", returned == path and os.path.isfile(path))
        check("save_load_roundtrip", document_to_l1(load_l1(path)) == text_a)

    failed = [name for name, passed, _ in checks if not passed]
    for name, passed, detail in checks:
        print("  [%s] %s%s" % ("PASS" if passed else "FAIL", name, (" :: " + detail) if detail else ""))
    print("DOCMODEL_SELFTEST: verdict=%s checks=%d failed=%d byte_identical=%d sha256_a=%s "
          "sha256_b=%s fixture=%s" % ("PASS" if not failed else "FAIL", len(checks), len(failed),
                                      1 if text_a == text_b else 0, sha_a, sha_b,
                                      "present" if os.path.isfile(fixture) else "absent"))
    return 0 if not failed else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("--selftest", "-s"):
        return selftest()
    if args[0] == "--hash" and len(args) == 2:
        import hashlib
        target = os.fsdecode(os.fspath(args[1]))
        text = document_to_l1(load_l1(target))
        data = text.encode("utf-8")
        print("DOCMODEL_HASH: path=%s bytes=%d sha256=%s"
              % (target, len(data), hashlib.sha256(data).hexdigest()))
        return 0
    if args[0] in ("-h", "--help"):
        print("usage: docmodel.py [--selftest] [--hash <l1-path>]")
        return 0
    print("unknown argument: %s" % args[0])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
