"""cadcore.edit.modify —— L1 草图的二维编辑命令族（AutoCAD modify 基本集）。

一句话用途
----------
把 ``move / copy / rotate / scale / mirror / offset / trim / extend / fillet / chamfer /
array_linear`` 这一批"改图"动作，作用在共享接口冻结的 L1 草图 dict 上::

    {"entities": [{uuid, kind, layer, construction, geom}], "constraints": [...], "dimensions": [...]}

长度一律**整数纳米**；实体 kind ∈ ``line`` / ``arc`` / ``circle`` / ``polyline``；
geom 字段：``line{x1,y1,x2,y2}`` / ``circle{cx,cy,r}`` / ``arc{cx,cy,r,start_deg,end_deg}`` /
``polyline{points:[[x,y],...]}``。每个命令返回**受影响的实体 uuid 列表**：

- **新建**实体的命令（`copy` / `mirror(keep_original=True)` / `offset` / `fillet` / `chamfer` /
  `array_linear`）返回**新 uuid**（原实体一个字节都不动）；
- **原地改写**的命令（`move` / `rotate` / `scale` / `mirror(keep_original=False)` / `trim` / `extend`）
  返回**被改写实体的原 uuid** —— 原地改写是硬需求，不是实现偷懒：L1 的 `constraints` / `dimensions`
  是按 uuid 引用实体的，若把移动做成"删旧建新"，用户已有的约束与尺寸会当场变成悬空引用。

为什么这样设计
--------------
1. **一个仿射核**：所有线性变换共用 ``_transform_entity(entity, matrix)``（2×3 仿射矩阵，
   ``x' = a·x + b·y + c``，``y' = d·x + e·y + f``）。每种实体各自正确变换：
   - `line`：两端点变换后取整；
   - `circle`：圆心变换、半径按 ``sqrt(|det|)`` 缩放；
   - `arc`：圆心/端点一起变换，半径缩放，角度由**变换后的端点**相对新圆心反算
     （平移/缩放不变，旋转整体加转角；镜像 ``det < 0`` 会翻转扫掠方向 ⇒ 交换 start/end，
     这样"逆时针从 start 扫到 end"的存储口径下，镜像后的点集与原弧严格对应）；
   - `polyline`：逐顶点变换。**没有第二份内联副本**，改一处即改全部命令。
2. **先算后写，绝不半接受**：所有命令先解析全部引用、算出全部新几何，全部校验通过后才写回文档；
   中途失败时草图保持原样。坏入参（非整数纳米、半径非正、因子 ≤ 0、退化镜像轴）当场抛 `CadError`。
3. **真求交，不用近似**：`trim` / `extend` / `fillet` / `chamfer` 走参数方程 + 叉积求两条**无限直线**的交点，
   纳米整数入参下用 `fractions.Fraction` 精确有理数运算（不引入浮点噪声），落盘前按"四舍五入、远离零"取整。
   平行/共线（叉积为 0）与无交点一律抛 `CadError`，**绝不"求不出来就返回原实体"**。
4. **错误族收敛到 `CadError`**：本模块的公开错误全部是 `CadError`（`cadcore.errors.CadError` 的子类，
   带机器可读的 `reason`）：`unsupported_geometry` / `no_intersection` / `parallel_lines` /
   `invalid_length` / `invalid_argument` / `empty_selection` / `unknown_reference` / `geometry_invalid` /
   `degenerate_*`。下游按 `reason` 归因，不解析文案。
5. **复用而非重写**：结构访问层（`_get` / `_geom` / `_geom_set` / `new_uuid` / `sketch_entities`）
   直接取自 `cadcore.sketch.constraints` —— L1 真源只有一份定义，本模块不另起一套副本。
6. **文案走 key**：本文件非 docstring 的字符串字面量一律 ASCII（面向用户的文案由语言层按 key 取词）。

有意限定的 MVP 边界（写下来，防止下游靠猜）
------------------------------------------
- `trim` / `extend` / `fillet` / `chamfer` **只做直线-直线**；target/cutter/boundary 只要出现非 `line`
  实体（含 `arc` / `circle` / `polyline`）就抛 `CadError(reason="unsupported_geometry")`。
  这是**有意缩小的 MVP 面**：直线-直线交点是解析解，圆弧/多段线的裁剪需要按扫掠区间与最近点参数化，
  留到后续版本；本期宁可显式报"不支持"，也不做近似裁剪或静默返回原实体。
- `trim` / `extend` 没有 pick point（签名只有 ref），故用显式关键字选定保留侧/延伸端：
  `trim(..., keep="start"|"end")`（缺省 `start`：去掉第一个交点之后伸向 `end` 的那段）、
  `extend(..., end="end"|"start")`（缺省 `end`：把 `x2/y2` 那头延伸到最近的边界交点）。
- `offset` 的 `side`：`+1` = 沿**左法向**偏移（对 `circle` / `arc` 即向外扩半径），`-1` = 右法向（向内缩）。
  圆/弧内缩到半径 ≤ 0 抛 `CadError(reason="degenerate_offset")`。`polyline` 按**开放折线**处理（逐段偏移 +
  内顶点求交成尖角/miter）；相邻段 180° 折返（尖点无解）抛 `CadError(reason="degenerate_offset")`。
- `array_linear` 的 `refs` 指**源实体**，返回 `rows*cols - 1` 个新 uuid（跳过原位的 `(0,0)`）；
  行列遍历顺序为 row-major（先行 `i`，后列 `j`），偏移量 `(j*dx_nm, i*dy_nm)`。
- `fillet` / `chamfer` 的"角"由**线段远端**确定：交点已知，每条线取**离交点更远**的那个端点方向作为
  进入材料的方向（共享端点成角的标准情形即由此得到正确分支）；两条直线平行/共线抛
  `CadError(reason="parallel_lines")`。切线长 `d = r / tan(θ/2)`，倒角 `d = distance_nm`；
  两条线被裁剪/延伸到切点（就近交点的那一端被替换）；若裁剪后线段长度归零则抛 `degenerate_*`。
- 变换类命令的 `refs` 里出现**未知 kind**（不在 `line/arc/circle/polyline` 内）同样抛
  `CadError(reason="unsupported_geometry")`。
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any, Mapping, Sequence

from ..errors import CadError as _BaseCadError
from ..sketch.constraints import (
    MAX_ABS_NM,
    MAX_ITEMS_PER_OPERATION,
    MIN_ABS_NM,
    Sketch as Sketch,
    _geom,
    _get,
    _geom_set,
    items_in_range,
    new_uuid,
    nm_in_range,
    sketch_entities,
)
from ..units import finite_number

__all__ = [
    "CadError",
    "array_linear",
    "chamfer",
    "copy",
    "extend",
    "fillet",
    "mirror",
    "move",
    "offset",
    "rotate",
    "scale",
    "transform_entity",
    "trim",
]

#: 本模块支持的实体种类（L1 冻结集）。
SUPPORTED_KINDS: tuple[str, ...] = ("line", "arc", "circle", "polyline")

#: 仿射矩阵口径：`(a, b, c, d, e, f)` 表示 `x' = a·x + b·y + c`、`y' = d·x + e·y + f`。
Matrix = tuple[float, float, float, float, float, float]

#: 角度落盘精度：与 `cadcore.sketch.commands` 的 `_ANGLE_DECIMALS` 一致（1e-6 度）。
_ANGLE_DECIMALS: int = 6

#: 方向判定的相对容差（平行/共线判定用；解析叉积下 1e-12 足够）。
_DIR_TOL: float = 1e-12


# --------------------------------------------------------------------------------------
# 错误族
# --------------------------------------------------------------------------------------


class CadError(_BaseCadError):
    """编辑命令的失败信号（`cadcore.errors.CadError` 的子类，带机器可读 `reason`）。

    与基类的唯一差别：**`message` 有缺省值**，于是 `CadError(reason="unsupported_geometry")`
    这种"只报原因"的用法直接可用；`details` 里带上定位用的 `uuid` / `kind` / `op`。
    """

    default_reason = "cad_error"

    def __init__(self, message: str = "edit command failed", *, reason: str = "", **details: Any) -> None:
        super().__init__(message, reason=reason, **details)


def _fail(reason: str, detail: str, **details: Any) -> "CadError":
    """构造失败信号（文案 ASCII；`reason` 是下游唯一的归因入口）。"""
    return CadError(detail, reason=reason, **details)


# --------------------------------------------------------------------------------------
# 入参把关（长度一律整数纳米；点是小整数对；坏输入当场报错）
# --------------------------------------------------------------------------------------


def _as_nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米（bool 不算数；非整数浮点、NaN、越界一律报 `invalid_length`）。

    量程与 `sketch/constraints._as_nm` 同一把尺子（`nm_in_range`，上限 2^53 nm）：越界值收下去，
    平移/缩放后的坐标就再也过不了 `float()`（DXF 导出裸 `OverflowError`，见 B6 缺陷 RC2）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("invalid_length", f"{what} must be an integer nanometre value", what=what, value=repr(value))
    if isinstance(value, float):
        if not math.isfinite(value) or float(value) != float(int(value)):
            raise _fail("invalid_length", f"{what} must be a whole number of nm", what=what, value=repr(value))
        value = int(value)
    if not nm_in_range(int(value)):
        raise _fail("out_of_range", f"{what} is outside the storable nm range (|v| <= 2**53)",
                    what=what, value=repr(value), min=MIN_ABS_NM, max=MAX_ABS_NM)
    if positive and value <= 0:
        raise _fail("invalid_length", f"{what} must be > 0 nm", what=what, value=int(value))
    return int(value)


def _as_point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("invalid_argument", f"{what} must be a (x_nm, y_nm) pair", what=what, value=repr(value))
    return (_as_nm(value[0], what=f"{what}.x"), _as_nm(value[1], what=f"{what}.y"))


def _as_factor(value: Any, *, what: str = "factor") -> float:
    """缩放因子 → 正的有限实数（负因子是镜像，不属于 `scale` 的语义，故拒收）。

    可表示性走 `finite_number`（唯一真源）：`float(10**309)` 会抛 `OverflowError`，
    "先转再判"的写法等于让参数校验自己泄漏裸内建异常（B6 缺陷 RC5）。
    """
    number = finite_number(value)
    if number is None:
        raise _fail("invalid_argument", f"{what} must be a finite number", what=what, value=repr(value))
    if number <= 0.0:
        raise _fail("invalid_argument", f"{what} must be > 0", what=what, value=repr(value))
    return number


def _as_angle(value: Any, *, what: str = "angle_deg") -> float:
    """角度入参 → 有限浮点度（角度不是长度，允许任意实数、按 mod 360 生效）。"""
    number = finite_number(value)
    if number is None:
        raise _fail("invalid_argument", f"{what} must be a finite angle in degrees",
                    what=what, value=repr(value))
    return number


def _as_count(value: Any, *, what: str) -> int:
    """计数入参 → 正整数，且**不许超过单次操作对象数上限**（`constraints.items_in_range`）。

    上限这一条是 B6 实测缺陷的根因：计数入参直接驱动 `range()` + 逐个 clone，10^309 这种
    无界整数会让请求永不返回、工作区锁被占死（`array_linear(rows=10**309)`）。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail("invalid_argument", f"{what} must be a positive integer", what=what, value=repr(value))
    if not items_in_range(int(value)):
        raise _fail("invalid_argument",
                    f"{what} is above the per-operation limit {MAX_ITEMS_PER_OPERATION}",
                    what=what, value=repr(value), max=MAX_ITEMS_PER_OPERATION)
    return int(value)


def _round_nm(value: float) -> int:
    """纳米取整：四舍五入**远离零**（`±0.5` 对称，结果可复现）。"""
    floor = math.floor(value)
    return int(floor) + 1 if value - floor >= 0.5 else int(floor)


def _ratio_to_nm(value: Fraction) -> int:
    """精确有理数 → 整数纳米（四舍五入远离零；不经过浮点，避免舍入噪声）。"""
    sign = -1 if value < 0 else 1
    numerator, denominator = abs(value.numerator), value.denominator
    quotient, remainder = divmod(numerator, denominator)
    if 2 * remainder >= denominator:
        quotient += 1
    return sign * int(quotient)


def _round_angle(deg: float) -> float:
    """角度落盘：归一化到 `[0, 360)` 并去掉 `-0.0`（与 A9 `add_arc` 的存储口径一致）。"""
    normalized = round(float(deg) % 360.0, _ANGLE_DECIMALS)
    return 0.0 if normalized == 0.0 else normalized


# --------------------------------------------------------------------------------------
# 实体读取/写入（走 constraints 的结构访问层，L1 真源只有一份定义）
# --------------------------------------------------------------------------------------


def _uuid_of(entity: Any) -> str:
    value = _get(entity, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("geometry_invalid", "entity has no usable uuid")
    return value


def _kind(entity: Any) -> str:
    """实体种类；不在 L1 冻结集内即报 `unsupported_geometry`（不静默当多段线处理）。"""
    kind = _get(entity, "kind", None)
    if kind not in SUPPORTED_KINDS:
        raise _fail("unsupported_geometry", f"entity kind {kind!r} is out of scope", kind=str(kind),
                    uuid=_uuid_of(entity))
    return str(kind)


def _field_int(entity: Any, field: str) -> int:
    """几何字段 → 整数纳米（浮点/布尔/非数一律报错：真源就是整数纳米）。"""
    value = _get(_geom(entity), field, None)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("geometry_invalid", f"geom.{field} must be an integer nanometre value",
                    field=field, uuid=_uuid_of(entity), value=repr(value))
    return int(value)


def _field_num(entity: Any, field: str) -> float:
    """几何字段 → 有限实数（角度字段用）。不可表示（309 位整数）按 `geometry_invalid` 报，不裸 OverflowError。"""
    value = _get(_geom(entity), field, None)
    number = finite_number(value)
    if number is None:
        raise _fail("geometry_invalid", f"geom.{field} must be a finite number", field=field,
                    uuid=_uuid_of(entity), value=repr(value))
    return number


def _geom_set_num(entity: Any, field: str, value: Any) -> None:
    _geom_set(entity, field, value)


def _find(sk: Sketch, ref_uuid: str) -> Any:
    """按 uuid 找实体；找不到报 `unknown_reference`（禁下标引用，也禁静默跳过）。"""
    if not isinstance(ref_uuid, str) or not ref_uuid:
        raise _fail("invalid_argument", f"entity reference must be a non-empty uuid string, got {ref_uuid!r}")
    for entity in sketch_entities(sk):
        if _get(entity, "uuid", None) == ref_uuid:
            return entity
    raise _fail("unknown_reference", f"no entity with uuid {ref_uuid}", reference=ref_uuid)


def _ref_uuid(ref: Any) -> str:
    """引用 → uuid：接受裸 uuid 字符串，也接受 `(entity_uuid, sub_shape_name)` 对（后者只用第一项）。"""
    if isinstance(ref, str):
        return ref
    if isinstance(ref, (bytes,)) or not isinstance(ref, Sequence) or len(ref) != 2 or not isinstance(ref[0], str):
        raise _fail("invalid_argument", f"reference must be a uuid string or a (uuid, sub) pair, got {ref!r}")
    return str(ref[0])


def _resolve_refs(sk: Sketch, refs: Any) -> list[Any]:
    """解析实体引用列表 → 实体对象列表（保序去重；空选择当场报错，不静默返 0）。"""
    if isinstance(refs, (str, bytes)):
        items: list[Any] = [refs]
    elif isinstance(refs, Sequence):
        items = list(refs)
    else:
        raise _fail("invalid_argument", f"refs must be a sequence of references, got {type(refs).__name__}")
    if not items:
        raise _fail("empty_selection", "refs is empty: nothing to edit (refusing to no-op silently)")
    entities: list[Any] = []
    seen: set[str] = set()
    for item in items:
        entity = _find(sk, _ref_uuid(item))
        key = _uuid_of(entity)
        if key in seen:
            continue
        seen.add(key)
        entities.append(entity)
    return entities


def _resolve_one(sk: Sketch, ref: Any) -> Any:
    """解析单个实体引用 → 实体对象。"""
    return _find(sk, _ref_uuid(ref))


def _line_points(entity: Any) -> tuple[tuple[int, int], tuple[int, int]]:
    """直线 → 两个端点（整数纳米）。"""
    return ((_field_int(entity, "x1"), _field_int(entity, "y1")),
            (_field_int(entity, "x2"), _field_int(entity, "y2")))


def _flat_points(entity: Any) -> tuple[int, int, int, int]:
    """直线 → `(x1, y1, x2, y2)`（字段名顺序，供字典构造用，避免手抄第二遍字段序）。"""
    start, end = _line_points(entity)
    return (start[0], start[1], end[0], end[1])


def _require_lines(entities: Sequence[Any], *, op: str) -> list[Any]:
    """MVP 边界：只接受直线；其它 kind 抛 `unsupported_geometry`（有意限定，见模块 docstring）。"""
    for entity in entities:
        if _kind(entity) != "line":
            raise _fail("unsupported_geometry",
                        f"{op} supports line-line only (this is a deliberate MVP boundary)",
                        op=op, kind=_kind(entity), uuid=_uuid_of(entity))
    return list(entities)


def _append(sk: Sketch, *, kind: str, layer: Any, geom: dict[str, Any], construction: Any = False,
            fixed: Any = None) -> str:
    """把新实体追加进草图并返回新 uuid（字段照 L1：uuid/kind/layer/construction/geom）。"""
    entity_uuid = new_uuid()
    entity: dict[str, Any] = {
        "uuid": entity_uuid,
        "kind": kind,
        "layer": layer,
        "construction": bool(construction),
        "geom": geom,
    }
    if fixed:
        entity["fixed"] = True
    sketch_entities(sk).append(entity)
    return entity_uuid


def _clone_geom(entity: Any) -> dict[str, Any]:
    """克隆实体的 geom（深拷贝：`copy`/`mirror(keep)`/`array_linear` 不许与原实体共享可变对象）。"""
    kind = _kind(entity)
    geom = _geom(entity)
    if kind == "line":
        return {key: _field_int(entity, key) for key in ("x1", "y1", "x2", "y2")}
    if kind == "circle":
        return {"cx": _field_int(entity, "cx"), "cy": _field_int(entity, "cy"), "r": _field_int(entity, "r")}
    if kind == "arc":
        return {"cx": _field_int(entity, "cx"), "cy": _field_int(entity, "cy"), "r": _field_int(entity, "r"),
                "start_deg": _field_num(entity, "start_deg"), "end_deg": _field_num(entity, "end_deg")}
    points = _get(geom, "points", None)
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)) or len(points) < 2:
        raise _fail("geometry_invalid", "polyline needs at least 2 points", uuid=_uuid_of(entity))
    return {"points": [[_as_nm(point[0], what="polyline.x"), _as_nm(point[1], what="polyline.y")]
                       for point in points]}


def _clone_entity(sk: Sketch, entity: Any) -> Any:
    """新建一个"几何待定"的实体条目（uuid 新生成；layer/construction/fixed 沿用源实体）。"""
    source = entity
    new_entity: dict[str, Any] = {
        "uuid": new_uuid(),
        "kind": _kind(source),
        "layer": _get(source, "layer", "0"),
        "construction": bool(_get(source, "construction", False)),
        "geom": _clone_geom(source),
    }
    if _get(source, "fixed", False):
        new_entity["fixed"] = True
    sketch_entities(sk).append(new_entity)
    return new_entity


# --------------------------------------------------------------------------------------
# 仿射核（所有线性变换共用；每种实体各自正确变换）
# --------------------------------------------------------------------------------------


def _mat_translate(dx: float, dy: float) -> Matrix:
    return (1.0, 0.0, float(dx), 0.0, 1.0, float(dy))


def _mat_rotate(angle_deg: float) -> Matrix:
    rad = math.radians(float(angle_deg))
    cos, sin = math.cos(rad), math.sin(rad)
    return (cos, -sin, 0.0, sin, cos, 0.0)


def _mat_scale(factor: float) -> Matrix:
    return (float(factor), 0.0, 0.0, 0.0, float(factor), 0.0)


def _mat_mirror(p1: tuple[int, int], p2: tuple[int, int]) -> Matrix:
    """过两点直线的镜像矩阵（`p1 == p2` 是退化轴 ⇒ 报错，不猜）。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length == 0.0:
        raise _fail("degenerate_axis", "mirror axis endpoints coincide", p1=repr(p1), p2=repr(p2))
    ux, uy = dx / length, dy / length
    return (ux * ux - uy * uy, 2.0 * ux * uy, 0.0, 2.0 * ux * uy, uy * uy - ux * ux, 0.0)


def _mat_apply(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + b * y + c, d * x + e * y + f)


def _mat_compose(outer: Matrix, inner: Matrix) -> Matrix:
    """`outer ∘ inner`（先施加 inner，再施加 outer）。"""
    a1, b1, c1, d1, e1, f1 = inner
    a2, b2, c2, d2, e2, f2 = outer
    return (a2 * a1 + b2 * d1, a2 * b1 + b2 * e1, a2 * c1 + b2 * f1 + c2,
            d2 * a1 + e2 * d1, d2 * b1 + e2 * e1, d2 * c1 + e2 * f1 + f2)


def _mat_about(matrix: Matrix, origin: tuple[int, int]) -> Matrix:
    """把线性变换挪到 `origin` 附近：`T(origin) ∘ matrix ∘ T(-origin)`。"""
    ox, oy = float(origin[0]), float(origin[1])
    return _mat_compose(_mat_translate(ox, oy), _mat_compose(matrix, _mat_translate(-ox, -oy)))


def _storable_nm(value: int, *, entity: Any, field: str) -> int:
    """变换结果 → 可存整数纳米；超出 `|v| <= 2**53` 当场报 `out_of_range`。

    为什么必须在**写回前**判（B6 缺陷 RC2b）：入参可以完全合法（`factor=2**63`），但结果会超出产品
    自述的可存范围 —— 旧行为是静默落进 L1 真源，之后任何 `float()` 通路（导出/再变换/捕捉）都失真或溢出。
    失败走 `CadError` ⇒ `Workspace._mutate` 回滚，文档不留半截（与坐标入参同口径：宁可结构化拒绝，
    不落一个存不下的数）。
    """
    if not nm_in_range(value):
        raise _fail("out_of_range",
                    f"transform result {field} is outside the storable nm range (|v| <= 2**53)",
                    field=field, uuid=_uuid_of(entity), value=repr(value),
                    min=MIN_ABS_NM, max=MAX_ABS_NM)
    return int(value)


def _write_nm_fields(entity: Any, values: Mapping[str, Any]) -> None:
    """一批长度字段**先全部过 `_storable_nm`、再统一写回**：失败时一个字段都没改（无半截变换）。

    库层也要原子：`Workspace._mutate` 的 `_snapshot→_restore` 只保 API 面，直接调 `edit.modify` 的
    调用方（脚本/其它包）拿到的是一份被改了一半的草图 —— 那正是"拒绝与半截状态同时发生"的温床。
    """
    checked = {field: _storable_nm(value, entity=entity, field=field)
               for field, value in values.items()}
    for field, value in checked.items():
        _geom_set_num(entity, field, value)


def _transform_entity(entity: Any, matrix: Matrix) -> Any:
    """**唯一的仿射核**：就地变换一个实体的 geom，返回该实体。

    正确性口径（每种 kind 各自成立）：
    - `line`：两端点变换后取整纳米；
    - `circle`：圆心变换、半径乘 `sqrt(|det|)`（相似变换下即缩放因子；镜像/旋转不变）；
    - `arc`：圆心与端点一起变换 ⇒ 半径缩放、角度按变换后的端点相对新圆心反算；
      `det < 0`（镜像）翻转扫掠方向 ⇒ 交换 start/end（保持"逆时针 start→end"的存储口径）；
    - `polyline`：逐顶点变换；
    - `det == 0`（退化矩阵）与非 L1 种类 ⇒ `CadError`，绝不静默跳过；
    - 结果一律过 `nm_in_range`（`_storable_nm`）：变换后存不下的坐标/半径 ⇒ `out_of_range`，
      绝不把超出 `|v| <= 2**53` 的值写进真源（B6 缺陷 RC2b）；且**先算完/查完再写回**，
      失败时实体保持原样（`_write_nm_fields`）。
    """
    kind = _kind(entity)
    a, b, c, d, e, f = (float(value) for value in matrix)
    det = a * e - b * d
    if not math.isfinite(det) or abs(det) <= 1e-15:
        raise _fail("degenerate_transform", "affine matrix is singular", uuid=_uuid_of(entity), det=det)
    scale = math.sqrt(abs(det))

    if kind == "line":
        x1, y1 = _mat_apply(matrix, _field_int(entity, "x1"), _field_int(entity, "y1"))
        x2, y2 = _mat_apply(matrix, _field_int(entity, "x2"), _field_int(entity, "y2"))
        if _round_nm(x1) == _round_nm(x2) and _round_nm(y1) == _round_nm(y2):
            raise _fail("degenerate_geometry", "line collapsed to a point under this transform", uuid=_uuid_of(entity))
        _write_nm_fields(entity, {"x1": _round_nm(x1), "y1": _round_nm(y1),
                                  "x2": _round_nm(x2), "y2": _round_nm(y2)})
        return entity

    cx, cy, radius = _field_int(entity, "cx"), _field_int(entity, "cy"), _field_int(entity, "r")
    new_cx, new_cy = _mat_apply(matrix, cx, cy)
    new_radius = _round_nm(radius * scale)
    if new_radius <= 0:
        raise _fail("degenerate_geometry", "radius collapsed to <= 0 under this transform",
                    uuid=_uuid_of(entity), radius_nm=new_radius)
    if kind == "circle":
        _write_nm_fields(entity, {"cx": _round_nm(new_cx), "cy": _round_nm(new_cy), "r": new_radius})
        return entity

    if kind == "arc":
        start_rad = math.radians(_field_num(entity, "start_deg"))
        end_rad = math.radians(_field_num(entity, "end_deg"))
        start_point = (cx + radius * math.cos(start_rad), cy + radius * math.sin(start_rad))
        end_point = (cx + radius * math.cos(end_rad), cy + radius * math.sin(end_rad))
        new_start = _mat_apply(matrix, start_point[0], start_point[1])
        new_end = _mat_apply(matrix, end_point[0], end_point[1])
        if det < 0.0:  # 镜像翻转扫掠方向 ⇒ 交换 start/end，点集与原弧一致
            new_start, new_end = new_end, new_start
        center = (_round_nm(new_cx), _round_nm(new_cy))
        start_deg = _round_angle(math.degrees(math.atan2(new_start[1] - center[1], new_start[0] - center[0])))
        end_deg = _round_angle(math.degrees(math.atan2(new_end[1] - center[1], new_end[0] - center[0])))
        if start_deg == end_deg:
            raise _fail("degenerate_geometry", "arc start/end coincide under this transform", uuid=_uuid_of(entity))
        _write_nm_fields(entity, {"cx": center[0], "cy": center[1], "r": new_radius})
        _geom_set_num(entity, "start_deg", start_deg)
        _geom_set_num(entity, "end_deg", end_deg)
        return entity

    # polyline：逐顶点变换（点表至少 2 个点，见 _clone_geom 的校验）；先算完再写回
    points = _clone_geom(entity)["points"]
    moved = [[_storable_nm(_round_nm(value), entity=entity, field="points")
              for value in _mat_apply(matrix, float(point[0]), float(point[1]))] for point in points]
    _geom_set_num(entity, "points", moved)
    return entity


def transform_entity(entity: Any, matrix: Matrix) -> Any:
    """`_transform_entity` 的公开别名（下游要复用同一个仿射核时走这里，别抄第二份）。"""
    return _transform_entity(entity, matrix)


# --------------------------------------------------------------------------------------
# 直线求交（参数方程 + 叉积；纳米整数入参下用精确有理数，绝不用近似）
# --------------------------------------------------------------------------------------


def _line_params(pa: tuple[int, int], pb: tuple[int, int],
                 qa: tuple[int, int], qb: tuple[int, int]) -> tuple[Fraction, Fraction] | None:
    """两条**无限直线**的交点参数 `(t, u)`：`P(t) = pa + t·(pb-pa)`，`Q(u) = qa + u·(qb-qa)`。

    叉积为 0（平行或共线）⇒ `None`（调用方据此抛 `CadError`，不许"求不出来就返回原实体"）。
    """
    rx, ry = pb[0] - pa[0], pb[1] - pa[1]
    sx, sy = qb[0] - qa[0], qb[1] - qa[1]
    denominator = rx * sy - ry * sx
    if denominator == 0:
        return None
    qpx, qpy = qa[0] - pa[0], qa[1] - pa[1]
    return (Fraction(qpx * sy - qpy * sx, denominator), Fraction(qpx * ry - qpy * rx, denominator))


def _point_at(pa: tuple[int, int], pb: tuple[int, int], t: Fraction) -> tuple[int, int]:
    """直线参数 → 落盘的整数纳米交点。"""
    x = Fraction(pa[0]) + t * (pb[0] - pa[0])
    y = Fraction(pa[1]) + t * (pb[1] - pa[1])
    return (_ratio_to_nm(x), _ratio_to_nm(y))


def _distance(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(float(p[0]) - float(q[0]), float(p[1]) - float(q[1]))


def _unit_direction(start: tuple[float, float], end: tuple[float, float], *, what: str) -> tuple[float, float]:
    length = _distance(start, end)
    if length == 0.0:
        raise _fail("degenerate_geometry", f"{what} has zero length")
    return ((float(end[0]) - float(start[0])) / length, (float(end[1]) - float(start[1])) / length)


def _near_end(start: tuple[int, int], end: tuple[int, int], point: tuple[float, float]) -> str:
    """两条端点里离 `point` **更近**的那一端（`"start"` / `"end"`；并列取 start）。"""
    return "start" if _distance(start, point) <= _distance(end, point) else "end"


def _far_endpoint(start: tuple[int, int], end: tuple[int, int], point: tuple[float, float]) -> tuple[int, int]:
    """两条端点里离 `point` **更远**的那一端（并列取 end）——角点分支的判据。"""
    return end if _distance(end, point) >= _distance(start, point) else start


def _set_endpoint(entity: Any, which: str, point: tuple[int, int]) -> None:
    """把直线的某一端替换为 `point`（左闭右开的字段命名与 L1 一致）。"""
    if which == "start":
        _geom_set_num(entity, "x1", int(point[0]))
        _geom_set_num(entity, "y1", int(point[1]))
    else:
        _geom_set_num(entity, "x2", int(point[0]))
        _geom_set_num(entity, "y2", int(point[1]))


def _corner_setup(entity_a: Any, entity_b: Any, *, op: str) -> tuple[tuple[int, int], tuple[float, float],
                                                                     tuple[float, float], tuple[int, int],
                                                                     tuple[int, int]]:
    """直线-直线的角点构造：返回 `(交点, u_a, u_b, 切点A, 切点B)` 之外的前三项。

    实际返回 `(intersection, unit_a, unit_b, near_a, near_b)`：`near_*` 是各自离交点更近的端点名。
    """
    a1, a2 = _line_points(entity_a)
    b1, b2 = _line_points(entity_b)
    params = _line_params(a1, a2, b1, b2)
    if params is None:
        raise _fail("parallel_lines", f"{op} needs two non-parallel lines",
                    a=_uuid_of(entity_a), b=_uuid_of(entity_b))
    intersection = _point_at(a1, a2, params[0])
    far_a = _far_endpoint(a1, a2, intersection)
    far_b = _far_endpoint(b1, b2, intersection)
    unit_a = _unit_direction(intersection, far_a, what=f"{op}.line_a")
    unit_b = _unit_direction(intersection, far_b, what=f"{op}.line_b")
    return intersection, unit_a, unit_b, _near_end(a1, a2, intersection), _near_end(b1, b2, intersection)


# --------------------------------------------------------------------------------------
# 变换类命令：move / copy / rotate / scale / mirror
# --------------------------------------------------------------------------------------


def move(sk: Sketch, refs: Sequence[str], *, dx_nm: int, dy_nm: int) -> list[str]:
    """平移 `refs` 选中的实体（原地改写），返回**被改写实体的原 uuid** 列表。

    硬断言口径：`dx_nm/dy_nm` 是整数纳米 ⇒ 平移后 `x1' == x1 + dx_nm`（逐字段精确相等，无舍入）。
    """
    dx = _as_nm(dx_nm, what="dx_nm")
    dy = _as_nm(dy_nm, what="dy_nm")
    entities = _resolve_refs(sk, refs)
    for entity in entities:  # 先全部校验（含 kind），再统一写回：绝不半接受
        _kind(entity)
    matrix = _mat_translate(dx, dy)
    for entity in entities:
        _transform_entity(entity, matrix)
    return [_uuid_of(entity) for entity in entities]


def copy(sk: Sketch, refs: Sequence[str], *, dx_nm: int, dy_nm: int) -> list[str]:
    """按 `(dx_nm, dy_nm)` 复制 `refs` 选中的实体，返回**新实体 uuid** 列表（原实体不变）。"""
    dx = _as_nm(dx_nm, what="dx_nm")
    dy = _as_nm(dy_nm, what="dy_nm")
    sources = _resolve_refs(sk, refs)
    for entity in sources:
        _kind(entity)
    matrix = _mat_translate(dx, dy)
    created: list[str] = []
    for entity in sources:
        clone = _clone_entity(sk, entity)
        _transform_entity(clone, matrix)
        created.append(_uuid_of(clone))
    return created


def rotate(sk: Sketch, refs: Sequence[str], *, center_nm: tuple[int, int], angle_deg: float) -> list[str]:
    """绕 `center_nm` 逆时针旋转 `angle_deg` 度（原地改写），返回被改写实体的原 uuid 列表。

    硬断言口径：绕原点旋转 90° 时 `(1, 0) -> (0, 1)`（整数纳米上精确成立）。
    """
    center = _as_point(center_nm, what="center_nm")
    angle = _as_angle(angle_deg)
    entities = _resolve_refs(sk, refs)
    for entity in entities:
        _kind(entity)
    matrix = _mat_about(_mat_rotate(angle), center)
    for entity in entities:
        _transform_entity(entity, matrix)
    return [_uuid_of(entity) for entity in entities]


def scale(sk: Sketch, refs: Sequence[str], *, center_nm: tuple[int, int], factor: float) -> list[str]:
    """以 `center_nm` 为基点按 `factor` 缩放（原地改写；`factor > 0`），返回被改写实体的原 uuid 列表。

    圆/弧的半径乘 `factor`；`factor` 必须为正（负因子是镜像语义，请用 `mirror`）。
    """
    center = _as_point(center_nm, what="center_nm")
    ratio = _as_factor(factor)
    entities = _resolve_refs(sk, refs)
    for entity in entities:
        _kind(entity)
    matrix = _mat_about(_mat_scale(ratio), center)
    for entity in entities:
        _transform_entity(entity, matrix)
    return [_uuid_of(entity) for entity in entities]


def mirror(sk: Sketch, refs: Sequence[str], *, p1_nm: tuple[int, int], p2_nm: tuple[int, int],
           keep_original: bool = True) -> list[str]:
    """关于过 `p1_nm`/`p2_nm` 的直线镜像；`keep_original=True` 保留原件。

    - `keep_original=True`（缺省）：新建镜像实体，返回**新 uuid**（原实体不动）；
    - `keep_original=False`：原地镜像，返回**原 uuid**。
    硬断言口径：镜像后端点互换（原 `(x1,y1)` 落到镜像位置，且 `x2` 对应原来的 `x1` 侧）。
    圆弧在 `det < 0` 时交换 `start_deg`/`end_deg`（扫掠方向翻转），圆心与半径正确变换。
    """
    axis_start = _as_point(p1_nm, what="p1_nm")
    axis_end = _as_point(p2_nm, what="p2_nm")
    entities = _resolve_refs(sk, refs)
    for entity in entities:
        _kind(entity)
    matrix = _mat_about(_mat_mirror(axis_start, axis_end), axis_start)
    if keep_original:
        created: list[str] = []
        for entity in entities:
            clone = _clone_entity(sk, entity)
            _transform_entity(clone, matrix)
            created.append(_uuid_of(clone))
        return created
    for entity in entities:
        _transform_entity(entity, matrix)
    return [_uuid_of(entity) for entity in entities]


# --------------------------------------------------------------------------------------
# offset（line / arc / circle / polyline）
# --------------------------------------------------------------------------------------


def _offset_line_endpoints(entity: Any, distance: float, side: int) -> dict[str, int]:
    """直线沿左法向（`side=+1`）偏移 `distance` 纳米后的两端点（整数纳米）。"""
    (x1, y1), (x2, y2) = _line_points(entity)
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0.0:
        raise _fail("degenerate_geometry", "cannot offset a zero-length line", uuid=_uuid_of(entity))
    nx, ny = -((y2 - y1) / length), (x2 - x1) / length  # 左法向 = 方向逆时针 90°
    ox, oy = side * distance * nx, side * distance * ny
    return {"x1": _round_nm(x1 + ox), "y1": _round_nm(y1 + oy),
            "x2": _round_nm(x2 + ox), "y2": _round_nm(y2 + oy)}


def _offset_polyline_points(entity: Any, distance: float, side: int) -> list[list[int]]:
    """开放折线偏移：逐段沿左法向平移，内顶点取相邻两偏移线段的交点（尖角/miter）。

    相邻段 180° 折返（尖点处无有限解）抛 `CadError(reason="degenerate_offset")`。
    """
    points = _clone_geom(entity)["points"]
    segments: list[tuple[tuple[int, int], tuple[int, int], float, float]] = []
    for index in range(len(points) - 1):
        ax, ay = int(points[index][0]), int(points[index][1])
        bx, by = int(points[index + 1][0]), int(points[index + 1][1])
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length == 0.0:
            raise _fail("degenerate_geometry", "polyline has a zero-length segment", uuid=_uuid_of(entity))
        segments.append(((ax, ay), (bx, by), side * distance * (-dy / length), side * distance * (dx / length)))

    def offset_point(point: tuple[int, int], offset: tuple[float, float]) -> tuple[float, float]:
        return (point[0] + offset[0], point[1] + offset[1])

    new_points: list[list[int]] = []
    first_start, first_offset = segments[0][0], (segments[0][2], segments[0][3])
    head = offset_point(first_start, first_offset)
    new_points.append([_round_nm(head[0]), _round_nm(head[1])])
    for index in range(len(segments) - 1):
        (_, corner, offset_a), (next_start, _, offset_b) = segments[index], segments[index + 1]
        pa = offset_point(corner, (offset_a[0], offset_a[1]))
        qa = offset_point(next_start, (offset_b[0], offset_b[1]))
        ra = (segments[index][1][0] - segments[index][0][0], segments[index][1][1] - segments[index][0][1])
        sa = (segments[index + 1][1][0] - segments[index + 1][0][0],
              segments[index + 1][1][1] - segments[index + 1][0][1])
        denominator = ra[0] * sa[1] - ra[1] * sa[0]
        if denominator == 0:  # 共线（同向）或 180° 折返
            if ra[0] * sa[0] + ra[1] * sa[1] > 0:  # 同向：直接用该顶点的偏移点
                vertex = offset_point(corner, (offset_a[0], offset_a[1]))
            else:
                raise _fail("degenerate_offset", "polyline reverses 180 degrees: no finite corner offset",
                            uuid=_uuid_of(entity), index=index)
        else:
            numerator = (qa[0] - pa[0]) * sa[1] - (qa[1] - pa[1]) * sa[0]
            t = numerator / denominator
            vertex = (pa[0] + t * ra[0], pa[1] + t * ra[1])
        new_points.append([_round_nm(vertex[0]), _round_nm(vertex[1])])
    last_start, last_end, last_dx, last_dy = segments[-1]
    tail = offset_point(last_end, (last_dx, last_dy))
    new_points.append([_round_nm(tail[0]), _round_nm(tail[1])])
    return new_points


def offset(sk: Sketch, ref: str, *, distance_nm: int, side: int = 1) -> list[str]:
    """把一条曲线按 `distance_nm` 偏移出一条新曲线，返回**新实体 uuid** 列表（`[uuid]`）。

    - `line`：沿左法向（`side=+1`）/右法向（`side=-1`）平移；新线与原线处处垂直距离**恰好** `distance_nm`
      （轴对齐时逐字段精确等于）；
    - `circle` / `arc`：半径 `r + side*distance_nm`，圆心与角度区间不变；内缩到 `r <= 0` 抛
      `CadError(reason="degenerate_offset")`；
    - `polyline`：按开放折线逐段偏移（内顶点求交成尖角），见模块 docstring 的 MVP 边界。
    """
    distance = _as_nm(distance_nm, what="distance_nm", positive=True)
    if side not in (1, -1) or isinstance(side, bool):
        raise _fail("invalid_argument", "side must be +1 (left normal) or -1 (right normal)", side=repr(side))
    entity = _resolve_one(sk, ref)
    kind = _kind(entity)
    if kind == "line":
        return [_append(sk, kind="line", layer=_get(entity, "layer", "0"),
                        geom=_offset_line_endpoints(entity, distance, side),
                        construction=_get(entity, "construction", False))]
    if kind in ("circle", "arc"):
        radius = _field_int(entity, "r")
        new_radius = radius + side * distance
        if new_radius <= 0:
            raise _fail("degenerate_offset", "inward offset collapses the radius",
                        uuid=_uuid_of(entity), radius_nm=radius, distance_nm=distance)
        geom: dict[str, Any] = {"cx": _field_int(entity, "cx"), "cy": _field_int(entity, "cy"), "r": new_radius}
        if kind == "arc":
            geom["start_deg"] = _field_num(entity, "start_deg")
            geom["end_deg"] = _field_num(entity, "end_deg")
        return [_append(sk, kind=kind, layer=_get(entity, "layer", "0"), geom=geom,
                        construction=_get(entity, "construction", False))]
    if kind == "polyline":
        return [_append(sk, kind="polyline", layer=_get(entity, "layer", "0"),
                        geom={"points": _offset_polyline_points(entity, distance, side)},
                        construction=_get(entity, "construction", False))]
    raise _fail("unsupported_geometry", f"offset does not support {kind}", kind=kind, uuid=_uuid_of(entity))


# --------------------------------------------------------------------------------------
# trim / extend（MVP：只做直线-直线）
# --------------------------------------------------------------------------------------


def trim(sk: Sketch, target_ref: str, *, cutter_refs: Sequence[str], keep: str = "start") -> list[str]:
    """用 `cutter_refs` 裁剪 `target_ref`：返回**被改写 target 的原 uuid** 列表（`[uuid]`）。

    **有意限定的 MVP 边界**：只做**直线-直线**；target/cutter 出现 `arc`/`circle`/`polyline` 一律抛
    `CadError(reason="unsupported_geometry")`（不近似裁剪、不静默返回原实体）。

    语义（签名里没有 pick point，故用 `keep` 显式指定保留侧）：
    - 求 target 所在**无限直线**与每条 cutter 所在无限直线的交点（参数方程 + 精确有理数）；
    - 只认**严格落在 target 内部**的交点 `0 < t < 1`；一个都没有（含平行、交点在线外）⇒
      `CadError(reason="no_intersection")`；
    - `keep="start"`（缺省）：删掉第一个交点之后那段 ⇒ `x2/y2` 落到**最小 t** 的交点上；
      `keep="end"`：`x1/y1` 落到**最大 t** 的交点上。

    硬断言口径：裁剪后该端点**恰好落在交点**（整数纳米上精确相等）。
    """
    if keep not in ("start", "end"):
        raise _fail("invalid_argument", "keep must be 'start' or 'end'", keep=repr(keep))
    target = _require_lines([_resolve_one(sk, target_ref)], op="trim")[0]
    cutters = _require_lines(_resolve_refs(sk, cutter_refs), op="trim")
    start, end = _line_points(target)
    hits: list[tuple[Fraction, tuple[int, int]]] = []
    for cutter in cutters:
        params = _line_params(start, end, *_line_points(cutter))
        if params is None:
            continue
        t = params[0]
        if 0 < t < 1:
            hits.append((t, _point_at(start, end, t)))
    if not hits:
        raise _fail("no_intersection", "trim found no intersection strictly inside the target",
                    target=_uuid_of(target))
    chosen = min(hits, key=lambda item: item[0]) if keep == "start" else max(hits, key=lambda item: item[0])
    point = chosen[1]
    which = "end" if keep == "start" else "start"
    other = start if which == "end" else end
    if point == other:
        raise _fail("degenerate_geometry", "trim would collapse the target to zero length", uuid=_uuid_of(target))
    _set_endpoint(target, which, point)
    return [_uuid_of(target)]


def extend(sk: Sketch, target_ref: str, *, boundary_refs: Sequence[str], end: str = "end") -> list[str]:
    """把 `target_ref` 延伸到最近的 `boundary_refs` 交点上：返回**被改写 target 的原 uuid** 列表。

    **有意限定的 MVP 边界**：只做**直线-直线**；非直线一律抛
    `CadError(reason="unsupported_geometry")`（不近似延伸、不静默返回原实体）。

    语义：沿 target 所在无限直线取参数 `t`；`end="end"`（缺省）只接受 `t > 1`（越过 `x2/y2`），
    `end="start"` 只接受 `t < 0`（越过 `x1/y1`），取**离原端点最近**的那个交点；
    边界与 target 平行或没有任何前向交点 ⇒ `CadError(reason="no_intersection")`。

    硬断言口径：延伸后端点在边界直线上，且另一端一个纳米都不动。
    """
    if end not in ("start", "end"):
        raise _fail("invalid_argument", "end must be 'start' or 'end'", end=repr(end))
    target = _require_lines([_resolve_one(sk, target_ref)], op="extend")[0]
    boundaries = _require_lines(_resolve_refs(sk, boundary_refs), op="extend")
    start, finish = _line_points(target)
    forward = end == "end"
    best: tuple[Fraction, tuple[int, int]] | None = None
    for boundary in boundaries:
        params = _line_params(start, finish, *_line_points(boundary))
        if params is None:
            continue
        t = params[0]
        if (forward and t > 1) or (not forward and t < 0):
            if best is None or abs(t - 1) < abs(best[0] - 1):
                best = (t, _point_at(start, finish, t))
    if best is None:
        raise _fail("no_intersection", "extend found no boundary ahead of the chosen end",
                    target=_uuid_of(target), end=end)
    point = best[1]
    anchor = start if forward else finish
    if point == anchor:
        raise _fail("degenerate_geometry", "extend produced a zero-length line", uuid=_uuid_of(target))
    _set_endpoint(target, end, point)
    return [_uuid_of(target)]


# --------------------------------------------------------------------------------------
# fillet / chamfer（MVP：只做直线-直线）
# --------------------------------------------------------------------------------------


def fillet(sk: Sketch, ref_a: str, ref_b: str, *, radius_nm: int) -> list[str]:
    """在两条直线间做半径 `radius_nm` 的圆角：返回**新建圆弧的 uuid** 列表（`[uuid]`）。

    **有意限定的 MVP 边界**：只做**直线-直线**；`ref_a`/`ref_b` 出现非 `line` 实体一律抛
    `CadError(reason="unsupported_geometry")`（圆弧-直线/圆弧-圆弧的相切构造留待后续版本）。

    语义：
    - 求两条无限直线的交点；平行/共线（叉积为 0）⇒ `CadError(reason="parallel_lines")`；
    - 每条线取**离交点更远**的那个端点方向作为"进入材料"的方向（共享端点成角的标准情形即由此得正确分支）；
    - 切线长 `d = r / tan(θ/2)`、圆心 = 交点 + 角平分线方向 × `r / sin(θ/2)`（θ = 两方向夹角）；
    - 两条线被裁剪/延伸到切点；新建圆弧的半径 = 落位后圆心到切点的距离（保证弧**真的过切点**），
      扫掠区间 = 劣弧 `180° - θ`，存储口径仍是"逆时针 start → end"。
    后置硬断言（本命令的验收尺子）：圆心到两条直线的距离 == 弧半径（相切），切点与两线新端点重合。
    """
    radius = _as_nm(radius_nm, what="radius_nm", positive=True)
    target_a = _require_lines([_resolve_one(sk, ref_a)], op="fillet")[0]
    target_b = _require_lines([_resolve_one(sk, ref_b)], op="fillet")[0]
    if _uuid_of(target_a) == _uuid_of(target_b):
        raise _fail("invalid_argument", "fillet needs two different lines", uuid=_uuid_of(target_a))

    intersection, unit_a, unit_b, near_a, near_b = _corner_setup(target_a, target_b, op="fillet")
    cross = unit_a[0] * unit_b[1] - unit_a[1] * unit_b[0]
    dot = unit_a[0] * unit_b[0] + unit_a[1] * unit_b[1]
    if abs(cross) <= _DIR_TOL:
        raise _fail("parallel_lines", "fillet needs two non-collinear lines",
                    a=_uuid_of(target_a), b=_uuid_of(target_b), cross=cross)
    theta = math.atan2(abs(cross), max(-1.0, min(1.0, dot)))  # (0, pi)
    tangent_length = radius / math.tan(theta / 2.0)
    bisector = (unit_a[0] + unit_b[0], unit_a[1] + unit_b[1])
    bisector_length = math.hypot(bisector[0], bisector[1])
    if bisector_length == 0.0:  # pragma: no cover - θ=π 已被平行判定拦住
        raise _fail("parallel_lines", "fillet bisector is undefined")
    center_distance = radius / math.sin(theta / 2.0)
    center = (_round_nm(intersection[0] + bisector[0] / bisector_length * center_distance),
              _round_nm(intersection[1] + bisector[1] / bisector_length * center_distance))
    point_a = (_round_nm(intersection[0] + unit_a[0] * tangent_length),
               _round_nm(intersection[1] + unit_a[1] * tangent_length))
    point_b = (_round_nm(intersection[0] + unit_b[0] * tangent_length),
               _round_nm(intersection[1] + unit_b[1] * tangent_length))

    new_a = dict(zip(("x1", "y1", "x2", "y2"), _flat_points(target_a)))
    new_b = dict(zip(("x1", "y1", "x2", "y2"), _flat_points(target_b)))
    new_a[f"x{1 if near_a == 'start' else 2}"] = point_a[0]
    new_a[f"y{1 if near_a == 'start' else 2}"] = point_a[1]
    new_b[f"x{1 if near_b == 'start' else 2}"] = point_b[0]
    new_b[f"y{1 if near_b == 'start' else 2}"] = point_b[1]
    for label, geom in (("a", new_a), ("b", new_b)):
        if (geom["x1"], geom["y1"]) == (geom["x2"], geom["y2"]):
            raise _fail("degenerate_fillet", f"fillet collapses line {label} to zero length", line=label)

    arc_radius = _round_nm(_distance(center, point_a))
    if arc_radius <= 0:
        raise _fail("degenerate_fillet", "fillet arc radius collapses to <= 0")
    angle_a = _round_angle(math.degrees(math.atan2(point_a[1] - center[1], point_a[0] - center[0])))
    angle_b = _round_angle(math.degrees(math.atan2(point_b[1] - center[1], point_b[0] - center[0])))
    expected_sweep = 180.0 - math.degrees(theta)
    sweep_ab = (angle_b - angle_a) % 360.0
    if abs(sweep_ab - expected_sweep) <= 1e-3:
        start_deg, end_deg = angle_a, angle_b
    else:
        start_deg, end_deg = angle_b, angle_a
    if start_deg == end_deg:  # pragma: no cover - 由 expected_sweep > 0 保证
        raise _fail("degenerate_fillet", "fillet arc has zero sweep")

    for key, value in new_a.items():
        _geom_set_num(target_a, key, int(value))
    for key, value in new_b.items():
        _geom_set_num(target_b, key, int(value))
    return [_append(sk, kind="arc", layer=_get(target_a, "layer", "0"),
                    geom={"cx": center[0], "cy": center[1], "r": arc_radius,
                          "start_deg": start_deg, "end_deg": end_deg},
                    construction=_get(target_a, "construction", False))]


def chamfer(sk: Sketch, ref_a: str, ref_b: str, *, distance_nm: int) -> list[str]:
    """在两条直线间做 `distance_nm` 的倒角：返回**新建倒角线段的 uuid** 列表（`[uuid]`）。

    **有意限定的 MVP 边界**：只做**直线-直线**；非直线抛 `CadError(reason="unsupported_geometry")`。

    语义：求交点（平行 ⇒ `CadError(reason="parallel_lines")`）；沿各自"进入材料"的方向量取
    `distance_nm` 得到两个倒角点；两条线被裁剪/延伸到倒角点；新建线段连接两点。
    倒角距离大于线段余长时会**延伸**该线（与 AutoCAD 的 CHAMFER 一致），但线段长度归零 ⇒ `degenerate_chamfer`。
    """
    distance = _as_nm(distance_nm, what="distance_nm", positive=True)
    target_a = _require_lines([_resolve_one(sk, ref_a)], op="chamfer")[0]
    target_b = _require_lines([_resolve_one(sk, ref_b)], op="chamfer")[0]
    if _uuid_of(target_a) == _uuid_of(target_b):
        raise _fail("invalid_argument", "chamfer needs two different lines", uuid=_uuid_of(target_a))

    intersection, unit_a, unit_b, near_a, near_b = _corner_setup(target_a, target_b, op="chamfer")
    point_a = (_round_nm(intersection[0] + unit_a[0] * distance),
               _round_nm(intersection[1] + unit_a[1] * distance))
    point_b = (_round_nm(intersection[0] + unit_b[0] * distance),
               _round_nm(intersection[1] + unit_b[1] * distance))
    if point_a == point_b:
        raise _fail("degenerate_chamfer", "chamfer endpoints coincide", a=_uuid_of(target_a), b=_uuid_of(target_b))

    new_a = dict(zip(("x1", "y1", "x2", "y2"), _flat_points(target_a)))
    new_b = dict(zip(("x1", "y1", "x2", "y2"), _flat_points(target_b)))
    new_a[f"x{1 if near_a == 'start' else 2}"] = point_a[0]
    new_a[f"y{1 if near_a == 'start' else 2}"] = point_a[1]
    new_b[f"x{1 if near_b == 'start' else 2}"] = point_b[0]
    new_b[f"y{1 if near_b == 'start' else 2}"] = point_b[1]
    for label, geom in (("a", new_a), ("b", new_b)):
        if (geom["x1"], geom["y1"]) == (geom["x2"], geom["y2"]):
            raise _fail("degenerate_chamfer", f"chamfer collapses line {label} to zero length", line=label)

    for key, value in new_a.items():
        _geom_set_num(target_a, key, int(value))
    for key, value in new_b.items():
        _geom_set_num(target_b, key, int(value))
    return [_append(sk, kind="line", layer=_get(target_a, "layer", "0"),
                    geom={"x1": point_a[0], "y1": point_a[1], "x2": point_b[0], "y2": point_b[1]},
                    construction=_get(target_a, "construction", False))]


# --------------------------------------------------------------------------------------
# array_linear
# --------------------------------------------------------------------------------------


def array_linear(sk: Sketch, refs: Sequence[str], *, rows: int, cols: int, dx_nm: int, dy_nm: int) -> list[str]:
    """矩形阵列：把 `refs` 选中的实体按 `rows × cols` 复制，返回**新实体 uuid** 列表。

    - 返回 `rows*cols - 1` 个新 uuid（跳过原位 `(i,j) = (0,0)` 的那一个副本）；
    - 遍历顺序 row-major（先行 `i`，再列 `j`），第 `(i,j)` 个副本偏移 `(j*dx_nm, i*dy_nm)`；
    - `rows == cols == 1` ⇒ **真为零**个新实体，返回 `[]`（不是"没算"）；`dx_nm == dy_nm == 0` 且要复制
      多于一个位置时抛 `CadError(reason="invalid_argument")`（叠在一处的副本不是阵列）；
    - `rows × cols` 超过单次操作对象数上限（`constraints.MAX_ITEMS_PER_OPERATION`）抛
      `CadError(reason="invalid_argument")` —— **先拒后循环**，不许拿无界计数去驱动 `range()`。
    """
    row_count = _as_count(rows, what="rows")
    col_count = _as_count(cols, what="cols")
    if not items_in_range(row_count * col_count):
        raise _fail("invalid_argument",
                    f"rows * cols would be {row_count * col_count} positions, above the per-operation "
                    f"limit {MAX_ITEMS_PER_OPERATION}",
                    rows=row_count, cols=col_count, max=MAX_ITEMS_PER_OPERATION)
    dx = _as_nm(dx_nm, what="dx_nm")
    dy = _as_nm(dy_nm, what="dy_nm")
    sources = _resolve_refs(sk, refs)
    for entity in sources:
        _kind(entity)
    if row_count * col_count > 1 and dx == 0 and dy == 0:
        raise _fail("invalid_argument", "dx_nm and dy_nm are both 0: the array would stack copies on the originals",
                    rows=row_count, cols=col_count)
    created: list[str] = []
    for i in range(row_count):
        for j in range(col_count):
            if i == 0 and j == 0:
                continue
            matrix = _mat_translate(j * dx, i * dy)
            for entity in sources:
                clone = _clone_entity(sk, entity)
                _transform_entity(clone, matrix)
                created.append(_uuid_of(clone))
    return created
