"""cadcore.sketch.constraints —— A10 自研二维约束求解：前向自动微分 + 阻尼最小二乘 + DoF 记账。

一句话用途
----------
把草图上的几何约束（重合 / 水平 / 垂直 / 平行 / 垂直相交 / 相切 / 距离 / 半径 / 同心 / 等长 / 共线 /
中点 / 定点）与驱动尺寸解成一组 **整数纳米**坐标，并如实记账：自由度
`dof = 变量数 − 独立方程数`、残差、迭代次数、是否收敛。

A10-E4 扩展（**增补**，既有 8 类关系的语义、引用形态与方程一字未改）
------------------------------------------------------------------
新增关系 `concentric` / `equal` / `collinear` / `midpoint` / `fix` 与删除入口
`delete_constraint(sk, constraint_uuid)`（删除后**立即重解**并返回新的 `SolveResult`）。
增补关系走的是**既有那条方程通路**（`validate_constraint` → `_EQUATION_BUILDERS` → 雅可比/秩/DoF），
所以它们必须也**真的进方程**：证据是几何断言（圆心相等 / 等长 / 共线残差 / 中点坐标 / 定点不动）
与记账断言（`equations` / `rank` / `dof` 同步变化）成对成立。
`fix` 的锚点 = **本次求解开始时的当前坐标**（文档在 `_write_back` 之前不被修改），即"钉住现在的位置"；
它**不**把变量从变量表里删掉 —— 删变量不进方程、不产生雅可比行，秩与 DoF 就成了假账。

为什么这样设计
--------------
1. **一行 GPL 代码都不抄**：候选求解器（SolveSpace / LibreCAD 等）全在许可红线段（R14 未评审即不采用），
   所以本文件从零实现：对偶数（前向模式自动微分）给出**解析**雅可比，再走 Levenberg–Marquardt 阻尼最小二乘。
   用解析导数而不是数值差分，是因为 DoF 要靠雅可比的**秩**来数 —— 差分噪声会让秩忽大忽小，dof 就成了假数。
2. **不收敛必须看得见**：`solve()` 绝不"解不出来也报成功"。不收敛时 `converged=False`，并**如实返回残差**
   （按落盘后的整数坐标复算）与 `status`；调用方据此报红，而不是把一张坏图当成品。
3. **DoF 是数出来的，不是猜的**：独立方程数 = 雅可比矩阵的数值秩（逐行均衡 + 部分主元高斯消元）。
   于是冗余约束（同一条线加两遍水平）不会把 dof 算成负数，而**矛盾**约束表现为
   `converged=False` + 残差 > 容差 —— 两者在 `redundant` / `converged` / `residual` 三个字段上可分辨。
4. **真源是整数纳米**：迭代在 float 里做，落盘前统一取整并做一轮"整数落位"（逐变量试 ±1 nm 取残差更小者），
   最后**按落盘后的整数坐标**复算残差 ⇒ 报出来的残差就是文档里那份几何的真实残差，不是浮点中间态的。
5. **与 A5 `docmodel` 结构解耦**：两个模块并行落盘，因此这里按**结构**接受草图（映射或对象，见 `_get`/`_set`），
   docmodel 落盘后本文件一行都不用改；反过来，写死 `docmodel.Sketch` 会让本模块在兄弟席缺席时连测都跑不了。
6. **文案走 key**：所有异常带 i18n key（`err.*`，语言包里已存在）与 ASCII 技术细节；
   `SketchError.localized()` 才把它翻成人读文案。源码里不留任何面向用户的中文（机检口径见 I18N-DESIGN §3）。

本模块冻结的口径（写下来防止下游靠猜）
--------------------------------------
- 长度一律**整数纳米**；圆弧角度是**浮点度**（角度不是长度，整数化会丢精度 —— 唯一的非 int 几何字段）。
- 变量集 = 各实体的长度字段：`line` 的 `x1,y1,x2,y2`；`circle`/`arc` 的 `cx,cy,r`。
  **圆弧角度不参与求解**（本期不做角度参数化，避开周期性与奇异），所以 `variables` 只数这些字段。
- `refs` 的第二项（子形状名）：
  `line` → `start` / `end`（点）、`""`（等价 `edge`，整条线）；
  `circle` → `center`（点）、`radius`（半径标量）、`""` / `edge`；
  `arc` → `center` / `start` / `end`（点；`start`/`end` 由 `cx,cy,r` + 冻结角度导出）、`radius`、`""` / `edge`。
- **锚定**（不锚定则刚体平移/转动自由度永远在，dof 到不了 0）：实体可选字段 `fixed: true`（持久锚定），
  或 `solve(fixed=[(uuid, sub), ...])`（临时锚定）。冻结粒度：点引用冻结其两个坐标，
  `radius` 冻结半径，`""` 冻结该实体全部变量。
- 角度类尺寸的 `value_nm` 口径 = **微度**（`1° = 1e6`）：L1 真源里不放裸浮点真值，故沿用 int 字段。
- 参考尺寸（`value_nm is None`）**不参与方程**，解算后把实测值写进 `measured_nm`（量不出来写 `None` = NA）。
"""

from __future__ import annotations

import math
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableMapping, Sequence

__all__ = [
    "ANGLE_FIELDS",
    "CONSTRAINT_KINDS",
    "DEFAULT_TOL_NM",
    "DIMENSION_KINDS",
    "LENGTH_FIELDS",
    "MAX_ABS_NM",
    "MAX_ITEMS_PER_OPERATION",
    "MAX_STEP_NM",
    "MIN_ABS_NM",
    "ORIGIN_POINT_NM",
    "ORIGIN_UUID",
    "SUB_SHAPES",
    "UDEG_PER_DEG",
    "GeometryError",
    "InvalidLength",
    "OutOfRange",
    "SketchError",
    "SolveResult",
    "UnknownReference",
    "UnsupportedKind",
    "add_constraint",
    "closed_profiles",
    "deg_to_udeg",
    "delete_constraint",
    "diagnose_conflicts",
    "dof_colors",
    "entity_kind",
    "find_entity",
    "is_origin_reference",
    "items_in_range",
    "measure_dimension",
    "new_uuid",
    "nm_in_range",
    "origin_point",
    "repair_conflicts",
    "select_region",
    "sketch_constraints",
    "sketch_dimensions",
    "sketch_entities",
    "sketch_state",
    "sketch_state_color",
    "solve",
    "udeg_to_deg",
    "validate_constraint",
    "validate_dimension",
]

# --------------------------------------------------------------------------------------
# 类型别名：草图/实体按**结构**接受（映射或对象），见模块 docstring 第 5 条
# --------------------------------------------------------------------------------------

#: A5 `docmodel.Sketch` 的占位别名。运行期不校验类型 —— 只认结构（`_get` / `_set`）。
Sketch = Any
#: 一个实体（`{uuid, kind, layer, construction, geom{...}}` 或等价对象）。
Entity = Any
#: 引用 = `(entity_uuid, sub_shape_name)`；**禁下标引用**（ARCHITECTURE D1 ②）。
Ref = tuple[str, str]

#: 契约冻结的约束种类 + A10-E4 **增补**的 5 类关系（同心 / 等长 / 共线 / 中点 / 定点）。
#: 增补只加不删：既有 8 类的引用形态与方程逐字未动（老文档、老测试照跑）。
CONSTRAINT_KINDS: tuple[str, ...] = (
    "coincident",
    "horizontal",
    "vertical",
    "parallel",
    "perpendicular",
    "tangent",
    "distance",
    "radius",
    # A10-E4 增补（同一条方程通路，见 `_EQUATION_BUILDERS`）：
    "concentric",
    "equal",
    "collinear",
    "midpoint",
    "fix",
)

#: 契约冻结的尺寸种类。`angular` 的 `value_nm` 口径是微度（见模块 docstring）。
DIMENSION_KINDS: tuple[str, ...] = ("linear", "radial", "diameter", "angular")

#: 参与求解的长度字段（同时是"必须是整数纳米"的判定面）。
LENGTH_FIELDS: dict[str, tuple[str, ...]] = {
    "line": ("x1", "y1", "x2", "y2"),
    "circle": ("cx", "cy", "r"),
    "arc": ("cx", "cy", "r"),
    # 本期不产出 polyline（A9 未暴露该命令），显式列出空元组而不是漏掉：
    # 遇到它时"0 个变量"是**真为零**，不是"没看见"。
    "polyline": (),
}

#: 角度字段：只在几何推导里用，不参与求解（本期口径）。
ANGLE_FIELDS: dict[str, tuple[str, ...]] = {"arc": ("start_deg", "end_deg")}

#: 子形状名白名单（`""` 与 `edge` 等价，均指整条曲线）。
SUB_SHAPES: dict[str, tuple[str, ...]] = {
    "line": ("", "edge", "start", "end"),
    "circle": ("", "edge", "center", "radius"),
    "arc": ("", "edge", "center", "start", "end", "radius"),
}

#: 草图**原点基准**（datum）：虚拟引用 uuid。原点不是 `entities` 里的条目（它没有可解变量、
#: 不该进 DoF 记账），但约束与尺寸可以把它当**点引用** —— 与 SolidWorks 的 origin 语义一致
#: （几何能约束到原点，尺寸能标注到原点）。引用写法：`("origin", "start")` / `("origin", "center")`。
ORIGIN_UUID: str = "origin"
#: 原点坐标（整数纳米，世界坐标零点）。
ORIGIN_POINT_NM: tuple[int, int] = (0, 0)
#: 允许把原点当点引用的约束种类；其余种类要曲线/半径/实体，原点不适用 ⇒ 显式拒绝而不是静默收下。
_ORIGIN_POINT_KINDS: tuple[str, ...] = ("coincident", "horizontal", "vertical", "collinear", "distance")

#: 角度整数口径：1 度 = 1e6 微度。
UDEG_PER_DEG: int = 1_000_000

#: 默认收敛容差（纳米）。真源分辨率就是 1 nm，故容差取 1 nm；
#: 目标值需要开方、整数上本来不可能有精确解的场合，调用方显式传更松的 `tol_nm`。
DEFAULT_TOL_NM: float = 1.0

#: 单次迭代单变量步长上限（1 m）：远端初值不许一步跳到离谱的位置去。
MAX_STEP_NM: float = 1.0e9

#: 长度/坐标可存的**绝对上限**（整数纳米，2^53 ≈ 9.007e15 nm ≈ 9.007e6 mm）。
#: 为什么必须有这一条：L1 真源是整数纳米，但下游（DXF 双精度坐标、图纸排布、PDF）全都要过
#: `float()` —— 超过 2^53 的整数在 float64 里不再逐位精确，再往上（如 10^309）连 `float()`
#: 本身都 `OverflowError`。缺这条校验时 `add_line` 会收下 10^309，等导出才以裸 `OverflowError`
#: 崩（实测缺陷 `extreme:huge_int_309digit`）。同口径先例：`ops/features/precision_ops__w28._MAX_ABS_NM`。
MAX_ABS_NM: int = 1 << 53
#: `MAX_ABS_NM` 对应的 i18n 占位符边界（`err.out_of_range` 的 `{min}` / `{max}`）。
MIN_ABS_NM: int = -MAX_ABS_NM


def nm_in_range(value: int) -> bool:
    """整数纳米是否在可存范围内（含端点）。**唯一真源**：三处 `_as_nm` 都调它。"""
    return MIN_ABS_NM <= int(value) <= MAX_ABS_NM


#: 单次操作的**对象数上限**（阵列位置数 / 多边形边数 / 阵列实例数，唯一真源）。
#: 为什么必须有这一条：`rows` / `cols` / `sides` / `count` 这类**用户计数**是无界整数，
#: 直接拿去驱动 `range()` + 逐个 clone ⇒ 服务在**持有工作区锁**的情况下无限循环（且文档被改到一半）：
#: 实测 `array_linear(rows=10**309)` 让 `POST /api/command` 20 s 不返回、其后**所有** API 全部超时
#: （锁被占死），`add_polygon(sides=10**12)` 同形。上限不是"性能调优"，是**不许开始**的边界：
#: 10 万个对象仍在可落盘范围，超过即结构化拒绝。
MAX_ITEMS_PER_OPERATION: int = 100_000


def items_in_range(value: int) -> bool:
    """单次操作的对象数是否在上限内（含端点）。**唯一真源**：计数型入口都调它。"""
    return 0 <= int(value) <= MAX_ITEMS_PER_OPERATION


#: 秩判定相对容差（逐行均衡后）：解析导数下 1e-9 足够区分"相关"与"独立"。
_RANK_TOL_REL: float = 1.0e-9

#: 退化逃逸预算（梯度全零 + 残差非零时，按确定性的 +1 nm 轮转轻推）。
_NUDGE_BUDGET: int = 4

#: 整数落位（逐变量试 ±1 nm）的轮数上限。
_SNAP_PASSES: int = 4

#: 一条约束产出几条方程（其余都是 1 条）：`coincident` / `concentric` / `midpoint` / `fix` 都是 x/y 两式。
_EQUATION_COUNT: dict[str, int] = {"coincident": 2, "concentric": 2, "midpoint": 2, "fix": 2}

_MISSING = object()


# --------------------------------------------------------------------------------------
# 错误族：本模块零包内依赖（与 A16 catalog 同一取舍），异常自带 i18n key
# --------------------------------------------------------------------------------------


class SketchError(Exception):
    """草图层的显式失败信号。

    单独定义而不依赖 `cadcore.errors`，理由与 A16 `catalog.CatalogError` 相同：
    **让本模块零包内依赖**，兄弟席缺席时也能单独跑起来（缺席即降级，绝不在 import 期硬失败）。

    构造口径：`key` = i18n key（`err.*`，语言包里已存在），`params` = 该 key 的占位符，
    `detail` = **ASCII** 技术细节（排障用，不是给用户看的文案）。
    `str(exc)` 返回 `"key: detail"`（永不含中文）；人读文案走 `localized()`。
    """

    def __init__(self, key: str, *, detail: str = "", **params: Any) -> None:
        self.key = str(key)
        self.params: dict[str, Any] = dict(params)
        self.detail = str(detail)
        super().__init__(f"{self.key}: {self.detail}" if self.detail else self.key)

    def localized(self) -> str:
        """按当前活动语言包取人读文案；语言层缺席时回落 ASCII 细节（绝不吞掉失败）。"""
        try:  # 语言层是兄弟模块：缺席不硬失败，降级可观测
            from ..i18n import catalog as _catalog

            text = _catalog.t(self.key, **self.params)
        except Exception:  # noqa: BLE001 -- 语言层坏掉不许把原始错误也弄丢
            text = self.detail or self.key
        return f"{text} ({self.detail})" if self.detail else text


class GeometryError(SketchError):
    """几何无效（零长线、半径非正、引用形状不匹配……）。"""


class UnknownReference(SketchError):
    """引用指向不存在的实体，或子形状名不属于该实体类型。"""


class InvalidLength(SketchError):
    """长度值非法（非整数纳米、非正、类型不对）。"""


class OutOfRange(SketchError):
    """数值越界（迭代次数、角度范围……）。"""


class UnsupportedKind(SketchError):
    """约束 / 尺寸 / 实体种类不在契约冻结集内。"""


def new_uuid() -> str:
    """新实体 / 约束 uuid（规范 UUID 字符串形态；L1 里引用一律用它，禁下标）。"""
    return str(_uuid.uuid4())


def deg_to_udeg(deg: float) -> int:
    """度 → 微度（角度类尺寸的整数口径）。"""
    return int(round(float(deg) * UDEG_PER_DEG))


def udeg_to_deg(udeg: int) -> float:
    """微度 → 度（只用于显示/导出，真源仍是 int）。"""
    return float(udeg) / UDEG_PER_DEG


# --------------------------------------------------------------------------------------
# 结构访问层：映射 / 对象双兼容（A5 docmodel 落盘前后都能跑）
# --------------------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = _MISSING) -> Any:
    """按结构取值：映射走 `[]`，对象走属性。取不到且没给默认值 ⇒ 抛错（不静默返 None）。"""
    if obj is None:
        raise GeometryError("err.geometry_invalid", detail=f"missing container while reading {name!r}")
    if isinstance(obj, Mapping):
        if name in obj:
            return obj[name]
    elif hasattr(obj, name):
        return getattr(obj, name)
    if default is not _MISSING:
        return default
    raise GeometryError("err.geometry_invalid", detail=f"container has no field {name!r}")


def _set(obj: Any, name: str, value: Any) -> None:
    """按结构写值：映射走 `[]`，对象走属性。写不进去 ⇒ 显式失败，不假装写成功。"""
    if isinstance(obj, MutableMapping):
        obj[name] = value
    elif isinstance(obj, Mapping):
        raise GeometryError("err.geometry_invalid", detail=f"sketch mapping is immutable, cannot set {name!r}")
    else:
        try:
            setattr(obj, name, value)
        except AttributeError as exc:
            raise GeometryError(
                "err.geometry_invalid", detail=f"cannot set {name!r} on {type(obj).__name__}: {exc}"
            ) from exc


def _field_list(obj: Any, name: str, *, create: bool = True) -> list[Any]:
    """取列表字段；缺席时按需创建并写回（`create=False` 时返回空列表，不写）。"""
    value = _get(obj, name, None)
    if value is None:
        if not create:
            return []
        value = []
        _set(obj, name, value)
    if not isinstance(value, list):
        raise GeometryError(
            "err.geometry_invalid", detail=f"sketch field {name!r} must be a list, got {type(value).__name__}"
        )
    return value


def sketch_entities(sk: Sketch) -> list[Entity]:
    """草图实体表（**唯一**读法：命令与求解器都走这里，避免两处内联副本）。"""
    return _field_list(sk, "entities")


def sketch_constraints(sk: Sketch) -> list[Any]:
    """草图约束表。"""
    return _field_list(sk, "constraints")


def sketch_dimensions(sk: Sketch) -> list[Any]:
    """草图尺寸表。"""
    return _field_list(sk, "dimensions")


def entity_uuid(entity: Entity) -> str:
    """实体 uuid（缺 uuid 的实体直接报错：L1 里引用一律靠 uuid）。"""
    value = _get(entity, "uuid", None)
    if not isinstance(value, str) or not value:
        raise GeometryError("err.geometry_invalid", detail="entity has no usable uuid")
    return value


def entity_kind(entity: Entity) -> str:
    """实体种类；未知种类抛 `UnsupportedKind`（不静默当 polyline 处理）。"""
    kind = _get(entity, "kind", None)
    if kind not in LENGTH_FIELDS:
        raise UnsupportedKind("err.not_implemented", detail=f"entity kind {kind!r}", feature=str(kind))
    return str(kind)


def find_entity(sk: Sketch, entity_uuid_: str) -> Entity:
    """按 uuid 找实体；找不到抛 `UnknownReference`（禁下标引用，也禁静默跳过）。"""
    for entity in sketch_entities(sk):
        if _get(entity, "uuid", None) == entity_uuid_:
            return entity
    raise UnknownReference("err.unknown_reference", reference=str(entity_uuid_))


def origin_point() -> tuple[int, int]:
    """草图**原点基准**的坐标（整数纳米）。约束/尺寸里用 `(ORIGIN_UUID, "start")` 引用它。

    原点不是实体条目（不进 DoF 变量表），但它是可被约束/标注的点 —— 与 SolidWorks 的 origin 一致。
    """
    return ORIGIN_POINT_NM


def is_origin_reference(ref: Any) -> bool:
    """该引用是不是原点基准；形态不合法返回 `False`（这是判定，不是校验，故不抛）。"""
    if isinstance(ref, (str, bytes)) or not isinstance(ref, Sequence) or len(ref) != 2:
        return False
    entity_ref, sub = ref
    if not _is_origin(entity_ref):
        return False
    return ("" if sub == "edge" else str(sub)) in ("", "start", "center")


# --------------------------------------------------------------------------------------
# 几何取值（真源 = 整数纳米；角度 = 浮点度）
# --------------------------------------------------------------------------------------


def _geom(entity: Entity) -> Any:
    geom = _get(entity, "geom", None)
    if geom is None:
        raise GeometryError("err.geometry_invalid", detail=f"entity {entity_uuid(entity)} has no geom")
    return geom


def geom_get(entity: Entity, field: str) -> float:
    """取几何字段（校验：字段属于该实体类型；长度必须是整数纳米，角度必须是有限实数）。"""
    kind = entity_kind(entity)
    if field not in LENGTH_FIELDS[kind] + ANGLE_FIELDS.get(kind, ()):
        raise GeometryError(
            "err.geometry_invalid", detail=f"{kind} {entity_uuid(entity)} has no geom field {field!r}"
        )
    value = _get(_geom(entity), field, _MISSING)
    if value is _MISSING:
        raise GeometryError(
            "err.geometry_invalid", detail=f"{kind} {entity_uuid(entity)} lacks geom field {field!r}"
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(
            "err.geometry_invalid",
            detail=f"geom.{field} of {entity_uuid(entity)} must be a number, got {type(value).__name__}",
        )
    if not math.isfinite(float(value)):
        raise GeometryError("err.geometry_invalid", detail=f"geom.{field} of {entity_uuid(entity)} is not finite")
    if field in LENGTH_FIELDS[kind] and not isinstance(value, int):
        raise GeometryError(
            "err.geometry_invalid",
            detail=f"geom.{field} of {entity_uuid(entity)} must be int nanometres (the truth is integer nm)",
        )
    return float(value)


def _geom_set(entity: Entity, field: str, value: Any) -> None:
    geom = _geom(entity)
    if isinstance(geom, MutableMapping):
        geom[field] = value
    else:
        try:
            setattr(geom, field, value)
        except AttributeError as exc:
            raise GeometryError(
                "err.geometry_invalid", detail=f"cannot set geom.{field} on {entity_uuid(entity)}: {exc}"
            ) from exc


def _normalize_sub(kind: str, sub: Any) -> str:
    """子形状名归一化（`edge` → `""`）并校验属于该实体类型。"""
    name = "" if sub == "edge" else str(sub)
    allowed = SUB_SHAPES.get(kind, ())
    if name not in allowed:
        raise UnknownReference(
            "err.unknown_reference",
            reference=f"({kind}, {sub!r})",
            detail=f"sub-shape {sub!r} is not valid for a {kind}; allowed={list(allowed)}",
        )
    return name


def _is_origin(entity_ref: Any) -> bool:
    """该 uuid 是不是原点基准的虚拟引用（原点**不在** `entities` 表里）。"""
    return entity_ref == ORIGIN_UUID


def _origin_sub(sub: Any) -> str:
    """原点引用的子形状名归一化：`""` / `edge` / `start` / `center` 都指同一个点 (0, 0)。"""
    name = "" if sub == "edge" else str(sub)
    if name not in ("", "start", "center"):
        raise UnknownReference(
            "err.unknown_reference",
            reference=f"({ORIGIN_UUID}, {sub!r})",
            detail=f"the origin datum is a point; sub-shape {sub!r} is not valid (use start/center/edge)",
        )
    return name


def _ref_kind(sk: Sketch, ref: Ref) -> str:
    """引用 → 实体种类；原点引用返回哨兵种类 `ORIGIN_UUID`（调用方按它分派）。"""
    if _is_origin(ref[0]):
        _origin_sub(ref[1])
        return ORIGIN_UUID
    return entity_kind(find_entity(sk, ref[0]))


def _ref_sub(kind: str, sub: Any) -> str:
    """`_normalize_sub` 的原点感知版本（原点不查 `SUB_SHAPES`）。"""
    return _origin_sub(sub) if kind == ORIGIN_UUID else _normalize_sub(kind, sub)


def _as_ref(ref: Any) -> Ref:
    """入参引用 → `(uuid, sub)`；形态不对即报错（禁下标引用）。"""
    if isinstance(ref, (str, bytes)) or not isinstance(ref, Sequence) or len(ref) != 2:
        raise GeometryError(
            "err.geometry_invalid",
            detail=f"reference must be a (entity_uuid, sub_shape_name) pair, got {ref!r}",
        )
    entity_ref, sub = ref[0], ref[1]
    if not isinstance(entity_ref, str) or not isinstance(sub, str):
        raise GeometryError("err.geometry_invalid", detail=f"reference parts must be strings, got {ref!r}")
    return (entity_ref, sub)


def _as_refs(refs: Any) -> list[Ref]:
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        raise GeometryError(
            "err.geometry_invalid", detail=f"refs must be a sequence of pairs, got {type(refs).__name__}"
        )
    return [_as_ref(ref) for ref in refs]


def _as_nm(value: Any, *, positive: bool = True, what: str = "value") -> int:
    """长度入参 → 整数纳米（bool 不算数；非整数浮点拒收并报 `InvalidLength`；越界报 `OutOfRange`）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidLength("err.invalid_length", text=repr(value), detail=f"{what} must be an integer nanometre value")
    if isinstance(value, float):
        if not math.isfinite(value) or float(value) != float(int(value)):
            raise InvalidLength("err.invalid_length", text=repr(value), detail=f"{what} must be a whole number of nm")
        value = int(value)
    if not nm_in_range(int(value)):
        raise OutOfRange("err.out_of_range", value=repr(value), min=MIN_ABS_NM, max=MAX_ABS_NM,
                         detail=f"{what} is outside the storable nm range (|v| <= 2**53)")
    if positive and value <= 0:
        raise InvalidLength("err.invalid_length", text=str(value), detail=f"{what} must be > 0 nm")
    return int(value)


def _point_fields(kind: str, sub: str) -> tuple[str, str]:
    """点引用 → 该点所用的两个长度字段（圆弧 start/end 由 cx,cy,r 导出，见 `_point`）。"""
    if kind == "line":
        return ("x1", "y1") if sub == "start" else ("x2", "y2")
    if kind in ("circle", "arc"):
        return ("cx", "cy")
    raise UnsupportedKind("err.not_implemented", detail=f"point of entity kind {kind!r}", feature=str(kind))


def _point(entity: Entity, sub: str) -> tuple[float, float]:
    """点引用 → 坐标（纳米，float）。圆弧 `start`/`end` 按冻结角度从圆心半径算出。

    圆弧端点是**导出量**：三角函数必然带 ~1e-9 nm 的浮点尾巴，直接比相等会让"本该接上"的两点
    差在皮米级而判不闭合。因此这里按**文档分辨率**（整数纳米）落位再比较 —— 真源本来就是整数纳米。
    求解器内部走的是另一条路（`_Context.point` 的对偶数，不取整），保留解析导数所需的平滑性。
    """
    kind = entity_kind(entity)
    sub = _normalize_sub(kind, sub)
    if kind == "arc" and sub in ("start", "end"):
        cx, cy, r = geom_get(entity, "cx"), geom_get(entity, "cy"), geom_get(entity, "r")
        rad = math.radians(geom_get(entity, "start_deg" if sub == "start" else "end_deg"))
        return (float(round(cx + r * math.cos(rad))), float(round(cy + r * math.sin(rad))))
    fields = _point_fields(kind, sub)
    return (geom_get(entity, fields[0]), geom_get(entity, fields[1]))


def _is_point_ref(kind: str, sub: str) -> bool:
    """点引用判定；原点基准恒为点引用（它的子形状名由 `_origin_sub` 把关）。"""
    if kind == ORIGIN_UUID:
        return True
    return _normalize_sub(kind, sub) in ("start", "end", "center")


def _curve_entity(sk: Sketch, ref: Ref) -> Entity:
    """取"整条曲线"引用（`""` / `edge`）；点了具体的点 ⇒ 报错（引用形状不匹配）。"""
    entity = find_entity(sk, ref[0])
    kind = entity_kind(entity)
    sub = _normalize_sub(kind, ref[1])
    if sub != "":
        raise GeometryError(
            "err.geometry_invalid", detail=f"expected a whole-curve reference, got ({kind}, {sub!r})"
        )
    return entity


def _radius_entity(sk: Sketch, ref: Ref) -> Entity:
    """取"带半径"的引用（circle/arc；`""` 或 `radius` 都算）。"""
    entity = find_entity(sk, ref[0])
    kind = entity_kind(entity)
    sub = _normalize_sub(kind, ref[1])
    if kind not in ("circle", "arc") or sub not in ("", "radius"):
        raise GeometryError(
            "err.geometry_invalid", detail=f"radius needs a circle/arc reference, got ({kind}, {sub!r})"
        )
    return entity


def _entity_fixed(entity: Entity) -> bool:
    """实体级持久锚定（增补的可选字段 `fixed: true`；缺席 = 未锚定）。"""
    return bool(_get(entity, "fixed", False))


# --------------------------------------------------------------------------------------
# 校验（写库前把关：坏约束不许进文档）
# --------------------------------------------------------------------------------------


def _need(condition: bool, kind: str, expectation: str, refs: Sequence[Ref]) -> None:
    if not condition:
        raise GeometryError(
            "err.geometry_invalid", detail=f"{kind} expects {expectation}, got refs={[list(r) for r in refs]}"
        )


def _require_no_value(value: Any, kind: str) -> None:
    if value is not None:
        raise GeometryError("err.geometry_invalid", detail=f"{kind} takes no value_nm (got {value!r})")


def _validate_distance_refs(sk: Sketch, refs: Sequence[Ref]) -> None:
    """距离类引用形态：1 条整线（线长）或 2 个点，或 点 + 整线（垂距）。"""
    kinds = [_ref_kind(sk, r) for r in refs]
    _need(len(refs) in (1, 2), "distance", "1 whole line or 2 references", refs)
    if len(refs) == 1:
        _need(kinds == ["line"] and _normalize_sub("line", refs[0][1]) == "", "distance",
              "a whole-line reference (its length)", refs)
        return
    points = [_is_point_ref(k, r[1]) for k, r in zip(kinds, refs)]
    curves = [_ref_sub(k, r[1]) == "" for k, r in zip(kinds, refs)]
    ok = all(points) or (points[0] and curves[1] and kinds[1] == "line") or (
        points[1] and curves[0] and kinds[0] == "line"
    )
    _need(ok, "distance", "2 points, or a point and a whole line", refs)


def validate_constraint(sk: Sketch, *, kind: str, refs: Sequence[Ref], value_nm: int | None = None) -> list[Ref]:
    """校验一条几何约束的引用与取值；返回归一化后的引用列表（失败抛错，绝不半接受）。"""
    if kind not in CONSTRAINT_KINDS:
        raise UnsupportedKind("err.not_implemented", detail=f"constraint kind {kind!r}", feature=str(kind))
    normalized = _as_refs(refs)
    kinds = []
    for entity_ref, sub in normalized:
        if _is_origin(entity_ref):
            _origin_sub(sub)
            kinds.append(ORIGIN_UUID)
            continue
        entity = find_entity(sk, entity_ref)
        entity_type = entity_kind(entity)
        _normalize_sub(entity_type, sub)  # 子形状名必须属于该实体类型
        kinds.append(entity_type)
    if ORIGIN_UUID in kinds and kind not in _ORIGIN_POINT_KINDS:
        raise UnknownReference(
            "err.unknown_reference", reference=ORIGIN_UUID,
            detail=f"the sketch origin is a point datum; {kind!r} needs curve/radius/entity references",
        )

    if kind == "coincident":
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 2, kind, "exactly 2 point references", normalized)
        _need(all(_is_point_ref(k, r[1]) for k, r in zip(kinds, normalized)), kind, "point references", normalized)
    elif kind in ("horizontal", "vertical"):
        _require_no_value(value_nm, kind)
        _need(len(normalized) in (1, 2), kind, "1 line or 2 point references", normalized)
        if len(normalized) == 1:
            _need(kinds == ["line"] and _normalize_sub("line", normalized[0][1]) == "", kind,
                  "a whole-line reference", normalized)
        else:
            _need(all(_is_point_ref(k, r[1]) for k, r in zip(kinds, normalized)), kind, "point references", normalized)
    elif kind in ("parallel", "perpendicular"):
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 2, kind, "exactly 2 whole-line references", normalized)
        _need(all(k == "line" and _normalize_sub(k, r[1]) == "" for k, r in zip(kinds, normalized)), kind,
              "whole-line references", normalized)
    elif kind == "tangent":
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 2, kind, "exactly 2 whole-curve references", normalized)
        _need(all(_normalize_sub(k, r[1]) == "" for k, r in zip(kinds, normalized)), kind,
              "whole-curve references", normalized)
        # 允许：线 × 带半径曲线（线-圆 / 线-弧）、两个带半径曲线（圆-圆 / 弧-弧 / 弧-圆）。
        # 两条线之间没有"相切"（那是共线），故至多允许一条线。
        _need(all(k in ("line", "circle", "arc") for k in kinds) and kinds.count("line") <= 1, kind,
              "a pair of curves with at most one line", normalized)
    elif kind == "distance":
        _validate_distance_refs(sk, normalized)
        _need(value_nm is not None, kind, "a value_nm", normalized)
        _as_nm(value_nm, positive=True, what="distance.value_nm")
    elif kind == "radius":
        _need(len(normalized) in (1, 2), kind,
              "1 circle/arc reference (plus an optional second one for equal radii)", normalized)
        for ref in normalized:
            _radius_entity(sk, ref)
        if len(normalized) == 1:
            _need(value_nm is not None, kind, "a value_nm", normalized)
            _as_nm(value_nm, positive=True, what="radius.value_nm")
        else:
            _require_no_value(value_nm, "radius (equal radii)")
    elif kind == "concentric":
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 2, kind, "exactly 2 circle/arc references", normalized)
        _need(all(k in ("circle", "arc") and _normalize_sub(k, r[1]) in ("", "center")
                  for k, r in zip(kinds, normalized)), kind, "circle/arc centre references", normalized)
    elif kind == "equal":
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 2, kind, "exactly 2 references", normalized)
        same_lines = all(k == "line" and _normalize_sub("line", r[1]) == "" for k, r in zip(kinds, normalized))
        same_radii = all(k in ("circle", "arc") and _normalize_sub(k, r[1]) in ("", "radius")
                         for k, r in zip(kinds, normalized))
        _need(same_lines or same_radii, kind,
              "2 whole lines (equal length) or 2 circle/arc references (equal radius)", normalized)
    elif kind == "collinear":
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 3, kind, "exactly 3 point references", normalized)
        _need(all(_is_point_ref(k, r[1]) for k, r in zip(kinds, normalized)), kind, "point references", normalized)
    elif kind == "midpoint":
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 2, kind, "1 point and 1 whole-line reference", normalized)
        points = [_is_point_ref(k, r[1]) for k, r in zip(kinds, normalized)]
        curves = [_normalize_sub(k, r[1]) == "" for k, r in zip(kinds, normalized)]
        _need((points[0] and curves[1] and kinds[1] == "line")
              or (points[1] and curves[0] and kinds[0] == "line"), kind, "a point and a whole line", normalized)
    elif kind == "fix":
        _require_no_value(value_nm, kind)
        _need(len(normalized) == 1, kind, "exactly 1 point reference", normalized)
        _need(_is_point_ref(kinds[0], normalized[0][1]), kind, "a point reference", normalized)
    return normalized


def validate_dimension(sk: Sketch, *, kind: str, refs: Sequence[Ref], value_nm: int | None = None) -> list[Ref]:
    """校验一条尺寸（驱动或参考）；`value_nm is None` = 参考尺寸（不参与方程）。"""
    if kind not in DIMENSION_KINDS:
        raise UnsupportedKind("err.not_implemented", detail=f"dimension kind {kind!r}", feature=str(kind))
    normalized = _as_refs(refs)
    if kind == "linear":
        _validate_distance_refs(sk, normalized)
        if value_nm is not None:
            _as_nm(value_nm, positive=True, what="linear.value_nm")
        return normalized
    if kind in ("radial", "diameter"):
        _need(len(normalized) == 1, kind, "exactly 1 circle/arc reference", normalized)
        _radius_entity(sk, normalized[0])
        if value_nm is not None:
            _as_nm(value_nm, positive=True, what=f"{kind}.value_nm")
        return normalized
    # angular：两条整线引用；value_nm 是**微度**，取值区间 (0, 360)
    _need(len(normalized) == 2, kind, "exactly 2 whole-line references", normalized)
    for entity_ref, sub in normalized:
        _need(entity_kind(find_entity(sk, entity_ref)) == "line" and _normalize_sub("line", sub) == "", kind,
              "whole-line references", normalized)
    if value_nm is not None:
        udeg = _as_nm(value_nm, positive=False, what="angular.value_nm (micro-degrees)")
        if not (0 < udeg < 360 * UDEG_PER_DEG):
            raise OutOfRange(
                "err.out_of_range", value=udeg, min=1, max=360 * UDEG_PER_DEG - 1,
                detail="angular dimension value_nm is micro-degrees and must be within (0, 360)",
            )
    return normalized


# --------------------------------------------------------------------------------------
# 前向模式自动微分（对偶数）：残差公式写一遍 ⇒ 值与解析雅可比一次拿到
# --------------------------------------------------------------------------------------


class _D:
    """对偶数：`v` = 值，`g` = 稀疏梯度 `{变量下标: 偏导}`。"""

    __slots__ = ("v", "g")

    def __init__(self, v: float, g: Mapping[int, float] | None = None) -> None:
        self.v = float(v)
        self.g: dict[int, float] = dict(g) if g else {}

    def __add__(self, other: Any) -> "_D":
        o = _d(other)
        return _D(self.v + o.v, _lin(self.g, 1.0, o.g, 1.0))

    __radd__ = __add__

    def __sub__(self, other: Any) -> "_D":
        o = _d(other)
        return _D(self.v - o.v, _lin(self.g, 1.0, o.g, -1.0))

    def __rsub__(self, other: Any) -> "_D":
        o = _d(other)
        return _D(o.v - self.v, _lin(o.g, 1.0, self.g, -1.0))

    def __mul__(self, other: Any) -> "_D":
        o = _d(other)
        return _D(self.v * o.v, _lin(self.g, o.v, o.g, self.v))

    __rmul__ = __mul__

    def __truediv__(self, other: Any) -> "_D":
        o = _d(other)
        if o.v == 0.0:
            raise GeometryError("err.geometry_invalid", detail="division by zero while building constraints")
        scale = 1.0 / o.v
        return _D(self.v * scale, _lin(self.g, scale, o.g, -self.v * scale * scale))

    def __neg__(self) -> "_D":
        return _D(-self.v, {i: -v for i, v in self.g.items()})

    def __repr__(self) -> str:  # pragma: no cover - 只为人读排障
        return f"_D(v={self.v!r}, g={self.g!r})"


def _d(value: Any) -> _D:
    return value if isinstance(value, _D) else _D(float(value))


def _lin(ga: Mapping[int, float], ka: float, gb: Mapping[int, float], kb: float) -> dict[int, float]:
    """`ka*ga + kb*gb`（稀疏梯度线性组合）。"""
    out: dict[int, float] = {}
    for index, value in ga.items():
        out[index] = ka * value
    for index, value in gb.items():
        out[index] = out.get(index, 0.0) + kb * value
    return out


def _scale(a: _D, k: float) -> _D:
    return _D(a.v * k, {i: v * k for i, v in a.g.items()})


def _sqrt(a: _D) -> _D:
    if a.v <= 0.0:
        # 值域边界：导数取 0（不伪造方向）；求解器对"零梯度但残差非零"有确定性的退化逃逸。
        return _D(0.0, {})
    root = math.sqrt(a.v)
    k = 0.5 / root
    return _D(root, {i: v * k for i, v in a.g.items()})


def _hypot(x: _D, y: _D) -> _D:
    """`sqrt(x² + y²)`（长度；值 ≤ 0 时退化为 0，见 `_sqrt`）。"""
    return _sqrt(x * x + y * y)


def _dot(a: tuple[_D, _D], b: tuple[_D, _D]) -> _D:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a: tuple[_D, _D], b: tuple[_D, _D]) -> _D:
    return a[0] * b[1] - a[1] * b[0]


def _atan2(y: _D, x: _D) -> _D:
    denom = x.v * x.v + y.v * y.v
    if denom == 0.0:
        raise GeometryError("err.geometry_invalid", detail="angle of a zero-length direction is undefined")
    return _D(math.atan2(y.v, x.v), _lin(x.g, -y.v / denom, y.g, x.v / denom))


def _wrap_pi(a: _D) -> _D:
    """把角度差折到 (-π, π]（折返点不分叉 ⇒ 导数原样带过去）。"""
    return _D(math.remainder(a.v, 2.0 * math.pi), a.g)


def _max_len(a: tuple[_D, _D], b: tuple[_D, _D]) -> _D:
    """`max(|a|, |b|, 1)`：给平行/垂直残差做尺度归一，使残差量纲回到纳米。"""
    la, lb = _hypot(a[0], a[1]), _hypot(b[0], b[1])
    longest = la if la.v >= lb.v else lb
    return longest if longest.v >= 1.0 else _D(1.0)


def _signed_abs(a: _D) -> _D:
    """`|a|`，符号在**当前点**冻结（分段光滑；避免 `abs` 在 0 处不可导引入抖动）。"""
    return _scale(a, 1.0 if a.v >= 0.0 else -1.0)


# --------------------------------------------------------------------------------------
# 求解上下文：把"实体 + 变量下标 + 当前值"包成取点/取方向的入口
# --------------------------------------------------------------------------------------


class _Context:
    """一次求解的取值上下文（变量走 `_D`，锚定的量走常量）。"""

    def __init__(self, sk: Sketch, varindex: Mapping[tuple[str, str], int]) -> None:
        self._sk = sk
        self._varindex = varindex
        self._cache: dict[str, Entity] = {}

    def entity(self, entity_uuid_: str) -> Entity:
        if entity_uuid_ not in self._cache:
            self._cache[entity_uuid_] = find_entity(self._sk, entity_uuid_)
        return self._cache[entity_uuid_]

    def ref_kind(self, ref: Ref) -> str:
        """引用 → 实体种类；原点基准返回哨兵种类（与模块级 `_ref_kind` 同一口径）。"""
        return ORIGIN_UUID if _is_origin(ref[0]) else entity_kind(self.entity(ref[0]))

    def _field(self, entity: Entity, field: str, x: Sequence[float]) -> _D:
        index = self._varindex.get((entity_uuid(entity), field))
        if index is None:
            return _D(geom_get(entity, field))
        return _D(x[index], {index: 1.0})

    def point(self, ref: Ref, x: Sequence[float]) -> tuple[_D, _D]:
        """点引用 → 两个 `_D` 坐标（圆弧端点按冻结角度导出；原点基准是常量 (0,0)）。"""
        if _is_origin(ref[0]):
            _origin_sub(ref[1])
            return (_D(float(ORIGIN_POINT_NM[0])), _D(float(ORIGIN_POINT_NM[1])))
        entity = self.entity(ref[0])
        kind = entity_kind(entity)
        sub = _normalize_sub(kind, ref[1])
        if not _is_point_ref(kind, sub):
            raise GeometryError("err.geometry_invalid", detail=f"expected a point reference, got ({kind}, {sub!r})")
        if kind == "arc" and sub in ("start", "end"):
            cx, cy = self._field(entity, "cx", x), self._field(entity, "cy", x)
            r = self._field(entity, "r", x)
            rad = math.radians(geom_get(entity, "start_deg" if sub == "start" else "end_deg"))
            return (cx + r * math.cos(rad), cy + r * math.sin(rad))
        fields = _point_fields(kind, sub)
        return (self._field(entity, fields[0], x), self._field(entity, fields[1], x))

    def direction(self, ref: Ref, x: Sequence[float]) -> tuple[_D, _D]:
        """整线引用 → 方向向量 `p2 - p1`。"""
        entity = _curve_entity(self._sk, ref)
        if entity_kind(entity) != "line":
            raise GeometryError("err.geometry_invalid", detail="a direction needs a whole-line reference")
        return (
            self._field(entity, "x2", x) - self._field(entity, "x1", x),
            self._field(entity, "y2", x) - self._field(entity, "y1", x),
        )

    def radius(self, ref: Ref, x: Sequence[float]) -> _D:
        """circle/arc 引用 → 半径。"""
        return self._field(_radius_entity(self._sk, ref), "r", x)


# --------------------------------------------------------------------------------------
# 残差方程（一条方程 = 一个 `_D`；单位一律折算到纳米）
# --------------------------------------------------------------------------------------


def _eq_coincident(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    p, q = ctx.point(refs[0], x), ctx.point(refs[1], x)
    return [p[0] - q[0], p[1] - q[1]]


def _eq_horizontal(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    if len(refs) == 1:
        return [ctx.direction(refs[0], x)[1]]
    p, q = ctx.point(refs[0], x), ctx.point(refs[1], x)
    return [p[1] - q[1]]


def _eq_vertical(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    if len(refs) == 1:
        return [ctx.direction(refs[0], x)[0]]
    p, q = ctx.point(refs[0], x), ctx.point(refs[1], x)
    return [p[0] - q[0]]


def _eq_parallel(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    a, b = ctx.direction(refs[0], x), ctx.direction(refs[1], x)
    return [_cross(a, b) / _max_len(a, b)]


def _eq_perpendicular(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    a, b = ctx.direction(refs[0], x), ctx.direction(refs[1], x)
    return [_dot(a, b) / _max_len(a, b)]


def _eq_tangent(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    kinds = [entity_kind(ctx.entity(r[0])) for r in refs]
    if "line" in kinds:
        line_index = kinds.index("line")
        line_ref, other = refs[line_index], refs[1 - line_index]
        p1 = ctx.point((line_ref[0], "start"), x)
        direction = ctx.direction(line_ref, x)
        center = ctx.point((other[0], "center"), x)
        signed = _cross(direction, (center[0] - p1[0], center[1] - p1[1])) / _hypot(direction[0], direction[1])
        return [signed - _scale(ctx.radius(other, x), 1.0 if signed.v >= 0.0 else -1.0)]
    c1, c2 = ctx.point((refs[0][0], "center"), x), ctx.point((refs[1][0], "center"), x)
    r1, r2 = ctx.radius(refs[0], x), ctx.radius(refs[1], x)
    distance = _hypot(c1[0] - c2[0], c1[1] - c2[1])
    outer = r1 + r2
    inner = _signed_abs(r1 - r2)
    # 内切/外切按**当前构形**就近取分支（分支在迭代中可能切换，属分段光滑的固有性质）
    target = outer if abs(distance.v - outer.v) <= abs(distance.v - inner.v) else inner
    return [distance - target]


def _distance_residual(ctx: _Context, x: Sequence[float], refs: Sequence[Ref]) -> _D:
    """距离残差（点-点 / 线长 / 点-线垂距），单位纳米。"""
    if len(refs) == 1:
        direction = ctx.direction(refs[0], x)
        return _hypot(direction[0], direction[1])
    first, second = refs
    a_is_point = _is_point_ref(ctx.ref_kind(first), first[1])
    if a_is_point and _is_point_ref(ctx.ref_kind(second), second[1]):
        p, q = ctx.point(first, x), ctx.point(second, x)
        return _hypot(p[0] - q[0], p[1] - q[1])
    point_ref, line_ref = (first, second) if a_is_point else (second, first)
    p = ctx.point(point_ref, x)
    p1 = ctx.point((line_ref[0], "start"), x)
    direction = ctx.direction(line_ref, x)
    return _signed_abs(_cross(direction, (p[0] - p1[0], p[1] - p1[1])) / _hypot(direction[0], direction[1]))


def _eq_distance(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    if value is None:  # pragma: no cover - 校验层已拦，双保险
        raise InvalidLength("err.invalid_length", text="null", detail="distance requires value_nm")
    return [_distance_residual(ctx, x, refs) - float(value)]


def _eq_radius(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    if len(refs) == 2:
        return [ctx.radius(refs[0], x) - ctx.radius(refs[1], x)]
    if value is None:  # pragma: no cover - 校验层已拦，双保险
        raise InvalidLength("err.invalid_length", text="null", detail="radius requires value_nm")
    return [ctx.radius(refs[0], x) - float(value)]


def _eq_diameter(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    if value is None:  # pragma: no cover - 校验层已拦，双保险
        raise InvalidLength("err.invalid_length", text="null", detail="diameter requires value_nm")
    return [_scale(ctx.radius(refs[0], x), 2.0) - float(value)]


def _eq_angular(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    if value is None:  # pragma: no cover - 校验层已拦，双保险
        raise InvalidLength("err.invalid_length", text="null", detail="angular requires value_nm (micro-degrees)")
    a, b = ctx.direction(refs[0], x), ctx.direction(refs[1], x)
    angle = _atan2(_cross(a, b), _dot(a, b))
    reference_length = _max_len(a, b)
    return [_scale(_wrap_pi(angle - math.radians(udeg_to_deg(value))), reference_length.v)]


def _center_ref(entity: Entity, ref: Ref) -> Ref:
    """`""` / `edge` 引用 → 显式 `center`（`_Context.point` 只认点形状名）。"""
    sub = _normalize_sub(entity_kind(entity), ref[1])
    return (ref[0], sub if sub else "center")


def _eq_concentric(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    """同心：两圆心重合（x/y 两式）。circle 与 arc 都按 `center` 取。"""
    a = ctx.point(_center_ref(ctx.entity(refs[0][0]), refs[0]), x)
    b = ctx.point(_center_ref(ctx.entity(refs[1][0]), refs[1]), x)
    return [a[0] - b[0], a[1] - b[1]]


def _eq_equal(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    """等长 / 等半径：两条整线比长度，两个 circle/arc 比半径（按第一条引用的实体类型分派）。"""
    if entity_kind(ctx.entity(refs[0][0])) == "line":
        a, b = ctx.direction(refs[0], x), ctx.direction(refs[1], x)
        return [_hypot(a[0], a[1]) - _hypot(b[0], b[1])]
    return [ctx.radius(refs[0], x) - ctx.radius(refs[1], x)]


def _eq_collinear(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    """共线：三点叉积 / 参考长度（量纲回到纳米 ⇒ 容差口径与其它关系一致）。"""
    p0, p1, p2 = (ctx.point(ref, x) for ref in refs)
    a = (p1[0] - p0[0], p1[1] - p0[1])
    b = (p2[0] - p0[0], p2[1] - p0[1])
    return [_cross(a, b) / _max_len(a, b)]


def _eq_midpoint(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    """中点：点 = 线段两端的中点（x/y 两式；点引用与整线引用**任意顺序**）。"""
    kinds = [ctx.ref_kind(ref) for ref in refs]
    point_index = 0 if _is_point_ref(kinds[0], refs[0][1]) else 1
    point_ref, line_ref = refs[point_index], refs[1 - point_index]
    p = ctx.point(point_ref, x)
    p1 = ctx.point((line_ref[0], "start"), x)
    p2 = ctx.point((line_ref[0], "end"), x)
    return [p[0] - _scale(p1[0] + p2[0], 0.5), p[1] - _scale(p1[1] + p2[1], 0.5)]


def _eq_fix(ctx: _Context, x: Sequence[float], refs: Sequence[Ref], value: int | None) -> list[_D]:
    """定点：把点钉在**本次求解开始时的当前坐标**（x/y 两式）。

    锚点直接读文档几何：`solve()` 只在迭代结束、整数落位之后才 `_write_back`，所以迭代期读到的
    就是求解前的原值（不是随 `x` 变的量）。刻意**不**走"把变量从变量表里删掉"那条路 ——
    删变量不产生方程/雅可比行，`equations`/`rank`/`dof` 三项就查不出它到底有没有生效。
    """
    anchor = _point(ctx.entity(refs[0][0]), refs[0][1])
    p = ctx.point(refs[0], x)
    return [p[0] - anchor[0], p[1] - anchor[1]]


#: 方程种类 → 残差构造器（尺寸种类在 `_entry_from` 里映射到同一批构造器）。
_EQUATION_BUILDERS: dict[str, Callable[[_Context, Sequence[float], Sequence[Ref], int | None], list[_D]]] = {
    "coincident": _eq_coincident,
    "horizontal": _eq_horizontal,
    "vertical": _eq_vertical,
    "parallel": _eq_parallel,
    "perpendicular": _eq_perpendicular,
    "tangent": _eq_tangent,
    "distance": _eq_distance,
    "radius": _eq_radius,
    "diameter": _eq_diameter,
    "angular": _eq_angular,
    # A10-E4 增补：
    "concentric": _eq_concentric,
    "equal": _eq_equal,
    "collinear": _eq_collinear,
    "midpoint": _eq_midpoint,
    "fix": _eq_fix,
}

#: 尺寸种类 → 等价的方程种类。
_DIMENSION_AS_CONSTRAINT: dict[str, str] = {
    "linear": "distance",
    "radial": "radius",
    "diameter": "diameter",
    "angular": "angular",
}


# --------------------------------------------------------------------------------------
# 条目读取（约束 + 驱动尺寸）；参考尺寸不进方程
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    """一条参与求解的方程来源（约束或驱动尺寸）。"""

    source: str  # "constraint" | "dimension"
    uuid: str
    kind: str  # 约束种类（尺寸已映射到等价约束种类）
    refs: tuple[Ref, ...]
    value_nm: int | None

    @property
    def label(self) -> str:
        return f"{self.source}:{self.uuid}:{self.kind}"


def _entry_from(item: Any, *, source: str) -> _Entry:
    """文档条目 → `_Entry`；种类非法即报错（不静默跳过坏约束）。"""
    kind = _get(item, "kind", None)
    refs = tuple(_as_refs(_get(item, "refs", None)))
    value = _get(item, "value_nm", None)
    item_uuid = _get(item, "uuid", None)
    if not isinstance(item_uuid, str) or not item_uuid:
        item_uuid = f"<{source}-without-uuid>"
    if source == "dimension":
        if kind not in DIMENSION_KINDS:
            raise UnsupportedKind("err.not_implemented", detail=f"dimension kind {kind!r}", feature=str(kind))
        mapped = _DIMENSION_AS_CONSTRAINT[kind]
    else:
        if kind not in CONSTRAINT_KINDS:
            raise UnsupportedKind("err.not_implemented", detail=f"constraint kind {kind!r}", feature=str(kind))
        mapped = kind
    return _Entry(source=source, uuid=item_uuid, kind=mapped, refs=refs, value_nm=value)


def _solve_entries(sk: Sketch) -> tuple[list[_Entry], list[Any]]:
    """参与求解的条目 + **参考尺寸**条目（后者只测量、不进方程）。

    尺寸在进方程前**重新校验**一遍（不只依赖 `add_dimension` 当时那一次）：
    手改过的 L1 文档、旧 schema 迁移来的文档都可能带坏引用，那属于结构性错误，必须当场报红。
    """
    driving: list[_Entry] = []
    reference: list[Any] = []
    for item in sketch_constraints(sk):
        driving.append(_entry_from(item, source="constraint"))
    for item in sketch_dimensions(sk):
        direction = _get(item, "value_nm", None)
        validate_dimension(
            sk,
            kind=_get(item, "kind", None),
            refs=_as_refs(_get(item, "refs", None)),
            value_nm=direction,
        )
        if direction is None:
            reference.append(item)
        else:
            driving.append(_entry_from(item, source="dimension"))
    return driving, reference


# --------------------------------------------------------------------------------------
# 变量抽取与锚定
# --------------------------------------------------------------------------------------


def _frozen_fields(sk: Sketch, fixed: Sequence[Ref]) -> set[tuple[str, str]]:
    """被锚定的 `(entity_uuid, field)` 集合：实体级 `fixed: true` + 调用方临时锚定。"""
    frozen: set[tuple[str, str]] = set()
    for entity in sketch_entities(sk):
        if _entity_fixed(entity):
            frozen.update((entity_uuid(entity), field) for field in LENGTH_FIELDS[entity_kind(entity)])
    for ref in _as_refs(fixed):
        entity = find_entity(sk, ref[0])
        kind = entity_kind(entity)
        sub = _normalize_sub(kind, ref[1])
        if sub in ("start", "end") and kind == "line":
            fields = _point_fields(kind, sub)
        elif sub in ("start", "end"):
            # 圆弧端点由 (cx, cy, r) 导出 ⇒ 冻结该点等价于冻结这三个变量
            fields = LENGTH_FIELDS["arc"]
        elif sub == "center":
            fields = _point_fields(kind, "center")
        elif sub == "radius":
            fields = ("r",)
        else:  # "" / "edge"：冻结整条曲线
            fields = LENGTH_FIELDS[kind]
        frozen.update((entity_uuid(entity), field) for field in fields)
    return frozen


def _build_variables(sk: Sketch, frozen: set[tuple[str, str]]) -> tuple[dict[tuple[str, str], int], list[float]]:
    """变量表：`(uuid, field) → 下标` 与初值（保持实体/字段顺序 ⇒ 结果可复现）。"""
    index: dict[tuple[str, str], int] = {}
    values: list[float] = []
    for entity in sketch_entities(sk):
        for field in LENGTH_FIELDS[entity_kind(entity)]:
            key = (entity_uuid(entity), field)
            if key in frozen or key in index:
                continue
            index[key] = len(values)
            values.append(geom_get(entity, field))
    return index, values


def _write_back(sk: Sketch, x: Sequence[float], varindex: Mapping[tuple[str, str], int]) -> None:
    """把解（整数纳米）写回实体几何；长度字段一律 int。"""
    for entity in sketch_entities(sk):
        for field in LENGTH_FIELDS[entity_kind(entity)]:
            index = varindex.get((entity_uuid(entity), field))
            if index is not None:
                _geom_set(entity, field, int(round(x[index])))


# --------------------------------------------------------------------------------------
# 线性代数：阻尼最小二乘 + 数值秩
# --------------------------------------------------------------------------------------


def _evaluate(
    builders: Sequence[Callable[[Sequence[float]], list[_D]]], x: Sequence[float]
) -> tuple[list[float], list[dict[int, float]]]:
    """一次求值同时拿到残差向量与解析雅可比（每行是稀疏梯度）。"""
    residuals: list[float] = []
    jacobian: list[dict[int, float]] = []
    for builder in builders:
        for dual in builder(x):
            residuals.append(dual.v)
            jacobian.append(dual.g)
    return residuals, jacobian


def _max_abs(values: Sequence[float]) -> float:
    return max((abs(v) for v in values), default=0.0)


def _lm_step(
    jacobian: Sequence[Mapping[int, float]], residuals: Sequence[float], lam: float, n: int
) -> list[float] | None:
    """解 `(JᵀJ + λI) δ = -Jᵀr`（牛顿法 + LM 阻尼；JᵀJ 奇异时阻尼项保证可解）。"""
    normal = [[0.0] * n for _ in range(n)]
    rhs = [0.0] * n
    for row, residual in zip(jacobian, residuals):
        items = list(row.items())
        for i, vi in items:
            rhs[i] -= vi * residual
            target = normal[i]
            for j, vj in items:
                target[j] += vi * vj
    for i in range(n):
        normal[i][i] += lam
    return _solve_linear(normal, rhs)


def _solve_linear(matrix: Sequence[Sequence[float]], rhs: Sequence[float]) -> list[float] | None:
    """部分主元高斯消元；奇异（主元全为 0）返回 `None`（由调用方加大阻尼重试）。"""
    n = len(rhs)
    if n == 0:
        return []
    a = [list(row) + [float(rhs[i])] for i, row in enumerate(matrix)]
    scale = max((abs(v) for row in a for v in row), default=0.0)
    if scale == 0.0:
        return None
    tol = 1e-14 * scale
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) <= tol:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        pv = a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] / pv
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                a[r][c] -= factor * a[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = a[r][n] - sum(a[r][c] * out[c] for c in range(r + 1, n))
        out[r] = acc / a[r][r]
    return out


def _matrix_rank(jacobian: Sequence[Mapping[int, float]], n: int, *, tol_rel: float = _RANK_TOL_REL) -> int:
    """雅可比的数值秩（**逐行均衡**后部分主元消元）——`dof` 的分子就是它，必须可复现、可解释。

    逐行均衡很重要：角度类残差天然带一个参考长度因子（量级 ~1e7 nm），不做均衡会让
    量级为 1 的行（水平/垂直/距离）在全局容差下被误判成"相关"，dof 于是虚高。
    """
    if n <= 0 or not jacobian:
        return 0
    matrix: list[list[float]] = []
    for row in jacobian:
        dense = [0.0] * n
        for column, value in row.items():
            if 0 <= column < n:
                dense[column] = float(value)
        longest = max((abs(v) for v in dense), default=0.0)
        if longest > 0.0:
            dense = [v / longest for v in dense]
        matrix.append(dense)
    if not any(any(v != 0.0 for v in row) for row in matrix):
        return 0
    tol = tol_rel * max(len(matrix), n)
    rank = 0
    for col in range(n):
        if rank >= len(matrix):
            break
        pivot = max(range(rank, len(matrix)), key=lambda r: abs(matrix[r][col]))
        if abs(matrix[pivot][col]) <= tol:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        pv = matrix[rank][col]
        for r in range(rank + 1, len(matrix)):
            factor = matrix[r][col] / pv
            if factor == 0.0:
                continue
            for c in range(col, n):
                matrix[r][c] -= factor * matrix[rank][c]
        rank += 1
    return rank


def _clamp_step(step: Sequence[float]) -> list[float]:
    """单次迭代步长上限（避免远端初值一步跳到离谱位形）。"""
    longest = _max_abs(step)
    if longest <= MAX_STEP_NM or longest == 0.0:
        return list(step)
    factor = MAX_STEP_NM / longest
    return [value * factor for value in step]


def _snap_to_integers(
    x: list[float], builders: Sequence[Callable[[Sequence[float]], list[_D]]], best: float
) -> tuple[list[float], float]:
    """整数落位：逐个自由变量试 ±1 nm，只接受**严格**变好的改动（把量化误差压到最小）。"""
    for _ in range(_SNAP_PASSES):
        improved = False
        for index in range(len(x)):
            base = x[index]
            for delta in (1.0, -1.0):
                x[index] = base + delta
                if _max_abs(_evaluate(builders, x)[0]) < best - 1e-12:
                    best = _max_abs(_evaluate(builders, x)[0])
                    base = x[index]
                    improved = True
                    break
                x[index] = base
        if not improved:
            break
    return x, best


# --------------------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SolveResult:
    """一次求解的事实记录（**三态口径**：`0` 真为零 / `-1` 未采 / `NA` 不适用）。

    - `dof`：自由度 = 变量数 − 独立方程数（秩）。变量集只含长度字段，见模块 docstring。
      草图里一个实体都没有 ⇒ `variables=0, rank=0, dof=0`（**真为零**，不是未采）。
    - `residual`：**落盘后整数坐标**下的最大绝对残差（纳米）；没有方程时恒为 `0.0`（真为零）。
    - `redundant`：冗余方程数 = 方程数 − 秩。> 0 即"过约束"；若同时 `converged=False` 就是**矛盾**。
    - `status`：`ok` / `empty` / `max_iter` / `stalled`；只有 `ok` 才算解出来。
    """

    converged: bool
    dof: int
    residual: float
    iterations: int
    status: str
    variables: int
    equations: int
    rank: int
    redundant: int
    residuals: tuple[float, ...] = ()
    labels: tuple[str, ...] = ()

    @property
    def over_constrained(self) -> bool:
        """是否存在冗余方程（过约束；矛盾与否另看 `converged`）。"""
        return self.redundant > 0

    @property
    def conflicting(self) -> bool:
        """过约束且解不出来 = 矛盾约束（必须报红，不许当成品）。"""
        return self.over_constrained and not self.converged

    def worst(self) -> tuple[str, float] | None:
        """残差最大的那条方程（`None` = 没有方程）；给人点名用。"""
        if not self.residuals:
            return None
        index = max(range(len(self.residuals)), key=lambda i: abs(self.residuals[i]))
        label = self.labels[index] if index < len(self.labels) else f"eq#{index}"
        return (label, self.residuals[index])

    def as_dict(self) -> dict[str, Any]:
        """机读快照（判定行/日志用；字段名即口径，不另起名）。"""
        return {
            "converged": self.converged,
            "dof": self.dof,
            "residual_nm": self.residual,
            "iterations": self.iterations,
            "status": self.status,
            "variables": self.variables,
            "equations": self.equations,
            "rank": self.rank,
            "redundant": self.redundant,
        }

    def solved_block(self) -> dict[str, Any]:
        """写回 `sketch.solved` 的块（契约字段 `dof`/`converged`/`cache` + 可观测的诊断字段）。"""
        block: dict[str, Any] = {"dof": self.dof, "converged": self.converged, "cache": True}
        block.update({k: v for k, v in self.as_dict().items() if k not in ("dof", "converged")})
        return block

    def verdict_line(self) -> str:
        """一行判定（可复跑、能报红；未收敛时点名最坏的方程）。"""
        worst = self.worst()
        tail = f" worst={worst[0]} worst_residual_nm={worst[1]:.6g}" if worst is not None and not self.converged else ""
        return (
            f"SKETCH_SOLVE: verdict={'PASS' if self.converged else 'FAIL'} status={self.status} "
            f"dof={self.dof} variables={self.variables} equations={self.equations} rank={self.rank} "
            f"redundant={self.redundant} residual_nm={self.residual:.6g} iterations={self.iterations}{tail}"
        )


# --------------------------------------------------------------------------------------
# 三态判定 → 画布配色（DoF 可视入口；`solve()` 的结果是唯一真源）
# --------------------------------------------------------------------------------------

#: 草图状态 → 颜色（唯一真源；UI 取色只走 `sketch_state_color()`，禁止再写一份状态→颜色表）。
DOF_STATE_COLORS: dict[str, str] = {
    "empty": "#8a8f98",     # 空草图：没有几何/约束可判
    "under": "#2f6fd0",     # 欠定义
    "fully": "#1f9d55",     # 完全定义
    "over": "#d9822b",      # 过定义（有冗余方程但解得出来）
    "conflict": "#c0392b",  # 矛盾（过定义且解不出来）
}


def _has_any_content(sk: Sketch) -> bool:
    return bool(sketch_entities(sk) or sketch_constraints(sk) or sketch_dimensions(sk))


def _state_from_result(sk: Sketch, result: "SolveResult") -> str:
    """`SolveResult` → 五态之一。优先级：空 > 矛盾 > 过定义 > 欠定义 > 完全定义。"""
    if not _has_any_content(sk):
        return "empty"
    if result.conflicting:
        return "conflict"
    if result.redundant > 0:
        return "over"
    if result.dof > 0:
        return "under"
    return "fully"


def dof_colors() -> dict[str, str]:
    """状态 → 颜色映射的**副本**（改返回值不影响本模块里的真源）。"""
    return dict(DOF_STATE_COLORS)


def sketch_state(sk: Sketch, *, result: "SolveResult | None" = None) -> str:
    """草图当前状态：empty / under / fully / over / conflict（不给 `result` 就现解一次）。"""
    return _state_from_result(sk, solve(sk) if result is None else result)


def sketch_state_color(sk: Sketch, *, result: "SolveResult | None" = None) -> dict[str, Any]:
    """三态判定的可视块：`{"state", "color", "dof", "redundant", "converged", "solved"}`。

    `color` 从 `DOF_STATE_COLORS` 现取 —— 画布按它上色即可（欠定义蓝 / 完全定义绿 / 过定义橙 /
    矛盾红 / 空灰），保证"判定"与"提示"同源，不会出现两处各写一套颜色。
    """
    sol = solve(sk) if result is None else result
    state = _state_from_result(sk, sol)
    return {
        "state": state,
        "color": DOF_STATE_COLORS[state],
        "dof": sol.dof,
        "redundant": sol.redundant,
        "converged": sol.converged,
        "solved": sol.solved_block(),
    }


# --------------------------------------------------------------------------------------
# solve：主循环
# --------------------------------------------------------------------------------------


def solve(
    sk: Sketch,
    *,
    max_iter: int = 200,
    tol_nm: float = DEFAULT_TOL_NM,
    fixed: Sequence[Ref] = (),
) -> SolveResult:
    """解算草图的几何约束与驱动尺寸，返回 `SolveResult` 并把 `dof/converged` 写回 `sketch.solved`。

    语义（与契约一致；增补项已标注）：
    - 变量 = 各实体长度字段（见模块 docstring）；`fixed=[(uuid, sub), ...]` 与实体字段 `fixed: true`
      是**锚定**（契约外增补：不锚定则刚体自由度永远在，dof 到不了 0）；
    - 驱动尺寸（`value_nm` 非空）与几何约束**同等**参与方程；参考尺寸（`value_nm is None`）
      不进方程，解算后把实测值写进 `measured_nm`（量不出来写 `None` = NA）；
    - 收敛判据：**落盘后整数坐标**下的最大绝对残差 ≤ `tol_nm`；
    - 不收敛**不抛错**（几何仍写回，便于看到当前状态），但 `converged=False` 且残差如实返回；
    - 结构性错误（引用不存在、种类未知、长度非法）**抛 `SketchError`**，不静默跳过。
    """
    if isinstance(max_iter, bool) or not isinstance(max_iter, int) or max_iter < 1:
        raise OutOfRange("err.out_of_range", value=max_iter, min=1, max="NA",
                         detail="max_iter must be a positive integer")
    if not isinstance(tol_nm, (int, float)) or isinstance(tol_nm, bool) or float(tol_nm) <= 0 \
            or not math.isfinite(float(tol_nm)):
        raise OutOfRange("err.out_of_range", value=tol_nm, min=0, max="NA",
                         detail="tol_nm must be a positive number of nanometres")

    entries, reference_dimensions = _solve_entries(sk)
    varindex, x = _build_variables(sk, _frozen_fields(sk, fixed))
    context = _Context(sk, varindex)
    solvable = [entry for entry in entries if entry.kind in _EQUATION_BUILDERS]
    builders = [_closure(context, entry) for entry in solvable]
    labels = [entry.label for entry in solvable for _ in range(_EQUATION_COUNT.get(entry.kind, 1))]

    n = len(x)
    residuals, jacobian = _evaluate(builders, x)
    best = _max_abs(residuals)
    iterations = 0
    lam = 1.0e-8
    nudges = 0
    status = "empty"

    while True:
        if not builders:
            status = "empty"
            break
        if best <= float(tol_nm):
            status = "ok"
            break
        if n == 0:
            status = "stalled"  # 没有自由变量可动（全被锚定）
            break
        if iterations >= max_iter:
            status = "max_iter"
            break
        iterations += 1
        accepted = False
        for _ in range(16):
            step = _lm_step(jacobian, residuals, lam, n)
            if step is None:
                lam *= 10.0
                continue
            trial = [value + delta for value, delta in zip(x, _clamp_step(step))]
            trial_residuals, trial_jacobian = _evaluate(builders, trial)
            trial_best = _max_abs(trial_residuals)
            if trial_best < best:
                x, residuals, jacobian, best = trial, trial_residuals, trial_jacobian, trial_best
                lam = max(lam / 3.0, 1e-12)
                accepted = True
                break
            lam *= 8.0
        if not accepted:
            if nudges < _NUDGE_BUDGET:
                # 退化位形（例如两点恰好重合时的距离约束）梯度为 0 ⇒ 确定性轻推一格再试
                nudges += 1
                x[nudges % n] += 1.0
                residuals, jacobian = _evaluate(builders, x)
                best = _max_abs(residuals)
                lam = 1.0e-8
                continue
            status = "stalled"  # 阻尼压不下去：最小二乘已到极值（过约束/矛盾）
            break

    # ── 整数落位 + 按落盘后的整数坐标复算残差 ────────────────────────────────────────
    if builders:
        x = [float(round(value)) for value in x]
        x, best = _snap_to_integers(x, builders, _max_abs(_evaluate(builders, x)[0]))
        residuals, jacobian = _evaluate(builders, x)
        best = _max_abs(residuals)
        if best <= float(tol_nm):
            status = "ok"
        elif status == "ok":  # pragma: no cover - 取整后反而越界 ⇒ 如实降级，不硬说收敛
            status = "stalled"

    _write_back(sk, x, varindex)
    rank = _matrix_rank(jacobian, n)
    result = SolveResult(
        converged=best <= float(tol_nm),
        dof=n - rank,
        residual=best,
        iterations=iterations,
        status=status,
        variables=n,
        equations=len(residuals),
        rank=rank,
        redundant=len(residuals) - rank,
        residuals=tuple(residuals),
        labels=tuple(labels),
    )
    _set(sk, "solved", result.solved_block())
    _measure_reference_dimensions(sk, reference_dimensions)
    return result


def _closure(context: _Context, entry: _Entry) -> Callable[[Sequence[float]], list[_D]]:
    """把一条条目绑成 `x → 残差列表` 的闭包（求解器只认这个接口）。"""
    builder = _EQUATION_BUILDERS[entry.kind]

    def evaluate_at(values: Sequence[float]) -> list[_D]:
        return builder(context, values, entry.refs, entry.value_nm)

    return evaluate_at


def _measure_reference_dimensions(sk: Sketch, dimensions: Sequence[Any]) -> None:
    """参考尺寸（`value_nm is None`）解算后回填 `measured_nm`；量不出来写 `None`（NA），不编数。"""
    for item in dimensions:
        try:
            value = measure_dimension(sk, item)
        except SketchError:
            value = None
        if isinstance(item, MutableMapping):
            item["measured_nm"] = value
        else:
            try:
                setattr(item, "measured_nm", value)
            except AttributeError:  # pragma: no cover - 只读条目
                pass


def measure_dimension(sk: Sketch, dimension: Any) -> int | None:
    """按当前几何实测一条尺寸（纳米；角度类为微度）；量不出来返回 `None` = NA。"""
    kind = _get(dimension, "kind", None)
    if kind not in DIMENSION_KINDS:
        raise UnsupportedKind("err.not_implemented", detail=f"dimension kind {kind!r}", feature=str(kind))
    refs = _as_refs(_get(dimension, "refs", None))
    if kind == "linear":
        return int(round(_plain_distance(sk, refs)))
    if kind in ("radial", "diameter"):
        if len(refs) != 1:
            return None
        radius = geom_get(_radius_entity(sk, refs[0]), "r")
        return int(round(radius * (2.0 if kind == "diameter" else 1.0)))
    if len(refs) != 2:
        return None
    a = _plain_direction(sk, refs[0])
    b = _plain_direction(sk, refs[1])
    if a is None or b is None:
        return None
    return deg_to_udeg(math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])))


def _plain_point(sk: Sketch, ref: Ref) -> tuple[float, float]:
    if _is_origin(ref[0]):
        _origin_sub(ref[1])
        return (float(ORIGIN_POINT_NM[0]), float(ORIGIN_POINT_NM[1]))
    return _point(find_entity(sk, ref[0]), ref[1])


def _plain_direction(sk: Sketch, ref: Ref) -> tuple[float, float] | None:
    entity = find_entity(sk, ref[0])
    if entity_kind(entity) != "line" or _normalize_sub("line", ref[1]) != "":
        return None
    return (geom_get(entity, "x2") - geom_get(entity, "x1"), geom_get(entity, "y2") - geom_get(entity, "y1"))


def _plain_distance(sk: Sketch, refs: Sequence[Ref]) -> float:
    """几何实测距离（点-点 / 线长 / 点-线垂距）——参考尺寸回填用。"""
    _validate_distance_refs(sk, refs)
    if len(refs) == 1:
        direction = _plain_direction(sk, refs[0])
        assert direction is not None  # `_validate_distance_refs` 已保证是整线引用
        return math.hypot(*direction)
    first, second = refs
    a_is_point = _is_point_ref(_ref_kind(sk, first), first[1])
    if a_is_point and _is_point_ref(_ref_kind(sk, second), second[1]):
        p, q = _plain_point(sk, first), _plain_point(sk, second)
        return math.hypot(p[0] - q[0], p[1] - q[1])
    point_ref, line_ref = (first, second) if a_is_point else (second, first)
    p = _plain_point(sk, point_ref)
    p1 = _plain_point(sk, (line_ref[0], "start"))
    direction = _plain_direction(sk, line_ref)
    assert direction is not None
    length = math.hypot(*direction)
    return abs(direction[0] * (p[1] - p1[1]) - direction[1] * (p[0] - p1[0])) / length


# --------------------------------------------------------------------------------------
# 命令侧入口：add_constraint（校验后落库，**不**顺手求解）
# --------------------------------------------------------------------------------------


def add_constraint(sk: Sketch, *, kind: str, refs: Sequence[Ref], value_nm: int | None = None) -> str:
    """把一条几何约束写进草图，返回约束 uuid；**只落库不求解**（求解是显式的 `solve`）。

    引用一律 `(entity_uuid, sub_shape_name)`，禁下标；坏引用/坏取值当场抛错，绝不半接受。
    """
    normalized = validate_constraint(sk, kind=kind, refs=refs, value_nm=value_nm)
    value = None if value_nm is None else _as_nm(value_nm, positive=False, what="constraint.value_nm")
    item = {
        "uuid": new_uuid(),
        "kind": kind,
        "refs": [[entity_ref, sub] for entity_ref, sub in normalized],
        "value_nm": value,
    }
    sketch_constraints(sk).append(item)
    return str(item["uuid"])


def delete_constraint(sk: Sketch, constraint_uuid: str) -> SolveResult:
    """按 uuid 删除一条约束并**立即重解**，返回重解后的 `SolveResult`（A10-E4 新增入口）。

    - 找不到该 uuid ⇒ `UnknownReference`（不静默成功、也不返回"删了 0 条"这种假绿）；
    - 删除 = 撤掉它贡献的那几条方程 ⇒ 重解后 DoF **如实**变化：删独立约束 ⇒ DoF 增加；
      删冗余约束 ⇒ 秩不变、DoF 不变（这是 `dof = 变量数 − 秩` 的定义，不是 bug）；
    - 只动约束表，不碰实体几何与尺寸表：几何由这次重解按**剩余**约束写回。
    """
    if not isinstance(constraint_uuid, str) or not constraint_uuid:
        raise GeometryError(
            "err.geometry_invalid", detail=f"constraint_uuid must be a non-empty string, got {constraint_uuid!r}"
        )
    constraints = sketch_constraints(sk)
    for index, item in enumerate(constraints):
        if _get(item, "uuid", None) == constraint_uuid:
            del constraints[index]
            break
    else:
        raise UnknownReference("err.unknown_reference", reference=str(constraint_uuid))
    return solve(sk)


# --------------------------------------------------------------------------------------
# 过定义诊断与冲突修复（SketchXpert 等价物的库级半支：点名来源 + 真删真重解）
# --------------------------------------------------------------------------------------


def _owner_of(label: str) -> tuple[str, str]:
    """方程 label（`source:uuid:kind`）→ `(source, uuid)`；解析不了就如实回落。"""
    source, _, rest = str(label).partition(":")
    uuid_, _, _kind = rest.partition(":")
    return (source or "unknown", uuid_ or "<unknown>")


def _equation_system(sk: Sketch) -> tuple[list[str], list[float], list[Mapping[int, float]], int]:
    """当前文档的方程系统快照：`(labels, residuals, jacobian, n_variables)`。

    诊断要在**落盘后的整数坐标**上看秩与残差，所以这里重放一遍求解器的建方程路径
    （与 `solve()` 同一批 `_EQUATION_BUILDERS`，不是第二份公式）。
    """
    entries, _reference = _solve_entries(sk)
    varindex, x = _build_variables(sk, _frozen_fields(sk, ()))
    context = _Context(sk, varindex)
    solvable = [entry for entry in entries if entry.kind in _EQUATION_BUILDERS]
    builders = [_closure(context, entry) for entry in solvable]
    labels = [entry.label for entry in solvable for _ in range(_EQUATION_COUNT.get(entry.kind, 1))]
    residuals, jacobian = _evaluate(builders, x)
    return labels, residuals, jacobian, len(varindex)


def diagnose_conflicts(sk: Sketch, *, tol_nm: float = DEFAULT_TOL_NM) -> dict[str, Any]:
    """点名冲突 / 冗余的**来源条目**（约束或驱动尺寸），不只给方程条数。

    返回（`0` = 真为零，`[]` = 真的没有）：
      - `conflicting`：是否存在解不出来的矛盾方程；
      - `conflicts`：残差 > `tol_nm` 的来源条目（去重，带 `kind` / 最坏残差 / 方程条数）；
      - `redundant`：冗余方程数（方程数 − 秩）；
      - `redundant_items`：逐步加行做秩判定的贪心识别结果（加进去秩不涨的那几条方程）；
      - `worst`：最大残差方程 `(label, residual_nm)`，无方程时为 `None`。
    """
    result = solve(sk, tol_nm=tol_nm)
    labels, residuals, jacobian, n = _equation_system(sk)
    conflicts: dict[str, dict[str, Any]] = {}
    for label, residual in zip(labels, residuals):
        if abs(residual) <= float(tol_nm):
            continue
        source, uuid_ = _owner_of(label)
        record = conflicts.setdefault(uuid_, {"uuid": uuid_, "source": source, "kind": label.rsplit(":", 1)[-1],
                                              "max_residual_nm": 0.0, "equations": 0, "labels": []})
        record["max_residual_nm"] = max(record["max_residual_nm"], abs(residual))
        record["equations"] += 1
        record["labels"].append(label)

    redundant_items: list[dict[str, Any]] = []
    basis: list[Mapping[int, float]] = []
    rank = 0
    for label, residual, row in zip(labels, residuals, jacobian):
        basis.append(row)
        grown = _matrix_rank(basis, n)
        if grown == rank:
            source, uuid_ = _owner_of(label)
            redundant_items.append({"uuid": uuid_, "source": source, "kind": label.rsplit(":", 1)[-1],
                                    "residual_nm": abs(residual), "label": label})
        rank = grown

    return {
        "conflicting": bool(result.conflicting),
        "converged": bool(result.converged),
        "residual_nm": float(result.residual),
        "equations": int(result.equations),
        "rank": int(result.rank),
        "redundant": int(result.redundant),
        "conflicts": sorted(conflicts.values(), key=lambda item: (-item["max_residual_nm"], item["uuid"])),
        "redundant_items": redundant_items,
        "worst": result.worst(),
        "result": result,
    }


def repair_conflicts(sk: Sketch, *, max_drop: int = 8, tol_nm: float = DEFAULT_TOL_NM) -> dict[str, Any]:
    """按诊断结果**真删**冲突来源（最坏残差优先）并重解，直到收敛或没有可删的来源。

    - 每轮只删一条（约束走 `constraints` 表，驱动尺寸走 `dimensions` 表），删完立即重解；
    - 返回 `{"removed": [uuid...], "dropped": [...], "reason", "converged", "result"}`；
    - 没有可删来源（诊断为空）⇒ `removed=[]`、`reason="no_conflict_source"`（真为零，不是失败）。
    """
    if isinstance(max_drop, bool) or not isinstance(max_drop, int) or max_drop < 0:
        raise OutOfRange("err.out_of_range", value=max_drop, min=0, max="NA",
                         detail="max_drop must be a non-negative integer")
    removed: list[str] = []
    dropped: list[dict[str, Any]] = []
    reason = "no_conflict_source"
    result = solve(sk, tol_nm=tol_nm)
    for _ in range(max_drop + 1):
        diag = diagnose_conflicts(sk, tol_nm=tol_nm)
        result = diag["result"]
        if not diag["conflicting"]:
            reason = "converged" if result.converged else "no_conflict_source"
            break
        if diag["conflicts"]:
            target = diag["conflicts"][0]
        elif diag["redundant_items"]:
            target = diag["redundant_items"][0]
        else:
            break
        uuid_ = str(target["uuid"])
        if not _drop_item(sk, uuid_):
            break
        removed.append(uuid_)
        dropped.append({"uuid": uuid_, "source": target.get("source"), "kind": target.get("kind")})
        result = solve(sk, tol_nm=tol_nm)
        if result.converged:
            reason = "converged"
            break
    return {"removed": removed, "dropped": dropped, "reason": reason,
            "converged": bool(result.converged), "result": result}


def _drop_item(sk: Sketch, item_uuid: str) -> bool:
    """按 uuid 从约束表 / 尺寸表删掉一条来源（都在 ⇒ 先删约束；找不到返回 False，不静默成功）。"""
    for container in (sketch_constraints(sk), sketch_dimensions(sk)):
        for index, item in enumerate(container):
            if _get(item, "uuid", None) == item_uuid:
                del container[index]
                return True
    return False


# --------------------------------------------------------------------------------------
# 闭合轮廓（供拉伸）：端点坐标严格相同才算接上，不拿约束声明当几何
# --------------------------------------------------------------------------------------


class _UnionFind:
    """并查集（节点合并用；只在 `tol_nm > 0` 时用到）。"""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, index: int) -> int:
        root = index
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[index] != root:
            self._parent[index], index = root, self._parent[index]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


def closed_profiles(sk: Sketch, *, tol_nm: float = 0.0) -> list[tuple[str, ...]]:
    """闭合轮廓（uuid 环），供拉伸使用。

    判据（**严格按几何，不按约束声明**）：端点坐标相同（`tol_nm=0` 时整数纳米严格相等）才算接上；
    节点度数恰为 2 且边数 ≥ 2 的连通分量才算一个环（两条圆弧拼成的透镜形也算），
    圆本身自闭合 ⇒ 每个圆单独成环。输出确定性：环内从最小 uuid 起、方向取字典序较小者，环间按元组排序。
    未闭合的链**不返回**（空列表 = 真为零，不是"没算"）。
    """
    loops: list[tuple[str, ...]] = []
    raw_edges: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    for entity in sketch_entities(sk):
        if bool(_get(entity, "construction", False)):
            continue  # 构造几何不进轮廓（SolidWorks 语义：参考线/参考圆不参与拉伸轮廓）
        kind = entity_kind(entity)
        uid = entity_uuid(entity)
        if kind == "circle":
            loops.append((uid,))
            continue
        if kind not in ("line", "arc"):
            continue
        start, end = _point(entity, "start"), _point(entity, "end")
        if start == end:
            continue  # 退化边（零长线 / 首尾同点的弧）不构成轮廓
        raw_edges.append((uid, start, end))

    if raw_edges:
        points: list[tuple[float, float]] = []
        edge_points = [(_node_index(points, start), _node_index(points, end)) for _, start, end in raw_edges]
        if tol_nm > 0:
            union = _UnionFind(len(points))
            for i in range(len(points)):
                for j in range(i + 1, len(points)):
                    if math.dist(points[i], points[j]) <= tol_nm:
                        union.union(i, j)
            merged = [(union.find(a), union.find(b)) for a, b in edge_points]
            remap = {node: index for index, node in enumerate(sorted({n for pair in merged for n in pair}))}
            edge_points = [(remap[a], remap[b]) for a, b in merged]

        edge_nodes: dict[str, tuple[int, int]] = {}
        adjacency: dict[int, list[tuple[int, str]]] = {}
        for (uid, _, _), (a, b) in zip(raw_edges, edge_points):
            if a == b:
                continue  # 容差合并后退化成自环 ⇒ 不是轮廓边
            edge_nodes[uid] = (a, b)
            adjacency.setdefault(a, []).append((b, uid))
            adjacency.setdefault(b, []).append((a, uid))
        for node in adjacency:
            adjacency[node].sort(key=lambda item: (item[1], item[0]))

        for component in _components(edge_nodes):
            if len(component) < 2:
                continue
            nodes = {node for uid in component for node in edge_nodes[uid]}
            if any(len(adjacency.get(node, ())) != 2 for node in nodes):
                continue  # 度数不为 2 ⇒ 不是简单环（悬边/分叉），不假装闭合
            ring = _walk_cycle(component, edge_nodes, adjacency)
            if ring is not None:
                loops.append(ring)

    return sorted(set(loops))


def _node_index(points: list[tuple[float, float]], point: tuple[float, float]) -> int:
    """点 → 节点下标（相同坐标复用同一节点）。"""
    for index, existing in enumerate(points):
        if existing == point:
            return index
    points.append(point)
    return len(points) - 1


def _components(edge_nodes: Mapping[str, tuple[int, int]]) -> list[list[str]]:
    """按共享节点把边分连通分量（确定性排序）。"""
    parent = {uid: uid for uid in edge_nodes}

    def find(uid: str) -> str:
        while parent[uid] != uid:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    node_owner: dict[int, str] = {}
    for uid, (a, b) in sorted(edge_nodes.items()):
        for node in (a, b):
            if node in node_owner:
                ra, rb = find(node_owner[node]), find(uid)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
            else:
                node_owner[node] = uid
    groups: dict[str, list[str]] = {}
    for uid in sorted(edge_nodes):
        groups.setdefault(find(uid), []).append(uid)
    return [groups[key] for key in sorted(groups)]


def _walk_cycle(
    component: Sequence[str], edge_nodes: Mapping[str, tuple[int, int]], adjacency: Mapping[int, list[tuple[int, str]]]
) -> tuple[str, ...] | None:
    """在"每点度数 2"的连通分量里走出环；返回**规范化** uuid 环。"""
    start = min(component)
    first_node = edge_nodes[start][0]
    ring: list[str] = []
    current_edge = start
    node = first_node
    while True:
        ring.append(current_edge)
        a, b = edge_nodes[current_edge]
        nxt = b if a == node else a
        if nxt == first_node:
            break
        candidates = [uid for other, uid in adjacency.get(nxt, ()) if uid != current_edge]
        if not candidates:  # pragma: no cover - 度数检查已排除
            return None
        current_edge = candidates[0]
        node = nxt
        if len(ring) > len(component):  # pragma: no cover - 防御性
            return None
    if len(ring) != len(component):
        return None
    return _canonical_ring(ring)


def _canonical_ring(ring: Sequence[str]) -> tuple[str, ...]:
    """环的规范形：从最小 uuid 起、方向取字典序较小者（两次调用逐字节一致）。"""
    smallest = min(range(len(ring)), key=lambda i: ring[i])

    def rotate(seq: Sequence[str]) -> tuple[str, ...]:
        return tuple(seq[smallest:]) + tuple(seq[:smallest])

    return min(rotate(ring), rotate(list(reversed(ring))))


# --------------------------------------------------------------------------------------
# 区域选择（点选闭合轮廓）：拉伸"吃哪个轮廓"的库级入口
# --------------------------------------------------------------------------------------


def _ring_polygon(sk: Sketch, ring: Sequence[str]) -> list[tuple[float, float]] | None:
    """环 → 顶点多边形（按环序取每边的 `start`；圆弧按弦近似）；整圆环返回 `None`（另走圆判定）。"""
    points: list[tuple[float, float]] = []
    for uid in ring:
        entity = find_entity(sk, uid)
        if entity_kind(entity) == "circle":
            return None
        points.append(_point(entity, "start"))
    return points if len(points) >= 3 else None


def _polygon_area(polygon: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(polygon, list(polygon[1:]) + [polygon[0]]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _point_in_polygon(point: tuple[float, float], polygon: Sequence[tuple[float, float]]) -> bool:
    """射线法（点在边界上的归属不承诺，交互点选够用）。"""
    x, y = point
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing:
                inside = not inside
    return inside


def select_region(sk: Sketch, *, point_nm: Sequence[float]) -> tuple[str, ...] | None:
    """点选闭合区域：返回**包含该点**的闭合轮廓（uuid 环，`closed_profiles` 的规范形）。

    - 环的判定与 `closed_profiles()` 同一把尺子（构造几何已剔除、严格闭合）；
    - 圆弧按弦近似（点选是交互动作，不需要 µm 级判据；拉伸消费的环本体仍是精确几何）；
    - 多个环同时包含该点（嵌套）⇒ 取**面积最小**的那个（最内层轮廓），确定性；
    - 返回 `None` = "点不在任何闭合区域里"（**真 NA**：不是空环，也不是未算）。
    """
    if isinstance(point_nm, (str, bytes)) or not isinstance(point_nm, Sequence) or len(point_nm) != 2:
        raise GeometryError("err.geometry_invalid", detail=f"point_nm must be a (x_nm, y_nm) pair, got {point_nm!r}")
    px = _as_nm(point_nm[0], positive=False, what="point_nm.x")
    py = _as_nm(point_nm[1], positive=False, what="point_nm.y")
    candidates: list[tuple[float, tuple[str, ...]]] = []
    for ring in closed_profiles(sk):
        if len(ring) == 1:
            entity = find_entity(sk, ring[0])
            if entity_kind(entity) == "circle":
                cx, cy, radius = geom_get(entity, "cx"), geom_get(entity, "cy"), geom_get(entity, "r")
                if math.hypot(px - cx, py - cy) <= radius:
                    candidates.append((math.pi * radius * radius, ring))
                continue
        polygon = _ring_polygon(sk, ring)
        if polygon is None or not _point_in_polygon((float(px), float(py)), polygon):
            continue
        candidates.append((_polygon_area(polygon), ring))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[1]
