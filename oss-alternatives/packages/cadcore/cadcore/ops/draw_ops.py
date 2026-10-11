#!/usr/bin/env python3
"""DRAW_OPS 族模块：绘图命令层（多段线编辑 / 参数化矩形·正多边形·腰形槽 / 阵列绘制侧）。

族接口（冻结）
--------------
::

    FAMILY = "draw_ops"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]     # 返回本次新建/被改写对象的 uuid 列表

长度一律**整数纳米**（`*_nm`；`bool` 与非整数浮点一律拒），角度用 `*_deg`（float）。
可预期错误一律抛 `cadcore.errors.CadError(reason=...)`：本模块自己校验的入参用本层
`CadError(reason=...)`；转发给 `cadcore.edit.modify` / `cadcore.sketch.primitives` 的调用统一走
`_delegate()`，把上游任何异常**translate** 成 `CadError` 并保留上游 `reason`（不让"上游另立
的错误类型"漏出族接口）。

返回值口径（不含糊）
--------------------
* **新建型**操作（`polyline_create` / `rectangle_params` / `polygon_params` / `slot_params` /
  `array_rect` / `array_polar`）返回**新建**对象 uuid，顺序在各自 docstring 里写死；
* **原地改写型**操作（`polyline_edit`）返回**被改写实体自身的 uuid**（恒为 `[ref]`）。
  原地改写是硬需求：L1 的 `constraints` / `dimensions` 按 uuid 引用实体，删旧建新会让既有引用悬空。
  返回值**不是变更计数器** —— "这次是否真的改了几何"请看实体 `geom`，不编码进返回值；
* **真为零**（`array_rect(rows=cols=1)`、`array_polar(count=1)`）返回 `[]`：那是"确实没有新对象"，
  与"没算"不同形（没算一律抛错）。

复用了什么（先读再写，不另立副本）
----------------------------------
* `cadcore.sketch.primitives`：`add_rectangle`（4 line + 2 水平 + 2 垂直约束）/ `add_polygon`
  （N line + N coincident）/ `add_slot`（2 line + 2 半圆 arc）/ `add_polyline` / `closed_profiles`
  （**超集**尺子，额外认闭合折线）—— 本模块**不重写**任何图元顶点数学与约束写入；
* `cadcore.edit.modify`：`array_linear`（矩形阵列真源）/ `copy` / `rotate` / `move`（极坐标阵列的
  克隆与落位）—— 仿射变换只有一份实现，改一处即改全部；
* `cadcore.sketch.constraints`：`sketch_entities`（实体表唯一读法）、`closed_profiles`（闭合轮廓上游尺子）；
* `cadcore.sketch.commands`：`line_create` / `circle_create` / `arc_create` 三个基本图元的**唯一实现**
  （`add_line` / `add_circle` / `add_arc`，含零长线、零半径、退化弧的校验），本模块只做族接口入参把关与
  错误翻译；自检夹具另用它造一条对照 `line`（阴性对照用）。

有意限定的 MVP 边界（写下来，防止下游靠猜）
--------------------------------------------
* `rectangle_params` 的**斜置**矩形（`angle_deg` 不是 90 的整数倍）落到**一个闭合 `polyline`**，
  而不是 4 条 line + 约束 —— 因为 `constraints.SUB_SHAPES` 里没有 `polyline` 项，
  折线顶点挂不上约束（见 `primitives.POLYLINE_CONSTRAINT_GAP`）。轴对齐矩形（`angle_deg % 90 == 0`）
  走 `add_rectangle`，返回 4 条 line uuid + 写 4 条约束。两种返回形态在 docstring 里写死。
* `polygon_params` 只做"尺寸参数 → 外接/内切圆半径"的换算入口，**不**提供起始角旋转参数：
  `add_polygon` 把起始顶点钉在 +Y（90°），本模块不改上游口径（要旋转请用族外 `modify.rotate`）。
* `polyline_edit` 的 `index` 对**闭合**折线指的是**去重后的环顶点序号**（末位那个与首点重复的存储点
  不可寻址）；闭合折线顶点仍挂不上约束（同上缺口）。
* `array_polar(rotate_items=False)` 的"朝向不变"落位按**关键点重心**（线取两端点、折线取全部顶点、
  圆/弧取圆心）为锚；`rotate_items=True` 时绕 `center_nm` 真旋转，任意角度受整数纳米取整影响
  （90° 整数倍精确，其余角度有 ≤1 nm 量级取整误差）。

自检（文件自带，不依赖 pytest）
--------------------------------

    python3 oss-alternatives/packages/cadcore/cadcore/ops/draw_ops.py

打印 `DRAW_OPS_OPS verdict=PASS ops=10 checks=6`（FAIL 时 rc=1）。

中文只出现在 docstring/注释里：`tools/cad/i18n_gate.py` 把"非 docstring 的含汉字字符串常量"
计为 `hardcoded_literals`，故本文件所有面向机器的字符串字面量一律 ASCII。
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from ..errors import CadError
from ..edit import modify as _modify
from ..sketch import commands as _commands
from ..sketch import constraints as _constraints
from ..sketch import primitives as _primitives

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "POLYLINE_ACTIONS",
    "line_create",
    "circle_create",
    "arc_create",
    "polyline_create",
    "polyline_edit",
    "rectangle_params",
    "polygon_params",
    "slot_params",
    "array_rect",
    "array_polar",
    "module_facts",
    "selftest",
]

#: 族 id（冻结接口）。
FAMILY = "draw_ops"

#: `polyline_edit` 支持的动作（冻结枚举，别在文档与代码里各写一份）。
POLYLINE_ACTIONS: tuple[str, ...] = ("move_vertex", "insert_vertex", "delete_vertex", "set_closed")

#: 开放折线的最少点数（与 `primitives.add_polyline` 同口径）。
_MIN_OPEN_POINTS: int = 2

#: 闭合折线的最少**不同**顶点数（与 `primitives.add_polyline` 同口径）。
_MIN_CLOSED_VERTICES: int = 3

#: 全圆判定的相对容差（`total_angle_deg % 360 == 0` 的浮点口径）。
_FULL_CIRCLE_TOL: float = 1e-9


# --------------------------------------------------------------------------------------
# 错误与入参校验（全部在落库之前跑完：绝不半接受）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造一条带机器可读 `reason` 的 `CadError`（文案一律 ASCII）。"""
    return CadError(str(message), reason=str(reason), **details)


def _delegate(callable_: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """转发到上游复用模块，并把**任何**上游异常收敛成族接口的 `CadError`。

    - 上游抛的已是 `cadcore.errors.CadError`（含子类）⇒ 原样透传，`reason` 不丢；
    - 上游抛的是**别的**错误类型（草图层的 `GeometryError` 等）⇒ translate 成 `CadError`，
      并把上游 `reason` / 类型名放进 `details`，下游仍按 `reason` 归因、不必解析文案。
    """
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
    """折线点串入参 → `[(x_nm, y_nm), ...]`。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("invalid_argument", f"{what} must be a sequence of (x_nm, y_nm) pairs, got {value!r}", what=what)
    return [_point(item, what=f"{what}[{index}]") for index, item in enumerate(value)]


def _count(value: Any, *, what: str, minimum: int, maximum: int | None = None) -> int:
    """计数入参 → int（bool 不算数；`< minimum` 报错；给了 `maximum` 就卡上限）。

    上限用**唯一真源** `constraints.items_in_range`（与 `edit.modify._as_count` 同一把尺子）：
    计数型入参是用来驱动循环的，先拒后循环（B6 实测：无界计数 ⇒ 请求永不返回、工作区锁占死）。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", f"{what} must be an integer, got {value!r}", what=what)
    if int(value) < int(minimum):
        raise _fail("invalid_argument", f"{what} must be >= {minimum}, got {int(value)}", what=what)
    if maximum is not None and not _constraints.items_in_range(int(value)):
        raise _fail("invalid_argument",
                    f"{what} is above the per-operation limit {maximum}", what=what, value=int(value),
                    max=int(maximum))
    return int(value)


def _index(value: Any, *, what: str, upper: int) -> int:
    """序号入参 → int（`0 <= index <= upper`，闭区间；越界报 `invalid_argument`）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", f"{what} must be an integer, got {value!r}", what=what)
    if int(value) < 0 or int(value) > int(upper):
        raise _fail("invalid_argument", f"{what} out of range: got {int(value)}, allowed 0..{int(upper)}", what=what)
    return int(value)


def _angle(value: Any, *, what: str = "angle_deg") -> float:
    """角度入参 → 有限 float（bool 与非数报错）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_argument", f"{what} must be a finite number, got {value!r}", what=what)
    number = float(value)
    if not math.isfinite(number):
        raise _fail("invalid_argument", f"{what} must be finite, got {value!r}", what=what)
    return number


def _flag(value: Any, *, what: str) -> bool:
    """布尔入参 → bool（"yes"/1 一律拒：口径要可判，不做隐式转换）。"""
    if not isinstance(value, bool):
        raise _fail("invalid_argument", f"{what} must be a bool, got {value!r}", what=what)
    return value


def _layer(value: Any) -> str:
    """图层名 → 非空 str（与 `primitives._layer` 同口径）。"""
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", f"layer must be a non-empty string, got {value!r}")
    return value


def _token(value: Any, *, what: str, allowed: Sequence[str]) -> str:
    """枚举入参 → 命中 `allowed` 的 str。"""
    if not isinstance(value, str) or value not in allowed:
        raise _fail("invalid_argument", f"{what} must be one of {list(allowed)}, got {value!r}", what=what)
    return value


def _refs_list(refs: Any) -> list[str]:
    """引用列表 → 非空 uuid 串列表（裸字符串是**非法**入参：逐字符迭代会静默变成 N 个引用）。"""
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence) or len(refs) == 0:
        raise _fail("empty_selection", f"refs must be a non-empty sequence of entity uuids, got {refs!r}")
    out: list[str] = []
    for index, item in enumerate(refs):
        if not isinstance(item, str) or not item:
            raise _fail("invalid_argument", f"refs[{index}] must be a non-empty uuid string, got {item!r}")
        out.append(item)
    return out


# --------------------------------------------------------------------------------------
# 结构访问（`sk_or_doc` → 草图；实体读取），结构取值映射/对象双兼容
# --------------------------------------------------------------------------------------


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """结构取值（映射 / 对象双兼容，与 A10 同一取舍）。"""
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
    """实体 geom → **可变映射**（L1 冻结形状；不是映射即拒，避免写回时静默丢改动）。"""
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


# --------------------------------------------------------------------------------------
# 折线几何口径（与 primitives.add_polyline 同一把尺子）
# --------------------------------------------------------------------------------------


def _polyline_points(entity: Any) -> list[tuple[int, int]]:
    """折线实体的存储点串 → `[(x_nm, y_nm), ...]`（先全量校验，坏数据当场拒）。"""
    geom = _geom_of(entity)
    return _points_nm(_field(geom, "points"), what="polyline.points")


def _is_closed_points(points: Sequence[tuple[int, int]]) -> bool:
    """几何闭合判据：首末点坐标**严格相等**（`tol=0`）且点数 ≥ 4。"""
    return len(points) >= 4 and points[0] == points[-1]


def _validate_polyline(points: Sequence[tuple[int, int]], *, closed: bool, what: str = "polyline") -> None:
    """落库前的折线形状校验（与 `primitives.add_polyline` 的判据一致）。"""
    if len(points) < _MIN_OPEN_POINTS:
        raise _fail("invalid_argument", f"{what} needs at least {_MIN_OPEN_POINTS} points, got {len(points)}")
    for previous, current in zip(points, points[1:]):
        if previous == current:
            raise _fail("invalid_argument", f"{what} has a zero-length segment at {current!r}")
    if closed:
        if points[0] != points[-1]:
            raise _fail("invalid_argument", f"closed {what} must repeat its first point at the end")
        if len(set(points[:-1])) < _MIN_CLOSED_VERTICES:
            raise _fail("invalid_argument",
                        f"a closed {what} needs at least {_MIN_CLOSED_VERTICES} distinct vertices, "
                        f"got {len(set(points[:-1]))}")


def _write_points(entity: Any, points: Sequence[tuple[int, int]]) -> None:
    """把点串写回实体 `geom.points`（**原地**，uuid 不变 ⇒ 既有引用不悬空）。"""
    geom = _geom_of(entity)
    geom["points"] = [[int(x), int(y)] for x, y in points]  # type: ignore[index]


# --------------------------------------------------------------------------------------
# 操作：折线（创建 / 编辑）
# --------------------------------------------------------------------------------------


def polyline_create(
    sk_or_doc: Any,
    *,
    points_nm: Sequence[tuple[int, int]],
    closed: bool = False,
    layer: str = "0",
) -> list[str]:
    """画一条折线（**一个** `polyline` 实体），返回 `[uuid]`（长度恒为 1）。

    转发 `primitives.add_polyline`：点数 < 2、相邻点重复、坐标不是整数纳米、`closed=True` 但
    不同顶点 < 3 —— 一律抛 `CadError`（校验先跑完，草图里一条几何都不多）。
    """
    sketch = _sketch_of(sk_or_doc)
    points = _points_nm(points_nm, what="points_nm")
    is_closed = _flag(closed, what="closed")
    layer_name = _layer(layer)
    _validate_polyline(points if not is_closed else (points + [points[0]] if points and points[0] != points[-1]
                                                     else points),
                       closed=is_closed, what="polyline_create")
    return list(_delegate(_primitives.add_polyline, sketch, points, closed=is_closed, layer=layer_name))


def polyline_edit(
    sk_or_doc: Any,
    *,
    ref: Any,
    action: str,
    index: Any = None,
    to_nm: Any = None,
    point_nm: Any = None,
    closed: Any = None,
) -> list[str]:
    """**原地**编辑一条折线，返回 `[被编辑实体的 uuid]`。

    `action`（`POLYLINE_ACTIONS`）与所需参数：

    - `"move_vertex"`：把第 `index` 个顶点移到 `to_nm`；
    - `"insert_vertex"`：在第 `index` 位**之前**插入 `point_nm`（`index == 顶点数` = 追加到末尾）；
    - `"delete_vertex"`：删掉第 `index` 个顶点（删完仍需满足最少点数/顶点数）；
    - `"set_closed"`：`closed=True` 收成闭合（末点补首点）、`False` 打开（去掉末位重复点）。

    口径：

    - **闭合**折线的 `index` 指**去重后的环顶点序号** `0..n-1`；第 0 个顶点同时对应存储的
      首点与末点（`move_vertex` 会一起改，保证几何闭合判据 `points[0] == points[-1]` 继续成立）；
    - 全量校验在写回**之前**跑完：越界序号、零长相邻段、删到少于最少点数、结果退化 —— 一律抛
      `CadError`，且**草图一个字节都不动**（绝不半接受）；
    - 返回恒为 `[ref uuid]`：原地改写返回**实体自身的 uuid**（不是新建对象），"这次是否真的改了"
      看 `geom.points`，不编码进返回值；
    - 非 `polyline` 实体（line/arc/circle…）抛 `CadError(reason="unsupported_geometry")`：
      折线顶点与线/弧端点不是同一种几何，不偷偷按端点改线（那会静默改错东西）。
    """
    sketch = _sketch_of(sk_or_doc)
    name = _token(action, what="action", allowed=POLYLINE_ACTIONS)
    entity = _find_entity(sketch, ref, kind="polyline")
    uuid_ = _entity_uuid(entity)
    points = _polyline_points(entity)
    was_closed = _is_closed_points(points)
    ring = list(points[:-1]) if was_closed else list(points)

    if name == "move_vertex":
        idx = _index(index, what="index", upper=len(ring) - 1)
        target = _point(to_nm, what="to_nm")
        new_ring = list(ring)
        new_ring[idx] = target
        new_closed = was_closed
    elif name == "insert_vertex":
        idx = _index(index, what="index", upper=len(ring))
        target = _point(point_nm, what="point_nm")
        new_ring = ring[:idx] + [target] + ring[idx:]
        new_closed = was_closed
    elif name == "delete_vertex":
        idx = _index(index, what="index", upper=len(ring) - 1)
        remaining = len(ring) - 1
        floor = _MIN_CLOSED_VERTICES if was_closed else _MIN_OPEN_POINTS
        if remaining < floor:
            raise _fail("invalid_argument",
                        f"deleting vertex {idx} would leave {remaining} vertices, minimum is {floor}",
                        index=idx, remaining=remaining, minimum=floor)
        new_ring = ring[:idx] + ring[idx + 1:]
        new_closed = was_closed
    else:  # set_closed
        want = _flag(closed, what="closed")
        new_ring = list(ring)
        new_closed = want

    new_points = list(new_ring) if not new_closed else list(new_ring) + [new_ring[0]]
    _validate_polyline(new_points, closed=new_closed, what="polyline_edit")
    _write_points(entity, new_points)
    return [uuid_]


# --------------------------------------------------------------------------------------
# 操作：参数化矩形 / 正多边形 / 腰形槽（入口层，图元数学全部复用 primitives）
# --------------------------------------------------------------------------------------


def _rectangle_corners(center: tuple[int, int], width: int, height: int, angle_deg: float) -> list[tuple[int, int]]:
    """中心 + 宽高 + 角度 → 4 个角点（逆时针：左下 → 右下 → 右上 → 左上，按角度旋转后取整）。"""
    radians = math.radians(angle_deg)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    half_w, half_h = width / 2.0, height / 2.0
    corners: list[tuple[int, int]] = []
    for dx, dy in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)):
        corners.append((int(round(center[0] + dx * cos_a - dy * sin_a)),
                        int(round(center[1] + dx * sin_a + dy * cos_a))))
    return corners


def rectangle_params(
    sk_or_doc: Any,
    *,
    p1_nm: Any = None,
    p2_nm: Any = None,
    center_nm: Any = None,
    width_nm: Any = None,
    height_nm: Any = None,
    angle_deg: Any = 0.0,
    layer: str = "0",
) -> list[str]:
    """参数化矩形入口：两种模式二选一，返回本次新建对象 uuid 列表。

    - **对角点模式** `p1_nm` + `p2_nm`（必须同时给，且不给 `center_nm`/`width_nm`/`height_nm`）：
      `angle_deg` 必须为 0（斜置请用中心模式）；零宽/零高/重合点抛 `CadError`；
    - **中心模式** `center_nm` + `width_nm` + `height_nm`（可带 `angle_deg`，逆时针为正）。

    返回形态（**按角度分支，docstring 写死，不许下游靠猜**）：

    - `angle_deg` 是 **90 的整数倍**（含缺省 0）⇒ 轴对齐矩形，转发 `primitives.add_rectangle`
      （4 条 `line` + 2 水平 + 2 垂直约束），返回**逆时针一圈的 4 个 line uuid**
      （下边 → 右边 → 上边 → 左边）；
    - 其余角度 ⇒ 斜置矩形落到**一个闭合 `polyline`**（顶点数 5 = 4 角 + 首点重复），返回 `[uuid]`。
      这是 `constraints.SUB_SHAPES` 没有 polyline 项的**显式缺口**（折线顶点挂不上约束），
      不是"忘了写约束"；闭合性用 `primitives.closed_profiles()` 这把超集尺子量得到。
    """
    sketch = _sketch_of(sk_or_doc)
    layer_name = _layer(layer)
    angle = _angle(angle_deg)
    diagonal_mode = p1_nm is not None or p2_nm is not None
    center_mode = center_nm is not None or width_nm is not None or height_nm is not None

    if diagonal_mode and center_mode:
        raise _fail("invalid_argument",
                    "give either p1_nm + p2_nm or center_nm + width_nm + height_nm, not both")
    if diagonal_mode:
        if p1_nm is None or p2_nm is None:
            raise _fail("invalid_argument", "diagonal mode needs both p1_nm and p2_nm")
        first, second = _point(p1_nm, what="p1_nm"), _point(p2_nm, what="p2_nm")
        if angle != 0.0:
            raise _fail("invalid_argument", "angle_deg is only supported with center_nm + width_nm + height_nm")
        if first == second or first[0] == second[0] or first[1] == second[1]:
            raise _fail("degenerate_rectangle",
                        f"degenerate rectangle (zero width or height): {first!r} -> {second!r}")
        return list(_delegate(_primitives.add_rectangle, sketch, p1_nm=first, p2_nm=second, layer=layer_name))

    if p1_nm is not None or p2_nm is not None:
        raise _fail("invalid_argument", "diagonal mode needs both p1_nm and p2_nm")
    if center_nm is None or width_nm is None or height_nm is None:
        raise _fail("invalid_argument",
                    "center mode needs center_nm + width_nm + height_nm (or diagonal mode p1_nm + p2_nm)")
    center = _point(center_nm, what="center_nm")
    width = _nm(width_nm, what="width_nm", positive=True)
    height = _nm(height_nm, what="height_nm", positive=True)

    if abs(angle % 90.0) < _FULL_CIRCLE_TOL or abs(angle % 90.0 - 90.0) < _FULL_CIRCLE_TOL:
        corners = _rectangle_corners(center, width, height, angle)
        xs = sorted({corner[0] for corner in corners})
        ys = sorted({corner[1] for corner in corners})
        if len(xs) < 2 or len(ys) < 2:
            raise _fail("degenerate_rectangle",
                        f"rectangle collapses at integer-nm resolution: center={center!r} "
                        f"width={width} height={height} angle_deg={angle}")
        return list(_delegate(_primitives.add_rectangle, sketch,
                              p1_nm=(xs[0], ys[0]), p2_nm=(xs[-1], ys[-1]), layer=layer_name))

    corners = _rectangle_corners(center, width, height, angle)
    if len(set(corners)) != 4:
        raise _fail("degenerate_rectangle",
                    f"rotated rectangle collapses at integer-nm resolution: {corners!r}")
    points = corners + [corners[0]]
    _validate_polyline(points, closed=True, what="rectangle_params")
    return list(_delegate(_primitives.add_polyline, sketch, points, closed=True, layer=layer_name))


def polygon_params(
    sk_or_doc: Any,
    *,
    sides: Any,
    radius_nm: Any = None,
    edge_nm: Any = None,
    across_flats_nm: Any = None,
    center_nm: Any = (0, 0),
    layer: str = "0",
) -> list[str]:
    """正多边形入口：三种尺寸口径**三选一**，转发 `primitives.add_polygon`，返回 N 个 line uuid。

    - `radius_nm`：**外接圆**半径（顶点落在该圆上）；
    - `edge_nm`：**边长** `s` ⇒ 外接圆半径 `R = s / (2·sin(π/N))`（就近取整到整数纳米）；
    - `across_flats_nm`：**对边距**（内切圆直径）`F` ⇒ 内切圆半径 `F/2`，走 `inscribed=False`
      分支（外接圆半径 = `(F/2) / cos(π/N)`）。

    返回 N 个 `line` uuid（首尾相接 + N 个相邻端点 `coincident` 约束，全部由 `add_polygon` 写入）。
    `sides < 3`、尺寸口径给了 0 个或 ≥ 2 个、半径在 1 nm 分辨率下把顶点挤成同一点 —— 抛 `CadError`。
    起始顶点固定在 **+Y（90°）**（`add_polygon` 的冻结口径），本入口**不**提供旋转参数。
    """
    sketch = _sketch_of(sk_or_doc)
    count = _count(sides, what="sides", minimum=3, maximum=_constraints.MAX_ITEMS_PER_OPERATION)
    layer_name = _layer(layer)
    center = _point(center_nm, what="center_nm")
    given = [name for name, value in (("radius_nm", radius_nm), ("edge_nm", edge_nm),
                                      ("across_flats_nm", across_flats_nm)) if value is not None]
    if len(given) != 1:
        raise _fail("invalid_argument",
                    f"give exactly one size spec of radius_nm / edge_nm / across_flats_nm, got {given!r}")
    spec = given[0]
    if spec == "radius_nm":
        radius, inscribed = _nm(radius_nm, what="radius_nm", positive=True), True
    elif spec == "across_flats_nm":
        across = _nm(across_flats_nm, what="across_flats_nm", positive=True)
        radius, inscribed = across // 2, False
    else:
        edge = _nm(edge_nm, what="edge_nm", positive=True)
        radius, inscribed = int(round(edge / (2.0 * math.sin(math.pi / count)))), True
    if radius <= 0:
        raise _fail("invalid_length", f"computed radius_nm={radius} is not positive (spec={spec})")
    if inscribed and radius < 1:
        raise _fail("invalid_length", f"radius_nm={radius} cannot resolve {count} vertices")
    return list(_delegate(_primitives.add_polygon, sketch, center_nm=center, radius_nm=radius,
                          sides=count, layer=layer_name, inscribed=inscribed))


def slot_params(
    sk_or_doc: Any,
    *,
    width_nm: Any,
    p1_nm: Any = None,
    p2_nm: Any = None,
    center_nm: Any = None,
    length_nm: Any = None,
    angle_deg: Any = 0.0,
    layer: str = "0",
) -> list[str]:
    """腰形槽入口：轴线两模式二选一，转发 `primitives.add_slot`，返回 4 个 uuid。

    - **两端点模式** `p1_nm` + `p2_nm`（不给 `center_nm`/`length_nm`；`angle_deg` 必须为 0）；
    - **中心模式** `center_nm` + `length_nm` + `angle_deg`（长度沿轴线，逆时针为正）：
      `p1 = center - (L/2)·(cosθ, sinθ)`、`p2 = center + (L/2)·(cosθ, sinθ)`，端点就近取整到整数纳米。

    `width_nm` 必须 **> 0 且为偶数**（两端半圆半径 = `width_nm/2` 必须是整数纳米）；
    零长槽、端点重合、整数纳米下两半圆塌成一点 —— 抛 `CadError`。
    返回顺序（`add_slot` 冻结口径）= `[上边线 p1侧→p2侧, 下边线 p2侧→p1侧, p2 端半圆, p1 端半圆]`，
    4 条边在整数纳米上首尾严格相接（`closed_profiles` 认得出闭环）。
    """
    sketch = _sketch_of(sk_or_doc)
    width = _nm(width_nm, what="width_nm", positive=True)
    if width % 2 != 0:
        raise _fail("invalid_length", f"width_nm must be even (cap radius in whole nm), got {width}")
    layer_name = _layer(layer)
    angle = _angle(angle_deg)
    axis_mode = p1_nm is not None or p2_nm is not None
    center_mode = center_nm is not None or length_nm is not None
    if axis_mode and center_mode:
        raise _fail("invalid_argument", "give either p1_nm + p2_nm or center_nm + length_nm + angle_deg, not both")
    if axis_mode:
        if p1_nm is None or p2_nm is None:
            raise _fail("invalid_argument", "axis mode needs both p1_nm and p2_nm")
        if angle != 0.0:
            raise _fail("invalid_argument", "angle_deg is only supported with center_nm + length_nm")
        first, second = _point(p1_nm, what="p1_nm"), _point(p2_nm, what="p2_nm")
    else:
        if center_nm is None or length_nm is None:
            raise _fail("invalid_argument", "center mode needs center_nm + length_nm (+ optional angle_deg)")
        center = _point(center_nm, what="center_nm")
        length = _nm(length_nm, what="length_nm", positive=True)
        radians = math.radians(angle)
        half_x = (length / 2.0) * math.cos(radians)
        half_y = (length / 2.0) * math.sin(radians)
        first = (int(round(center[0] - half_x)), int(round(center[1] - half_y)))
        second = (int(round(center[0] + half_x)), int(round(center[1] + half_y)))
    if first == second:
        raise _fail("degenerate_slot", f"slot has zero length: p1_nm == p2_nm == {first!r}")
    return list(_delegate(_primitives.add_slot, sketch, p1_nm=first, p2_nm=second,
                          width_nm=width, layer=layer_name))


# --------------------------------------------------------------------------------------
# 操作：阵列绘制侧（矩形阵列转发真源；极坐标阵列用 copy + rotate/move 真几何实现）
# --------------------------------------------------------------------------------------


def array_rect(
    sk_or_doc: Any,
    *,
    refs: Any,
    rows: Any,
    cols: Any,
    dx_nm: Any,
    dy_nm: Any,
) -> list[str]:
    """矩形阵列入口：转发 `modify.array_linear`，返回**新实体** uuid 列表。

    `rows × cols - 1` 个新 uuid（跳过原位 `(0,0)`，遍历顺序 row-major，偏移 `(j·dx_nm, i·dy_nm)`）；
    `rows == cols == 1` ⇒ **真为零**个新实体，返回 `[]`；`dx_nm == dy_nm == 0` 且要复制多于一个位置
    抛 `CadError(reason="invalid_argument")`（叠在一处的副本不是阵列）。几何实现只有
    `cadcore.edit.modify` 那一份，本入口只做族接口的参数把关（`refs` 裸字符串是非法入参）。
    """
    sketch = _sketch_of(sk_or_doc)
    ref_list = _refs_list(refs)
    row_count = _count(rows, what="rows", minimum=1, maximum=_constraints.MAX_ITEMS_PER_OPERATION)
    col_count = _count(cols, what="cols", minimum=1, maximum=_constraints.MAX_ITEMS_PER_OPERATION)
    dx = _nm(dx_nm, what="dx_nm")
    dy = _nm(dy_nm, what="dy_nm")
    if row_count * col_count > 1 and dx == 0 and dy == 0:
        raise _fail("invalid_argument",
                    "dx_nm and dy_nm are both 0: the array would stack copies on the originals",
                    rows=row_count, cols=col_count)
    return list(_delegate(_modify.array_linear, sketch, ref_list,
                          rows=row_count, cols=col_count, dx_nm=dx, dy_nm=dy))


def _point_key_points(entity: Any) -> list[tuple[int, int]]:
    """实体的**关键点**（阵列锚点用）：线取两端点、折线取全部顶点、圆/弧取圆心。"""
    kind = _entity_kind(entity)
    geom = _geom_of(entity)
    if kind == "line":
        return [(_nm(_field(geom, "x1"), what="line.x1"), _nm(_field(geom, "y1"), what="line.y1")),
                (_nm(_field(geom, "x2"), what="line.x2"), _nm(_field(geom, "y2"), what="line.y2"))]
    if kind == "polyline":
        return _polyline_points(entity)
    if kind in ("circle", "arc"):
        return [(_nm(_field(geom, "cx"), what=f"{kind}.cx"), _nm(_field(geom, "cy"), what=f"{kind}.cy"))]
    raise _fail("unsupported_geometry", f"unsupported entity kind for polar array anchor: {kind!r}", kind=kind)


def _anchor_of(sketch: Any, refs: Sequence[str]) -> tuple[int, int]:
    """选中实体的**关键点重心**（就近取整到整数纳米）；空选择抛错。"""
    points: list[tuple[int, int]] = []
    for ref in refs:
        points.extend(_point_key_points(_find_entity(sketch, ref)))
    if not points:
        raise _fail("empty_selection", "cannot compute a polar-array anchor from an empty selection")
    return (int(round(sum(x for x, _ in points) / len(points))),
            int(round(sum(y for _, y in points) / len(points))))


def _rotate_point(point: tuple[int, int], center: tuple[int, int], angle_deg: float) -> tuple[int, int]:
    """点绕 `center` 逆时针转 `angle_deg`（就近取整到整数纳米）。"""
    radians = math.radians(angle_deg)
    dx, dy = point[0] - center[0], point[1] - center[1]
    return (int(round(center[0] + dx * math.cos(radians) - dy * math.sin(radians))),
            int(round(center[1] + dx * math.sin(radians) + dy * math.cos(radians))))


def array_polar(
    sk_or_doc: Any,
    *,
    refs: Any,
    center_nm: Any,
    count: Any,
    total_angle_deg: Any = 360.0,
    rotate_items: Any = True,
    anchor_nm: Any = None,
) -> list[str]:
    """极坐标（环形）阵列：绕 `center_nm` 铺 `count` 个成员（**含源实体**），返回新实体 uuid 列表。

    角步长（写死，别猜）：

    - `total_angle_deg` 是 360 的整数倍 ⇒ **整圆**，步长 `total/count`（首尾不重复，源占 0°）；
    - 否则 ⇒ 步长 `total/(count-1)`，阵列**铺满** `total`（含末端位置）；
    - 参数域 `0 < total_angle_deg <= 360`，越界抛 `CadError(reason="invalid_argument")`。

    `rotate_items`：

    - `True`（缺省）⇒ 每个副本**真旋转** `θ`（转发 `modify.rotate`），朝向随阵列转；
    - `False` ⇒ 朝向保持不变，只把副本**移到**旋转后的位置：锚点 = `anchor_nm`（缺省 =
      选中实体关键点重心，见 `_anchor_of`）绕 `center_nm` 转 `θ` 后的落点（转发 `modify.move`）。

    返回顺序 = **步长优先**：`for step in 1..count-1: for entity in refs` ⇒ 第 k 个副本块是
    `k` 号步长的全部源实体克隆。`count == 1` ⇒ **真为零**个新对象，返回 `[]`。
    几何全部走 `modify.copy` + `modify.rotate` / `modify.move`（仿射核只有一份实现）；
    任意角度受整数纳米取整影响：90° 整数倍精确，其余角度有 ≤1 nm 量级误差。
    """
    sketch = _sketch_of(sk_or_doc)
    ref_list = _refs_list(refs)
    center = _point(center_nm, what="center_nm")
    total_count = _count(count, what="count", minimum=1, maximum=_constraints.MAX_ITEMS_PER_OPERATION)
    total = _angle(total_angle_deg, what="total_angle_deg")
    if not (0.0 < total <= 360.0):
        raise _fail("invalid_argument", f"total_angle_deg must be in (0, 360], got {total}")
    rotate_flag = _flag(rotate_items, what="rotate_items")
    if total_count == 1:
        return []  # 真为零：阵列里只有源实体自己，没有任何新对象
    if abs(total % 360.0) < _FULL_CIRCLE_TOL:
        step = total / total_count
    else:
        step = total / (total_count - 1)

    anchor: tuple[int, int] | None = None
    if not rotate_flag:
        anchor = _point(anchor_nm, what="anchor_nm") if anchor_nm is not None else _anchor_of(sketch, ref_list)

    created: list[str] = []
    for step_index in range(1, total_count):
        angle = step * step_index
        clones = list(_delegate(_modify.copy, sketch, ref_list, dx_nm=0, dy_nm=0))
        if rotate_flag:
            _delegate(_modify.rotate, sketch, clones, center_nm=center, angle_deg=angle)
        else:
            assert anchor is not None  # 分支已保证：rotate_items=False 时 anchor 必已算出
            landing = _rotate_point(anchor, center, angle)
            _delegate(_modify.move, sketch, clones,
                      dx_nm=landing[0] - anchor[0], dy_nm=landing[1] - anchor[1])
        created.extend(clones)
    return created


# --------------------------------------------------------------------------------------
# 操作：基本图元（线段 / 整圆 / 圆弧）——顶点数学与落库**全部复用** sketch.commands
# --------------------------------------------------------------------------------------


def line_create(sk_or_doc: Any, *, p1_nm: Any, p2_nm: Any, layer: str = "0") -> list[str]:
    """画一条线段（**一个** `line` 实体），返回 `[uuid]`（长度恒为 1）。

    转发 `sketch.commands.add_line`（实体 `geom = {x1,y1,x2,y2}`）；`p1_nm == p2_nm`（零长线）
    抛 `CadError(reason="geometry_invalid")`（上游 `GeometryError` 由 `_delegate` 翻成族接口错误）。
    """
    sketch = _sketch_of(sk_or_doc)
    first = _point(p1_nm, what="p1_nm")
    second = _point(p2_nm, what="p2_nm")
    if first == second:
        raise _fail("geometry_invalid", f"zero-length line at {first!r}")
    return [str(_delegate(_commands.add_line, sketch, p1=first, p2=second, layer=_layer(layer)))]


def circle_create(sk_or_doc: Any, *, center_nm: Any, radius_nm: Any, layer: str = "0") -> list[str]:
    """画一个整圆（**一个** `circle` 实体），返回 `[uuid]`（长度恒为 1）。

    转发 `sketch.commands.add_circle`（实体 `geom = {cx,cy,r}`；`radius_nm` 是**半径**不是直径）；
    `radius_nm <= 0`（含 bool / 非整数纳米）抛 `CadError(reason="invalid_length")`。
    """
    sketch = _sketch_of(sk_or_doc)
    center = _point(center_nm, what="center_nm")
    radius = _nm(radius_nm, what="radius_nm", positive=True)
    return [str(_delegate(_commands.add_circle, sketch, center=center, radius_nm=radius,
                          layer=_layer(layer)))]


def arc_create(sk_or_doc: Any, *, center_nm: Any, radius_nm: Any, start_deg: Any = 0.0,
               end_deg: Any = 90.0, layer: str = "0") -> list[str]:
    """画一段**逆时针**圆弧（`start_deg → end_deg`），返回 `[uuid]`（长度恒为 1）。

    转发 `sketch.commands.add_arc`（实体 `geom = {cx,cy,r,start_deg,end_deg}`；端点由圆心+半径+
    两角导出，角度不参与求解）。首尾同角（含 `0 → 360`）是退化弧 ⇒ 抛
    `CadError(reason="geometry_invalid")`（整圆请用 `circle_create`）。
    """
    sketch = _sketch_of(sk_or_doc)
    center = _point(center_nm, what="center_nm")
    radius = _nm(radius_nm, what="radius_nm", positive=True)
    start = _angle(start_deg, what="start_deg")
    end = _angle(end_deg, what="end_deg")
    if abs(end - start) % 360.0 < _FULL_CIRCLE_TOL:
        raise _fail("geometry_invalid", f"degenerate arc: start_deg={start} end_deg={end}")
    return [str(_delegate(_commands.add_arc, sketch, center=center, radius_nm=radius,
                          start_deg=start, end_deg=end, layer=_layer(layer)))]


# --------------------------------------------------------------------------------------
# 操作：边界创建 / 面域 / 圆环（L1 可实现部分；顶点、面积、周长全部现算，不落桩）
# --------------------------------------------------------------------------------------


def _polygon_twice_area(vertices: Sequence[tuple[int, int]]) -> int:
    """鞋带公式（整数，返回 2×带符号面积；单位 nm²）。"""
    total = 0
    count = len(vertices)
    for index, (x, y) in enumerate(vertices):
        next_x, next_y = vertices[(index + 1) % count]
        total += x * next_y - next_x * y
    return total


def _polygon_perimeter(vertices: Sequence[tuple[int, int]]) -> int:
    """周长（整数纳米：逐段 `math.isqrt(dx²+dy²)` 向下取整后求和，不引浮点）。"""
    total = 0
    count = len(vertices)
    for index, (x, y) in enumerate(vertices):
        next_x, next_y = vertices[(index + 1) % count]
        total += math.isqrt((next_x - x) * (next_x - x) + (next_y - y) * (next_y - y))
    return total


def _ring_vertices(sketch: Any, loop: Sequence[str]) -> list[tuple[int, int]]:
    """闭合环（uuid 环）→ 顶点串（**不含**末尾重复的首点）。

    - 单成员 `polyline` ⇒ 取存储点（首末必须重合）；
    - 单成员 `circle` ⇒ 抛 `unsupported_geometry`（圆没有顶点串，调用方走圆分支）；
    - 多成员 `line` 环 ⇒ 按环序首尾相接串成顶点串；
    - 环里有 `arc` ⇒ 抛 `unsupported_geometry`：**弦近似会静默改几何**，L1 宁可显式不支持。
    """
    if len(loop) == 1:
        entity = _find_entity(sketch, loop[0])
        kind = _entity_kind(entity)
        if kind == "circle":
            raise _fail("unsupported_geometry", f"{loop[0]} is a circle: no vertex ring")
        if kind != "polyline":
            raise _fail("unsupported_geometry",
                        f"single-member ring must be a circle or a closed polyline, got {kind!r}")
        points = _polyline_points(entity)
        if not _is_closed_points(points):
            raise _fail("profile_not_closed", f"polyline {loop[0]} is not closed")
        return list(points[:-1])

    vertices: list[tuple[int, int]] = []
    for ref in loop:
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
    return vertices


def _circle_of_ring(sketch: Any, loop: Sequence[str]) -> tuple[tuple[int, int], int]:
    """单成员圆环 → `(圆心, 半径)`；不是圆抛 `unsupported_geometry`。"""
    entity = _find_entity(sketch, loop[0])
    if _entity_kind(entity) != "circle":
        raise _fail("unsupported_geometry", f"{loop[0]} is not a circle")
    geom = _geom_of(entity)
    return ((_nm(_field(geom, "cx"), what="circle.cx"), _nm(_field(geom, "cy"), what="circle.cy")),
            _nm(_field(geom, "r"), what="circle.r", positive=True))


def _point_in_polygon(point: tuple[int, int], vertices: Sequence[tuple[int, int]]) -> bool:
    """整数射线法（even-odd）；拾取点落在边上按**在内**算（边上也是环内点）。"""
    inside = False
    count = len(vertices)
    for index in range(count):
        ax, ay = vertices[index]
        bx, by = vertices[(index + 1) % count]
        cross = (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax)
        if cross == 0 and min(ax, bx) <= point[0] <= max(ax, bx) \
                and min(ay, by) <= point[1] <= max(ay, by):
            return True
        if (ay > point[1]) != (by > point[1]):
            # 交点 x 与 point[0] 的大小关系用整数交叉相乘判定，避免浮点
            if (cross > 0) == (by - ay > 0):
                inside = not inside
    return inside


def _segments_cross(a: tuple[int, int], b: tuple[int, int],
                    c: tuple[int, int], d: tuple[int, int]) -> bool:
    """两条线段是否**真交叉**（四端点各自严格异侧；共线重叠不在此判）。"""
    def side(p: tuple[int, int], q: tuple[int, int], r: tuple[int, int]) -> int:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def _self_intersection(vertices: Sequence[tuple[int, int]]) -> tuple[int, int] | None:
    """自交定位：返回第一对真交叉线段的**起点**（整数纳米）；无自交返回 `None`。"""
    count = len(vertices)
    for i in range(count):
        for j in range(i + 1, count):
            if (i + 1) % count == j or (j + 1) % count == i:
                continue  # 相邻边共享端点，不算自交
            if _segments_cross(vertices[i], vertices[(i + 1) % count],
                               vertices[j], vertices[(j + 1) % count]):
                return vertices[i]
    return None


def boundary(
    sk_or_doc: Any,
    *,
    point_nm: Any,
    layer: str = "0",
    construction: Any = False,
) -> list[str]:
    """边界创建（BOUNDARY）：给环内拾取点，从既有图形反求**最小闭合环**并新建边界图元。

    返回**新建**边界对象的 uuid 列表（长度恒为 1）；既有图元 uuid 与几何**一个字节都不动**。

    - 候选环用 `primitives.closed_profiles()` 这把**超集**尺子现取（line/arc/circle 环 + 闭合折线）；
    - 拾取点判定是**整数射线法**（`_point_in_polygon`，even-odd；落在边上按在内算）；
    - 多个环都含点 ⇒ 取**面积最小**者；圆面积用 `355·r²/113` 整数近似，**只用于比大小**；
    - 环里有 `arc` ⇒ `CadError(reason="unsupported_geometry")`（弦近似会静默改几何，L1 不假装支持）；
    - 找不到含点的环 ⇒ `CadError(reason="draw_ops_boundary_not_found")`（与 `profile_not_closed` 不同形）；
    - 新建形态：多边形环 ⇒ 一个闭合 `polyline`（环顶点 + 首点重复）；圆环 ⇒ 同圆心同半径的**新 circle**；
    - `construction=True` ⇒ `CadError(reason="invalid_argument")`：L1 落库路径没有 construction 位，
      本层不静默丢参数。
    """
    sketch = _sketch_of(sk_or_doc)
    point = _point(point_nm, what="point_nm")
    layer_name = _layer(layer)
    if _flag(construction, what="construction"):
        raise _fail("invalid_argument", "construction=True is not supported by boundary in L1")

    best: tuple[int, tuple[str, ...], list[tuple[int, int]] | None] | None = None
    for loop in _primitives.closed_profiles(sketch):
        if len(loop) == 1 and _entity_kind(_find_entity(sketch, loop[0])) == "circle":
            center, radius = _circle_of_ring(sketch, loop)
            if (point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2 <= radius * radius:
                area = (355 * radius * radius) // 113
                if best is None or area < best[0]:
                    best = (area, loop, None)
            continue
        try:
            vertices = _ring_vertices(sketch, loop)
        except CadError:
            continue  # 环成员本层不支持（arc 等）：跳过该候选，不猜几何
        if _point_in_polygon(point, vertices):
            area = abs(_polygon_twice_area(vertices)) // 2
            if best is None or area < best[0]:
                best = (area, loop, vertices)

    if best is None:
        raise _fail("draw_ops_boundary_not_found",
                    f"no closed profile contains pick point {point!r}", point=list(point))
    _, loop, vertices = best
    if vertices is None:
        center, radius = _circle_of_ring(sketch, loop)
        return [str(_delegate(_commands.add_circle, sketch, center=center, radius_nm=radius,
                              layer=layer_name))]
    points = list(vertices) + [vertices[0]]
    _validate_polyline(points, closed=True, what="boundary")
    return list(_delegate(_primitives.add_polyline, sketch, points, closed=True, layer=layer_name))


def region(
    sk_or_doc: Any,
    *,
    boundary_refs: Any,
    layer: str = "0",
) -> list[str]:
    """面域（REGION）：把既有闭合轮廓转成 `kind="region"` 的二维面对象，面积/周长**现算**。

    返回 `[新建面域对象的 uuid]`；既有边界图元不动。口径：

    - `boundary_refs` 必须**恰好**是 `primitives.closed_profiles()` 里的**一个**环（按集合比，顺序无关），
      否则抛 `CadError(reason="profile_not_closed")`（复用既有键，不另立）；
    - 环顶点自交（真交叉）⇒ `CadError(reason="draw_ops_self_intersecting", point=<交叉段起点 nm>)`；
    - 落库 `geom = {"boundary": [[x, y], ...], "area_nm2": <整数鞋带面积>, "perimeter_nm": <整数周长>,
      "closed": True}`：面积 `|Σ x_i·y_{i+1} − x_{i+1}·y_i| // 2`（nm²，精确整数，**不是桩值**），
      周长逐段 `isqrt(dx²+dy²)` 求和；
    - 单成员 `circle` 环 / 含 `arc` 的环 ⇒ `CadError(reason="unsupported_geometry")`：圆与弧要离散化，
      L1 不做静默弦近似（**显式缺口**，不是忘写）；
    - `boundary_refs` 含不存在 uuid ⇒ `unknown_reference`；含重复 ⇒ `invalid_argument`。
    """
    sketch = _sketch_of(sk_or_doc)
    refs = _refs_list(boundary_refs)
    layer_name = _layer(layer)
    if len(set(refs)) != len(refs):
        raise _fail("invalid_argument", "boundary_refs contain duplicate uuids")
    for ref in refs:
        _find_entity(sketch, ref)  # 不存在 ⇒ unknown_reference（既有归因，不吞）
    wanted = set(refs)
    loops = [loop for loop in _primitives.closed_profiles(sketch) if set(loop) == wanted]
    if not loops:
        raise _fail("profile_not_closed",
                    f"boundary_refs are not exactly one closed profile of this sketch ({len(refs)} refs)")
    vertices = _ring_vertices(sketch, loops[0])
    crossing = _self_intersection(vertices)
    if crossing is not None:
        raise _fail("draw_ops_self_intersecting",
                    f"boundary profile self-intersects at {crossing!r}", point=list(crossing))
    if len(vertices) < 3:
        raise _fail("invalid_argument", f"region needs at least 3 distinct vertices, got {len(vertices)}")
    area = abs(_polygon_twice_area(vertices)) // 2
    if area <= 0:
        raise _fail("draw_ops_region_failed",
                    f"computed area_nm2={area} is not positive", count=len(vertices))
    geom: dict[str, Any] = {
        "boundary": [[x, y] for x, y in vertices],
        "area_nm2": area,
        "perimeter_nm": _polygon_perimeter(vertices),
        "closed": True,
    }
    return [str(_delegate(_primitives._append_entity, sketch, kind="region",
                          layer=layer_name, geom=geom))]


def donut(
    sk_or_doc: Any,
    *,
    center_nm: Any,
    outer_diameter_nm: Any,
    inner_diameter_nm: Any,
    layer: str = "0",
) -> list[str]:
    """圆环（DONUT）：同心内外两个**整圆**（L1 可实现的部分），返回 `[外圆 uuid, 内圆 uuid]`。

    参数口径（规格里本 op `api=NA`，由本模块定并写死）：

    - 直径入参（AutoCAD DONUT 口径）：`outer_diameter_nm > inner_diameter_nm > 0`，且**都必须是偶数**
      —— 半径 = 直径/2 必须落在整数纳米上，奇数直径会引入 0.5 nm 的静默取整；
    - 非整数纳米/非正 ⇒ `CadError(reason="invalid_length")`；`inner >= outer` ⇒
      `CadError(reason="degenerate_donut")`（**不**静默当实心圆）；
    - L1 没有填充实体（图案填充在 `cadcore.ops.hatch_measure` 族）⇒ 本 op **只落两个圆**，
      环间填充是**显式缺口**，不在本 op 的 L1 范围内；两个圆都是普通 `circle`，可与既有链路对接。
    """
    sketch = _sketch_of(sk_or_doc)
    center = _point(center_nm, what="center_nm")
    layer_name = _layer(layer)
    outer = _nm(outer_diameter_nm, what="outer_diameter_nm", positive=True)
    inner = _nm(inner_diameter_nm, what="inner_diameter_nm", positive=True)
    if outer % 2 != 0 or inner % 2 != 0:
        raise _fail("invalid_length",
                    f"diameters must be even (radius in whole nm), got outer={outer} inner={inner}")
    if inner >= outer:
        raise _fail("degenerate_donut",
                    f"inner_diameter_nm {inner} must be < outer_diameter_nm {outer}")
    outer_uuid = _delegate(_commands.add_circle, sketch, center=center, radius_nm=outer // 2,
                           layer=layer_name)
    inner_uuid = _delegate(_commands.add_circle, sketch, center=center, radius_nm=inner // 2,
                           layer=layer_name)
    return [str(outer_uuid), str(inner_uuid)]


# --------------------------------------------------------------------------------------
# 判定面与自检
# --------------------------------------------------------------------------------------


def module_facts() -> dict[str, Any]:
    """族模块机读事实（操作名单 + 折线动作名单 + 长度单位口径）。"""
    return {
        "family": FAMILY,
        "operations": sorted(OPERATIONS),
        "op_count": len(OPERATIONS),
        "polyline_actions": list(POLYLINE_ACTIONS),
        "length_unit": "nm",
        "inplace_ops": ["polyline_edit", "layer_apply"],
        "returns_new_uuids": sorted(name for name in OPERATIONS
                                    if name not in ("polyline_edit", "layer_apply")),
    }


# --------------------------------------------------------------------------------------
# 操作：椭圆 / 样条（L1 **没有**原生曲线实体 ⇒ 一个闭合 `polyline` 逼近；口径写进 docstring）
# --------------------------------------------------------------------------------------

#: 1 mm 的纳米数（本文件内自用，避免跨模块常量依赖）。
_MM_NM: int = 1_000_000

#: 椭圆默认采样点数（**必须是 4 的倍数**：保证 0/90/180/270 四个极值点落在采样上 ⇒ bbox 逐纳米精确）。
_ELLIPSE_SAMPLES: int = 256

#: 椭圆采样上限（防"用采样数换精度"把 L1 草图撑爆）。
_ELLIPSE_MAX_SAMPLES: int = 4096

#: 样条每段默认采样数（Catmull-Rom 段内等参数采样）。
_SPLINE_SAMPLES_PER_SPAN: int = 16

#: 构造线方向载体长度：1 m（= 1e9 nm）。**不是几何 extent**，只承载方向（无限长）。
_XLINE_RAY_LEN_NM: int = 1_000_000_000

#: 填充单次最多生成的剖面线条数（超了显式报错，**不静默截断**）。
_HATCH_MAX_LINES: int = 20_000

#: 填充默认剖面线间距。
_HATCH_DEFAULT_PITCH_NM: int = 3 * _MM_NM


def _append_entity(sketch: Any, *, kind: str, geom: dict[str, Any], layer: str,
                   construction: bool = False) -> str:
    """按 L1 冻结字段追加实体并返回 uuid（字段集与 `edit/modify.py` 逐字一致）。"""
    entity = {"uuid": _constraints.new_uuid(), "kind": kind, "layer": layer,
              "construction": bool(construction), "geom": geom}
    _constraints.sketch_entities(sketch).append(entity)
    return str(entity["uuid"])


def _set_property(entity: Any, name: str, value: Any) -> None:
    """把属性写到实体上（映射实体写键、对象实体写属性，两种 L1 表示都支持）。"""
    if isinstance(entity, dict):
        entity[name] = value
        return
    setattr(entity, name, value)


def _ascii_token(value: Any, *, what: str) -> str:
    """非空 ASCII 记号（≤32 字符、不含空白）：图案名 / 线型名的统一校验。"""
    if not isinstance(value, str):
        raise _fail("invalid_argument", f"{what} must be a string, got {value!r}")
    text = value.strip()
    if not text or len(text) > 32 or not text.isascii() or any(ch.isspace() for ch in text):
        raise _fail("invalid_argument",
                    f"{what} must be a non-empty ASCII token (<=32 chars, no space), got {value!r}")
    return text


def _dedupe_points(points: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """去掉**相邻**重复点（保留闭合环末尾那个与首点重复的存储点）。"""
    out: list[tuple[int, int]] = []
    for point in points:
        if out and out[-1] == point:
            continue
        out.append(point)
    return out


def _ellipse_points(center: tuple[int, int], rx: int, ry: int, rotation_deg: float,
                    samples: int) -> list[tuple[int, int]]:
    """等参数采样椭圆（**含** 0°/90°/180°/270° 极值点；逆时针，取整到整数纳米）。"""
    phi = math.radians(rotation_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    points: list[tuple[int, int]] = []
    for index in range(samples):
        theta = 2.0 * math.pi * index / samples
        x = rx * math.cos(theta)
        y = ry * math.sin(theta)
        points.append((int(round(center[0] + x * cos_phi - y * sin_phi)),
                       int(round(center[1] + x * sin_phi + y * cos_phi))))
    return points


def ellipse_create(
    sk_or_doc: Any,
    *,
    center_nm: Any = None,
    rx_nm: Any = None,
    ry_nm: Any = None,
    center: Any = None,
    major_nm: Any = None,
    minor_nm: Any = None,
    rotation_deg: Any = 0.0,
    samples: Any = _ELLIPSE_SAMPLES,
    layer: str = "0",
) -> list[str]:
    """画一个椭圆（L1 **无原生椭圆实体** ⇒ 落到**一个闭合 `polyline`** 逼近），返回 `[uuid]`。

    逼近口径（写死在这里，别让下游猜）
    ----------------------------------
    * 采样：`samples` 个等参数点 `(cx + rx·cosθ·cosφ − ry·sinθ·sinφ, cy + rx·cosθ·sinφ + ry·sinθ·cosφ)`
      （φ=`rotation_deg`，逆时针），首点重复一次闭合成环；`samples` 必须是 **4 的倍数**且落在
      `[8, 4096]`，默认 256，否则 `CadError(reason="invalid_argument")`。4 的倍数保证四个极值点
      **落在采样上** ⇒ 产物 bbox 与解析 bbox **逐纳米相等**（这一点不是近似）。
    * **弦高（逼近）误差上界**：`e ≤ r_max·(1 − cos(Δθ/2))`，`Δθ = 2π/samples`。默认 256 采样、
      `r_max ≤ 20 mm` ⇒ `e ≤ 1.6e3 nm`（≈1.6 µm），相对误差 ≤ 7.6e-5；误差 ∝ 1/samples²，
      要更紧就调大 `samples`（**容差由此式现算，不写第二份**）。
    * 别名：`center` 等价 `center_nm`；`major_nm`/`minor_nm` 在本族口径下是**半轴**（等价
      `rx_nm`/`ry_nm`，**不是** AutoCAD 的整轴长）。同一语义给两个名 ⇒ `invalid_argument`。
    * 退化与边界：`rx_nm == ry_nm` 合法（是圆，但仍是折线实体，不当整圆）；`rx_nm`/`ry_nm` ≤ 0
      ⇒ `invalid_length`；`rotation_deg` 只做整椭圆旋转，顶点取整误差 ≤ 1 nm/顶点。
    * **如实记的缺失**：L1 既无原生 `ellipse`/`ellipse_arc` 实体，也无 `ELLIPSE` 的圆弧（A）选项；
      下游按 `polyline` 语义裁剪/偏移（弧长、离心角参数化不适用）。
    """
    sketch = _sketch_of(sk_or_doc)
    if center_nm is not None and center is not None:
        raise _fail("invalid_argument", "give either center_nm or center, not both")
    raw_center = center_nm if center_nm is not None else center
    if raw_center is None:
        raise _fail("invalid_argument", "center_nm (or its alias center) is required")
    if rx_nm is not None and major_nm is not None:
        raise _fail("invalid_argument", "give either rx_nm or major_nm, not both")
    if ry_nm is not None and minor_nm is not None:
        raise _fail("invalid_argument", "give either ry_nm or minor_nm, not both")
    raw_rx = rx_nm if rx_nm is not None else major_nm
    raw_ry = ry_nm if ry_nm is not None else minor_nm
    if raw_rx is None or raw_ry is None:
        raise _fail("invalid_argument", "rx_nm and ry_nm (half axes) are required")
    center_pt = _point(raw_center, what="center_nm")
    rx = _nm(raw_rx, what="rx_nm", positive=True)
    ry = _nm(raw_ry, what="ry_nm", positive=True)
    rotation = _angle(rotation_deg, what="rotation_deg")
    count = _count(samples, what="samples", minimum=8)
    if count % 4 or count > _ELLIPSE_MAX_SAMPLES:
        raise _fail("invalid_argument",
                    f"samples must be a multiple of 4 within [8, {_ELLIPSE_MAX_SAMPLES}], got {count}")
    points = _ellipse_points(center_pt, rx, ry, rotation, count)
    return list(_delegate(_primitives.add_polyline, sketch, points + [points[0]], closed=True,
                          layer=_layer(layer)))


def _catmull_rom_points(fit: Sequence[tuple[int, int]], *, closed: bool,
                        per_span: int) -> list[tuple[int, int]]:
    """均匀 Catmull-Rom 插值：**过每个拟合点**（段首直接取原拟合点，不做浮点复算）+ 段内等参数采样。"""
    total = len(fit)
    out: list[tuple[int, int]] = []
    spans = total if closed else total - 1
    for index in range(spans):
        p1 = fit[index]
        p2 = fit[(index + 1) % total]
        p0 = fit[(index - 1) % total] if (closed or index > 0) else p1
        p3 = fit[(index + 2) % total] if (closed or index + 2 < total) else p2
        out.append((int(p1[0]), int(p1[1])))
        for step in range(1, per_span):
            t = step / per_span
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((int(round(x)), int(round(y))))
    out.append((int(fit[0][0]), int(fit[0][1])) if closed
               else (int(fit[-1][0]), int(fit[-1][1])))
    return out


def spline_create(
    sk_or_doc: Any,
    *,
    points: Any = None,
    points_nm: Any = None,
    closed: Any = False,
    samples_per_span: Any = _SPLINE_SAMPLES_PER_SPAN,
    layer: str = "0",
) -> list[str]:
    """画一条样条曲线（L1 **无原生 spline 实体** ⇒ 落到**一个 `polyline`** 逼近，过全部拟合点）。

    逼近口径（写死在这里）
    ----------------------
    * 插值：**均匀 Catmull-Rom**（C¹、每段由相邻 4 个拟合点确定）；`t=0` 处直接落**原拟合点**，
      故每个输入点都**逐纳米**在产物顶点里（不是"拟合结果接近"）。开放样条两端用重复点补齐
      （`p0=p1` / `p3=p2`），闭合样条按环取邻居。
    * 采样：每段 `samples_per_span` 个点（默认 16，允许 2..256），`closed=True` 时首点重复闭合。
    * **弦高（逼近）误差上界**：`e ≤ Δs²·κ/8`（Δs = 段长/samples_per_span，κ = 该段最大曲率）。
      例：30 mm 段、κ ≤ 1/(10 mm) ⇒ 默认 16 采样时 `e ≤ 4.4e4 nm`（≈44 µm）；误差 ∝ 1/samples_per_span²，
      要更紧就调大该参数（**容差由此式现算**）。
    * 输入校验：拟合点 ≥ 2（闭合 ≥ 3 个不同点）、相邻点不得重合、坐标必须是整数纳米 ⇒ 否则
      `CadError`（`invalid_argument` / `invalid_length`）；`points` 与 `points_nm` 是同一入参的两个名，
      同时给 ⇒ `invalid_argument`。
    * **如实记的缺失**：L1 无 SPLINE 的控制点/拟合点容差（SPLMETHOD/SPLKNOTS）、无原生样条实体；
      下游按折线语义编辑（顶点可见、可被 `polyline_edit` 改）。
    """
    sketch = _sketch_of(sk_or_doc)
    if points is not None and points_nm is not None:
        raise _fail("invalid_argument", "give either points or points_nm, not both")
    raw = points if points is not None else points_nm
    if raw is None:
        raise _fail("invalid_argument", "points (or its alias points_nm) is required")
    fit = _points_nm(raw, what="points")
    is_closed = _flag(closed, what="closed")
    per_span = _count(samples_per_span, what="samples_per_span", minimum=2)
    if per_span > 256:
        raise _fail("invalid_argument", f"samples_per_span must be <= 256, got {per_span}")
    for previous, current in zip(fit, fit[1:]):
        if previous == current:
            raise _fail("invalid_argument", f"spline fit points must not repeat: {current!r}")
    if is_closed:
        if len(set(fit)) < _MIN_CLOSED_VERTICES:
            raise _fail("invalid_argument",
                        f"a closed spline needs at least {_MIN_CLOSED_VERTICES} distinct fit points, "
                        f"got {len(set(fit))}")
    elif len(fit) < _MIN_OPEN_POINTS:
        raise _fail("invalid_argument", f"a spline needs at least {_MIN_OPEN_POINTS} fit points")
    curve = _catmull_rom_points(fit, closed=is_closed, per_span=per_span)
    loop = _dedupe_points(curve)
    _validate_polyline(loop, closed=is_closed, what="spline_create")
    return list(_delegate(_primitives.add_polyline, sketch, loop, closed=is_closed,
                          layer=_layer(layer)))


# --------------------------------------------------------------------------------------
# 操作：构造线 / 点 / 填充（L1 真实体；构造线与点无 extent，填充是真扫掠出的线段）
# --------------------------------------------------------------------------------------


def xline_create(
    sk_or_doc: Any,
    *,
    origin_nm: Any = None,
    origin: Any = None,
    direction_nm: Any = None,
    angle_deg: Any = None,
    layer: str = "0",
) -> list[str]:
    """画一条**构造线**（XLINE，无限长参照线）：返回 `[uuid]`（长度恒为 1）。

    形态口径（L1 真实体，不是桩）
    ----------------------------
    * 落库实体 `kind="xline"`、`construction=True`、`geom={ox,oy,dx_nm,dy_nm,infinite,angle_deg,
      ray_len_nm}`：`(ox,oy)` 是**基点**（必给，`origin_nm` 或其别名 `origin`），方向由
      `direction_nm`（非零整数纳米向量）**或** `angle_deg`（逆时针，+x 轴为 0°）二选一给出
      （同时给 ⇒ `invalid_argument`）；方向统一归一化到 `ray_len_nm = 1e9 nm`（1 m）的载体上，
      角度分辨率 ≤ 1e-9 rad（取整误差 ≤ 1 nm）。
    * `infinite=True`：它**没有几何终点**，`ray_len_nm` 只是方向载体长度，**不是可打印段**。
      要出图必须由族外的裁剪/打断把它变成有穷段（本族不假装 `xline` 可打印，也不偷偷截成线段）。
    * 缺失（如实记）：`RAY`（单向射线）与 `XLINE` 的 `H/V`（水平/垂直）开关**没有**实现，
      请直接传 `angle_deg=0/90`。
    """
    sketch = _sketch_of(sk_or_doc)
    if origin_nm is not None and origin is not None:
        raise _fail("invalid_argument", "give either origin_nm or origin, not both")
    raw_origin = origin_nm if origin_nm is not None else origin
    if raw_origin is None:
        raise _fail("invalid_argument", "origin_nm (or its alias origin) is required")
    base = _point(raw_origin, what="origin_nm")
    if direction_nm is not None and angle_deg is not None:
        raise _fail("invalid_argument", "give either direction_nm or angle_deg, not both")
    if angle_deg is not None:
        heading = _angle(angle_deg, what="angle_deg")
        theta = math.radians(heading)
        dx = int(round(_XLINE_RAY_LEN_NM * math.cos(theta)))
        dy = int(round(_XLINE_RAY_LEN_NM * math.sin(theta)))
        heading %= 360.0
    elif direction_nm is not None:
        direction = _point(direction_nm, what="direction_nm")
        if direction == (0, 0):
            raise _fail("geometry_invalid", f"zero-length xline direction at {direction!r}")
        scale = _XLINE_RAY_LEN_NM / math.hypot(direction[0], direction[1])
        dx = int(round(direction[0] * scale))
        dy = int(round(direction[1] * scale))
        heading = math.degrees(math.atan2(direction[1], direction[0])) % 360.0
    else:
        raise _fail("invalid_argument", "direction_nm (or angle_deg) is required")
    if (dx, dy) == (0, 0):
        raise _fail("geometry_invalid", "xline direction underflowed to zero after normalisation")
    geom = {"ox": base[0], "oy": base[1], "dx_nm": dx, "dy_nm": dy, "infinite": True,
            "angle_deg": heading, "ray_len_nm": _XLINE_RAY_LEN_NM}
    return [_append_entity(sketch, kind="xline", geom=geom, layer=_layer(layer), construction=True)]


def point_create(
    sk_or_doc: Any,
    *,
    point_nm: Any = None,
    point: Any = None,
    pdmode: Any = None,
    pdsize_nm: Any = 0,
    layer: str = "0",
) -> list[str]:
    """画一个**点**（POINT）：返回 `[uuid]`（长度恒为 1）。

    形态口径
    --------
    * 落库实体 `kind="point"`、`geom={x,y,pdmode,pdsize_nm}`：几何是**零延展**的定位点（`(x,y)` 必给，
      `point_nm` 或其别名 `point`），显示样式另存：
      `pdmode`（AutoCAD PDMODE 位码，0..2047；`None` ⇒ 落 0）、
      `pdsize_nm` 是**点样式尺寸**（nm；正数=绝对尺寸，负数=相对视口百分比，与 AutoCAD PDSIZE 同口径）。
    * 点样式是**显示属性**不是几何：本层只存不画（L1 没有渲染器）；`pdmode` 越界 ⇒ `invalid_argument`。
    * 缺失（如实记）：`DDPTYPE` 对话框语义、点样式在渲染层的实际形状，均不在本层。
    """
    sketch = _sketch_of(sk_or_doc)
    if point_nm is not None and point is not None:
        raise _fail("invalid_argument", "give either point_nm or point, not both")
    raw = point_nm if point_nm is not None else point
    if raw is None:
        raise _fail("invalid_argument", "point_nm (or its alias point) is required")
    location = _point(raw, what="point_nm")
    mode = 0 if pdmode is None else _nm(pdmode, what="pdmode")
    if not 0 <= mode <= 2047:
        raise _fail("invalid_argument", f"pdmode must be within [0, 2047], got {mode}")
    size = _nm(pdsize_nm, what="pdsize_nm")
    geom = {"x": location[0], "y": location[1], "pdmode": mode, "pdsize_nm": size}
    return [_append_entity(sketch, kind="point", geom=geom, layer=_layer(layer))]


def _hatch_segments(vertices: Sequence[tuple[int, int]], *, pitch_nm: int,
                    angle_deg: float) -> list[list[int]]:
    """even-odd 扫描线：在 `angle_deg` 方向的等距平行线上与多边形求交，按 x 排序两两配对成线段。"""
    phi = math.radians(angle_deg)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    rotated = [(x * cos_phi + y * sin_phi, -x * sin_phi + y * cos_phi) for x, y in vertices]
    ys = [point[1] for point in rotated]
    low, high = min(ys), max(ys)
    steps = int(math.floor((high - low) / pitch_nm)) + 1
    if steps > _HATCH_MAX_LINES:
        raise _fail("hatch_too_dense",
                    f"pitch_nm={pitch_nm} would need {steps} > {_HATCH_MAX_LINES} scanlines")
    segments: list[list[int]] = []
    count = len(rotated)
    for index in range(1, steps + 1):
        line_y = low + index * pitch_nm
        if line_y >= high:
            break
        crossings: list[float] = []
        for position in range(count):
            xa, ya = rotated[position]
            xb, yb = rotated[(position + 1) % count]
            if (ya <= line_y < yb) or (yb <= line_y < ya):
                t = (line_y - ya) / (yb - ya)
                crossings.append(xa + t * (xb - xa))
        crossings.sort()
        for pair in range(0, len(crossings) - 1, 2):
            start, end = crossings[pair], crossings[pair + 1]
            if end - start <= 0.0:
                continue
            a = (start * cos_phi - line_y * sin_phi, start * sin_phi + line_y * cos_phi)
            b = (end * cos_phi - line_y * sin_phi, end * sin_phi + line_y * cos_phi)
            segments.append([int(round(a[0])), int(round(a[1])), int(round(b[0])), int(round(b[1]))])
    return segments


def hatch_create(
    sk_or_doc: Any,
    *,
    refs: Any = None,
    boundary_nm: Any = None,
    pattern: Any = "ANSI31",
    pitch_nm: Any = _HATCH_DEFAULT_PITCH_NM,
    angle_deg: Any = 45.0,
    solid: Any = False,
    associative: Any = True,
    layer: str = "0",
) -> list[str]:
    """图案填充（HATCH）：在边界内**真扫掠出剖面线段**，返回 `[uuid]`（新建 `kind="hatch"` 实体）。

    口径（写死在这里）
    ------------------
    * 边界二选一：`boundary_nm`（显式顶点，≥3 个不同点、首尾重复可省）**或** `refs`（**恰好一个**
      `primitives.closed_profiles()` 里的闭合环，按集合比对，顺序无关）；同时给/都不给 ⇒ `invalid_argument`。
      环里含 `arc`/`circle` ⇒ `CadError(reason="unsupported_geometry")`（L1 只做直边多边形环，
      不把圆弧弦近似成边界 —— 那会静默改面积）；边界自交 ⇒ `invalid_argument`；面积 0 ⇒ `geometry_invalid`。
    * 图案：`solid=True` ⇒ `pattern="SOLID"`、不生成线；否则按 `pattern`（非空 ASCII 记号）+ `pitch_nm`
      （>0）在 `angle_deg` 方向生成等距平行线，用 **even-odd 扫描线**与边界求交、排序配对，
      产出**真线段**（每条线段端点都算过并落库为整数纳米）；超过 `_HATCH_MAX_LINES` 条 ⇒
      `CadError(reason="hatch_too_dense")`（**不静默截断**）。
    * 实体 `geom={pattern,pitch_nm,angle_deg,solid,associative,area_nm2,line_count,lines,boundary_nm,
      boundary_refs}`：`area_nm2` 由鞋带公式**现算**（不是入参回声）；`lines` 是本次生成的线段。
    * **关联填充口径（如实记）**：`associative=True` 时只把边界 uuid 记进 `boundary_refs`（供下游 regen
      重算），**L1 不自动重生成**；边界被改后要重新调用本操作。渐变色（GRADIENT）、填充比例
      （HPSCALE 的图案自适应）、孤岛检测（NORMAL/OUTER/IGNORE）**均未实现**，按缺失记。
    """
    sketch = _sketch_of(sk_or_doc)
    if (refs is None) == (boundary_nm is None):
        raise _fail("invalid_argument", "give exactly one of refs / boundary_nm")
    layer_name = _layer(layer)
    boundary_refs: list[str] = []
    if boundary_nm is not None:
        vertices = _points_nm(boundary_nm, what="boundary_nm")
        if len(vertices) > 1 and vertices[0] == vertices[-1]:
            vertices = vertices[:-1]
    else:
        boundary_refs = _refs_list(refs)
        loop: Sequence[str] | None = None
        for candidate in _primitives.closed_profiles(sketch):
            if len(candidate) == len(boundary_refs) and set(candidate) == set(boundary_refs):
                loop = candidate
                break
        if loop is None:
            raise _fail("profile_not_closed",
                        "refs must be exactly one closed profile of this sketch", refs=boundary_refs)
        try:
            vertices = _ring_vertices(sketch, loop)
        except CadError as exc:
            raise _fail("unsupported_geometry",
                        "hatch boundary must be a straight-edge closed loop in L1",
                        upstream=exc.reason) from exc
    if len(set(vertices)) < _MIN_CLOSED_VERTICES:
        raise _fail("invalid_argument",
                    f"hatch boundary needs at least {_MIN_CLOSED_VERTICES} distinct vertices, "
                    f"got {len(set(vertices))}")
    crossing = _self_intersection(vertices)
    if crossing is not None:
        raise _fail("invalid_argument", f"hatch boundary self-intersects at {crossing!r}")
    area = abs(_polygon_twice_area(vertices)) // 2
    if area == 0:
        raise _fail("geometry_invalid", "hatch boundary encloses zero area")
    is_solid = _flag(solid, what="solid")
    assoc = _flag(associative, what="associative")
    pitch = _nm(pitch_nm, what="pitch_nm", positive=True)
    angle = _angle(angle_deg, what="angle_deg")
    pattern_name = "SOLID" if is_solid else _ascii_token(pattern, what="pattern")
    lines = [] if is_solid else _hatch_segments(vertices, pitch_nm=pitch, angle_deg=angle)
    geom: dict[str, Any] = {"pattern": pattern_name, "pitch_nm": pitch, "angle_deg": angle,
                            "solid": is_solid, "associative": assoc, "area_nm2": area,
                            "line_count": len(lines), "lines": lines,
                            "boundary_nm": [[int(x), int(y)] for x, y in vertices],
                            "boundary_refs": list(boundary_refs)}
    return [_append_entity(sketch, kind="hatch", geom=geom, layer=layer_name)]


# --------------------------------------------------------------------------------------
# 操作：对象捕捉追踪（OTRACK，真助手）/ 图层与对象特性（属性赋值，非几何）
# --------------------------------------------------------------------------------------


def otrack(
    sk_or_doc: Any,
    *,
    point_nm: Any = None,
    point: Any = None,
    base_nm: Any = None,
    bases_nm: Any = None,
    polar_increments_deg: Any = (90.0,),
    tolerance_nm: Any = 1 * _MM_NM,
    mark: Any = True,
    layer: str = "0",
) -> list[str]:
    """**对象捕捉追踪助手**（OTRACK）：把光标点锁到已获取基点（base points）的极轴对齐路径上。

    它是**助手**不是几何命令（口径写死）
    ------------------------------------
    * 输入：光标点 `point_nm`（或其别名 `point`）；一个基点 `base_nm` **或**基点集 `bases_nm`
      （二选一，同时给 ⇒ `invalid_argument`）；`polar_increments_deg` 是允许的对齐增量（默认 `(90.0,)`
      = 正交追踪；每项须在 `(0, 180]`，去重后 ≥1 且 ≤8）；`tolerance_nm` 是**垂距容差**（默认 1 mm，
      >0）：光标到某条对齐路径的**垂距** ≤ 容差 ⇒ 该路径候选。
    * 锁定：在所有 (基点 × 增量角) 路径里取**垂距最小**者，落点 = 光标在该**整条对齐线**（过基点、
      `angle_deg` 方向，双向）上的**正交投影**；没有任何路径在容差内 ⇒ **不锁**，落点就是原光标点。
    * 真落库：`mark=True`（默认）把锁定结果落成一个 `kind="point"` 实体
      （`geom={x,y,tracked,base_index,axis_deg,tool:"otrack",source_x,source_y,hits}`）并返回 `[uuid]`；
      `mark=False` **只算不落库** ⇒ 返回 `[]`（真为零，与"没算"不同形）。
    * **证伪口径**：`tracked=False` ⇒ 落点 == 原光标点（未锁定）；`tracked=True` ⇒ 落点在路径上
      且垂距 ≤ `tolerance_nm`（自检/单测按这条判，不靠"看起来对齐了"）。
    * 缺失（如实记）：OTRACK 的多路径**同时显示**与 F11 总开关状态位不在本层（状态位归族外状态机）；
      本助手只做一次求解 + 落点，"橡皮筋 → 点击成实体"由调用方二次操作完成。
    """
    sketch = _sketch_of(sk_or_doc)
    if point_nm is not None and point is not None:
        raise _fail("invalid_argument", "give either point_nm or point, not both")
    raw_point = point_nm if point_nm is not None else point
    if raw_point is None:
        raise _fail("invalid_argument", "point_nm (or its alias point) is required")
    cursor = _point(raw_point, what="point_nm")
    if base_nm is not None and bases_nm is not None:
        raise _fail("invalid_argument", "give either base_nm or bases_nm, not both")
    if base_nm is not None:
        bases = [_point(base_nm, what="base_nm")]
    elif bases_nm is not None:
        if not isinstance(bases_nm, Sequence) or isinstance(bases_nm, (str, bytes)):
            raise _fail("invalid_argument", f"bases_nm must be a sequence of points, got {bases_nm!r}")
        bases = [_point(item, what=f"bases_nm[{index}]") for index, item in enumerate(bases_nm)]
        if not bases:
            raise _fail("invalid_argument", "bases_nm must not be empty")
    else:
        raise _fail("invalid_argument", "base_nm (or bases_nm) is required")
    if not isinstance(polar_increments_deg, Sequence) or isinstance(polar_increments_deg, (str, bytes)):
        raise _fail("invalid_argument",
                    f"polar_increments_deg must be a sequence of angles, got {polar_increments_deg!r}")
    increments = sorted({_angle(item, what="polar_increments_deg") % 180.0
                         for item in polar_increments_deg})
    if not increments or len(increments) > 8 or increments[0] <= 0.0:
        raise _fail("invalid_argument",
                    f"polar_increments_deg needs 1..8 distinct angles within (0, 180], got {increments!r}")
    tolerance = _nm(tolerance_nm, what="tolerance_nm", positive=True)
    want_mark = _flag(mark, what="mark")

    rays: list[float] = []
    for increment in increments:
        steps = int(math.ceil(180.0 / increment))
        for step in range(steps):
            angle = increment * step
            if angle < 180.0:
                rays.append(angle)
    rays = sorted(set(rays))

    best: tuple[float, int, float, tuple[int, int]] | None = None
    for base_index, base in enumerate(bases):
        for ray in rays:
            theta = math.radians(ray)
            ux, uy = math.cos(theta), math.sin(theta)
            vx, vy = cursor[0] - base[0], cursor[1] - base[1]
            projection = vx * ux + vy * uy
            perpendicular = abs(-vx * uy + vy * ux)
            if perpendicular <= float(tolerance) and (best is None or perpendicular < best[0]):
                locked = (int(round(base[0] + projection * ux)), int(round(base[1] + projection * uy)))
                best = (perpendicular, base_index, ray, locked)

    landing = best[3] if best is not None else cursor
    if not want_mark:
        return []
    geom = {"x": landing[0], "y": landing[1], "tracked": best is not None,
            "base_index": best[1] if best is not None else None,
            "axis_deg": best[2] if best is not None else None,
            "tool": "otrack",
            "source_x": cursor[0], "source_y": cursor[1],
            "hits": len(rays) * len(bases)}
    return [_append_entity(sketch, kind="point", geom=geom, layer=_layer(layer))]


def layer_apply(
    sk_or_doc: Any,
    *,
    refs: Any,
    layer: Any = None,
    color: Any = None,
    linetype: Any = None,
    lineweight_nm: Any = None,
) -> list[str]:
    """**图层与对象特性赋值**（等价 `CHPROP` / 特性面板改图层）：**原地**写实体属性，返回 `refs`。

    口径（把"图层到底是不是操作"写清楚）
    ------------------------------------
    * L1 里**图层是实体属性**（每条实体的 `layer` 字段），**不是**独立操作对象：草图/文档里
      **没有图层表**（无新建/删除图层、无当前图层、无开/关/冻结/锁定、无打印样式表、无 `LAYERSTATE`）。
      因此本操作**只做属性赋值**，不假装它是 AutoCAD 的 `LAYER` 命令。
    * 文档级的"当前图层 / 图层表"归**文档状态族**（`organization` 一侧）承接，**不在本族**；
      本族对此按**缺失**记录。属性不是几何，但**同样是可量取的后果**：族门 `parity_draw_ops_gate.py`
      对本条绑的是**文档模型属性探针**（`chk_layer_apply`：layer/color/linetype/lineweight 真落库
      ∧ 几何一个字节没动 ∧ 没新建对象）⇒ 本条在门里记 `implemented`，不再是"只有命名级证据"的 `partial`。
    * 可写属性（至少给一个，否则 `invalid_argument`；"什么都没改"不许与"改了"同形）：
      - `layer`：非空图层名（沿用本模块 `_layer` 的既有校验）；
      - `color`：ACI 索引 1..256 的整数，或 `"BYLAYER"` / `"BYBLOCK"` 两个具名值（大小写不敏感，落库大写）；
      - `linetype`：非空 ASCII 记号（≤32 字符，无空白；如 `CONTINUOUS`/`DASHED`）—— 线型**定义表**不在 L1，
        本层只写名字（缺失，如实记）；
      - `lineweight_nm`：整数纳米线宽 `>= 0`，或 AutoCAD 的 `-1/-2/-3`（BYLAYER/BYBLOCK/DEFAULT）。
    * 返回 `refs`（**原地改写型**，与 `polyline_edit` 同口径：返回被改写实体自身的 uuid，顺序同入参）；
      某个 ref 不存在 ⇒ `unknown_reference`（**先全量校验再写**：任一 ref 坏 ⇒ 一个字段都不落）。
    """
    sketch = _sketch_of(sk_or_doc)
    targets = _refs_list(refs)
    if layer is None and color is None and linetype is None and lineweight_nm is None:
        raise _fail("invalid_argument",
                    "layer_apply needs at least one of layer/color/linetype/lineweight_nm")
    layer_name = None if layer is None else _layer(layer)
    if color is None:
        color_value: Any = None
    elif isinstance(color, str):
        token = color.strip().upper()
        if token not in ("BYLAYER", "BYBLOCK"):
            raise _fail("invalid_argument",
                        f"color must be an ACI index in [1, 256] or BYLAYER/BYBLOCK, got {color!r}")
        color_value = token
    else:
        color_value = _nm(color, what="color", positive=True)
        if color_value > 256:
            raise _fail("invalid_argument", f"color ACI index must be <= 256, got {color_value}")
    linetype_value = None if linetype is None else _ascii_token(linetype, what="linetype")
    if lineweight_nm is None:
        weight: int | None = None
    else:
        weight = _nm(lineweight_nm, what="lineweight_nm")
        if weight not in (-1, -2, -3) and weight < 0:
            raise _fail("invalid_argument", f"lineweight_nm must be >= 0 or -1/-2/-3, got {weight}")
    entities = [_find_entity(sketch, ref) for ref in targets]   # 先全量校验：坏 ref 不许半写
    for entity in entities:
        if layer_name is not None:
            _set_property(entity, "layer", layer_name)
        if color_value is not None:
            _set_property(entity, "color", color_value)
        if linetype_value is not None:
            _set_property(entity, "linetype", linetype_value)
        if weight is not None:
            _set_property(entity, "lineweight_nm", weight)
    return list(targets)


#: 新增操作名一并进 `__all__`（冻结接口三处一致：`__all__` / `OPERATIONS` / 自带 selftest）。
__all__ = __all__ + ["ellipse_create", "spline_create", "xline_create", "point_create",
                     "hatch_create", "otrack", "layer_apply"]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "line_create": line_create,
    "circle_create": circle_create,
    "arc_create": arc_create,
    "polyline_create": polyline_create,
    "polyline_edit": polyline_edit,
    "rectangle_params": rectangle_params,
    "polygon_params": polygon_params,
    "slot_params": slot_params,
    "array_rect": array_rect,
    "array_polar": array_polar,
    "boundary": boundary,
    "region": region,
    "donut": donut,
    "ellipse_create": ellipse_create,
    "spline_create": spline_create,
    "xline_create": xline_create,
    "point_create": point_create,
    "hatch_create": hatch_create,
    "otrack": otrack,
    "layer_apply": layer_apply,
}


def _empty_sketch() -> dict[str, Any]:
    """自检夹具：空的 L1 草图（三张表齐全，不依赖任何夹具文件）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def _entity_by_uuid(sketch: Any, uuid_: str) -> Any:
    """自检用：按 uuid 取实体（取不到返回 None，让断言自己红）。"""
    for entity in _constraints.sketch_entities(sketch):
        if _entity_uuid(entity) == uuid_:
            return entity
    return None


def selftest() -> int:
    """自检：9 项（正向链路 + 真为零 + 阴性对照），返回 rc（0 = 全绿）。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((str(name), bool(passed), str(detail)))

    def geom_of(uuid_: str) -> dict[str, Any]:
        """自检用：按 uuid 取 geom（取不到给空表 —— 让断言自己红，别让尺子自己崩）。"""
        entity = _entity_by_uuid(basic, uuid_)
        return dict(_geom_of(entity)) if entity is not None else {}

    facts = module_facts()
    check("ops_registered",
          facts["family"] == "draw_ops" and facts["op_count"] == len(OPERATIONS) == 20
          and all(callable(value) for value in OPERATIONS.values())
          and sorted(OPERATIONS) == facts["operations"],
          "ops=%d" % facts["op_count"])

    # -- 边界创建：矩形环内拾取 → 新闭合折线；原图元 uuid 不变；无环拾取 → 专属 reason -------------
    bnd = _empty_sketch()
    rect_before = rectangle_params(bnd, center_nm=(5_000_000, 2_500_000), width_nm=10_000_000,
                                   height_nm=5_000_000, layer="0")
    before_uuids = {_entity_uuid(entity) for entity in _constraints.sketch_entities(bnd)}
    bnd_uuids = boundary(bnd, point_nm=(5_000_000, 2_500_000), layer="0")
    bnd_entity = _entity_by_uuid(bnd, bnd_uuids[0]) if bnd_uuids else None
    bnd_points = _polyline_points(bnd_entity) if _entity_kind(bnd_entity) == "polyline" else []
    after_uuids = {_entity_uuid(entity) for entity in _constraints.sketch_entities(bnd)}
    boundary_not_found = None
    try:
        boundary(_empty_sketch(), point_nm=(0, 0), layer="0")
    except CadError as exc:
        boundary_not_found = exc.reason
    boundary_ok = (len(bnd_uuids) == 1 and len(bnd_points) == 5 and bnd_points[0] == bnd_points[-1]
                   and len(set(bnd_points[:-1])) == 4
                   and set(rect_before) <= after_uuids and bnd_uuids[0] not in before_uuids
                   and boundary_not_found == "draw_ops_boundary_not_found"
                   and len(_primitives.closed_profiles(bnd)) == 2)
    check("boundary", boundary_ok,
          "new=%d points=%d kept=%s not_found=%s" % (len(bnd_uuids), len(bnd_points),
                                                     set(rect_before) <= after_uuids,
                                                     boundary_not_found))

    # -- 面域：面积/周长现算（10mm×5mm ⇒ 5e13 nm² / 3e7 nm）；蝴蝶形自交必拒 ----------------------
    reg = _empty_sketch()
    reg_refs = rectangle_params(reg, center_nm=(5_000_000, 2_500_000), width_nm=10_000_000,
                                height_nm=5_000_000, layer="0")
    reg_uuids = region(reg, boundary_refs=reg_refs, layer="0")
    reg_entity = _entity_by_uuid(reg, reg_uuids[0]) if reg_uuids else None
    reg_geom = dict(_geom_of(reg_entity)) if reg_entity is not None else {}
    bow = _empty_sketch()
    bow_refs = polyline_create(bow, points_nm=[(0, 0), (10_000_000, 0), (0, 10_000_000),
                                               (10_000_000, 10_000_000)], closed=True, layer="0")
    region_cross = None
    try:
        region(bow, boundary_refs=bow_refs, layer="0")
    except CadError as exc:
        region_cross = exc.reason
    region_ok = (len(reg_uuids) == 1 and _entity_kind(reg_entity) == "region"
                 and reg_geom.get("area_nm2") == 50_000_000_000_000
                 and reg_geom.get("perimeter_nm") == 30_000_000
                 and region_cross == "draw_ops_self_intersecting")
    check("region", region_ok,
          "uuids=%d area=%s perimeter=%s cross=%s" % (len(reg_uuids), reg_geom.get("area_nm2"),
                                                      reg_geom.get("perimeter_nm"), region_cross))

    # -- 圆环：两个同心整圆（半径 = 直径/2）；内径 ≥ 外径必拒 -------------------------------------
    dnt = _empty_sketch()
    dnt_uuids = donut(dnt, center_nm=(0, 0), outer_diameter_nm=20_000_000,
                      inner_diameter_nm=10_000_000, layer="0")
    dnt_radii = sorted(int(_field(_geom_of(_entity_by_uuid(dnt, item)), "r"))
                       for item in dnt_uuids) if dnt_uuids else []
    donut_bad = None
    try:
        donut(_empty_sketch(), center_nm=(0, 0), outer_diameter_nm=10_000_000,
              inner_diameter_nm=10_000_000, layer="0")
    except CadError as exc:
        donut_bad = exc.reason
    donut_ok = (len(dnt_uuids) == 2 and dnt_radii == [5_000_000, 10_000_000]
                and len(_primitives.closed_profiles(dnt)) == 2 and donut_bad == "degenerate_donut")
    check("donut", donut_ok, "uuids=%d radii=%s bad=%s" % (len(dnt_uuids), dnt_radii, donut_bad))

    # -- 基本图元：线段/整圆/圆弧 真调用 → uuid 落地 → 几何量取；阴性对照：零长线/零半径必拒 ------
    basic = _empty_sketch()
    seg = line_create(basic, p1_nm=(0, 0), p2_nm=(40_000_000, 30_000_000), layer="0")
    circ = circle_create(basic, center_nm=(10_000_000, 20_000_000), radius_nm=5_000_000, layer="0")
    arc = arc_create(basic, center_nm=(0, 0), radius_nm=10_000_000, start_deg=0.0, end_deg=90.0,
                     layer="0")
    seg_geom, circ_geom, arc_geom = geom_of(seg[0]), geom_of(circ[0]), geom_of(arc[0])
    basic_ok = (len(seg) == 1 and len(circ) == 1 and len(arc) == 1
                and len(_constraints.sketch_entities(basic)) == 3
                and _entity_kind(_entity_by_uuid(basic, seg[0])) == "line"
                and (seg_geom.get("x1"), seg_geom.get("y1"), seg_geom.get("x2"), seg_geom.get("y2"))
                == (0, 0, 40_000_000, 30_000_000)
                and _entity_kind(_entity_by_uuid(basic, circ[0])) == "circle"
                and (circ_geom.get("cx"), circ_geom.get("cy"), circ_geom.get("r"))
                == (10_000_000, 20_000_000, 5_000_000)
                and _entity_kind(_entity_by_uuid(basic, arc[0])) == "arc"
                and (arc_geom.get("cx"), arc_geom.get("cy"), arc_geom.get("r"),
                     arc_geom.get("start_deg"), arc_geom.get("end_deg"))
                == (0, 0, 10_000_000, 0.0, 90.0))
    zero_radius = zero_line = None
    try:
        circle_create(basic, center_nm=(0, 0), radius_nm=0, layer="0")
    except CadError as exc:
        zero_radius = exc.reason
    try:
        line_create(basic, p1_nm=(7, 7), p2_nm=(7, 7), layer="0")
    except CadError as exc:
        zero_line = exc.reason
    check("line_circle_arc", basic_ok and zero_radius == "invalid_length"
          and zero_line == "geometry_invalid",
          "basic=%s zero_radius=%s zero_line=%s" % (basic_ok, zero_radius, zero_line))

    # -- 矩形：轴对齐走 add_rectangle（4 line + 4 约束 + 闭环）；斜置走闭合 polyline --------------
    rect_axis, rect_rot = _empty_sketch(), _empty_sketch()
    axis_uuids = rectangle_params(rect_axis, center_nm=(0, 0), width_nm=40_000_000, height_nm=20_000_000,
                                  layer="0")
    first_line = _geom_of(_entity_by_uuid(rect_axis, axis_uuids[0])) if axis_uuids else {}
    axis_ok = (len(axis_uuids) == 4 and len(_constraints.sketch_entities(rect_axis)) == 4
               and len(_constraints.sketch_constraints(rect_axis)) == 4
               and len(_primitives.closed_profiles(rect_axis)) == 1
               and (first_line.get("x1"), first_line.get("y1"), first_line.get("x2"), first_line.get("y2"))
               == (-20_000_000, -10_000_000, 20_000_000, -10_000_000))
    rot_uuids = rectangle_params(rect_rot, center_nm=(0, 0), width_nm=40_000_000, height_nm=20_000_000,
                                 angle_deg=30.0)
    rot_entity = _entity_by_uuid(rect_rot, rot_uuids[0]) if rot_uuids else None
    rot_points = _polyline_points(rot_entity) if rot_entity is not None else []
    rot_ok = (len(rot_uuids) == 1 and _entity_kind(rot_entity) == "polyline"
              and len(rot_points) == 5 and rot_points[0] == rot_points[-1]
              and len(set(rot_points[:-1])) == 4
              and len(_primitives.closed_profiles(rect_rot)) == 1)
    check("rectangle_params", axis_ok and rot_ok, "axis=%s rotated=%s" % (axis_ok, rot_ok))

    # -- 正多边形（边长口径）/ 腰形槽：line 环 + 闭环 -------------------------------------------
    poly, slot = _empty_sketch(), _empty_sketch()
    poly_uuids = polygon_params(poly, sides=6, edge_nm=10_000_000, center_nm=(0, 0), layer="0")
    poly_ok = (len(poly_uuids) == 6 and len(_constraints.sketch_entities(poly)) == 6
               and len(_primitives.closed_profiles(poly)) == 1)
    slot_uuids = slot_params(slot, width_nm=10_000_000, p1_nm=(0, 0), p2_nm=(30_000_000, 0), layer="0")
    slot_ok = (len(slot_uuids) == 4 and len(_constraints.sketch_entities(slot)) == 4
               and len(_primitives.closed_profiles(slot)) == 1)
    check("polygon_slot_params", poly_ok and slot_ok, "polygon=%s slot=%s" % (poly_ok, slot_ok))

    # -- 折线：创建 → 插入 → 移动 → 闭合/打开；阴性对照：编辑 line 必须被拒 ----------------------
    sketch = _empty_sketch()
    created = polyline_create(sketch, points_nm=[(0, 0), (10_000_000, 0), (10_000_000, 10_000_000)],
                              layer="0")
    line_ref = created[0]
    polyline_edit(sketch, ref=line_ref, action="insert_vertex", index=1, point_nm=(5_000_000, 5_000_000))
    polyline_edit(sketch, ref=line_ref, action="move_vertex", index=0, to_nm=(0, 1_000_000))
    edited = _polyline_points(_entity_by_uuid(sketch, line_ref))
    edit_ok = edited == [(0, 1_000_000), (5_000_000, 5_000_000), (10_000_000, 0), (10_000_000, 10_000_000)]
    polyline_edit(sketch, ref=line_ref, action="set_closed", closed=True)
    closed_points = _polyline_points(_entity_by_uuid(sketch, line_ref))
    edit_ok = edit_ok and len(closed_points) == 5 and closed_points[0] == closed_points[-1]
    polyline_edit(sketch, ref=line_ref, action="set_closed", closed=False)
    edit_ok = edit_ok and len(_polyline_points(_entity_by_uuid(sketch, line_ref))) == 4
    other_line = _commands.add_line(sketch, p1=(0, 0), p2=(1_000_000, 0), layer="0")
    negative_reason = None
    try:
        polyline_edit(sketch, ref=other_line, action="move_vertex", index=0, to_nm=(0, 0))
    except CadError as exc:
        negative_reason = exc.reason
    check("polyline_edit", edit_ok and negative_reason == "unsupported_geometry",
          "points_ok=%s negative_reason=%s" % (edit_ok, negative_reason))

    # -- 阵列：矩形阵列（真为零 + 2 个新件）/ 极坐标（90° 精确 + count=1 真为零 + 朝向保持）------
    arr = _empty_sketch()
    base = _commands.add_line(arr, p1=(0, 0), p2=(10_000_000, 0), layer="0")
    zero_new = array_rect(arr, refs=[base], rows=1, cols=1, dx_nm=0, dy_nm=0)
    rect_new = array_rect(arr, refs=[base], rows=1, cols=3, dx_nm=1_000_000, dy_nm=0)
    arr_ok = zero_new == [] and len(rect_new) == 2
    polar_sketch = _empty_sketch()
    polar_base = _commands.add_line(polar_sketch, p1=(0, 0), p2=(10_000_000, 0), layer="0")
    polar_uuids = array_polar(polar_sketch, refs=[polar_base], center_nm=(0, 0), count=4)
    polar_first = _geom_of(_entity_by_uuid(polar_sketch, polar_uuids[0])) if polar_uuids else {}
    polar_ok = (len(polar_uuids) == 3
                and (polar_first.get("x1"), polar_first.get("y1"), polar_first.get("x2"), polar_first.get("y2"))
                == (0, 0, 0, 10_000_000)
                and array_polar(polar_sketch, refs=[polar_base], center_nm=(0, 0), count=1) == [])
    flat_sketch = _empty_sketch()
    flat_base = _commands.add_line(flat_sketch, p1=(0, 0), p2=(10_000_000, 0), layer="0")
    flat_uuids = array_polar(flat_sketch, refs=[flat_base], center_nm=(0, 0), count=2,
                             total_angle_deg=180.0, rotate_items=False)
    flat_geom = _geom_of(_entity_by_uuid(flat_sketch, flat_uuids[0])) if flat_uuids else {}
    flat_ok = ((flat_geom.get("x1"), flat_geom.get("y1"), flat_geom.get("x2"), flat_geom.get("y2"))
               == (-10_000_000, 0, 0, 0))
    check("array_rect_polar", arr_ok and polar_ok and flat_ok,
          "rect=%s polar=%s keep_orientation=%s" % (arr_ok, polar_ok, flat_ok))

    for name, passed, detail in checks:
        print("DRAW_OPS_CHECK name=%s status=%s detail=%s" % (name, "PASS" if passed else "FAIL", detail))
    ok = bool(checks) and all(passed for _, passed, _ in checks)
    print("DRAW_OPS_OPS verdict=%s ops=%d checks=%d" % ("PASS" if ok else "FAIL", len(OPERATIONS), len(checks)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
