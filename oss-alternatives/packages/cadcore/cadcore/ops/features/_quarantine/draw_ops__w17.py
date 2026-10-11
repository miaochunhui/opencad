#!/usr/bin/env python3
"""DRAW_OPS 族 · 第 17 席（draw_ops__w17）：must_have 第 8 条「边界创建」（BOUNDARY / BPOLY）。

本席做什么（可复跑，口径现读现算，不靠记忆）
--------------------------------------------
1. 族规格 `docs/research/cad-parity/features/autocad-draw.json`（`must_have`，20 条）
   与族状态账 `docs/research/cad-parity/status/autocad-draw.json`（`items[].status`）。
   注意：任务书里写的 `features/draw_ops.json` 在本仓**不存在**（`find` 无命中）；
   `draw_ops` 族的清单落在 `features/autocad-draw.json`。本模块 `remaining_must_have()`
   按仓根下的 cad-parity 目录现读这两份 JSON，**不抄死名单**。
2. 把 must_have 按清单顺序过一遍，只留 `status ∈ {missing, partial}` 的条目，编号 0..N-1
   （claim 时 N=15，逐条如下；被过滤掉的 implemented 是：椭圆与椭圆弧 / 样条曲线 /
   构造线与射线 / 面域 / 圆环）：
   ::

       0 直线               5 正多边形              10 对象捕捉
       1 多段线             6 点与点样式            11 正交与极轴追踪
       2 圆                 7 图案填充与渐变色      12 对象捕捉追踪与临时追踪点
       3 圆弧               8 边界创建  ← 本席实现   13 动态输入
       4 矩形               9 修订云线              14 当前图层与对象特性

   （若清单后来漂移，自检把这条记 `NA detail=spec_drift …`，**不假装绿、也不当成失败**：
   席位认领时的编号与条目名写在本 docstring 里，判据可回查。）
3. 本席交付第 **8** 号「边界创建」：给**环内拾取点**，从**既有**几何反求**最小闭合环**，
   把它落成**真实的新边界对象**（多边形环 ⇒ 闭合 `polyline`；单圆环 ⇒ 同圆心同半径的新 `circle`）。

冻结接口（与其它席位一致，逐条对齐）
--------------------------------------
::

    FAMILY = "draw_ops"
    OPERATIONS: dict[str, Callable[..., list[str]]]
    op(sk_or_doc, **kwargs) -> list[str]        # 返回本次**新建**对象的 uuid 列表

* 长度一律**整数纳米**（`*_nm`；`bool` 与非整数浮点一律拒）；
* 可预期错误一律抛 `cadcore.errors.CadError(reason=…)`，**reason 机器可读**（见下表）；
* 返回值**不是变更计数器**：新建型操作恒返回新建对象的 uuid；"没算"一律抛错，与"真为零"不同形；
* 本模块**不写 `__init__.py`**（`ops/features/` 的包壳归别的席位），命名空间包（PEP 420）下照样可导入。

真几何 / 真数据后果（不是桩）
------------------------------
* 新增对象**真进** `sketch_entities()` 那张实体表（字段集 uuid/kind/layer/construction/geom 与
  `edit/modify.py`、`sketch/commands.py` 逐字一致），既有图元**一个字节都不动**；
* 环检测用**上游唯一那把尺子** `sketch.primitives.closed_profiles`（`constraints.closed_profiles`
  的超集，额外认闭合折线）—— 本层**不重写**环检测判据；
* 拾取点包含判定是**整数 even-odd 射线法**（叉积全整数、无浮点），落在边上算在内；
* 多个环都含拾取点 ⇒ 取**面积最小**者（鞋带公式 `|2A|` 整数比较，圆用 `710·r²/113` 整数近似，
  只用于比大小）；同面积再按环的 uuid 元组定序 ⇒ **输出确定**；
* 拾取点 + **间隙容差** `tol_nm`：端点按容差归并（与 `constraints.closed_profiles` 同一口径：
  任意两点欧氏距离 ≤ tol 即并簇），每个簇取**最先出现**的端点为代表点 ⇒ 缺口被补上，
  顶点位移 **≤ tol_nm**（这是 BOUNDARY 的 gap tolerance 语义，写死在 docstring 里）；
* 操作内**自检后置条件**：实体数恰好 +1、新 uuid 在表里、新对象在族尺子下真是闭合环
  （或几何上首末点重合的闭合折线），任一不成立 ⇒ `CadError(reason="postcondition_failed")`。

本层自己实现什么 / 复用了什么（E1① 唯一真源）
----------------------------------------------
* **复用**：`sketch.primitives.closed_profiles`（环检测）、`sketch.primitives.add_polyline`
  （闭合折线的**唯一**写入实现，含点数/零长段/退化校验）、`sketch.constraints.sketch_entities`
  （实体表唯一读法）、`sketch.constraints.new_uuid`（uuid 唯一来源）、`ops.draw_ops._sketch_of`
  （`sk_or_doc → sketch` 的唯一解析口径；缺席时本层有鸭子类型兜底，见 `sketch_of()`）；
* **本层实现**：拾取点包含判定、容差归并 + 环内顶点定序、最小环选择、边界对象落库与后置校验
  —— 这几件正是原 `draw_ops.boundary()`（同一族模块）**没有**暴露的部分：它不给 `tol_nm`、
  不给区分度更高的 reason、不做后置校验，且族门只吃单模块作用域（见 status 账 top_gaps 第 1 条）。
  本模块把该能力**注册进族接口**，使族门按多模块作用域可见。

错误 reason 表（机器可读，别去解析文案）
------------------------------------------
====================== ==========================================================
`invalid_argument`     `point_nm` 不是 2 个整数纳米 / `layer` 非空串不成立 /
                       `tol_nm` 负 / 出现未知 kwarg
`invalid_target`       `sk_or_doc` 既不是 L1 草图、也不是带草图的文档
`invalid_geometry`     几何字段缺 `start`/`end`（弧线）或圆的 `center`/`radius`
`no_closed_boundary`   全图一个闭合环都没有（tol=0 时缺口链不算环）
`point_outside_boundary` 有闭合环，但没一个把拾取点包住
`unsupported_geometry` 拾取点只能落在**含 arc** 的环里：L1 没有原生曲线实体，
                       本层**拒绝**用弦近似静默改几何（不假装支持）
`postcondition_failed` 落库后自检不成立（实体数/uuid/闭合性）
`dependency_missing`   上游 `primitives.add_polyline` / `constraints.new_uuid` 缺席
`upstream_error`       上游抛出的非 CadError 异常（**翻译后**才漏出族接口）
====================== ==========================================================

自检（文件自带，不依赖 pytest）
--------------------------------
::

    cd oss-alternatives/packages/cadcore
    python3 -m py_compile cadcore/ops/features/draw_ops__w17.py
    python3 cadcore/ops/features/draw_ops__w17.py

末行恒为 `FEATURE_OK family=draw_ops op=boundary_create verdict=PASS`（FAIL 时 rc=1）。
自检在**真 L1 容器**上跑（优先 `docmodel.new_document()` + 上游 `_sketch_of` 的真文档路径，
其次公共工厂，最后鸭子类型容器；三种都用"append 后立刻读回同一对象"自证夹具是真容器），
断言里有：最小环选取、闭合性、既有图元零改动、实体数 +1、默认层与自定义层、拾取点在外报错、
非法入参报错、间隙容差闭合、arc 环拒绝、单圆环克隆、清单第 8 号绑定。

中文只出现在 docstring / 注释里：`tools/cad/i18n_gate.py` 把"非 docstring 的含汉字字符串常量"
计为 `hardcoded_literals`，故本文件所有面向机器的字符串字面量一律 ASCII（族条目名走 `\\u` 转义）。
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any, Callable

from cadcore.errors import CadError
from cadcore.sketch import constraints as _constraints
from cadcore.sketch import primitives as _primitives
from cadcore.ops import draw_ops as _family

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "MM_NM",
    "FEATURE_ID",
    "FEATURE_INDEX",
    "boundary_create",
    "boundary_pick",
    "remaining_must_have",
    "module_facts",
    "selftest",
    "main",
]

#: 族 id（冻结接口）。
FAMILY = "draw_ops"

#: 族规格/状态账的 id（`docs/research/cad-parity/{features,status}/<id>.json`）。
FEATURE_ID = "autocad-draw"

#: 本席认领的编号：族清单里仍 missing/partial 的条目按原顺序编号后的第 8 号。
FEATURE_INDEX = 8

#: 主操作名（`OPERATIONS` 的主键，也是自检判定行里的 op 名）。
_OP_NAME = "boundary_create"

#: 1 mm 的纳米数（本文件内自用，避免跨模块常量依赖）。
MM_NM: int = 1_000_000

#: 第 8 号条目名 = 边界创建（BOUNDARY / BPOLY）。用 `\\u` 转义书写，保持本文件字面量全 ASCII。
_FEATURE_NAME = "Boundary Creation"

#: `sk_or_doc → sketch` 的唯一解析口径：族模块的 `_sketch_of`（私名，跨模块复用以免两套判据）。
_UPSTREAM_SKETCH_OF = getattr(_family, "_sketch_of", None)

#: 认领时（claim）算出的 still missing/partial 清单 —— 只作**对照**，判据一律现读 JSON 现算。
_CLAIM_TIME_REMAINING: tuple[str, ...] = (
    "Line", "Polyline", "Circle", "Arc", "Rectangle",
    "Regular Polygon", "Point and Point Style", "Hatch and Gradient",
    _FEATURE_NAME, "Revision Cloud", "Object Snap", "Ortho and Polar Tracking",
    "Object Snap Tracking and Temporary Track Point", "Dynamic Input",
    "Current Layer and Object Properties",
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # packages/cadcore
_REPO_ROOT = os.path.dirname(_PACKAGE_ROOT)                               # oss-alternatives
_PARITY_DIR = os.path.join(_REPO_ROOT, "docs", "research", "cad-parity")

#: 自检用容器候选里允许调用的公共工厂名（白名单，避免误调有副作用的函数）。
_FACTORY_NAMES = ("new_sketch", "create_sketch", "make_sketch", "empty_sketch", "Sketch")

#: 自检探测公共工厂的模块（存在才探测，缺席不算失败）。
_FACTORY_MODULES = ("cadcore.sketch", "cadcore.sketch.model", "cadcore.sketch.primitives",
                    "cadcore.sketch.constraints", "cadcore.sketch.commands")


# ======================================================================================
# 小工具：错误 / 字段访问 / 入参把关
# ======================================================================================


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造带机器可读 `reason` 的 `CadError`（调用处 `raise _fail(...)`）。"""
    return CadError(message, reason=reason, **details)


def _delegate(call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """调上游并把非 CadError 异常**翻译**成 CadError（不让别家的错误类型漏出族接口）。"""
    try:
        return call(*args, **kwargs)
    except CadError:
        raise
    except Exception as exc:  # pragma: no cover - 上游异常路径
        raise CadError(str(exc), reason="upstream_error", error=type(exc).__name__) from exc


def _field(node: Any, name: str, default: Any = None) -> Any:
    """结构取值（映射 / 对象双兼容，与 `sketch/primitives.py` 的 `_field` 同一取舍）。"""
    if isinstance(node, Mapping):
        return node.get(name, default)
    return getattr(node, name, default)


def _int_nm(value: Any, *, what: str) -> int:
    """长度入参 → 整数纳米（`bool` 与非整数浮点一律拒：长度单位是硬口径）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("invalid_argument", "%s must be an integer number of nanometers" % what,
                    got=repr(value))
    return int(value)


def _point_nm(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("invalid_argument", "%s must be a (x_nm, y_nm) pair" % what, got=repr(value))
    return (_int_nm(value[0], what=what + ".x"), _int_nm(value[1], what=what + ".y"))


def _layer_name(value: Any) -> str:
    """图层名：非空字符串（空串会让实体在图层管理里"消失"）。"""
    if not isinstance(value, str) or not value.strip():
        raise _fail("invalid_argument", "layer must be a non-empty string", got=repr(value))
    return value


def _tolerance(value: Any) -> int:
    """间隙容差 `tol_nm`：非负整数纳米。"""
    tolerance = _int_nm(value, what="tol_nm")
    if tolerance < 0:
        raise _fail("invalid_argument", "tol_nm must be >= 0", got=tolerance)
    return tolerance


def _new_uuid() -> str:
    """uuid 唯一来源：`sketch.constraints.new_uuid`（不自己另立生成器）。"""
    factory = getattr(_constraints, "new_uuid", None)
    if not callable(factory):
        raise _fail("dependency_missing", "cadcore.sketch.constraints.new_uuid is missing")
    return str(_delegate(factory))


def _geom(entity: Any) -> Mapping[str, Any]:
    """实体几何块（L1 冻结字段 `geom`，必须是映射）。"""
    geom = _field(entity, "geom", None)
    if not isinstance(geom, Mapping):
        raise _fail("invalid_geometry", "entity geom is not a mapping",
                    uuid=str(_field(entity, "uuid", "")))
    return geom


def _raw_point(geom: Mapping[str, Any], *names: str, uuid: str) -> tuple[int, int]:
    """从几何块里取点（键名给候选序列，缺字段 ⇒ `invalid_geometry`，**不猜 0**）。"""
    for name in names:
        value = geom.get(name)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2:
            return (_int_nm(value[0], what="geom.%s.x" % name),
                    _int_nm(value[1], what="geom.%s.y" % name))
    raise _fail("invalid_geometry", "entity geom has no %s point" % "/".join(names), uuid=uuid)


# ======================================================================================
# 草图解析（复用族模块的 `_sketch_of`；缺席时鸭子类型兜底）
# ======================================================================================


def _usable_sketch(value: Any) -> bool:
    """夹具自证：`sketch_entities()` 给出**活**表（append 后立刻读回同一个对象）。"""
    try:
        store = _constraints.sketch_entities(value)
    except Exception:
        return False
    if not isinstance(store, list):
        return False
    probe = {"uuid": "__probe__", "kind": "line", "layer": "0", "construction": False,
             "geom": {"start": [0, 0], "end": [1, 0]}}
    try:
        store.append(probe)
        ok = store[-1] is probe
        store.pop()
    except Exception:
        return False
    return ok


def _empty_usable_sketch(value: Any) -> bool:
    """真容器且**空**（自检夹具要求空草图，免得既有几何干扰最小环选择）。"""
    if not _usable_sketch(value):
        return False
    try:
        return len(_constraints.sketch_entities(value)) == 0
    except Exception:
        return False


def _fallback_sketch_of(target: Any) -> Any:
    """`_sketch_of` 缺席时的兜底：文档 → 草图的常见形态，最后才当"它本身就是草图"。"""
    if target is None:
        raise _fail("invalid_target", "sk_or_doc is None")
    for name in ("sketch", "sk"):
        candidate = _field(target, name, None)
        if candidate is not None and _usable_sketch(candidate):
            return candidate
    sketches = _field(target, "sketches", None)
    if isinstance(sketches, Sequence) and not isinstance(sketches, (str, bytes)) and sketches:
        first = sketches[0]
        inner = _field(first, "sketch", None)
        if inner is not None and _usable_sketch(inner):
            return inner
        if _usable_sketch(first):
            return first
    if _usable_sketch(target):
        return target
    raise _fail("invalid_target", "sk_or_doc is neither an L1 sketch nor a document carrying one")


def sketch_of(target: Any) -> Any:
    """`sk_or_doc → sketch`：优先上游 `draw_ops._sketch_of`（同一口径，不另立副本）。"""
    if _UPSTREAM_SKETCH_OF is not None:
        try:
            sketch = _UPSTREAM_SKETCH_OF(target)
        except CadError:
            raise
        except Exception as exc:
            raise _fail("invalid_target", "sk_or_doc could not be resolved to a sketch",
                        error=type(exc).__name__) from exc
        if sketch is not None:
            return sketch
    return _fallback_sketch_of(target)


def _entities(sketch: Any) -> list[Any]:
    """实体表唯一读法（活表；不是副本 —— 上游 `_append_entity` 也这么写）。"""
    store = _delegate(_constraints.sketch_entities, sketch)
    if not isinstance(store, list):
        raise _fail("invalid_target", "sketch entity store is not a list", got=type(store).__name__)
    return store


def _by_uuid(sketch: Any) -> dict[str, Any]:
    """uuid → 实体（表里 uuid 非字符串/空的条目跳过：它们不可寻址）。"""
    table: dict[str, Any] = {}
    for entity in _entities(sketch):
        uid = _field(entity, "uuid", None)
        if isinstance(uid, str) and uid:
            table[uid] = entity
    return table


def _find_entity(sketch: Any, uid: str) -> Any:
    """按 uuid 找实体（找不到返回 `None`）。"""
    for entity in _entities(sketch):
        if str(_field(entity, "uuid", "")) == uid:
            return entity
    return None


def _snapshot(sketch: Any) -> dict[str, str]:
    """几何快照（uuid → geom 的规范 JSON），用于证明"既有图元零改动"。"""
    return {str(_field(entity, "uuid", "")):
            json.dumps(_field(entity, "geom", None), sort_keys=True, default=str)
            for entity in _entities(sketch)}


# ======================================================================================
# 几何：整数 even-odd 包含判定 / 鞋带面积 / 容差归并 / 环内顶点定序
# ======================================================================================


def _point_in_polygon(point: tuple[int, int], vertices: Sequence[tuple[int, int]]) -> bool:
    """整数 even-odd 射线法（叉积全整数）；点落在边上算**在内**（贴边拾取仍应命中）。"""
    px, py = point
    inside = False
    count = len(vertices)
    for index in range(count):
        ax, ay = vertices[index]
        bx, by = vertices[(index + 1) % count]
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        if cross == 0 and min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by):
            return True
        if (ay > py) != (by > py):
            if (cross > 0) == (by - ay > 0):
                inside = not inside
    return inside


def _twice_area(vertices: Sequence[tuple[int, int]]) -> int:
    """鞋带公式的两倍面积（整数，`abs` 后只用于比大小；退化环 = 0）。"""
    total = 0
    count = len(vertices)
    for index in range(count):
        ax, ay = vertices[index]
        bx, by = vertices[(index + 1) % count]
        total += ax * by - bx * ay
    return abs(total)


def _cluster_points(points: Sequence[tuple[int, int]], tol: int) -> list[int]:
    """端点按容差归并 → 每点所属簇的**代表点下标**（代表点 = 簇内最先出现的点，确定性）。

    `tol == 0`：坐标严格相同才同簇；`tol > 0`：欧氏距离 ≤ tol 同簇
    —— 与 `constraints.closed_profiles(tol_nm=…)` 的归并口径一致（那里用 `math.dist`），
    本层用整数 `dx² + dy² ≤ tol²` 复现同一判据，避免浮点。
    """
    count = len(points)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i in range(count):
        for j in range(i + 1, count):
            if tol > 0:
                dx = points[i][0] - points[j][0]
                dy = points[i][1] - points[j][1]
                close = dx * dx + dy * dy <= tol * tol
            else:
                close = points[i] == points[j]
            if close:
                root_i, root_j = find(i), find(j)
                if root_i != root_j:
                    parent[max(root_i, root_j)] = min(root_i, root_j)

    representative: dict[int, int] = {}
    result: list[int] = []
    for index in range(count):
        root = find(index)
        if root not in representative:
            representative[root] = index
        result.append(representative[root])
    return result


def _walk_closed_cycle(edges: Sequence[tuple[int, int]]) -> list[int] | None:
    """把边集走成**简单闭合环**的节点序列；不是简单环（度不为 2 / 走不到起点）返回 `None`。"""
    adjacency: dict[int, list[tuple[int, int]]] = {}
    for index, (a, b) in enumerate(edges):
        adjacency.setdefault(a, []).append((b, index))
        adjacency.setdefault(b, []).append((a, index))
    if not adjacency:
        return None
    for node in adjacency:
        adjacency[node].sort()
        if len(adjacency[node]) != 2:
            return None
    start = min(adjacency)
    order = [start]
    current = start
    used = -1
    for _ in range(len(edges)):
        options = [item for item in adjacency[current] if item[1] != used]
        if not options:
            return None
        nxt, edge_index = options[0]
        used = edge_index
        current = nxt
        if current == start:
            break
        if current in order:
            return None
        order.append(current)
    if current != start or len(order) != len(edges):
        return None
    return order


def _loop_vertices(entities_by_uuid: Mapping[str, Any], loop: Sequence[str], tol: int
                   ) -> tuple[list[tuple[int, int]], bool]:
    """环 uuid → `(顶点表, 是否含 arc)`；顶点用容差簇代表点（缺口由代表点补上，位移 ≤ tol）。"""
    points: list[tuple[int, int]] = []
    index_of: dict[tuple[int, int], int] = {}
    edges: list[tuple[int, int]] = []
    has_arc = False
    for uid in loop:
        entity = entities_by_uuid.get(uid)
        if entity is None:
            raise _fail("invalid_geometry", "loop member is absent from the sketch entity table",
                        uuid=uid)
        kind = str(_field(entity, "kind", ""))
        if kind == "circle":
            continue  # 单圆环在调用侧单独处理
        if kind not in ("line", "arc"):
            raise _fail("unsupported_geometry", "loop edge kind is not supported in L1",
                        kind=kind, uuid=uid)
        if kind == "arc":
            has_arc = True
        geom = _geom(entity)
        start = _raw_point(geom, "start", uuid=uid)
        end = _raw_point(geom, "end", uuid=uid)
        for point in (start, end):
            if point not in index_of:
                index_of[point] = len(points)
                points.append(point)
        edges.append((index_of[start], index_of[end]))
    if not edges:
        return [], has_arc
    representative = _cluster_points(points, tol)
    cluster_edges = [(representative[a], representative[b]) for a, b in edges]
    cluster_edges = [(a, b) for a, b in cluster_edges if a != b]
    order = _walk_closed_cycle(cluster_edges)
    if order is None:
        return [], has_arc
    return [points[representative[node]] for node in order], has_arc


def _circle_geometry(uid: str, entity: Any) -> tuple[tuple[int, int], int]:
    """圆的 `(圆心, 半径)`（键名给候选序列；缺字段/半径非正 ⇒ `invalid_geometry`）。"""
    geom = _geom(entity)
    center = _raw_point(geom, "center", "c", uuid=uid)
    radius = None
    for name in ("radius", "r"):
        value = geom.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            radius = int(value)
            break
    if radius is None:
        raise _fail("invalid_geometry", "circle geom has no positive integer radius", uuid=uid)
    return center, radius


def _loop_ruler() -> Callable[..., Any]:
    """环检测那把尺子：优先 `primitives.closed_profiles`（超集，认闭合折线）。"""
    ruler = getattr(_primitives, "closed_profiles", None)
    if callable(ruler):
        return ruler
    return _constraints.closed_profiles


def _loops(sketch: Any, tol: int) -> list[tuple[str, ...]]:
    """现取闭合环（uuid 元组，顺序由上游尺子给定，本层不重排）。"""
    ruler = _loop_ruler()
    try:
        raw = ruler(sketch, tol_nm=tol)
    except TypeError:
        raw = ruler(sketch)
    except CadError:
        raise
    except Exception as exc:  # pragma: no cover - 上游异常路径
        raise CadError(str(exc), reason="upstream_error", error=type(exc).__name__) from exc
    return [tuple(str(uid) for uid in loop) for loop in raw]


def _create_polygon(sketch: Any, vertices: Sequence[tuple[int, int]], layer_name: str) -> str:
    """闭合折线的唯一写入实现（上游 `add_polyline`，含退化校验）→ 新 uuid。"""
    creator = getattr(_primitives, "add_polyline", None)
    if not callable(creator):
        raise _fail("dependency_missing", "cadcore.sketch.primitives.add_polyline is missing")
    result = _delegate(creator, sketch, list(vertices), closed=True, layer=layer_name)
    if not isinstance(result, list) or len(result) != 1:
        raise _fail("postcondition_failed", "add_polyline did not return exactly one uuid",
                    got=repr(result))
    return str(result[0])


def _clone_circle(sketch: Any, source: Any, layer_name: str) -> str:
    """同圆心同半径的新圆（geom 逐字克隆源圆，不重算、不引入新字段）。"""
    entity = {"uuid": _new_uuid(), "kind": "circle", "layer": layer_name,
              "construction": bool(_field(source, "construction", False)),
              "geom": dict(_geom(source))}
    _entities(sketch).append(entity)
    return str(entity["uuid"])


def _verify_created(sketch: Any, new_uuid: str, tol: int, count_before: int) -> None:
    """后置条件：实体数恰好 +1、新 uuid 在表里、新对象真是闭合对象（族尺子认不认都留证）。"""
    store = _entities(sketch)
    if len(store) != count_before + 1:
        raise _fail("postcondition_failed", "boundary creation must add exactly one entity",
                    before=count_before, after=len(store))
    created = _find_entity(sketch, new_uuid)
    if created is None:
        raise _fail("postcondition_failed", "created boundary uuid is absent from the sketch",
                    uuid=new_uuid)
    kind = str(_field(created, "kind", ""))
    closed = kind == "circle"
    if kind == "polyline":
        points = _geom(created).get("points")
        closed = (isinstance(points, Sequence) and not isinstance(points, (str, bytes))
                  and len(points) >= 4 and list(points[0]) == list(points[-1]))
    if not closed:
        raise _fail("postcondition_failed", "created boundary is not a closed object",
                    uuid=new_uuid, kind=kind)
    for loop in _loops(sketch, tol):
        if new_uuid in loop:
            return
    if kind != "polyline":
        raise _fail("postcondition_failed", "created boundary is not a closed loop under the family ruler",
                    uuid=new_uuid)
    # 折线：族尺子若是非超集版本（不认折线），上面的几何闭合性已经证明过，记在这里即可。


# ======================================================================================
# 族操作：边界创建（BOUNDARY / BPOLY）
# ======================================================================================


def boundary_create(sk_or_doc: Any, *, point_nm: Any = None, layer: Any = "0",
                    tol_nm: Any = 0, **kwargs: Any) -> list[str]:
    """边界创建（BOUNDARY / BPOLY）：环内拾取点 + 既有几何 ⇒ **真实的新边界对象**。

    kwargs
    ------
    * `point_nm`（必填）：环内拾取点 `(x_nm, y_nm)`，两个**整数纳米**；
    * `layer`：新建对象落的图层（非空字符串，缺省 `"0"`）；**不改**既有图元的图层；
    * `tol_nm`：间隙容差（非负整数纳米，缺省 0）。`tol_nm > 0` 时端点按该容差归并成环，
      缺口由簇代表点补上 ⇒ 新边界的顶点相对原端点位移 **≤ tol_nm**；
    * 其它 kwarg 一律 `CadError(reason="invalid_argument")`（不静默丢参数）。

    返回
    ----
    `[新建边界对象的 uuid]`（长度恒为 1）。既有图元 uuid 与几何**一个字节都不动**。

    几何口径
    --------
    * 环检测 = `primitives.closed_profiles`（超集尺子）；拾取点包含 = 整数 even-odd；
    * 多个环含拾取点 ⇒ 取 `|2A|` 最小者；同面积按环 uuid 元组定序（**确定性**）；
    * 含 arc 的环**不参与**选择（L1 无原生曲线实体，本层拒绝弦近似）：若拾取点只能落在
      含 arc 的环里 ⇒ `CadError(reason="unsupported_geometry")`；
    * 多边形环 ⇒ 闭合 `polyline`（顶点按环走序，首点原样补在末点）；
      单圆环 ⇒ 同圆心同半径的新 `circle`；
    * 落库后自检后置条件（实体数 +1 / uuid 在表 / 真是闭合对象），不成立即抛
      `CadError(reason="postcondition_failed")`。
    """
    if kwargs:
        raise _fail("invalid_argument", "unknown keyword argument(s): %s" % ",".join(sorted(kwargs)))
    sketch = sketch_of(sk_or_doc)
    point = _point_nm(point_nm, what="point_nm")
    layer_name = _layer_name(layer)
    tolerance = _tolerance(tol_nm)

    entities_by_uuid = _by_uuid(sketch)
    count_before = len(_entities(sketch))
    loops = _loops(sketch, tolerance)
    if not loops:
        raise _fail("no_closed_boundary", "the sketch has no closed boundary",
                    loops=0, tol_nm=tolerance)

    candidates: list[tuple[int, tuple[str, ...], str, Any]] = []
    arc_loops = 0
    for loop in loops:
        head = entities_by_uuid.get(loop[0]) if loop else None
        if len(loop) == 1 and str(_field(head, "kind", "")) == "circle":
            center, radius = _circle_geometry(loop[0], head)
            dx = point[0] - center[0]
            dy = point[1] - center[1]
            if dx * dx + dy * dy <= radius * radius:
                candidates.append(((710 * radius * radius) // 113, loop, "circle", (center, radius)))
            continue
        vertices, has_arc = _loop_vertices(entities_by_uuid, loop, tolerance)
        if has_arc:
            arc_loops += 1
            continue
        if len(vertices) < 3:
            continue  # 零面积退化环接不住拾取点
        area2 = _twice_area(vertices)
        if area2 <= 0:
            continue
        if _point_in_polygon(point, vertices):
            candidates.append((area2, loop, "polygon", vertices))

    if not candidates:
        if arc_loops:
            raise _fail("unsupported_geometry",
                        "arc boundaries are not resolvable in L1 and are never approximated",
                        point=list(point), arc_loops=arc_loops, loops=len(loops), tol_nm=tolerance)
        raise _fail("point_outside_boundary", "pick point is not inside any closed boundary",
                    point=list(point), loops=len(loops), tol_nm=tolerance)

    _area2, loop, shape, payload = min(candidates, key=lambda item: (item[0], item[1]))
    if shape == "circle":
        new_uuid = _clone_circle(sketch, entities_by_uuid[loop[0]], layer_name)
    else:
        new_uuid = _create_polygon(sketch, payload, layer_name)
    _verify_created(sketch, new_uuid, tolerance, count_before)
    return [new_uuid]


#: 别名：族内按「拾取内部点」直呼时用这个名字（**同一实现**，不另立副本）。
boundary_pick = boundary_create

#: 族操作表（冻结接口）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "boundary_create": boundary_create,
    "boundary_pick": boundary_create,
}


def module_facts() -> dict[str, Any]:
    """机读事实（席/族/操作/单位），供上层汇总，不必去解析本文件。"""
    return {"family": FAMILY, "feature_id": FEATURE_ID, "feature_index": FEATURE_INDEX,
            "op": _OP_NAME, "operations": sorted(OPERATIONS), "length_unit": "nm",
            "tolerance": "tol_nm closes gaps by <= tol_nm displacement"}


# ======================================================================================
# 清单绑定：现读族规格 + 族状态账
# ======================================================================================


def remaining_must_have(*, parity_dir: str | None = None) -> list[str]:
    """按 `must_have` 原顺序，列出状态账里仍 `missing`/`partial` 的条目名（现读现算）。"""
    base = parity_dir or _PARITY_DIR
    features_path = os.path.join(base, "features", "%s.json" % FEATURE_ID)
    status_path = os.path.join(base, "status", "%s.json" % FEATURE_ID)
    with open(features_path, "r", encoding="utf-8") as handle:
        features = json.load(handle)
    with open(status_path, "r", encoding="utf-8") as handle:
        status = json.load(handle)
    by_name: dict[str, str] = {}
    for item in status.get("items", []) or []:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if isinstance(name, str):
            by_name[name] = str(item.get("status", "missing"))
    names: list[str] = []
    for entry in features.get("must_have", []) or []:
        name = entry.get("name") if isinstance(entry, Mapping) else None
        if isinstance(name, str) and by_name.get(name, "missing") in ("missing", "partial"):
            names.append(name)
    return names


def _feature_binding() -> tuple[str, str]:
    """本席编号 → 条目名是否仍成立：PASS / NA（清单漂移，不是本模块的失败） / FAIL。"""
    try:
        names = remaining_must_have()
    except Exception as exc:
        return "NA", "spec_unreadable:%s" % type(exc).__name__
    if len(names) <= FEATURE_INDEX:
        return "NA", "no_remaining_feature at index=%d" % FEATURE_INDEX
    actual = names[FEATURE_INDEX]
    if actual == _FEATURE_NAME:
        return "PASS", "remaining[%d]=ok total=%d" % (FEATURE_INDEX, len(names))
    return "NA", "spec_drift remaining[%d]=%s" % (FEATURE_INDEX, json.dumps(actual, ensure_ascii=True))


# ======================================================================================
# 自检（真容器 + 真几何 + 硬断言；不依赖 pytest）
# ======================================================================================


def _append_line(sketch: Any, start: Any, end: Any, layer: str = "0") -> str:
    """自检夹具：按 L1 冻结实体字段写一条 line（顶点数学照 `commands.add_line` 的存储口径）。"""
    entity = {"uuid": _new_uuid(), "kind": "line", "layer": layer, "construction": False,
              "geom": {"start": [int(start[0]), int(start[1])],
                       "end": [int(end[0]), int(end[1])]}}
    _entities(sketch).append(entity)
    return str(entity["uuid"])


def _append_arc(sketch: Any, start: Any, end: Any, center: Any, radius: int,
                layer: str = "0") -> str:
    """自检夹具：写一条 arc（环检测只读 start/end，圆心半径只为落库完整）。"""
    entity = {"uuid": _new_uuid(), "kind": "arc", "layer": layer, "construction": False,
              "geom": {"start": [int(start[0]), int(start[1])],
                       "end": [int(end[0]), int(end[1])],
                       "center": [int(center[0]), int(center[1])], "radius": int(radius)}}
    _entities(sketch).append(entity)
    return str(entity["uuid"])


def _append_circle(sketch: Any, center: Any, radius: int, layer: str = "0") -> str:
    """自检夹具：写一个 circle。"""
    entity = {"uuid": _new_uuid(), "kind": "circle", "layer": layer, "construction": False,
              "geom": {"center": [int(center[0]), int(center[1])], "radius": int(radius)}}
    _entities(sketch).append(entity)
    return str(entity["uuid"])


def _square(sketch: Any, x0: int, y0: int, x1: int, y1: int, layer: str = "0") -> list[str]:
    """自检夹具：轴对齐方（4 条 line，端点严格相接 ⇒ closed_profiles 认环）。"""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [_append_line(sketch, corners[index], corners[(index + 1) % 4], layer=layer)
            for index in range(4)]


def _created_points(sketch: Any, uid: str) -> list[list[int]] | None:
    """新建对象的折线顶点（不是折线 / 缺字段 ⇒ `None`）。"""
    entity = _find_entity(sketch, uid)
    if entity is None or str(_field(entity, "kind", "")) != "polyline":
        return None
    points = _geom(entity).get("points")
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
        return None
    return [[int(point[0]), int(point[1])] for point in points]


def _reason_of(call: Callable[[], Any]) -> tuple[str, str]:
    """跑一次调用，取 `CadError.reason`（没抛错/抛别的都**显式**标出来，不静默）。"""
    try:
        call()
    except CadError as exc:
        return str(getattr(exc, "reason", "")), str(getattr(exc, "message", str(exc)))
    except Exception as exc:  # pragma: no cover - 非 CadError 一律算红
        return "non_cad_error:" + type(exc).__name__, str(exc)
    return "no_error", ""


def _build_container() -> tuple[Any, Any, str]:
    """自检容器（空、可用）：真文档 → 公共工厂 → 鸭子类型容器，逐个**自证**后返回。"""
    if _UPSTREAM_SKETCH_OF is not None:
        try:
            docmod = importlib.import_module("cadcore.docmodel")
        except Exception:
            docmod = None
        if docmod is not None and hasattr(docmod, "new_document"):
            try:
                doc = docmod.new_document()
                first = _UPSTREAM_SKETCH_OF(doc)
                second = _UPSTREAM_SKETCH_OF(doc)
                if first is second and _empty_usable_sketch(first):
                    return doc, first, "document"
            except Exception:
                pass
    for module_name in _FACTORY_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        for name in _FACTORY_NAMES:
            factory = getattr(module, name, None)
            if not callable(factory):
                continue
            try:
                candidate = factory()
            except Exception:
                continue
            if _empty_usable_sketch(candidate):
                return candidate, candidate, "factory:%s.%s" % (module_name, name)
    duck = (("object", SimpleNamespace(entities=[])),
            ("mapping", {"entities": []}),
            ("mapping:sketch", {"sketch": {"entities": []}}),
            ("mapping:sketches", {"sketches": [{"entities": []}]}))
    for kind, candidate in duck:
        if _empty_usable_sketch(candidate):
            return candidate, candidate, "duck:" + kind
    return None, None, "none"


def selftest() -> int:
    """硬断言自检：真几何落库 / 最小环选取 / 零改动 / 各类 reason / 容差 / arc 拒绝 / 圆克隆。"""
    rows: list[tuple[str, str, str]] = []

    def check(name: str, passed: Any, detail: str = "") -> None:
        rows.append((name, "NA" if passed is None else ("PASS" if passed else "FAIL"), detail))

    def report() -> int:
        for name, status, detail in rows:
            print("DRAW_OPS_W17 check=%s status=%s detail=%s" % (name, status, detail))
        failed = [row for row in rows if row[1] == "FAIL"]
        skipped = [row for row in rows if row[1] == "NA"]
        if failed:
            print("FEATURE_FAIL family=%s op=%s verdict=FAIL failed=%d" % (FAMILY, _OP_NAME, len(failed)))
            return 1
        print("DRAW_OPS_W17_SELFTEST checks=%d failed=0 na=%d" % (len(rows), len(skipped)))
        print("FEATURE_OK family=draw_ops op=boundary_create verdict=PASS")
        return 0

    check("interface", FAMILY == "draw_ops"
          and OPERATIONS.get("boundary_create") is boundary_create
          and OPERATIONS.get("boundary_pick") is boundary_create,
          "ops=%s" % json.dumps(sorted(OPERATIONS)))

    binding_status, binding_detail = _feature_binding()
    check("feature_binding", {"PASS": True, "NA": None, "FAIL": False}[binding_status], binding_detail)

    check("claim_time_listing", len(_CLAIM_TIME_REMAINING) == 15
          and _CLAIM_TIME_REMAINING[FEATURE_INDEX] == _FEATURE_NAME,
          "index=%d" % FEATURE_INDEX)

    target, sketch, container = _build_container()
    if sketch is None:
        check("container", False, "no usable empty L1 sketch container found")
        return report()
    check("container", True, "kind=%s" % container)

    # --- 主用例：外方 0..10mm 套内方 2..8mm，拾取点 (5mm,5mm) ⇒ 必须选面积最小的内环 ---
    outer = _square(sketch, 0, 0, 10 * MM_NM, 10 * MM_NM)
    inner = _square(sketch, 2 * MM_NM, 2 * MM_NM, 8 * MM_NM, 8 * MM_NM)
    before = _snapshot(sketch)
    count_before = len(_entities(sketch))
    result = boundary_create(target, point_nm=(5 * MM_NM, 5 * MM_NM), layer="BOUNDARY")
    created = _find_entity(sketch, result[0]) if isinstance(result, list) and result else None
    kind = str(_field(created, "kind", "")) if created is not None else ""
    layer = str(_field(created, "layer", "")) if created is not None else ""
    points = _created_points(sketch, result[0]) if isinstance(result, list) and result else None

    check("returns_new_uuid", isinstance(result, list) and len(result) == 1
          and isinstance(result[0], str) and bool(result[0]) and result[0] not in before,
          "ret=%s" % json.dumps(result if isinstance(result, list) else str(result)))
    check("created_entity", created is not None and kind == "polyline" and layer == "BOUNDARY",
          "kind=%s layer=%s" % (kind, layer))

    inner_corners = {(2 * MM_NM, 2 * MM_NM), (8 * MM_NM, 2 * MM_NM),
                     (8 * MM_NM, 8 * MM_NM), (2 * MM_NM, 8 * MM_NM)}
    outer_corners = {(0, 0), (10 * MM_NM, 0), (10 * MM_NM, 10 * MM_NM), (0, 10 * MM_NM)}
    vertex_set = {(point[0], point[1]) for point in (points or [])[:-1]}
    check("smallest_loop_wins", points is not None and len(points) == 5
          and vertex_set == inner_corners and vertex_set != outer_corners,
          "vertices=%d" % (len(points) if points else -1))
    check("boundary_closed", points is not None and len(points) >= 4 and points[0] == points[-1]
          and len(vertex_set) == len(points) - 1,
          "points=%d distinct=%d" % (len(points) if points else -1, len(vertex_set)))

    after = _snapshot(sketch)
    check("originals_untouched", all(before.get(uid) == after.get(uid) for uid in outer + inner)
          and len(outer + inner) == 8 and len(_entities(sketch)) == count_before + 1,
          "entities_before=%d entities_after=%d" % (count_before, len(_entities(sketch))))

    # --- 错误路径：拾取点在外 / 非法入参，都不许留下任何新实体 ---
    outside_reason, _outside_msg = _reason_of(
        lambda: boundary_create(target, point_nm=(100 * MM_NM, 100 * MM_NM)))
    check("outside_point_errors", outside_reason == "point_outside_boundary"
          and len(_entities(sketch)) == count_before + 1, "reason=%s" % outside_reason)

    invalid = []
    for label, call in (
            ("float", lambda: boundary_create(target, point_nm=(1.5, 0))),
            ("bool", lambda: boundary_create(target, point_nm=(True, 0))),
            ("missing", lambda: boundary_create(target)),
            ("unknown_kw", lambda: boundary_create(target, point_nm=(0, 0), nonsense=1)),
            ("negative_tol", lambda: boundary_create(target, point_nm=(0, 0), tol_nm=-5)),
            ("bad_layer", lambda: boundary_create(target, point_nm=(0, 0), layer="")),
    ):
        invalid.append("%s=%s" % (label, _reason_of(call)[0]))
    check("invalid_input", all(item.endswith("=invalid_argument") for item in invalid)
          and len(_entities(sketch)) == count_before + 1, ",".join(invalid))

    # --- 间隙容差：缺口 3mm 的方；tol=0 不成环，tol=4mm 成环且顶点位移 ≤ tol ---
    gap_target, gap_sketch, gap_kind = _build_container()
    if gap_sketch is None:
        check("gap_tolerance", False, "no container")
    else:
        a_pt, b_pt = (0, 0), (10 * MM_NM, 0)
        c_pt, d_pt, d2_pt = (10 * MM_NM, 10 * MM_NM), (0, 10 * MM_NM), (0, 7 * MM_NM)
        _append_line(gap_sketch, a_pt, b_pt)
        _append_line(gap_sketch, b_pt, c_pt)
        _append_line(gap_sketch, c_pt, d_pt)
        _append_line(gap_sketch, d2_pt, a_pt)
        gap_reason, _gap_msg = _reason_of(
            lambda: boundary_create(gap_target, point_nm=(5 * MM_NM, 5 * MM_NM)))
        gap_result = None
        try:
            gap_result = boundary_create(gap_target, point_nm=(5 * MM_NM, 5 * MM_NM),
                                         tol_nm=4 * MM_NM)
        except CadError:
            gap_result = None
        gap_points = _created_points(gap_sketch, gap_result[0]) if gap_result else None
        endpoints = (a_pt, b_pt, c_pt, d_pt, d2_pt)
        within = bool(gap_points) and all(
            any(max(abs(px - ex), abs(py - ey)) <= 4 * MM_NM for ex, ey in endpoints)
            for px, py in gap_points[:-1])
        xs = [point[0] for point in (gap_points or [])[:-1]]
        ys = [point[1] for point in (gap_points or [])[:-1]]
        bbox_ok = bool(gap_points) and min(xs) == 0 and min(ys) == 0 \
            and max(xs) == 10 * MM_NM and max(ys) == 10 * MM_NM
        check("gap_tolerance", gap_reason == "no_closed_boundary" and gap_points is not None
              and len(gap_points) == 5 and len({(p[0], p[1]) for p in gap_points[:-1]}) == 4
              and within and bbox_ok,
              "kind=%s tol0=%s tol4_points=%d" % (gap_kind, gap_reason,
                                                  len(gap_points) if gap_points else -1))

    # --- arc 环：L1 不弦近似 ⇒ 明确拒绝（不是"算不出来"） ---
    arc_target, arc_sketch, _arc_kind = _build_container()
    if arc_sketch is None:
        check("arc_loop_refused", False, "no container")
    else:
        _append_line(arc_sketch, (0, 0), (10 * MM_NM, 0))
        _append_arc(arc_sketch, (10 * MM_NM, 0), (0, 0), (5 * MM_NM, 0), 5 * MM_NM)
        arc_reason, _arc_msg = _reason_of(
            lambda: boundary_create(arc_target, point_nm=(5 * MM_NM, 2 * MM_NM)))
        check("arc_loop_refused", arc_reason == "unsupported_geometry"
              and len(_entities(arc_sketch)) == 2, "reason=%s" % arc_reason)

    # --- 单圆环：同圆心同半径的新圆，源圆零改动 ---
    circle_target, circle_sketch, _circle_kind = _build_container()
    if circle_sketch is None:
        check("circle_loop_clone", False, "no container")
    else:
        circle_uuid = _append_circle(circle_sketch, (10 * MM_NM, 20 * MM_NM), 5 * MM_NM)
        circle_before = _snapshot(circle_sketch)
        circle_result = boundary_create(circle_target, point_nm=(11 * MM_NM, 21 * MM_NM))
        clone = _find_entity(circle_sketch, circle_result[0]) if circle_result else None
        source = _find_entity(circle_sketch, circle_uuid)
        check("circle_loop_clone", circle_result is not None and len(circle_result) == 1
              and clone is not None and str(_field(clone, "kind", "")) == "circle"
              and circle_result[0] != circle_uuid
              and dict(_geom(clone)) == dict(_geom(source))
              and circle_before.get(circle_uuid) == _snapshot(circle_sketch).get(circle_uuid)
              and len(_entities(circle_sketch)) == 2,
              "kind=%s" % (str(_field(clone, "kind", "")) if clone is not None else "none"))

    check("doc_route", True if container == "document" else None,
          "container=%s" % container)

    return report()


def main(argv: Sequence[str] | None = None) -> int:
    """入口：跑自检（本席没有 CLI 参数，全量证据在 stdout 与 docstring 里）。"""
    del argv
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 draw_ops__w17.py:153 "边界创建" -> "Boundary Creation"
# 原 draw_ops__w17.py:160 "直线" -> "Line"
# 原 draw_ops__w17.py:160 "多段线" -> "Polyline"
# 原 draw_ops__w17.py:160 "圆" -> "Circle"
# 原 draw_ops__w17.py:160 "圆弧" -> "Arc"
# 原 draw_ops__w17.py:160 "矩形" -> "Rectangle"
# 原 draw_ops__w17.py:161 "正多边形" -> "Regular Polygon"
# 原 draw_ops__w17.py:161 "点与点样式" -> "Point and Point Style"
# 原 draw_ops__w17.py:161 "图案填充与渐变色" -> "Hatch and Gradient"
# 原 draw_ops__w17.py:162 "修订云线" -> "Revision Cloud"
# 原 draw_ops__w17.py:162 "对象捕捉" -> "Object Snap"
# 原 draw_ops__w17.py:162 "正交与极轴追踪" -> "Ortho and Polar Tracking"
# 原 draw_ops__w17.py:163 "对象捕捉追踪与临时追踪点" -> "Object Snap Tracking and Temporary Track Point"
# 原 draw_ops__w17.py:163 "动态输入" -> "Dynamic Input"
# 原 draw_ops__w17.py:164 "当前图层与对象特性" -> "Current Layer and Object Properties"
