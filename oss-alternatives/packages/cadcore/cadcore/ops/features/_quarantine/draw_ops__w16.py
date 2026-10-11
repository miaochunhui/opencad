#!/usr/bin/env python3
"""DRAW_OPS 族 · 片 w16：图案填充与渐变色（HATCH / GRADIENT）在 L1 文档模型上的实现。

本片负责哪一条（任务 ①②的落点）
----------------------------------
读 `docs/research/cad-parity/features/autocad-draw.json`（must_have 清单）与
`docs/research/cad-parity/status/autocad-draw.json`（逐条 status），把该族**仍 missing/partial**
的 must_have 按清单顺序编号 0..N-1（status 记录 implemented 的 5 条不计入：椭圆与椭圆弧 /
样条曲线 / 构造线与射线 / 面域 / 圆环；missing=0，故 N = 15 条 partial）：

::

    0  直线                        8  边界创建
    1  多段线                      9  修订云线
    2  圆                         10  对象捕捉
    3  圆弧                       11  正交与极轴追踪
    4  矩形                       12  对象捕捉追踪与临时追踪点
    5  正多边形                   13  动态输入
    6  点与点样式                 14  当前图层与对象特性
    7  图案填充与渐变色  <== 本片

**7 号 = 图案填充与渐变色（HATCH/GRADIENT；关联填充、HPBOUND）**。本文件只实现这一条，
不改别人名下的任何文件（本目录的 `__init__.py` 归别的席，本片不写）。

族接口（冻结）
--------------
::

    FAMILY = "draw_ops"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]     # 返回本次新建对象的 uuid 列表

长度一律**整数纳米**（`*_nm`，bool 与非整数浮点一律拒），角度用 `*_deg`（float）。
可预期错误一律抛 `cadcore.errors.CadError(reason=...)`；本模块自己校验的入参用本层
`CadError`，转发给 `sketch.commands` / `sketch.primitives` 的调用统一走 `_delegate()`，
把上游任何异常收敛成带 `reason` 的 `CadError`（不让上游另立的错误类型漏出族接口）。

L1 装得下什么、装不下什么（先写死口径，别让下游靠猜）
--------------------------------------------------------
* **装得下（本片真做）**：图案填充的**真几何**。给定边界环与图案（ANSI31 单 45° / ANSI37 十字 /
  USER 自定角度+间距），把一族平行线 `{p : p·n = k·间距}` 与边界环逐边求交、交点按沿线参数排序后
  **偶数-奇数配对**成环内区间（凹环得到多区间），每条区间落成一个真 `line` 实体；端点取整到整数
  纳米（≤0.5 nm/端点），并且**落库之前**逐条核对「区间中点确实在环内」——不满足就抛错、一个实体
  都不建（绝不半接受）。渐变色落成**色彩分带的几何分解**：沿渐变方向把凸边界环切成 `bands` 个
  真闭合多边形（Sutherland–Hodgman 半平面裁剪），落库前核对「各带面积之和 == 环面积」。
* **装不下（显式缺口，不假装）**：**颜色**本身。L1 实体没有 `color` / `linetype` / `lineweight`
  字段（status 记录：CELTYPE/LTSCALE 全仓无命中；「当前图层与对象特性」正是因此仍 partial）。
  所以：①`pattern="SOLID"` 只落**闭合边界对象**（HPBOUND 口径的几何载体），实心**着色**不可表达；
  ②`gradient_create` 落的是**分带几何**，两色/色标（色彩斜坡）仍缺。这两条写在下面的 docstring
  与自检判定行 `gradient_colors=missing_l1` 里，不抬成"渐变已实现"。
* **另两处 L1 缺口**（宁可显式报错，不做弦近似静默改几何）：圆/弧边界没有顶点串 ⇒
  `CadError(reason="unsupported_geometry")`；`gradient_create` 的分带裁剪要求**凸**边界环
  （凹环上 Sutherland–Hodgman 会产出跨凹口的"桥"多边形），凹环同样抛 `unsupported_geometry`。

复用了什么（先读再写，不另立副本）
------------------------------------
* `cadcore.sketch.primitives`：`add_polyline`（闭合边界对象与渐变带的**唯一**落库实现）、
  `closed_profiles`（闭合轮廓**唯一**尺子，`boundary_refs` 的合法性就按它判）；
* `cadcore.sketch.commands`：`add_line`（填充线的**唯一**落库实现，含零长线校验）；
* `cadcore.sketch.constraints`：`sketch_entities`（实体表唯一读法）；
* 兄弟模块 `cadcore.ops.draw_ops` 的 `_polygon_twice_area`（整数鞋带面积）：取得到就用它（软复用），
  取不到（例如本文件被单独拷走）本地兜底，两者口径相同（都是整数 2×带符号面积）。
  **环顶点读法**（`_ring_vertices`）本文件自带一份，口径与 `draw_ops._ring_vertices` 逐条对齐
  （单成员闭合折线取存储点；多成员 line 环按环序串接；遇 arc 抛 unsupported_geometry），
  理由：那是兄弟模块的**私有**函数、由别的席维护，跨模块引用私有名会把本片的绿挂到别人的改动上。

自检（文件自带，不依赖 pytest）
--------------------------------

    python3 cadcore/ops/features/draw_ops__w16.py

打印判定行 `FEATURE_OK family=draw_ops op=hatch_create verdict=PASS checks=<n> ...`，
FAIL 时打印 `FEATURE_FAIL ...` 并 rc=1。自检夹具先**自证是 L1 真件**（真容器 + 真几何往返），
尺子另带**阴性对照**（环外点必须判 False），夹具找不到可用 L1 容器时**主动报红**，不静默降级。

中文只出现在 docstring/注释里：面向机器的字符串字面量（错误文案、判定行）一律 ASCII。
"""

from __future__ import annotations

import copy
import importlib
import math
import os
import re
import sys
import types
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.sketch import commands as _commands
from cadcore.sketch import constraints as _constraints
from cadcore.sketch import primitives as _primitives

try:  # 软复用：兄弟模块的整数鞋带面积（纯函数，无副作用）
    from ..draw_ops import _polygon_twice_area as _sibling_twice_area  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 - 兄弟模块不在（脚本运行）或签名变了：本地兜底
    try:
        from cadcore.ops.draw_ops import (  # type: ignore[attr-defined,no-redef]
            _polygon_twice_area as _sibling_twice_area,
        )
    except Exception:  # noqa: BLE001
        _sibling_twice_area = None  # type: ignore[assignment]

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "HATCH_PATTERNS",
    "HATCH_ANGLES",
    "DEFAULT_SPACING_NM",
    "hatch_create",
    "gradient_create",
    "selftest",
]

#: 族 id（冻结接口）。
FAMILY = "draw_ops"

#: 支持的图案（冻结枚举；`SOLID` 走"闭合边界对象"分支，见模块 docstring 的缺口说明）。
HATCH_PATTERNS: tuple[str, ...] = ("ANSI31", "ANSI37", "SOLID", "USER")

#: 固定图案的角度口径（ANSI31 = 单 45°，ANSI37 = 45° + 135° 十字）。
HATCH_ANGLES: dict[str, tuple[float, ...]] = {"ANSI31": (45.0,), "ANSI37": (45.0, 135.0)}

#: 图案缺省间距（纳米）= 3 mm（ANSI31/ANSI37 的图案比例；`USER` 必须显式给）。
DEFAULT_SPACING_NM: int = 3_000_000

#: 交点参数去重容差（纳米）：远小于 1 nm 分辨率，只为吸收浮点重复点。
_PARAM_TOL_NM: float = 1e-3

#: "贴边"判定容差（纳米）：中点到环的距离 ≤ 它 ⇒ 该弦与边界重合，不算内部弦。
_EXACT_TOL_NM: float = 1e-6

#: 取整后中点允许的容差（纳米）：≤0.5 nm/端点的取整误差不该把整条弦判红。
_MIDPOINT_TOL_NM: float = 2.0

#: 渐变分带面积和的允许误差（纳米² = 环周长(nm) × 本值(nm)）。
_AREA_TOL_NM: float = 8.0

#: L1 草图容器发现用：候选模块与候选属性名（真件优先，取不到才退到最小容器）。
_SKETCH_MODULES: tuple[str, ...] = (
    "cadcore.document", "cadcore.doc", "cadcore.documents", "cadcore.model",
    "cadcore.models", "cadcore.data", "cadcore.core", "cadcore.store",
    "cadcore.sketch", "cadcore.sketch.model", "cadcore.sketch.document",
    "cadcore.sketch.sketch", "cadcore.sketch.data", "cadcore.sketch.core",
    "cadcore.sketch.state", "cadcore.sketch.build", "cadcore.sketch.store",
    "cadcore.sketch.primitives", "cadcore.sketch.commands", "cadcore.sketch.constraints",
    "cadcore.ops",
)
_SKETCH_ATTRS: tuple[str, ...] = (
    "Sketch", "new_sketch", "empty_sketch", "create_sketch", "make_sketch",
    "blank_sketch", "sketch_new", "SketchModel", "SketchData",
    "Document", "new_document", "empty_document", "create_document",
    "Doc", "new_doc", "Drawing", "new_drawing",
)
_SKETCH_NAME_RE = re.compile(r"(?i)(sketch|document|drawing)")


# --------------------------------------------------------------------------------------
# 错误与入参校验（全部在落库之前跑完：绝不半接受）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造一条带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(str(message), reason=str(reason), **details)


def _delegate(callable_: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """转发到上游复用模块，并把**任何**上游异常收敛成族接口的 `CadError`。"""
    try:
        return callable_(*args, **kwargs)
    except CadError:
        raise
    except Exception as exc:  # noqa: BLE001 - 这里是族接口的错误收敛边界
        reason = str(getattr(exc, "reason", "") or "").strip() or "geometry_invalid"
        detail = str(getattr(exc, "message", "") or exc)
        raise CadError(detail, reason=reason, source=type(exc).__name__) from exc


def _nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米（bool 不算数；非整数浮点 / 非数 / 非正一律报错）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_length", f"{what} must be an integer nanometre value, got {value!r}", what=what)
    if isinstance(value, float):
        if not math.isfinite(value) or float(value) != float(int(value)):
            raise _fail("invalid_length", f"{what} must be a whole number of nm, got {value!r}", what=what)
        value = int(value)
    if positive and int(value) <= 0:
        raise _fail("invalid_length", f"{what} must be > 0 nm, got {int(value)}", what=what)
    return int(value)


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)`（必须是长度为 2 的序列，坐标各自过 `_nm`）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("invalid_argument", f"{what} must be a (x_nm, y_nm) pair, got {value!r}", what=what)
    return (_nm(value[0], what=f"{what}.x"), _nm(value[1], what=f"{what}.y"))


def _points_nm(value: Any, *, what: str) -> list[tuple[int, int]]:
    """点串入参 → `[(x_nm, y_nm), ...]`。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("invalid_argument", f"{what} must be a sequence of (x_nm, y_nm) pairs, got {value!r}", what=what)
    return [_point(item, what=f"{what}[{index}]") for index, item in enumerate(value)]


def _count(value: Any, *, what: str, minimum: int) -> int:
    """计数入参 → int（bool 不算数；`< minimum` 报错）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", f"{what} must be an integer, got {value!r}", what=what)
    if int(value) < int(minimum):
        raise _fail("invalid_argument", f"{what} must be >= {minimum}, got {int(value)}", what=what)
    return int(value)


def _angle(value: Any, *, what: str = "angle_deg") -> float:
    """角度入参 → 有限 float（bool 与非数报错）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_argument", f"{what} must be a finite number, got {value!r}", what=what)
    number = float(value)
    if not math.isfinite(number):
        raise _fail("invalid_argument", f"{what} must be finite, got {value!r}", what=what)
    return number


def _layer(value: Any) -> str:
    """图层名 → 非空 str。"""
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", f"layer must be a non-empty string, got {value!r}")
    return value


def _token(value: Any, *, what: str, allowed: Sequence[str]) -> str:
    """枚举入参 → 命中 `allowed` 的 str。"""
    if not isinstance(value, str) or value not in allowed:
        raise _fail("invalid_argument", f"{what} must be one of {list(allowed)}, got {value!r}", what=what)
    return value


def _refs_list(refs: Any) -> list[str]:
    """引用列表 → 非空 uuid 串列表（裸字符串是非法入参：逐字符迭代会静默变成 N 个引用）。"""
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence) or len(refs) == 0:
        raise _fail("empty_selection",
                    f"boundary_refs must be a non-empty sequence of entity uuids, got {refs!r}")
    out: list[str] = []
    for index, item in enumerate(refs):
        if not isinstance(item, str) or not item:
            raise _fail("invalid_argument",
                        f"boundary_refs[{index}] must be a non-empty uuid string, got {item!r}")
        out.append(item)
    return out


# --------------------------------------------------------------------------------------
# 结构访问（`sk_or_doc` → 草图；实体读取），结构取值映射/对象双兼容
# --------------------------------------------------------------------------------------


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """结构取值（映射 / 对象双兼容）。"""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _sketch_of(target: Any, *, what: str = "sk_or_doc") -> Any:
    """`sk_or_doc` → 草图：①含 `entities` 的映射本身；②文档（`sketches[0]`）。

    解析不出来抛 `CadError(reason="no_sketch")` —— **不**凭空造一张空草图（那会把"没给草图"
    伪装成"画成功了只是看不见"）。
    """
    entities = _field(target, "entities")
    if isinstance(entities, list):
        return target
    sketches = _field(target, "sketches")
    if isinstance(sketches, Sequence) and not isinstance(sketches, (str, bytes)) and len(sketches) > 0:
        first = sketches[0]
        if isinstance(_field(first, "entities"), list):
            return first
        raise _fail("no_sketch", f"{what}.sketches[0] has no entity list", what=what)
    raise _fail("no_sketch", f"{what} is neither a sketch (entities) nor a document (sketches)", what=what)


def _entity_uuid(entity: Any) -> str:
    """实体 uuid → str（缺失返回空串；不猜、不补）。"""
    value = _field(entity, "uuid", "")
    return value if isinstance(value, str) else ""


def _entity_kind(entity: Any) -> str:
    """实体 kind → str（缺失返回空串）。"""
    value = _field(entity, "kind", "")
    return value if isinstance(value, str) else ""


def _geom_of(entity: Any) -> Mapping[str, Any]:
    """实体 geom → 映射（不是映射即拒，避免写回时静默丢改动）。"""
    geom = _field(entity, "geom", None)
    if not isinstance(geom, Mapping) or isinstance(geom, (str, bytes)):
        raise _fail("unsupported_geometry", f"entity geom must be a mapping, got {type(geom).__name__}")
    return geom  # type: ignore[return-value]


def _find_entity(sketch: Any, ref: Any, *, kind: str | None = None) -> Any:
    """按 uuid（或带 uuid 的实体对象）在草图里取实体；`kind` 给定则校验种类。"""
    target = ref if isinstance(ref, str) else _field(ref, "uuid")
    if not isinstance(target, str) or not target:
        raise _fail("invalid_argument", f"ref must be an entity uuid, got {ref!r}")
    for entity in _constraints.sketch_entities(sketch):
        if _entity_uuid(entity) != target:
            continue
        if kind is not None and _entity_kind(entity) != kind:
            raise _fail("unsupported_geometry",
                        f"{target} is a {_entity_kind(entity)!r}, this operation needs {kind!r}",
                        uuid=target, kind=_entity_kind(entity))
        return entity
    raise _fail("unknown_reference", f"no entity with uuid {target!r} in this sketch", uuid=target)


def _polyline_points(entity: Any) -> list[tuple[int, int]]:
    """折线实体的存储点串 → `[(x_nm, y_nm), ...]`（先全量校验，坏数据当场拒）。"""
    return _points_nm(_field(_geom_of(entity), "points"), what="polyline.points")


def _is_closed_points(points: Sequence[tuple[int, int]]) -> bool:
    """几何闭合判据：首末点坐标**严格相等**且点数 ≥ 4（与兄弟模块同口径）。"""
    return len(points) >= 4 and points[0] == points[-1]


def _validate_polyline(points: Sequence[tuple[int, int]], *, closed: bool, what: str = "polyline") -> None:
    """落库前的折线形状校验（口径与 `primitives.add_polyline` 对齐）。"""
    if len(points) < 2:
        raise _fail("invalid_argument", f"{what} needs at least 2 points, got {len(points)}")
    for previous, current in zip(points, points[1:]):
        if previous == current:
            raise _fail("invalid_argument", f"{what} has a zero-length segment at {current!r}")
    if closed:
        if points[0] != points[-1]:
            raise _fail("invalid_argument", f"closed {what} must repeat its first point at the end")
        if len(set(points[:-1])) < 3:
            raise _fail("invalid_argument",
                        f"a closed {what} needs at least 3 distinct vertices, got {len(set(points[:-1]))}")


# --------------------------------------------------------------------------------------
# 边界环：点串入参 / 既有闭合轮廓引用 两条路径（都先校验，后落库）
# --------------------------------------------------------------------------------------


def _ring_from_points(points_nm: Any, *, what: str) -> list[tuple[int, int]]:
    """显式点串 → 环顶点（去掉末尾重复首点；<3 个不同顶点 / 零长边一律拒）。"""
    points = _points_nm(points_nm, what=what)
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) < 3 or len(set(points)) < 3:
        raise _fail("profile_not_closed",
                    f"{what} needs at least 3 distinct vertices, got {len(set(points))}", what=what)
    for index, (first, second) in enumerate(zip(points, points[1:] + [points[0]])):
        if first == second:
            raise _fail("invalid_argument", f"{what} has a zero-length edge at index {index}")
    return points


def _ring_vertices(sketch: Any, loop: Sequence[str]) -> list[tuple[int, int]]:
    """闭合环（uuid 环）→ 顶点串（**不含**末尾重复的首点）。

    口径与 `draw_ops._ring_vertices` 逐条对齐（本文件自带一份，理由见模块 docstring）：

    - 单成员 `polyline` ⇒ 取存储点（首末必须重合）；
    - 单成员 `circle` ⇒ 抛 `unsupported_geometry`（圆没有顶点串，调用方自行处理）；
    - 多成员 `line` 环 ⇒ 按环序首尾相接串成顶点串；
    - 环里有 `arc` ⇒ 抛 `unsupported_geometry`：**弦近似会静默改几何**，L1 宁可显式不支持。
    """
    members = list(loop)
    if len(members) == 1:
        entity = _find_entity(sketch, members[0])
        kind = _entity_kind(entity)
        if kind == "circle":
            raise _fail("unsupported_geometry", f"{members[0]} is a circle: no vertex ring")
        if kind != "polyline":
            raise _fail("unsupported_geometry",
                        f"single-member ring must be a circle or a closed polyline, got {kind!r}")
        points = _polyline_points(entity)
        if not _is_closed_points(points):
            raise _fail("profile_not_closed", f"polyline {members[0]} is not closed")
        return list(points[:-1])

    vertices: list[tuple[int, int]] = []
    for ref in members:
        entity = _find_entity(sketch, ref)
        kind = _entity_kind(entity)
        if kind == "arc":
            raise _fail("unsupported_geometry",
                        f"ring member {ref} is an arc: chord approximation is not geometry")
        if kind != "line":
            raise _fail("unsupported_geometry", f"ring member {ref} has kind {kind!r}, expected line")
        geom = _geom_of(entity)
        first = (_nm(_field(geom, "x1"), what="line.x1"), _nm(_field(geom, "y1"), what="line.y1"))
        second = (_nm(_field(geom, "x2"), what="line.x2"), _nm(_field(geom, "y2"), what="line.y2"))
        if not vertices:
            vertices.extend((first, second))
        elif vertices[-1] == first:
            vertices.append(second)
        elif vertices[-1] == second:
            vertices.append(first)
        else:
            raise _fail("profile_not_closed", f"ring member {ref} does not continue at {vertices[-1]!r}")
    if len(vertices) > 1 and vertices[0] == vertices[-1]:
        vertices.pop()
    return _ring_from_points(vertices, what="boundary_refs")


def _ring_from_refs(sketch: Any, refs: Any) -> list[tuple[int, int]]:
    """既有闭合轮廓引用 → 环顶点：`refs` 必须**恰好**是 `closed_profiles()` 里的一个环。"""
    ref_list = _refs_list(refs)
    wanted = set(ref_list)
    for loop in _primitives.closed_profiles(sketch):
        members = [str(item) if isinstance(item, str) else _entity_uuid(item) for item in loop]
        if len(members) != len(ref_list) or set(members) != wanted:
            continue
        return _ring_vertices(sketch, members)
    raise _fail("profile_not_closed",
                "boundary_refs is not one of this sketch's closed profiles (use primitives.closed_profiles)",
                refs=list(ref_list))


def _resolve_ring(sketch: Any, *, boundary_refs: Any, points_nm: Any, op: str) -> list[tuple[int, int]]:
    """`boundary_refs` / `points_nm` **二选一** → 环顶点；两个都给或都不给都报错。"""
    has_refs = boundary_refs is not None
    has_points = points_nm is not None
    if has_refs == has_points:
        raise _fail("invalid_argument",
                    f"{op} needs exactly one of boundary_refs / points_nm "
                    f"(got refs={has_refs}, points={has_points})", op=op)
    if has_points:
        return _ring_from_points(points_nm, what=f"{op}.points_nm")
    return _ring_from_refs(sketch, boundary_refs)


# --------------------------------------------------------------------------------------
# 整数几何：面积 / 周长 / 点是否在环内 / 点到环距离（全部不落桩，现算）
# --------------------------------------------------------------------------------------


def _twice_area(vertices: Sequence[tuple[int, int]]) -> int:
    """鞋带公式（整数，返回 2×带符号面积；单位 nm²）。优先复用兄弟模块的同一把尺子。"""
    if _sibling_twice_area is not None:
        return int(_sibling_twice_area(vertices))
    total = 0
    count = len(vertices)
    for index, (x, y) in enumerate(vertices):
        next_x, next_y = vertices[(index + 1) % count]
        total += x * next_y - next_x * y
    return total


def _area(vertices: Sequence[tuple[int, int]]) -> int:
    """|带符号面积|（整数 nm²）。"""
    return abs(_twice_area(vertices)) // 2


def _perimeter(vertices: Sequence[tuple[int, int]]) -> int:
    """周长（整数纳米：逐段 `math.isqrt(dx²+dy²)` 向下取整后求和，不引浮点）。"""
    total = 0
    count = len(vertices)
    for index, (x, y) in enumerate(vertices):
        next_x, next_y = vertices[(index + 1) % count]
        total += math.isqrt((next_x - x) * (next_x - x) + (next_y - y) * (next_y - y))
    return total


def _point_in_ring(point: tuple[float, float], ring: Sequence[tuple[int, int]]) -> bool:
    """浮点偶数-奇数射线法（向右射线）；落在边上时结果不作保证（判内部弦前另查距离）。"""
    x, y = point
    inside = False
    count = len(ring)
    for index in range(count):
        ax, ay = float(ring[index][0]), float(ring[index][1])
        bx, by = float(ring[(index + 1) % count][0]), float(ring[(index + 1) % count][1])
        if (ay > y) != (by > y):
            cross_x = ax + (y - ay) * (bx - ax) / (by - ay)
            if cross_x > x:
                inside = not inside
    return inside


def _dist_to_ring(point: tuple[float, float], ring: Sequence[tuple[int, int]]) -> float:
    """点到环（线段集合）的最短距离，单位纳米（浮点）。"""
    best = float("inf")
    count = len(ring)
    for index in range(count):
        ax, ay = float(ring[index][0]), float(ring[index][1])
        bx, by = float(ring[(index + 1) % count][0]), float(ring[(index + 1) % count][1])
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq == 0.0:
            distance = math.hypot(point[0] - ax, point[1] - ay)
        else:
            t = ((point[0] - ax) * dx + (point[1] - ay) * dy) / length_sq
            t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            distance = math.hypot(point[0] - (ax + t * dx), point[1] - (ay + t * dy))
        if distance < best:
            best = distance
    return best


def _midpoint_ok(point: tuple[float, float], ring: Sequence[tuple[int, int]], *, tol_nm: float) -> bool:
    """取整后中点是否可接受：环内，或贴边（取整误差 ≤0.5 nm/端点）而不越界。"""
    if _point_in_ring(point, ring):
        return True
    return _dist_to_ring(point, ring) <= tol_nm


def _is_interior_midpoint(point: tuple[float, float], ring: Sequence[tuple[int, int]]) -> bool:
    """**未取整**区间中点是否真在环内部（不贴边、也不是边界本身）。

    贴边判据（`_dist_to_ring <= _EXACT_TOL_NM`）用来丢掉"与边界共线"的伪弦：
    偶数-奇数配对给出的区间中点在简单多边形内部恒为真，只有边界共线那种退化情形
    会让中点落在边界上，此时**不落库**（否则会画出与边界重合的填充线）。
    """
    if _dist_to_ring(point, ring) <= _EXACT_TOL_NM:
        return False
    return _point_in_ring(point, ring)


def _is_convex(ring: Sequence[tuple[int, int]]) -> bool:
    """凸性判定（整数叉积；共线点不算转折；全部共线判非凸）。"""
    count = len(ring)
    sign = 0
    for index in range(count):
        ax, ay = ring[index]
        bx, by = ring[(index + 1) % count]
        cx, cy = ring[(index + 2) % count]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross == 0:
            continue
        current = 1 if cross > 0 else -1
        if sign == 0:
            sign = current
        elif current != sign:
            return False
    return sign != 0


# --------------------------------------------------------------------------------------
# 图案填充真几何：一族平行线 × 边界环求交、参数排序、偶数-奇数配对成环内区间
# --------------------------------------------------------------------------------------


def _axes(angle_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """角度 → `(沿线单位向量 u, 法向单位向量 n)`；直线族写作 `{p : p·n = c}`。"""
    radians = math.radians(angle_deg)
    return (math.cos(radians), math.sin(radians)), (-math.sin(radians), math.cos(radians))


def _interval_endpoints(ring: Sequence[tuple[int, int]], c: float,
                        u: tuple[float, float], n: tuple[float, float]) -> list[tuple[
                            tuple[float, float], tuple[float, float]]]:
    """一条图案线与环的交点 → 环内区间（未取整）；偶数-奇数配对，凹环得到多段。"""
    hits: list[tuple[float, tuple[float, float]]] = []
    count = len(ring)
    for index in range(count):
        ax, ay = float(ring[index][0]), float(ring[index][1])
        bx, by = float(ring[(index + 1) % count][0]), float(ring[(index + 1) % count][1])
        d_a = ax * n[0] + ay * n[1] - c
        d_b = bx * n[0] + by * n[1] - c
        if d_a == 0.0 and d_b == 0.0:  # 边与图案线共线：两个端点都算交点
            hits.append((ax * u[0] + ay * u[1], (ax, ay)))
            hits.append((bx * u[0] + by * u[1], (bx, by)))
        elif d_a == 0.0:
            hits.append((ax * u[0] + ay * u[1], (ax, ay)))
        elif d_b == 0.0:
            hits.append((bx * u[0] + by * u[1], (bx, by)))
        elif (d_a < 0.0) != (d_b < 0.0):
            t = d_a / (d_a - d_b)
            x = ax + t * (bx - ax)
            y = ay + t * (by - ay)
            hits.append((x * u[0] + y * u[1], (x, y)))
    hits.sort(key=lambda item: item[0])
    unique: list[tuple[float, tuple[float, float]]] = []
    for param, point in hits:
        if unique and abs(param - unique[-1][0]) <= _PARAM_TOL_NM:
            continue
        unique.append((param, point))
    intervals: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for index in range(0, len(unique) - 1, 2):
        intervals.append((unique[index][1], unique[index + 1][1]))
    return intervals


def _hatch_segments(ring: Sequence[tuple[int, int]], *, angle_deg: float,
                    spacing_nm: int) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """一族间距 `spacing_nm`、角度 `angle_deg` 的平行线 → 环内**真**线段（取整到整数纳米）。

    落库前逐条核对：①未取整中点必须在环**内部**（`_is_interior_midpoint`）；②取整后中点不得
    越界（`_midpoint_ok`，容差 `_MIDPOINT_TOL_NM`）。任一条不满足即抛错，调用方一个实体都不建。
    """
    u, n = _axes(angle_deg)
    projections = [x * n[0] + y * n[1] for x, y in ring]
    lowest, highest = min(projections), max(projections)
    first_k = math.ceil(lowest / spacing_nm - 1e-9)
    last_k = math.floor(highest / spacing_nm + 1e-9)
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for k in range(first_k, last_k + 1):
        c = k * spacing_nm
        for start, end in _interval_endpoints(ring, c, u, n):
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if not _is_interior_midpoint(midpoint, ring):
                continue  # 贴边/共线的退化弦：不是环内填充线
            p0 = (int(round(start[0])), int(round(start[1])))
            p1 = (int(round(end[0])), int(round(end[1])))
            if p0 == p1:
                continue  # 取整后塌成一点（<1 nm 的薄片）：零长线不能落库
            store_midpoint = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
            if not _midpoint_ok(store_midpoint, ring, tol_nm=_MIDPOINT_TOL_NM):
                raise _fail("draw_ops_hatch_geometry_invalid",
                            f"clipped hatch segment {p0}->{p1} is not inside the ring",
                            p0=list(p0), p1=list(p1))
            segments.append((p0, p1))
    return segments


def hatch_create(
    sk_or_doc: Any,
    *,
    pattern: str = "ANSI31",
    spacing_nm: Any = None,
    angle_deg: Any = None,
    boundary_refs: Any = None,
    points_nm: Any = None,
    layer: str = "0",
) -> list[str]:
    """图案填充（HATCH）：把图案**真几何**裁剪进边界环，返回本次新建对象的 uuid 列表。

    边界（**二选一**，两个都给/都不给都抛 `CadError(reason="invalid_argument")`）：

    - `boundary_refs`：既有闭合轮廓的 uuid（必须**恰好**是 `primitives.closed_profiles()` 里的
      一个环，否则 `CadError(reason="profile_not_closed")`；环里有 arc 或单成员圆 ⇒
      `unsupported_geometry`，不做弦近似）；
    - `points_nm`：显式环顶点（末尾重复首点可省；<3 个不同顶点 / 零长边 ⇒ 报错）。

    图案（`HATCH_PATTERNS`）：

    - `"ANSI31"`：单方向 45°；`"ANSI37"`：45° + 135° 十字；两者的间距 = `spacing_nm`
      （缺省 `DEFAULT_SPACING_NM` = 3 mm）；显式给 `angle_deg` 一律报错（固定图案的角度不可改，
      要自定角度请用 `"USER"`）；
    - `"USER"`：`spacing_nm` 与 `angle_deg` **都必须显式给**；
    - `"SOLID"`：**没有填充线**，落一个与边界同顶点的**闭合 `polyline`**（HPBOUND 口径的几何载体），
      返回 `[uuid]`；给 `spacing_nm` / `angle_deg` 一律报错。**实心着色装不下** —— L1 实体没有
      `color` 字段（见模块 docstring 的缺口说明），本分支不假装能上色。

    返回：`ANSI31`/`ANSI37`/`USER` ⇒ 每条环内线段一个**新建 `line`** 的 uuid（顺序 = 先按角度，
    再按偏移 `k` 从小到大，最后按同一条线上的区间顺序）；一条线段都落不下（例如间距比环还大）
    ⇒ 返回 `[]`（**真为零**，与"没算"不同形：任何没算/算错的情形都抛错）。
    落库前全量校验 + 环内核对跑完，绝不半接受。
    """
    sketch = _sketch_of(sk_or_doc)
    name = _token(pattern, what="pattern", allowed=HATCH_PATTERNS)
    layer_name = _layer(layer)
    ring = _resolve_ring(sketch, boundary_refs=boundary_refs, points_nm=points_nm, op="hatch_create")

    if name == "SOLID":
        if spacing_nm is not None or angle_deg is not None:
            raise _fail("invalid_argument",
                        "pattern='SOLID' takes no spacing_nm / angle_deg (there are no pattern lines)")
        points = list(ring) + [ring[0]]
        _validate_polyline(points, closed=True, what="hatch_create(solid)")
        return [str(uuid_) for uuid_ in
                _delegate(_primitives.add_polyline, sketch, points, closed=True, layer=layer_name)]

    if name == "USER":
        if spacing_nm is None or angle_deg is None:
            raise _fail("invalid_argument", "pattern='USER' needs both spacing_nm and angle_deg")
        angles = [_angle(angle_deg, what="angle_deg")]
    else:
        if angle_deg is not None:
            raise _fail("invalid_argument",
                        f"pattern={name!r} has a fixed angle; use pattern='USER' to choose one")
        angles = list(HATCH_ANGLES[name])

    spacing = DEFAULT_SPACING_NM if spacing_nm is None else _nm(spacing_nm, what="spacing_nm", positive=True)
    segments: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for angle in angles:
        segments.extend(_hatch_segments(ring, angle_deg=angle, spacing_nm=spacing))
    created: list[str] = []
    for first, second in segments:
        created.append(str(_delegate(_commands.add_line, sketch, p1=first, p2=second, layer=layer_name)))
    return created


# --------------------------------------------------------------------------------------
# 渐变色：沿渐变方向的色彩分带（几何分解，凸环精确裁剪；颜色本身 L1 装不下）
# --------------------------------------------------------------------------------------


def _clip_half_plane(polygon: Sequence[tuple[float, float]],
                     normal: tuple[float, float], bound: float) -> list[tuple[float, float]]:
    """Sutherland–Hodgman 半平面裁剪：保留 `normal·p <= bound` 的一侧（凸多边形精确）。"""
    out: list[tuple[float, float]] = []
    count = len(polygon)
    if count == 0:
        return out
    for index in range(count):
        current = polygon[index]
        following = polygon[(index + 1) % count]
        d_current = normal[0] * current[0] + normal[1] * current[1] - bound
        d_following = normal[0] * following[0] + normal[1] * following[1] - bound
        current_inside = d_current <= 0.0
        following_inside = d_following <= 0.0
        if current_inside:
            out.append(current)
        if current_inside != following_inside:
            denominator = d_current - d_following
            if denominator != 0.0:
                t = d_current / denominator
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
                out.append((current[0] + t * (following[0] - current[0]),
                            current[1] + t * (following[1] - current[1])))
    return out


def gradient_create(
    sk_or_doc: Any,
    *,
    bands: Any = 8,
    angle_deg: Any = 0.0,
    boundary_refs: Any = None,
    points_nm: Any = None,
    layer: str = "0",
) -> list[str]:
    """渐变色（GRADIENT）的**几何分解**：沿渐变方向把边界环切成 `bands` 个真闭合多边形。

    边界口径与 `hatch_create` 完全相同（`boundary_refs` / `points_nm` 二选一）。
    `angle_deg` 是渐变方向（逆时针为正，0° = 沿 +X 从低到高），`bands >= 2` 按沿该方向的
    投影区间等分；第 k 带 = 环 ∩ {`left_k <= p·d <= right_k`}（Sutherland–Hodgman 半平面裁剪），
    取整到整数纳米后落成一个**新建的闭合 `polyline`**，返回按 k 递增的 uuid 列表。

    口径与缺口（**不许把缺口说成已实现**）：

    - 裁剪要求边界环**凸**（凹环上 SH 会产出跨凹口的"桥"多边形）⇒ 凹环抛
      `CadError(reason="unsupported_geometry")`；圆/弧边界同样 `unsupported_geometry`；
    - 落库前核对「各带面积之和 == 环面积」（容差 = 环周长 × `_AREA_TOL_NM` 纳米²），
      不成立抛 `CadError(reason="draw_ops_gradient_area_mismatch")`，一个实体都不建；
    - 某带在整数纳米下塌成 <3 个不同顶点 ⇒ `CadError(reason="draw_ops_gradient_degenerate")`；
    - **颜色（两色/色标/色彩斜坡）落不了库**：L1 实体没有 `color` 字段（见模块 docstring）。
      本操作给的是色彩分带的**几何载体**；着色本身仍是 missing，自检判定行显式写
      `gradient_colors=missing_l1`。
    """
    sketch = _sketch_of(sk_or_doc)
    count = _count(bands, what="bands", minimum=2)
    angle = _angle(angle_deg)
    layer_name = _layer(layer)
    ring = _resolve_ring(sketch, boundary_refs=boundary_refs, points_nm=points_nm, op="gradient_create")
    if not _is_convex(ring):
        raise _fail("unsupported_geometry",
                    "gradient band clipping in L1 requires a convex boundary ring "
                    "(Sutherland-Hodgman would bridge concave notches)",
                    vertices=len(ring))

    direction = (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
    projections = [x * direction[0] + y * direction[1] for x, y in ring]
    lowest, highest = min(projections), max(projections)
    step = (highest - lowest) / count
    if step <= 0.0:
        raise _fail("invalid_length",
                    f"gradient direction span is {highest - lowest} nm: cannot cut {count} bands")

    float_ring = [(float(x), float(y)) for x, y in ring]
    band_rings: list[list[tuple[int, int]]] = []
    total_area = 0
    for index in range(count):
        left = lowest + step * index
        right = highest if index == count - 1 else lowest + step * (index + 1)
        clipped = _clip_half_plane(float_ring, (-direction[0], -direction[1]), -left)
        clipped = _clip_half_plane(clipped, direction, right)
        points: list[tuple[int, int]] = []
        for x, y in clipped:
            candidate = (int(round(x)), int(round(y)))
            if not points or points[-1] != candidate:
                points.append(candidate)
        if len(points) >= 2 and points[0] == points[-1]:
            points.pop()
        if len(set(points)) < 3:
            raise _fail("draw_ops_gradient_degenerate",
                        f"band {index} collapses at integer-nm resolution", band=index)
        area = _area(points)
        if area <= 0:
            raise _fail("draw_ops_gradient_degenerate", f"band {index} has zero area", band=index)
        total_area += area
        band_rings.append(points)

    ring_area = _area(ring)
    tolerance = _perimeter(ring) * _AREA_TOL_NM
    if abs(total_area - ring_area) > tolerance:
        raise _fail("draw_ops_gradient_area_mismatch",
                    f"band areas sum to {total_area} nm^2, ring is {ring_area} nm^2",
                    total_area_nm2=total_area, ring_area_nm2=ring_area)

    created: list[str] = []
    for points in band_rings:
        closed_points = points + [points[0]]
        _validate_polyline(closed_points, closed=True, what="gradient_create")
        created.extend(str(uuid_) for uuid_ in
                       _delegate(_primitives.add_polyline, sketch, closed_points,
                                 closed=True, layer=layer_name))
    return created


#: 族操作表（冻结接口）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "hatch_create": hatch_create,
    "gradient_create": gradient_create,
}


# --------------------------------------------------------------------------------------
# 自检：L1 夹具先自证是真件，再对真几何/真后果下硬断言（含阴性对照）
# --------------------------------------------------------------------------------------


def _as_sketch(obj: Any) -> Any:
    """候选对象 → 含 `entities` 列表的草图（文档则取/建 `sketches[0]`）；不是就返回 None。"""
    if obj is None:
        return None
    if isinstance(_field(obj, "entities"), list):
        return obj
    sketches = _field(obj, "sketches")
    if isinstance(sketches, list):
        if sketches and isinstance(_field(sketches[0], "entities"), list):
            return sketches[0]
        for attr in ("add_sketch", "new_sketch", "create_sketch", "start_sketch", "ensure_sketch"):
            method = getattr(obj, attr, None)
            if callable(method):
                try:
                    made = method()
                except Exception:  # noqa: BLE001 - 候选构造器不匹配就继续找下一个
                    continue
                if isinstance(_field(made, "entities"), list):
                    return made
    return None


def _instantiate(target: Callable[..., Any]) -> Any:
    """按几种常见签名试构造（都不成才返回 None）。"""
    attempts: tuple[tuple[tuple[Any, ...], dict[str, Any]], ...] = (
        ((), {}),
        ((), {"entities": []}),
        ((), {"name": "l1-w16"}),
        (("l1-w16",), {}),
        ((), {"sketches": []}),
    )
    for args, kwargs in attempts:
        try:
            return target(*args, **kwargs)
        except Exception:  # noqa: BLE001 - 签名不匹配：换下一种
            continue
    return None


def _candidate_factories() -> list[Callable[[], Any]]:
    """按「真件优先、最小容器兜底」的顺序给出草图工厂候选。"""
    factories: list[Callable[[], Any]] = []
    seen: set[int] = set()
    for module_name in _SKETCH_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - 模块不存在/依赖不齐：换下一个
            continue
        targets: list[Any] = []
        for attr in _SKETCH_ATTRS:
            target = getattr(module, attr, None)
            if target is not None:
                targets.append(target)
        for attr in dir(module):
            if attr.startswith("_") or not _SKETCH_NAME_RE.search(attr):
                continue
            target = getattr(module, attr, None)
            if target is None or isinstance(target, (str, bytes, int, float, bool, type(Ellipsis))):
                continue
            targets.append(target)
        for target in targets:
            if id(target) in seen:
                continue
            seen.add(id(target))
            if isinstance(target, type) or callable(target):
                factories.append(lambda _t=target: _as_sketch(_instantiate(_t)))
            else:
                probe = _as_sketch(target)
                if probe is not None:
                    factories.append(lambda _o=probe: _as_sketch(copy.deepcopy(_o)))
    factories.append(lambda: types.SimpleNamespace(entities=[]))
    factories.append(lambda: {"entities": []})
    return factories


def _pick_sketch_factory() -> Callable[[], Any]:
    """挑一个**被真 API 验证过**的草图工厂：真 `add_polyline` 能写进去、`sketch_entities` 读得回。

    验证不过的候选全部丢弃；一个都没有就抛 `CadError(reason="l1_fixture_unavailable")`
    （自检据此**主动报红**，不静默降级成"看起来跑了"）。
    """
    probe_ring = [(0, 0), (1, 0), (1, 1), (0, 0)]
    for factory in _candidate_factories():
        try:
            sketch = _as_sketch(factory())
            if sketch is None:
                continue
            made = list(_primitives.add_polyline(sketch, probe_ring, closed=True, layer="0"))
            if len(made) != 1:
                continue
            entities = list(_constraints.sketch_entities(sketch))
            if len(entities) != 1 or _entity_kind(entities[0]) != "polyline":
                continue
            if _polyline_points(entities[0]) != probe_ring:
                continue
            return factory
        except Exception:  # noqa: BLE001 - 该候选不可用，换下一个
            continue
    raise _fail("l1_fixture_unavailable",
                "no usable L1 sketch container found (tried cadcore modules and minimal containers)")


class _Checks:
    """自检计数器：每条断言必须真跑过，失败立刻抛出（不放宽、不跳过）。"""

    def __init__(self) -> None:
        self.count = 0

    def ok(self, condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(f"check failed: {label}")
        self.count += 1


def _square_ring(side_nm: int = 20_000_000) -> list[tuple[int, int]]:
    """自检用 20 mm 正方形环（逆时针，不含重复首点）。"""
    return [(0, 0), (side_nm, 0), (side_nm, side_nm), (0, side_nm)]


def _rect_ring(width_nm: int, height_nm: int) -> list[tuple[int, int]]:
    """自检用轴对齐矩形环。"""
    return [(0, 0), (width_nm, 0), (width_nm, height_nm), (0, height_nm)]


def _l_shape_ring() -> list[tuple[int, int]]:
    """自检用凹环（L 形，闭合、无零长边）：给渐变的 unsupported 分支做阳性输入。"""
    return [(0, 0), (30_000_000, 0), (30_000_000, 10_000_000),
            (10_000_000, 10_000_000), (10_000_000, 20_000_000), (0, 20_000_000)]


def _add_ring(sketch: Any, ring: Sequence[tuple[int, int]], layer: str = "0") -> str:
    """把环落成一条闭合 `polyline`，返回其 uuid（自检夹具用的**真**落库路径）。"""
    points = list(ring) + [ring[0]]
    made = list(_primitives.add_polyline(sketch, points, closed=True, layer=layer))
    if len(made) != 1:
        raise AssertionError(f"fixture add_polyline returned {len(made)} uuids, expected 1")
    return str(made[0])


def _line_endpoints(sketch: Any, uuid_: str) -> tuple[int, int, int, int]:
    """取一条 line 的整数纳米端点。"""
    geom = _geom_of(_find_entity(sketch, uuid_, kind="line"))
    return (_nm(_field(geom, "x1"), what="line.x1"), _nm(_field(geom, "y1"), what="line.y1"),
            _nm(_field(geom, "x2"), what="line.x2"), _nm(_field(geom, "y2"), what="line.y2"))


def _raises_reason(call: Callable[[], Any], expected: str) -> tuple[bool, str]:
    """调用必须抛 `CadError` 且 `reason == expected`；返回 (是否达标, 实际 reason)。"""
    try:
        call()
    except CadError as exc:
        actual = str(getattr(exc, "reason", "") or "")
        return (actual == expected, actual)
    except Exception as exc:  # noqa: BLE001 - 别的错误类型也算不达标
        return (False, f"{type(exc).__name__}: {exc}")
    return (False, "no_error")


def selftest() -> int:
    """自检入口：夹具自证 + 真几何硬断言 + 错误路径 + 尺子阴性对照。返回进程退出码。"""
    checks = _Checks()
    try:
        factory = _pick_sketch_factory()
        checks.ok(callable(factory), "l1_sketch_factory_is_callable")

        # --- C1 夹具自证：用的是 L1 真容器，几何真落库、真读回（不是桩） ---
        sketch = factory()
        container = f"{type(sketch).__module__}.{type(sketch).__name__}"
        ring_uuid = _add_ring(sketch, _square_ring())
        entities = list(_constraints.sketch_entities(sketch))
        checks.ok(len(entities) == 1, "fixture_sketch_has_exactly_one_entity")
        fixture = _find_entity(sketch, ring_uuid)
        checks.ok(_entity_kind(fixture) == "polyline", "fixture_entity_kind_is_polyline")
        checks.ok(_polyline_points(fixture) == _square_ring() + [_square_ring()[0]],
                  "fixture_geometry_roundtrips_exactly")

        # --- C2 ANSI31 真裁剪：20 mm 方 / 45° / 2 mm ⇒ 恰好 15 条，每条真在环内 ---
        sketch2 = factory()
        ring2 = _add_ring(sketch2, _square_ring())
        lines = hatch_create(sketch2, pattern="ANSI31", spacing_nm=2_000_000, boundary_refs=[ring2])
        checks.ok(len(lines) == 15, "ansi31_square_yields_15_clipped_lines")
        checks.ok(len(list(_constraints.sketch_entities(sketch2))) == 16,
                  "ansi31_creates_exactly_15_new_entities")
        total_length = 0
        for uuid_ in lines:
            x1, y1, x2, y2 = _line_endpoints(sketch2, uuid_)
            checks.ok(abs(abs(x2 - x1) - abs(y2 - y1)) <= 2, "ansi31_line_is_at_45_degrees")
            length = math.isqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
            checks.ok(length > 0, "ansi31_line_has_positive_length")
            checks.ok(_point_in_ring(((x1 + x2) / 2.0, (y1 + y2) / 2.0), _square_ring()),
                      "ansi31_line_midpoint_is_inside_ring")
            total_length += length
        expected_length = (20_000_000 ** 2) / 2_000_000  # Cavalieri：Σ弦长 ≈ 面积 / 间距
        checks.ok(abs(total_length - expected_length) / expected_length <= 0.05,
                  "ansi31_total_chord_length_matches_area_over_spacing")

        # --- C3 ANSI37 十字：45° 15 条 + 135° 14 条（过 (0,0) 顶点那条零长，正确丢弃）---
        sketch3 = factory()
        ring3 = _add_ring(sketch3, _square_ring())
        cross = hatch_create(sketch3, pattern="ANSI37", spacing_nm=2_000_000, boundary_refs=[ring3])
        checks.ok(len(cross) == 29, "ansi37_square_yields_29_clipped_lines")

        # --- C4 SOLID：只落闭合边界对象（HPBOUND 载体），且几何与边界一致 ---
        sketch4 = factory()
        ring4 = _add_ring(sketch4, _square_ring())
        solid = hatch_create(sketch4, pattern="SOLID", boundary_refs=[ring4])
        checks.ok(len(solid) == 1, "solid_yields_one_object")
        solid_entity = _find_entity(sketch4, solid[0])
        checks.ok(_entity_kind(solid_entity) == "polyline", "solid_object_is_a_closed_polyline")
        checks.ok(_polyline_points(solid_entity) == _square_ring() + [_square_ring()[0]],
                  "solid_object_geometry_equals_boundary")
        checks.ok(_area(_polyline_points(solid_entity)[:-1]) == 400_000_000_000_000,
                  "solid_boundary_area_is_400mm2")

        # --- C5 稀疏间距 ⇒ 真为零（[] 且草图实体数不变），与"没算"不同形 ---
        sketch5 = factory()
        ring5 = _add_ring(sketch5, _square_ring())
        before = len(list(_constraints.sketch_entities(sketch5)))
        sparse = hatch_create(sketch5, pattern="ANSI31", spacing_nm=100_000_000, boundary_refs=[ring5])
        checks.ok(sparse == [], "sparse_spacing_yields_empty_list")
        checks.ok(len(list(_constraints.sketch_entities(sketch5))) == before,
                  "sparse_spacing_creates_no_entity")

        # --- C6 渐变带：30×20 mm 矩形 / 4 带 ⇒ 每带 7.5×20 mm，面积逐带精确、总和 == 矩形面积 ---
        sketch6 = factory()
        ring6 = _add_ring(sketch6, _rect_ring(30_000_000, 20_000_000))
        band_uuids = gradient_create(sketch6, bands=4, angle_deg=0.0, boundary_refs=[ring6])
        checks.ok(len(band_uuids) == 4, "gradient_yields_four_bands")
        band_area_sum = 0
        for index, uuid_ in enumerate(band_uuids):
            band = _polyline_points(_find_entity(sketch6, uuid_))
            checks.ok(_is_closed_points(band), "gradient_band_is_closed")
            xs = [point[0] for point in band]
            checks.ok(min(xs) == 7_500_000 * index and max(xs) == 7_500_000 * (index + 1),
                      "gradient_band_x_range_is_exact")
            band_area = _area(band[:-1])
            checks.ok(band_area == 150_000_000_000_000, "gradient_band_area_is_150mm2")
            band_area_sum += band_area
        checks.ok(band_area_sum == 600_000_000_000_000,
                  "gradient_band_area_sum_equals_rectangle_area")

        # --- C7 错误路径：每条都要 CadError 且带机器可读 reason ---
        sketch7 = factory()
        ring7 = _add_ring(sketch7, _square_ring())
        l_shape = _add_ring(sketch7, _l_shape_ring())
        bad_spacing = _raises_reason(
            lambda: hatch_create(sketch7, pattern="ANSI31", spacing_nm=0, boundary_refs=[ring7]),
            "invalid_length")
        checks.ok(bad_spacing[0], f"zero_spacing_raises_invalid_length (got {bad_spacing[1]})")
        bad_refs = _raises_reason(
            lambda: hatch_create(sketch7, pattern="ANSI31", spacing_nm=2_000_000,
                                 boundary_refs=["deadbeef"]),
            "profile_not_closed")
        checks.ok(bad_refs[0], f"foreign_ref_raises_profile_not_closed (got {bad_refs[1]})")
        bad_pattern = _raises_reason(
            lambda: hatch_create(sketch7, pattern="NOPE", boundary_refs=[ring7]),
            "invalid_argument")
        checks.ok(bad_pattern[0], f"unknown_pattern_raises_invalid_argument (got {bad_pattern[1]})")
        bad_solid = _raises_reason(
            lambda: hatch_create(sketch7, pattern="SOLID", spacing_nm=1_000_000, boundary_refs=[ring7]),
            "invalid_argument")
        checks.ok(bad_solid[0], f"solid_with_spacing_raises_invalid_argument (got {bad_solid[1]})")
        concave = _raises_reason(
            lambda: gradient_create(sketch7, bands=4, boundary_refs=[l_shape]),
            "unsupported_geometry")
        checks.ok(concave[0], f"concave_gradient_raises_unsupported_geometry (got {concave[1]})")
        bad_bands = _raises_reason(
            lambda: gradient_create(sketch7, bands=1, boundary_refs=[ring7]),
            "invalid_argument")
        checks.ok(bad_bands[0], f"single_band_raises_invalid_argument (got {bad_bands[1]})")
        both_bounds = _raises_reason(
            lambda: hatch_create(sketch7, pattern="ANSI31", boundary_refs=[ring7],
                                 points_nm=_square_ring()),
            "invalid_argument")
        checks.ok(both_bounds[0], f"two_boundary_sources_raise_invalid_argument (got {both_bounds[1]})")

        # --- C8 尺子阴性对照：环外点必须判 False，环内点必须判 True ---
        checks.ok(not _midpoint_ok((100_000_000.0, 100_000_000.0), _square_ring(), tol_nm=2.0),
                  "ruler_rejects_point_outside_ring")
        checks.ok(_midpoint_ok((10_000_000.0, 10_000_000.0), _square_ring(), tol_nm=2.0),
                  "ruler_accepts_point_inside_ring")
        checks.ok(not _is_interior_midpoint((0.0, 10_000_000.0), _square_ring()),
                  "ruler_rejects_boundary_coincident_midpoint")
        checks.ok(_is_convex(_square_ring()) and not _is_convex(_l_shape_ring()),
                  "convexity_ruler_separates_square_from_l_shape")

        print(f"FEATURE_OK family={FAMILY} op=hatch_create verdict=PASS checks={checks.count} "
              f"ops={len(OPERATIONS)} container={container} gradient_colors=missing_l1")
        return 0
    except Exception as exc:  # noqa: BLE001 - 自检失败必须原样报红，不吞
        print(f"FEATURE_FAIL family={FAMILY} op=hatch_create verdict=FAIL checks={checks.count} "
              f"reason={type(exc).__name__}: {exc} rc=1")
        return 1


if __name__ == "__main__":
    sys.exit(selftest())
