"""cadcore.sketch.primitives —— 成型图元命令：矩形 / 正多边形 / 腰形槽 / 折线。

一句话用途
----------
把"画一个图元"变成 L1 文档里**互相咬合的成型几何**：矩形 = 4 条 `line` + 2 水平 + 2 垂直约束；
正多边形 = N 条 `line` 首尾相接 + N 个相邻端点 `coincident`；腰形槽 = 2 条平行线 + 2 段半圆 `arc`；
折线 = **一个** `polyline` 实体。返回值一律是 uuid 列表，坐标一律**整数纳米**。

与 `commands.py`（A9）的分工
----------------------------
`commands.py` 管"一笔"（线 / 圆 / 弧 / 尺寸），本文件管"一个图元"：一次调用产出多条几何，并把**咬合关系
显式写成约束**（矩形 2 水平 + 2 垂直；多边形 N 个相邻端点重合）。这样 `solve()` 才数得出自由度、
拖动时形状不会散架 —— 这正是"4 条散线"缺的那一半。

口径（与 A10 契约一致，照抄不自创）
-----------------------------------
- 草图 = `{"entities": [...], "constraints": [...], "dimensions": [...]}`；实体 =
  `{uuid, kind, layer, construction, geom}`；长度一律整数纳米；
- `line{x1,y1,x2,y2}` / `circle{cx,cy,r}` / `arc{cx,cy,r,start_deg,end_deg}` / `polyline{points:[[x,y],...]}`；
- 圆弧按**逆时针**从 `start_deg` 扫到 `end_deg`（与 A9 `add_arc` 同一口径）。

失败口径
--------
本层**所有**参数非法（`p1_nm == p2_nm`、退化矩形、`sides < 3`、`radius_nm <= 0`、`width_nm <= 0`、
`points < 2`、相邻点重复、坐标不是整数纳米、layer 空串……）一律抛 `CadError`。
`CadError` 同时继承既有错误族（`GeometryError` / `InvalidLength` / `OutOfRange` / `UnknownReference` /
`UnsupportedKind`），因此 `except CadError`、`except GeometryError`、`except SketchError` 三种既有写法都接得住。
**先全量校验再落库**：校验没过时文档里一条几何都不会多（禁止半接受）。

已知缺口（显式记账，不静默假绿）
--------------------------------
`constraints.closed_profiles()` 的循环体写死 `if kind not in ("line", "arc"): continue`，**没有能力表可挂钩**
⇒ 一个 `polyline` 实体（哪怕首尾点完全相同）在它眼里返回空列表；`constraints.SUB_SHAPES` 里也没有 polyline
⇒ `constraints.add_constraint` 会以 `UnknownReference` 拒绝任何 polyline 引用（强行写进文档也会让
`solve()` 在 `_point_fields("polyline", ...)` 处抛 `UnsupportedKind`）。
因此：矩形 / 正多边形 / 腰形槽**不受影响**（全由 line/arc 组成，闭合性被原样识别，见测试）；
折线则**不写**相邻端点约束（折线的相邻段本来就共享同一个点，那会是一条恒等式），
闭合轮廓请走本模块的 `closed_profiles()`（= 上游那支的**超集**，额外认闭合折线）。
根治办法是 `constraints.py` 属主补一个 polyline 分支；本席**不改**该文件（文件边界），
也**不**做运行期 monkeypatch（会污染其它席）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .commands import add_arc, add_line
from .constraints import (
    MAX_ABS_NM,
    MAX_ITEMS_PER_OPERATION,
    MIN_ABS_NM,
    GeometryError,
    InvalidLength,
    OutOfRange,
    UnknownReference,
    UnsupportedKind,
    add_constraint,
    closed_profiles as constraints_closed_profiles,
    new_uuid,
    nm_in_range,
    sketch_entities,
)

__all__ = [
    "POLYLINE_CONSTRAINT_GAP",
    "CadError",
    "add_polygon",
    "add_polyline",
    "add_rectangle",
    "add_slot",
    "closed_profiles",
    "constraints_closed_profiles",
]

#: 折线端点约束的缺口（真源事实，写下来防止下游靠猜）。
POLYLINE_CONSTRAINT_GAP: str = (
    "constraints.SUB_SHAPES has no 'polyline' entry (and closed_profiles skips any kind but line/arc), "
    "so a polyline vertex cannot carry a coincident constraint with the frozen constraints.py"
)

#: 与 A9 一致的 i18n key（本文件不产出用户可见文案，只带 key + ASCII 细节）。
_KEY_GEOMETRY: str = "err.geometry_invalid"
_KEY_LENGTH: str = "err.invalid_length"
_KEY_RANGE: str = "err.out_of_range"


class CadError(GeometryError, InvalidLength, OutOfRange, UnknownReference, UnsupportedKind):
    """图元层的失败信号：参数非法一律抛它。

    多继承不是炫技：既有代码里 `except GeometryError` / `except InvalidLength` / `except SketchError`
    三种写法都在用（`commands.py` 抛前两种），本层若只继承 `SketchError`，这些既有 catch 点会漏接。
    `CadError` 同时继承这批兄弟类型 ⇒ 新老写法都接得住，且 `type(exc).__name__ == "CadError"`
    让"错在图元层"在日志里一眼可辨。
    """


# --------------------------------------------------------------------------------------
# 入参把关（全部在落库之前跑完）
# --------------------------------------------------------------------------------------


def _nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米（bool 不算数；非整数浮点 / 非数 / **越界** / 非正一律报错）。

    量程口径与 `commands`/`edit.modify` 同一把尺子（`constraints.nm_in_range`，上限 2^53 nm）：
    超过它的值下游 `float()` 会溢出或丢位，L1 里就不该收。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CadError(_KEY_LENGTH, text=repr(value), detail=f"{what} must be an integer nanometre value")
    if isinstance(value, float):
        if not math.isfinite(value) or float(value) != float(int(value)):
            raise CadError(_KEY_LENGTH, text=repr(value), detail=f"{what} must be a whole number of nm")
        value = int(value)
    if not nm_in_range(int(value)):
        raise CadError(_KEY_RANGE, value=repr(value), min=MIN_ABS_NM, max=MAX_ABS_NM,
                       detail=f"{what} is outside the storable nm range (|v| <= 2**53)")
    if positive and value <= 0:
        raise CadError(_KEY_LENGTH, text=str(value), detail=f"{what} must be > 0 nm")
    return int(value)


def _point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise CadError(_KEY_GEOMETRY, detail=f"{what} must be a (x_nm, y_nm) pair, got {value!r}")
    return (_nm(value[0], what=f"{what}.x"), _nm(value[1], what=f"{what}.y"))


def _points(value: Any, *, what: str = "points_nm") -> list[tuple[int, int]]:
    """折线点串入参 → `[(x_nm, y_nm), ...]`。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise CadError(_KEY_GEOMETRY, detail=f"{what} must be a sequence of (x_nm, y_nm) pairs, got {value!r}")
    return [_point(item, what=f"{what}[{index}]") for index, item in enumerate(value)]


def _integer(value: Any, *, what: str, minimum: int, maximum: int | None = None) -> int:
    """整数入参（`bool` 不算数）；给了 `maximum` 就一并卡上限（**先拒后循环**）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CadError(_KEY_GEOMETRY, detail=f"{what} must be an integer, got {value!r}")
    if value < minimum:
        raise CadError(_KEY_GEOMETRY, detail=f"{what} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise CadError(_KEY_GEOMETRY, detail=f"{what} must be <= {maximum}, got {value}")
    return int(value)


def _layer(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CadError(_KEY_GEOMETRY, detail=f"layer must be a non-empty string, got {value!r}")
    return value


def _flag(value: Any, *, what: str) -> bool:
    if not isinstance(value, bool):
        raise CadError(_KEY_GEOMETRY, detail=f"{what} must be a bool, got {value!r}")
    return value


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米（就近取整；真源分辨率就是 1 nm）。"""
    return int(round(value))


def _normalized_deg(value: float) -> float:
    """与 A9 `add_arc` 的 `_as_deg` **逐位一致**的归一化（`[0, 360)` + 6 位小数，去 -0.0）。

    必须逐位一致：圆弧端点由 `(cx, cy, r)` + **存储角度**导出，本文件要在落库前把同样的整数端点算出来，
    才能让"线端"与"弧端"在整数纳米上严格相等（`closed_profiles` 按严格相等判闭合）。
    """
    normalized = round(float(value) % 360.0, 6)
    return 0.0 if normalized == 0.0 else normalized


def _arc_endpoint(center: tuple[int, int], radius: int, deg: float) -> tuple[int, int]:
    """圆弧端点（与 `constraints._point` 对 `arc` 的 start/end 用**同一个公式**）。"""
    rad = math.radians(deg)
    return (_round_nm(center[0] + radius * math.cos(rad)), _round_nm(center[1] + radius * math.sin(rad)))


def _append_entity(sk: Any, *, kind: str, layer: str, geom: dict[str, Any]) -> str:
    """按契约形状追加实体并返回 uuid（`polyline` 不在 A9 的命令里，故本层自己落库）。"""
    entity: dict[str, Any] = {
        "uuid": new_uuid(),
        "kind": kind,
        "layer": layer,
        "construction": False,
        "geom": geom,
    }
    sketch_entities(sk).append(entity)
    return str(entity["uuid"])


# --------------------------------------------------------------------------------------
# 矩形
# --------------------------------------------------------------------------------------


def add_rectangle(
    sk: Any,
    *,
    p1_nm: tuple[int, int],
    p2_nm: tuple[int, int],
    layer: str = "0",
) -> list[str]:
    """加一个**与坐标轴平行**的矩形（4 条 `line` + 2 条 `horizontal` + 2 条 `vertical`），返回 4 个 line uuid。

    返回顺序 = 逆时针一圈：下边 `p1→(x2,y1)`、右边、上边、左边（`(x1,y2)→p1`）。
    约束顺序 = `horizontal(下)`、`vertical(右)`、`horizontal(上)`、`vertical(左)`。
    退化（`p1_nm == p2_nm`，或两点同 x / 同 y）抛 `CadError` —— 零宽/零高的"矩形"不是矩形。
    斜矩形不在这里（那要 4 条线 + 2 平行 + 2 垂直 + 1 角度，属于约束链路，请用 `add_polyline` + `add_constraint`）。
    """
    first = _point(p1_nm, what="p1_nm")
    second = _point(p2_nm, what="p2_nm")
    layer_name = _layer(layer)
    if first == second:
        raise CadError(_KEY_GEOMETRY, detail=f"degenerate rectangle: p1_nm == p2_nm == {first!r}")
    if first[0] == second[0] or first[1] == second[1]:
        raise CadError(_KEY_GEOMETRY, detail=f"degenerate rectangle (zero width or height): {first!r} -> {second!r}")

    corners = (
        (first[0], first[1]),
        (second[0], first[1]),
        (second[0], second[1]),
        (first[0], second[1]),
    )
    uuids = [
        add_line(sk, p1=corners[index], p2=corners[(index + 1) % 4], layer=layer_name) for index in range(4)
    ]
    for index, kind in enumerate(("horizontal", "vertical", "horizontal", "vertical")):
        add_constraint(sk, kind=kind, refs=[(uuids[index], "")])
    return uuids


# --------------------------------------------------------------------------------------
# 正多边形
# --------------------------------------------------------------------------------------


def add_polygon(
    sk: Any,
    *,
    center_nm: tuple[int, int],
    radius_nm: int,
    sides: int,
    layer: str = "0",
    inscribed: bool = True,
) -> list[str]:
    """加一个正多边形（N 条 `line` 首尾相接 + N 个相邻端点 `coincident`），返回 N 个 line uuid。

    - `radius_nm`：`inscribed=True`（缺省）时是**外接圆半径**（顶点落在该圆上；`radius_nm <= 0` 抛 `CadError`）；
      `inscribed=False` 时是**内切圆半径**（各边与该圆相切，外接圆半径 = `radius_nm / cos(π/N)`）；
    - `sides < 3` 抛 `CadError`（`sides` 必须是 int，`3.0` 不算）；`sides` 超过单次操作对象数上限
      （`constraints.MAX_ITEMS_PER_OPERATION`）也抛 `CadError` —— **先拒后循环**：每个顶点要建 line +
      coincident 约束，无界边数（如 10^12）会把调用方挂在 `range()` 里（B6 实测同形缺陷）；
    - 顶点从 **+Y（90°）** 起逆时针均布，坐标就近取整到整数纳米；若该半径在 1 nm 分辨率下会把顶点挤成同一个点
      （半径太小 / 边数太多），抛 `CadError` 而不是产出一堆零长线；
    - 多边形**由 line 组成**（不是单个 polyline）：这样 `constraints.closed_profiles()` 直接认得出它是闭合轮廓，
      与拉伸链路兼容（polyline 的缺口见模块 docstring）。
    """
    center = _point(center_nm, what="center_nm")
    radius = _nm(radius_nm, what="radius_nm", positive=True)
    count = _integer(sides, what="sides", minimum=3, maximum=MAX_ITEMS_PER_OPERATION)
    layer_name = _layer(layer)
    # `_flag` 是**校验器**（返回校验后的 bool），不是判据：False 是合法入参（内切圆口径）。
    # 旧写法 `if not _flag(...)` 会把合法的 inscribed=False 当成非法拒掉，同时让非 bool 的
    # 非法值（如 "yes"）漏过校验直达下面的算术。
    _flag(inscribed, what="inscribed")

    circumradius = float(radius) if inscribed else float(radius) / math.cos(math.pi / count)
    if circumradius < 1.0:
        raise CadError(
            _KEY_LENGTH, text=str(radius),
            detail=f"radius_nm={radius} cannot resolve {count} vertices at integer-nm resolution",
        )
    vertices: list[tuple[int, int]] = []
    for index in range(count):
        angle = math.radians(90.0 + 360.0 * index / count)
        vertices.append(
            (_round_nm(center[0] + circumradius * math.cos(angle)), _round_nm(center[1] + circumradius * math.sin(angle)))
        )
    if len(set(vertices)) != count:
        raise CadError(
            _KEY_LENGTH, text=str(radius),
            detail=f"radius_nm={radius} collapses vertices of a {count}-gon at integer-nm resolution: {vertices!r}",
        )

    uuids = [
        add_line(sk, p1=vertices[index], p2=vertices[(index + 1) % count], layer=layer_name) for index in range(count)
    ]
    for index in range(count):
        add_constraint(
            sk,
            kind="coincident",
            refs=[(uuids[index], "end"), (uuids[(index + 1) % count], "start")],
        )
    return uuids


# --------------------------------------------------------------------------------------
# 腰形槽（两条平行线 + 两端半圆）
# --------------------------------------------------------------------------------------


def add_slot(
    sk: Any,
    *,
    p1_nm: tuple[int, int],
    p2_nm: tuple[int, int],
    width_nm: int,
    layer: str = "0",
) -> list[str]:
    """加一个腰形槽（2 条平行 `line` + 两端半圆 `arc`），返回 4 个 uuid。

    返回顺序 = `[上边线 p1侧→p2侧, 下边线 p2侧→p1侧, p2 端半圆, p1 端半圆]`；
    两条线沿轴线方向平移 ±`width_nm/2`，两端各是一段**逆时针扫 180°** 的半圆（半径 = `width_nm/2`）。

    - `p1_nm == p2_nm`（零长槽）、`width_nm <= 0`、`width_nm` 为**奇数**（半圆半径必须是整数纳米）、
      或槽太短以致整数纳米下端点重合 —— 一律抛 `CadError`；
    - 端点按"落库后的存储角度"复算再喂给线（见 `_arc_endpoint`）⇒ 线端与弧端在整数纳米上**严格相等**，
      `constraints.closed_profiles()` 能把 4 条边认成一个闭环（有测试钉住，含斜置槽）。
    """
    first = _point(p1_nm, what="p1_nm")
    second = _point(p2_nm, what="p2_nm")
    width = _nm(width_nm, what="width_nm", positive=True)
    layer_name = _layer(layer)
    if first == second:
        raise CadError(_KEY_GEOMETRY, detail=f"degenerate slot: p1_nm == p2_nm == {first!r}")
    if width % 2 != 0:
        raise CadError(
            _KEY_LENGTH, text=str(width),
            detail=f"width_nm must be even (cap radius must be a whole number of nm), got {width}",
        )
    radius = width // 2

    axis_deg = math.degrees(math.atan2(second[1] - first[1], second[0] - first[0]))
    start_deg = _normalized_deg(axis_deg + 90.0)  # 法向一侧（+n）
    end_deg = _normalized_deg(axis_deg + 270.0)  # 另一侧（-n）
    fix, six = _arc_endpoint(first, radius, start_deg), _arc_endpoint(first, radius, end_deg)
    s2x, s2y = _arc_endpoint(second, radius, start_deg), _arc_endpoint(second, radius, end_deg)
    if fix == s2x or six == s2y:
        raise CadError(
            _KEY_LENGTH, text=str(width),
            detail=f"slot is too short to resolve two distinct caps at integer-nm resolution: "
                   f"{first!r} -> {second!r} width={width}",
        )

    return [
        add_line(sk, p1=fix, p2=s2x, layer=layer_name),
        add_line(sk, p1=s2y, p2=six, layer=layer_name),
        add_arc(sk, center=second, radius_nm=radius, start_deg=start_deg, end_deg=end_deg, layer=layer_name),
        add_arc(sk, center=first, radius_nm=radius, start_deg=start_deg, end_deg=end_deg, layer=layer_name),
    ]


# --------------------------------------------------------------------------------------
# 折线（一个 polyline 实体）
# --------------------------------------------------------------------------------------


def add_polyline(
    sk: Any,
    points_nm: Sequence[tuple[int, int]],
    *,
    closed: bool = False,
    layer: str = "0",
) -> list[str]:
    """加一条折线 —— **一个** `polyline` 实体（`geom={"points": [[x, y], ...]}`），返回 `[uuid]`（长度恒为 1）。

    - `points_nm < 2` 个点、相邻点重复（零长段）、坐标不是整数纳米 ⇒ 抛 `CadError`；
    - `closed=True` 时把首点**原样补在末点**（几何闭合，不靠额外字段）⇒ "端点坐标严格相同"这条通用判据成立；
      **少于 3 个不同顶点**的"闭合折线"是退化的，抛 `CadError`；首末点已相同的输入不重复补点（结果几何相同）；
    - 相邻端点由**同一个点**共享（折线的定义就是共享顶点），因此本函数**不写** coincident 约束：
      那会是一条恒等式，而且 `constraints.SUB_SHAPES` 里没有 polyline（见 `POLYLINE_CONSTRAINT_GAP`）。
    """
    points = _points(points_nm)
    layer_name = _layer(layer)
    is_closed = _flag(closed, what="closed")
    if len(points) < 2:
        raise CadError(_KEY_GEOMETRY, detail=f"polyline needs at least 2 points, got {len(points)}")
    for previous, current in zip(points, points[1:]):
        if previous == current:
            raise CadError(_KEY_GEOMETRY, detail=f"polyline has a zero-length segment at {current!r}")
    if is_closed:
        if len(points) < 3:
            raise CadError(_KEY_GEOMETRY, detail=f"a closed polyline needs at least 3 distinct points, got {len(points)}")
        if points[0] != points[-1]:
            points = points + [points[0]]

    uuid_ = _append_entity(
        sk,
        kind="polyline",
        layer=layer_name,
        geom={"points": [[x, y] for x, y in points]},
    )
    return [uuid_]


# --------------------------------------------------------------------------------------
# 闭合轮廓：上游那支的超集（只补 polyline 一支，不重写上游判据）
# --------------------------------------------------------------------------------------


def _field(entity: Any, name: str, default: Any = None) -> Any:
    """结构取值（映射 / 对象双兼容，与 A10 同一取舍）。"""
    if isinstance(entity, Mapping):
        return entity.get(name, default)
    return getattr(entity, name, default)


def closed_profiles(sk: Any, *, tol_nm: float = 0.0) -> list[tuple[str, ...]]:
    """闭合轮廓（uuid 环）：`constraints.closed_profiles()` 的**超集**，额外认"闭合折线"。

    - `line`/`arc`/`circle` 的环**原样交给上游**（同一把尺子，不重写、不复制判据）；
    - `polyline`：首末点坐标（`tol_nm=0` 时整数纳米严格相等）相同、且不同顶点 ≥ 3 ⇒ 它自己成环
      （一个实体一条边，故环长 1）；否则不返回（未闭合的折线不是轮廓）。
    - 上游缺口与根治办法见模块 docstring；本函数是折线在**当前冻结的** `constraints.py` 下
      能与拉伸链路对接的可用路径。
    """
    loops = list(constraints_closed_profiles(sk, tol_nm=tol_nm))
    for entity in sketch_entities(sk):
        if _field(entity, "kind") != "polyline":
            continue
        if _field(entity, "construction", False):
            continue  # 与上游同口径：构造几何不进轮廓
        raw_points = _field(_field(entity, "geom", {}) or {}, "points") or []
        points = [(int(point[0]), int(point[1])) for point in raw_points]
        if len(points) < 4 or len(set(points[:-1])) < 3:
            continue
        if tol_nm > 0:
            is_closed = math.dist(points[0], points[-1]) <= float(tol_nm)
        else:
            is_closed = points[0] == points[-1]
        if is_closed:
            loops.append((str(_field(entity, "uuid", "")),))
    return sorted(set(loops))


# 注：`constraints_closed_profiles`（上游那支，未打补丁）由上面的 import 直接再导出，
# 便于调用方把两把尺子摆在一起对比 —— 本模块**不** monkeypatch、**不**改上游文件。
