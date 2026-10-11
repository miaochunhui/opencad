#!/usr/bin/env python3
"""DRAW-OPS-EXTRA 族模块：补 `draw_ops` 规格里 4 条 **unprobed** 能力（状态 + 几何助手）。

被补的 4 条（证据：`oss-alternatives/docs/research/cad-parity/spec/draw_ops.json` 的 `must_have`
里 `api="NA"`、`reuse="NA"/部分覆盖 的条目）：

    1. 修订云线（REVCLOUD；弧长 A、矩形/多边形 O）      -> `revcloud` / `revcloud_rect`
    2. 对象捕捉开关（总开关 + 参与类型）                -> `osnap_toggle` / `osnap_kinds_set`
    3. 正交与极轴追踪（正交锁定 / 极轴增量）            -> `ortho_polar_set` / `ortho_polar_apply`
    4. 动态输入（长度 + 角度 -> 落点）                  -> `dyninput_set` / `dyninput_point`
    另加一次**对象捕捉真几何**（规格里那条 osnap 子句的几何腿）-> `osnap_probe`

族接口（冻结，与 `draw_ops` / `draw_extra` / `precision_ops` 逐字一致）：

    FAMILY = "draw_ops_extra"
    OPERATIONS: dict[str, Callable]
    每个操作  op(sk_or_doc, **kwargs) -> list[str]

返回值口径（全族统一）：

* **状态助手**（`*_set` / `*_toggle`）返回被改动的**状态记录 uuid**（记录落在本模块自己的状态表里，
  调用方可在 `sketch["extra"]["draw_ops_extra"]["modes"][name]["uuid"]` 上解析到），
  语义与 `precision_ops.snap_enable` 等一致（返回模式记录 uuid，不是实体 uuid）；
* **几何助手**（`revcloud*` / `osnap_probe` / `ortho_polar_apply` / `dyninput_point`）返回**本次新建的
  实体 uuid**，每个 uuid 都能在草图里解析到，实体按 L1 冻结字段落库
  `{uuid, kind, layer, construction, geom}`（不自造第六个字段 ⇒ `docmodel.check_invariants()` 不被污染）；
* 「没做」绝不写进返回值：没命中 / 被开关挡住 / 追踪不上，一律抛
  `cadcore.errors.CadError(reason=...)`，**不返回空列表冒充成功**。

输入口径：

* `sk_or_doc` 接受三种形态：① `docmodel.Document`；② L1 文档映射（含 `sketches` 键）；
  ③ 裸草图（含 `entities` 键的映射）。①② 用 `sketch_index`（缺省 0）选草图。
* 长度一律**整数纳米**（`bool` 与"非整数值"浮点一律拒绝，抛 `CadError`）；角度用 `*_deg`（float）。
* 本模块**自洽**：只 import `cadcore.errors` 与 `cadcore.sketch.constraints`（后者是实体字段名与
  `new_uuid()` 的唯一真源，与 `draw_extra` 同一口径），**不引用任何尚未落盘的名字**；
  osnap / ortho / polar 的几何在本文件内自成一套纯函数（不依赖 `cadcore.precision` 是否可用）。

状态表（本模块自己的，落在草图映射的 `extra` 容器里，不占实体表）：

    {"schema": 1, "modes": {"osnap"|"ortho"|"polar"|"dyninput":
        {"uuid": str, "enabled": bool, "config": {...}}}}

出厂值（唯一真源 `_default_config`）：osnap 关（容差 5 mm、6 类全开）、ortho 关（容差 3°）、
polar 关（增量 15/30/45/90°，容差 3°）、dyninput 开（字段 length/angle）。状态表在第一次状态
写入（或 `dyninput_point`）时按需建出；**没建表 = 没配过** ⇒ 正交/极轴未启用（`no_tracking_mode`）。

已知边界（显式写清，不静默近似）：

* `revcloud` 的云弧是**折线采样**的近似（L1 无 bulge 字段）：每个弦长 ≤ `arc_length_nm` 的半圆
  用 `bulge_samples` 段采样，**不是**精确圆弧；凸向按路径朝向算（闭合路径取"朝外"，开路径取左侧）；
  闭合路径**不重复末点**（`closed=True` 已经表达闭合）；
* `osnap_probe` 支持 kind：endpoint / midpoint / center / quadrant / intersection / nearest；
  实体种类支持 line / polyline / circle / arc / ellipse / point，**其它 kind 不产生候选**（不报错、
  也不假装有候选）；`intersection` 只做**线段 × 线段**（圆/弧的交点不在本模块草率实现）；
  `nearest` 对椭圆用参数角近似（docstring 已声明）；
* `ortho_polar_apply` 在容差外 ⇒ `CadError(reason="not_tracked")`（**不**静默返回原点）；
* `dyninput_point` 受 dyninput 开关约束（关掉 ⇒ `dyninput_disabled`），换算关系与规格测试向量一致：
  长度 10 mm + 角度 90° ⇒ 落点 (0, 10000000) nm。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/ops/draw_ops_extra.py

文末打印 `EXTRA_OPS verdict=PASS|FAIL ops=N`。中文只出现在 docstring/注释里
（`tools/cad/i18n_gate.py` 的口径）。
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import MutableMapping
from typing import Any, Callable, Mapping, Sequence

import os as _os
import sys as _sys

_PKG_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _PKG_ROOT not in _sys.path:  # 直跑脚本与包内导入共用同一条绝对导入路径
    _sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError
from cadcore.sketch import constraints as _constraints

__all__ = [
    "FAMILY", "OPERATIONS", "NM_PER_MM", "STATE_KEY", "SNAP_KINDS", "TRACK_MODES", "DYN_FIELDS",
    "revcloud", "revcloud_rect", "osnap_toggle", "osnap_kinds_set", "osnap_probe",
    "ortho_polar_set", "ortho_polar_apply", "dyninput_set", "dyninput_point",
    "module_facts", "selftest",
]

FAMILY = "draw_ops_extra"
NM_PER_MM = 1_000_000
STATE_KEY = "draw_ops_extra"
SCHEMA = 1
SNAP_KINDS = ("endpoint", "midpoint", "center", "quadrant", "intersection", "nearest")
TRACK_MODES = ("ortho", "polar")
DYN_FIELDS = ("length", "angle", "x", "y")
DEFAULT_TOLERANCE_NM = 5 * NM_PER_MM
DEFAULT_TOLERANCE_DEG = 3.0
DEFAULT_POLAR_INCREMENTS_DEG = (15.0, 30.0, 45.0, 90.0)
DEFAULT_LAYER = "0"
_EPS = 1e-9


# --------------------------------------------------------------------------------------
# 参数校验（唯一的失败入口：一律 CadError + 机器可读 reason）
# --------------------------------------------------------------------------------------


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _nm(value: Any, what: str, *, positive: bool = False) -> int:
    """整数纳米校验（bool 拒绝；整数值浮点接受；其余抛 `not_integer_nm`）。"""
    if isinstance(value, bool):
        raise _fail("not_integer_nm", "nanometre value must be an int", what=what, value=repr(value))
    if isinstance(value, int):
        out = int(value)
    elif isinstance(value, float) and value.is_integer():
        out = int(value)
    else:
        raise _fail("not_integer_nm", "nanometre value must be an int", what=what, value=repr(value))
    if positive and out <= 0:
        raise _fail("non_positive_nm", "nanometre value must be > 0", what=what, value=out)
    return out


def _deg(value: Any, what: str = "angle_deg") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail("not_a_number", "angle must be a real number", what=what, value=repr(value))
    out = float(value)
    if not math.isfinite(out):
        raise _fail("not_a_number", "angle must be finite", what=what, value=repr(value))
    return out


def _point(value: Any, what: str) -> tuple[int, int]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise _fail("bad_point", "point must be a 2-sequence of nanometres", what=what, value=repr(value))
    return (_nm(value[0], what + ".x"), _nm(value[1], what + ".y"))


def _points(value: Any, what: str) -> list[tuple[int, int]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) < 2:
        raise _fail("bad_points", "point list must hold >= 2 nanometre points", what=what, value=repr(value))
    return [_point(item, "%s[%d]" % (what, index)) for index, item in enumerate(value)]


def _flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise _fail("not_a_bool", "flag must be a bool", what=what, value=repr(value))
    return bool(value)


def _layer(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("bad_layer", "layer must be a non-empty string", value=repr(value))
    return value


def _token(value: Any, what: str, allowed: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _fail("unknown_token", "value is not in the allowed set", what=what,
                    value=repr(value), allowed=list(allowed))
    return value


def _tolerance_nm(value: Any) -> int:
    tol = _nm(value, "tolerance_nm")
    if tol < 0:
        raise _fail("negative_tolerance", "tolerance_nm must be >= 0", value=tol)
    return tol


def _tolerance_deg(value: Any) -> float:
    tol = _deg(value, "tolerance_deg")
    if tol < 0.0 or tol > 90.0:
        raise _fail("bad_tolerance_deg", "tolerance_deg must be within [0, 90]", value=tol)
    return tol


def _increments_deg(value: Any) -> list[float]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise _fail("bad_increments", "increments_deg must be a non-empty sequence", value=repr(value))
    out: list[float] = []
    for item in value:
        angle = _deg(item, "increments_deg[]")
        if not (0.0 < angle < 180.0):
            raise _fail("bad_increments", "each increment must be within (0, 180)", value=angle)
        out.append(angle)
    if len(set(out)) != len(out):
        raise _fail("bad_increments", "increments_deg must not repeat", value=sorted(out))
    return sorted(out)


def _round_nm(value: float) -> int:
    """浮点纳米 → 整数纳米（非有限值显式报错，不静默变 0）。"""
    if not math.isfinite(value):
        raise _fail("not_finite", "geometry produced a non-finite coordinate", value=repr(value))
    return int(round(value))


# --------------------------------------------------------------------------------------
# 草图 / 实体适配（模型解释的唯一入口；字段名真源 = cadcore.sketch.constraints）
# --------------------------------------------------------------------------------------


def _sketch(sk_or_doc: Any, *, sketch_index: int = 0) -> Mapping[str, Any]:
    """`sk_or_doc` → 草图映射（三种入参形态见模块 docstring；取不到抛 `no_sketch`）。"""
    if isinstance(sk_or_doc, Mapping):
        if "entities" in sk_or_doc:
            return sk_or_doc
        sketches: Any = sk_or_doc.get("sketches")
    else:
        sketches = getattr(sk_or_doc, "sketches", None)
        if sketches is None and hasattr(sk_or_doc, "entities"):
            return sk_or_doc
    if isinstance(sketches, (str, bytes)) or not isinstance(sketches, Sequence) or not sketches:
        raise _fail("no_sketch", "source exposes no sketch to draw into")
    if isinstance(sketch_index, bool) or not isinstance(sketch_index, int) or sketch_index < 0:
        raise _fail("bad_sketch_index", "sketch_index must be a non-negative int", value=repr(sketch_index))
    if sketch_index >= len(sketches):
        raise _fail("no_sketch", "sketch_index out of range", sketch_index=sketch_index,
                    sketch_count=len(sketches))
    sketch = sketches[sketch_index]
    if not isinstance(sketch, Mapping):
        raise _fail("sketch_not_mapping", "sketch entry is not an L1 mapping",
                    sketch_index=sketch_index, value_type=type(sketch).__name__)
    return sketch


def _entities(sk: Any) -> list[Any]:
    try:
        return _constraints.sketch_entities(sk)
    except CadError as exc:
        raise _fail("sketch_invalid", "sketch has no usable entity list", detail=exc.message) from exc


def _geom_of(entity: Any) -> Mapping[str, Any]:
    geom = entity.get("geom") if isinstance(entity, Mapping) else None
    if not isinstance(geom, Mapping):
        return {}
    return geom


def _append(sk: Any, *, kind: str, geom: dict[str, Any], layer: str, construction: bool) -> str:
    """按 L1 冻结字段追加实体并返回 uuid（字段集与 `edit/modify.py` 逐字一致）。"""
    entity = {
        "uuid": _constraints.new_uuid(),
        "kind": kind,
        "layer": layer,
        "construction": bool(construction),
        "geom": geom,
    }
    _entities(sk).append(entity)
    return str(entity["uuid"])


def _num(geom: Mapping[str, Any], key: str) -> float | None:
    value = geom.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


# --------------------------------------------------------------------------------------
# 状态表（本模块自己的；osnap / ortho / polar / dyninput 四个模式记录）
# --------------------------------------------------------------------------------------


def _default_config(name: str) -> dict[str, Any]:
    """单个模式的出厂配置（**唯一真源**：新表只从这里长，别在调用点手搓）。"""
    if name == "osnap":
        return {"tolerance_nm": int(DEFAULT_TOLERANCE_NM), "kinds": {k: True for k in SNAP_KINDS}}
    if name == "ortho":
        return {"tolerance_deg": float(DEFAULT_TOLERANCE_DEG)}
    if name == "polar":
        return {"increments_deg": [float(a) for a in DEFAULT_POLAR_INCREMENTS_DEG],
                "tolerance_deg": float(DEFAULT_TOLERANCE_DEG)}
    if name == "dyninput":
        return {"fields": ["length", "angle"]}
    raise _fail("unknown_mode", "mode is not a known state mode", value=repr(name),
                known=list(TRACK_MODES) + ["osnap", "dyninput"])


def _empty_store() -> dict[str, Any]:
    return {"schema": int(SCHEMA), "modes": {}}


def _ensure(store: dict[str, Any]) -> dict[str, Any]:
    """就地补齐缺省记录并校验结构（坏结构报 `corrupt_state`，不静默重置成缺省）。"""
    schema = store.get("schema")
    if schema is None:
        store["schema"] = int(SCHEMA)
    elif schema != SCHEMA:
        raise _fail("unsupported_schema", "state schema is not supported",
                    schema=repr(schema), supported=int(SCHEMA))
    modes = store.get("modes")
    if modes is None:
        modes = {}
        store["modes"] = modes
    if not isinstance(modes, MutableMapping):
        raise _fail("corrupt_state", "state 'modes' must be a mapping",
                    value_type=type(modes).__name__)
    for name in ("osnap", "ortho", "polar", "dyninput"):
        record = modes.get(name)
        if record is None:
            modes[name] = {"uuid": _constraints.new_uuid(), "name": name,
                           "enabled": name == "dyninput", "config": _default_config(name)}
            continue
        if not isinstance(record, MutableMapping) or not isinstance(record.get("uuid"), str):
            raise _fail("corrupt_state", "state mode record is malformed",
                        mode=name, value=repr(record))
        if not isinstance(record.get("enabled"), bool):
            record["enabled"] = bool(name == "dyninput")
        if not isinstance(record.get("config"), MutableMapping):
            record["config"] = _default_config(name)
    return store


def _state(sk_or_doc: Any, *, create: bool = True, sketch_index: int = 0) -> dict[str, Any] | None:
    """`sk_or_doc` → 状态表（`create=False` 时没表返回 `None`，不建表）。"""
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    if not isinstance(sk, MutableMapping):
        raise _fail("state_not_writable", "sketch is not a mutable mapping",
                    value_type=type(sk).__name__)
    extra = sk.get("extra")
    if extra is None:
        if not create:
            return None
        extra = {}
        sk["extra"] = extra
    if not isinstance(extra, MutableMapping):
        raise _fail("corrupt_state", "sketch 'extra' container is not a mapping",
                    value_type=type(extra).__name__)
    store = extra.get(STATE_KEY)
    if store is None:
        if not create:
            return None
        store = _empty_store()
        extra[STATE_KEY] = store
    if not isinstance(store, MutableMapping):
        raise _fail("corrupt_state", "state store is not a mapping",
                    value_type=type(store).__name__)
    return _ensure(store)


def _mode(store: Mapping[str, Any], name: str) -> MutableMapping[str, Any]:
    record = store["modes"][name]
    if not isinstance(record, MutableMapping):  # pragma: no cover - _ensure 已挡
        raise _fail("corrupt_state", "state mode record is malformed", mode=name)
    return record


# --------------------------------------------------------------------------------------
# 几何：修订云线
# --------------------------------------------------------------------------------------


def _signed_area(ring: Sequence[tuple[float, float]]) -> float:
    total = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _dedupe(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for point in points:
        if not out or out[-1] != point:
            out.append(point)
    return out


def _scallop(ring: Sequence[tuple[int, int]], arc_length_nm: int, samples: int,
             *, closed: bool) -> list[tuple[int, int]]:
    """把折线路径换成"每弦 ≤ arc_length_nm 的半圆"云线折线（凸向：闭合朝外 / 开路径朝左）。"""
    floats = [(float(x), float(y)) for x, y in ring]
    if closed:
        sign = -1.0 if _signed_area(floats) > 0.0 else 1.0  # CCW 内部在左 ⇒ 朝外 = 右转 90°
    else:
        sign = 1.0
    out: list[tuple[int, int]] = []
    for index in range(len(floats) - 1):
        ax, ay = floats[index]
        bx, by = floats[index + 1]
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len <= 0.0:
            continue
        chunks = max(1, int(math.ceil(seg_len / float(arc_length_nm) - _EPS)))
        ux, uy = (bx - ax) / seg_len, (by - ay) / seg_len
        nx, ny = sign * (-uy), sign * ux
        step = seg_len / chunks
        radius = step / 2.0
        for chunk in range(chunks):
            start = chunk * step
            px, py = ax + ux * start, ay + uy * start
            qx, qy = ax + ux * (start + step), ay + uy * (start + step)
            mx, my = (px + qx) / 2.0, (py + qy) / 2.0
            for sample in range(samples + 1):
                theta = -math.pi / 2.0 + math.pi * sample / samples
                out.append((_round_nm(mx + radius * (math.sin(theta) * ux + math.cos(theta) * nx)),
                            _round_nm(my + radius * (math.sin(theta) * uy + math.cos(theta) * ny))))
    out = _dedupe(out)
    if closed and len(out) > 2 and out[0] == out[-1]:
        out = out[:-1]  # 闭合由 geom["closed"] 表达，不重复末点
    return out


def revcloud(sk_or_doc: Any, *, points_nm: Any, arc_length_nm: Any, closed: bool = True,
             bulge_samples: int = 8, layer: str = DEFAULT_LAYER,
             construction: bool = False, sketch_index: int = 0) -> list[str]:
    """修订云线（折线路径）：返回 `[新建 polyline 实体 uuid]`（长度恒为 1）。

    路径按段切成 `chunks = ceil(段长 / arc_length_nm)` 个弦，每个弦鼓成半径 = 弦长/2 的半圆
    （`bulge_samples` 段采样）⇒ 云弧弦长 ≤ `arc_length_nm`。凸向：闭合路径朝外，开路径朝左。
    点数 < 2、`closed=True` 且顶点 < 3 或首尾重合、`arc_length_nm <= 0` ⇒ `CadError`（不落几何）。
    """
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    points = _points(points_nm, "points_nm")
    is_closed = _flag(closed, what="closed")
    arc_length = _nm(arc_length_nm, "arc_length_nm", positive=True)
    if isinstance(bulge_samples, bool) or not isinstance(bulge_samples, int) or bulge_samples < 2:
        raise _fail("bad_bulge_samples", "bulge_samples must be an int >= 2", value=repr(bulge_samples))
    layer_name = _layer(layer)
    construction_flag = _flag(construction, what="construction")
    if is_closed:
        if len(points) < 3:
            raise _fail("bad_revcloud_path", "closed cloud needs >= 3 distinct vertices",
                        vertices=len(points))
        if points[0] == points[-1]:
            points = points[:-1]
            if len(points) < 3:
                raise _fail("bad_revcloud_path", "closed cloud needs >= 3 distinct vertices",
                            vertices=len(points) + 1)
        ring = points + [points[0]]
    else:
        ring = list(points)
    for index in range(len(ring) - 1):
        if ring[index] == ring[index + 1]:
            raise _fail("bad_revcloud_path", "adjacent vertices must differ", index=index)
    polyline = _scallop(ring, arc_length, bulge_samples, closed=is_closed)
    if len(polyline) < 2:
        raise _fail("degenerate_revcloud", "scallop produced fewer than 2 vertices",
                    vertices=len(polyline))
    uuid_ = _append(sk, kind="polyline",
                    geom={"points": [[int(x), int(y)] for x, y in polyline], "closed": is_closed},
                    layer=layer_name, construction=construction_flag)
    return [uuid_]


def revcloud_rect(sk_or_doc: Any, *, center_nm: Any, width_nm: Any, height_nm: Any,
                  arc_length_nm: Any, rotation_deg: Any = 0.0, bulge_samples: int = 8,
                  layer: str = DEFAULT_LAYER, construction: bool = False,
                  sketch_index: int = 0) -> list[str]:
    """修订云线（矩形 O 选项）：绕 `center_nm` 逆时针转 `rotation_deg` 的矩形路径 → 云线。

    返回 `[新建 polyline 实体 uuid]`；`width_nm`/`height_nm` 必须 > 0，`arc_length_nm` > 0。
    """
    center = _point(center_nm, "center_nm")
    width = _nm(width_nm, "width_nm", positive=True)
    height = _nm(height_nm, "height_nm", positive=True)
    arc_length = _nm(arc_length_nm, "arc_length_nm", positive=True)
    angle = math.radians(_deg(rotation_deg, "rotation_deg"))
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    hx, hy = width / 2.0, height / 2.0
    corners: list[tuple[int, int]] = []
    for dx, dy in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)):
        corners.append((_round_nm(center[0] + dx * cos_a - dy * sin_a),
                        _round_nm(center[1] + dx * sin_a + dy * cos_a)))
    return revcloud(sk_or_doc, points_nm=corners, arc_length_nm=arc_length, closed=True,
                    bulge_samples=bulge_samples, layer=layer, construction=construction,
                    sketch_index=sketch_index)


# --------------------------------------------------------------------------------------
# 几何：对象捕捉（纯函数候选集 + 落点）
# --------------------------------------------------------------------------------------


def _segments_of(entity: Mapping[str, Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    kind = str(entity.get("kind") or "")
    geom = _geom_of(entity)
    if kind == "line":
        x1, y1 = _num(geom, "x1"), _num(geom, "y1")
        x2, y2 = _num(geom, "x2"), _num(geom, "y2")
        if None in (x1, y1, x2, y2):
            return []
        return [((x1, y1), (x2, y2))]
    if kind == "polyline":
        raw = geom.get("points")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            return []
        pts: list[tuple[float, float]] = []
        for item in raw:
            if not isinstance(item, Sequence) or len(item) != 2:
                return []
            x, y = _num({"x": item[0]}, "x"), _num({"y": item[1]}, "y")
            if x is None or y is None:
                return []
            pts.append((x, y))
        segs = list(zip(pts, pts[1:]))
        if bool(geom.get("closed")) and len(pts) > 2:
            segs.append((pts[-1], pts[0]))
        return segs
    return []


def _arc_sweep(geom: Mapping[str, Any]) -> tuple[float, float] | None:
    start = _num(geom, "start_deg")
    end = _num(geom, "end_deg")
    if start is None or end is None:
        return None
    sweep = (end - start) % 360.0
    if sweep <= _EPS:
        sweep = 360.0
    return start, sweep


def _arc_point(center: tuple[float, float], radius: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    return (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))


def _candidates(entity: Mapping[str, Any], kinds: Sequence[str]) -> list[tuple[str, tuple[float, float]]]:
    """单个实体的候选捕捉点（按 kind 生成；不支持的种类返回空，不报错、不假装）。"""
    kind = str(entity.get("kind") or "")
    geom = _geom_of(entity)
    out: list[tuple[str, tuple[float, float]]] = []
    segs = _segments_of(entity)
    if "endpoint" in kinds:
        if kind == "point":
            x, y = _num(geom, "x"), _num(geom, "y")
            if x is not None and y is not None:
                out.append(("endpoint", (x, y)))
        else:
            for seg in segs:
                out.append(("endpoint", seg[0]))
            if segs:
                out.append(("endpoint", segs[-1][1]))
        if kind == "arc":
            center = (_num(geom, "cx"), _num(geom, "cy"))
            radius = _num(geom, "r")
            sweep = _arc_sweep(geom)
            if None not in center and radius is not None and sweep is not None:
                out.append(("endpoint", _arc_point(center, radius, sweep[0])))
                out.append(("endpoint", _arc_point(center, radius, sweep[0] + sweep[1])))
    if "midpoint" in kinds:
        for start, end in segs:
            out.append(("midpoint", ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)))
        if kind == "arc":
            center = (_num(geom, "cx"), _num(geom, "cy"))
            radius = _num(geom, "r")
            sweep = _arc_sweep(geom)
            if None not in center and radius is not None and sweep is not None:
                out.append(("midpoint", _arc_point(center, radius, sweep[0] + sweep[1] / 2.0)))
    if "center" in kinds and kind in ("circle", "arc", "ellipse"):
        cx, cy = _num(geom, "cx"), _num(geom, "cy")
        if cx is not None and cy is not None:
            out.append(("center", (cx, cy)))
    if "quadrant" in kinds:
        if kind in ("circle", "arc"):
            cx, cy = _num(geom, "cx"), _num(geom, "cy")
            radius = _num(geom, "r")
            if cx is not None and cy is not None and radius is not None:
                sweep = _arc_sweep(geom) if kind == "arc" else (0.0, 360.0)
                if sweep is not None:
                    for quadrant in (0.0, 90.0, 180.0, 270.0):
                        if (quadrant - sweep[0]) % 360.0 <= sweep[1] + _EPS:
                            out.append(("quadrant", _arc_point((cx, cy), radius, quadrant)))
        if kind == "ellipse":
            cx, cy = _num(geom, "cx"), _num(geom, "cy")
            rx, ry = _num(geom, "rx"), _num(geom, "ry")
            rot = _num(geom, "rotation_deg") or 0.0
            if None not in (cx, cy) and rx is not None and ry is not None:
                for angle in (0.0, 90.0, 180.0, 270.0):
                    ex = rx * math.cos(math.radians(angle))
                    ey = ry * math.sin(math.radians(angle))
                    cos_r, sin_r = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                    out.append(("quadrant", (cx + ex * cos_r - ey * sin_r,
                                             cy + ex * sin_r + ey * cos_r)))
    return out


def _seg_intersection(a1: tuple[float, float], a2: tuple[float, float],
                      b1: tuple[float, float], b2: tuple[float, float]) -> tuple[float, float] | None:
    d1x, d1y = a2[0] - a1[0], a2[1] - a1[1]
    d2x, d2y = b2[0] - b1[0], b2[1] - b1[1]
    denom = d1x * d2y - d1y * d2x
    if abs(denom) <= _EPS:
        return None
    t = ((b1[0] - a1[0]) * d2y - (b1[1] - a1[1]) * d2x) / denom
    u = ((b1[0] - a1[0]) * d1y - (b1[1] - a1[1]) * d1x) / denom
    if -_EPS <= t <= 1.0 + _EPS and -_EPS <= u <= 1.0 + _EPS:
        return (a1[0] + t * d1x, a1[1] + t * d1y)
    return None


def _intersections(entities: Sequence[Any]) -> list[tuple[str, tuple[float, float]]]:
    segs: list[tuple[tuple[float, float], tuple[float, float], int]] = []
    for index, entity in enumerate(entities):
        if isinstance(entity, Mapping):
            for seg in _segments_of(entity):
                segs.append((seg[0], seg[1], index))
    out: list[tuple[str, tuple[float, float]]] = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if segs[i][2] == segs[j][2]:
                continue
            hit = _seg_intersection(segs[i][0], segs[i][1], segs[j][0], segs[j][1])
            if hit is not None:
                out.append(("intersection", hit))
    return out


def _nearest(entities: Sequence[Any], target: tuple[float, float]) -> list[tuple[str, tuple[float, float]]]:
    out: list[tuple[str, tuple[float, float]]] = []
    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        kind = str(entity.get("kind") or "")
        geom = _geom_of(entity)
        for start, end in _segments_of(entity):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length_sq = dx * dx + dy * dy
            if length_sq <= 0.0:
                continue
            t = ((target[0] - start[0]) * dx + (target[1] - start[1]) * dy) / length_sq
            t = min(1.0, max(0.0, t))
            out.append(("nearest", (start[0] + t * dx, start[1] + t * dy)))
        if kind in ("circle", "arc"):
            cx, cy = _num(geom, "cx"), _num(geom, "cy")
            radius = _num(geom, "r")
            if None not in (cx, cy) and radius is not None and radius > 0.0:
                vx, vy = target[0] - cx, target[1] - cy
                norm = math.hypot(vx, vy)
                if norm > _EPS:
                    angle = math.degrees(math.atan2(vy, vx))
                    if kind == "circle":
                        out.append(("nearest", _arc_point((cx, cy), radius, angle)))
                    else:
                        sweep = _arc_sweep(geom)
                        if sweep is not None and (angle - sweep[0]) % 360.0 <= sweep[1] + _EPS:
                            out.append(("nearest", _arc_point((cx, cy), radius, angle)))
        if kind == "ellipse":
            cx, cy = _num(geom, "cx"), _num(geom, "cy")
            rx, ry = _num(geom, "rx"), _num(geom, "ry")
            if None not in (cx, cy) and rx and ry:
                # 参数角近似：取与目标同参数方向的椭圆点（**不是**最小欧氏距离的精确解，见模块边界）。
                t = math.atan2((target[1] - cy) / ry, (target[0] - cx) / rx)
                out.append(("nearest", (cx + rx * math.cos(t), cy + ry * math.sin(t))))
        if kind == "point":
            x, y = _num(geom, "x"), _num(geom, "y")
            if x is not None and y is not None:
                out.append(("nearest", (x, y)))
    return out


def osnap_toggle(sk_or_doc: Any, *, enabled: Any, sketch_index: int = 0) -> list[str]:
    """对象捕捉**总开关**：返回 `[osnap 模式记录 uuid]`。

    关掉后 `osnap_probe` 一律 `osnap_disabled`；各捕捉类型的开关位原样保留（重开不用重设）。
    """
    flag = _flag(enabled, what="enabled")
    store = _state(sk_or_doc, sketch_index=sketch_index)
    assert store is not None  # create=True ⇒ 必得表
    record = _mode(store, "osnap")
    record["enabled"] = flag
    return [str(record["uuid"])]


def osnap_kinds_set(sk_or_doc: Any, *, kinds: Any, sketch_index: int = 0) -> list[str]:
    """设置**哪些捕捉类型参与**（列出的开、没列出的关）。返回 `[osnap 模式记录 uuid]`。

    `kinds` 是 `SNAP_KINDS` 的子集（空集合法 = 全关）；出现未知类型 ⇒ `unknown_snap_kind`。
    """
    if isinstance(kinds, (str, bytes)) or not isinstance(kinds, Sequence):
        raise _fail("bad_kinds", "kinds must be a sequence of snap kind names", value=repr(kinds))
    wanted = [_token(item, "kinds[]", SNAP_KINDS) for item in kinds]
    if len(set(wanted)) != len(wanted):
        raise _fail("bad_kinds", "kinds must not repeat", value=wanted)
    store = _state(sk_or_doc, sketch_index=sketch_index)
    assert store is not None
    record = _mode(store, "osnap")
    record["config"]["kinds"] = {kind: kind in wanted for kind in SNAP_KINDS}
    return [str(record["uuid"])]


def osnap_probe(sk_or_doc: Any, *, point_nm: Any, kinds: Any = None, tolerance_nm: Any = None,
                layer: str = DEFAULT_LAYER, construction: bool = False,
                sketch_index: int = 0) -> list[str]:
    """用**持久化设置**做一次对象捕捉并把结果**落成 `point` 实体**：返回 `[新实体 uuid]`。

    命中判据：候选点距离 ≤ 容差，取最近的；同距按 `SNAP_KINDS` 优先级。落点实体
    `geom = {x, y, snap_kind}`（`snap_kind` 是命中的类型名，便于机读归因）。
    总开关关掉 ⇒ `osnap_disabled`；没有任何候选命中 ⇒ `osnap_no_hit`（**不落几何、不返空表**）。
    `kinds` / `tolerance_nm` 给了就覆盖本次设置（只影响这一次，不写回状态表）。
    """
    target = _point(point_nm, "point_nm")
    store = _state(sk_or_doc, create=False, sketch_index=sketch_index)
    if store is None:
        raise _fail("osnap_disabled", "no osnap state: object snap is off by default")
    record = _mode(store, "osnap")
    if not record["enabled"]:
        raise _fail("osnap_disabled", "object snap master switch is off")
    config = record["config"]
    if kinds is None:
        if isinstance(config.get("kinds"), Mapping):
            wanted = [kind for kind in SNAP_KINDS if config["kinds"].get(kind)]
        else:  # pragma: no cover - _ensure 已补齐
            wanted = list(SNAP_KINDS)
    else:
        if isinstance(kinds, (str, bytes)) or not isinstance(kinds, Sequence):
            raise _fail("bad_kinds", "kinds must be a sequence of snap kind names", value=repr(kinds))
        wanted = [_token(item, "kinds[]", SNAP_KINDS) for item in kinds]
    tolerance = _tolerance_nm(config.get("tolerance_nm")) if tolerance_nm is None else _tolerance_nm(tolerance_nm)
    layer_name = _layer(layer)
    construction_flag = _flag(construction, what="construction")
    if not wanted:
        raise _fail("osnap_no_kinds", "no snap kind is enabled")
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    entities = [entity for entity in _entities(sk) if isinstance(entity, Mapping)]
    candidates: list[tuple[str, tuple[float, float]]] = []
    for entity in entities:
        candidates.extend(_candidates(entity, wanted))
    if "intersection" in wanted:
        candidates.extend(_intersections(entities))
    if "nearest" in wanted:
        candidates.extend(_nearest(entities, (float(target[0]), float(target[1]))))
    best: tuple[float, int, str, tuple[float, float]] | None = None
    for kind, point in candidates:
        if kind not in wanted:
            continue
        distance = math.hypot(point[0] - target[0], point[1] - target[1])
        if distance > tolerance + _EPS:
            continue
        rank = SNAP_KINDS.index(kind)
        if best is None or (distance, rank) < (best[0], best[1]):
            best = (distance, rank, kind, point)
    if best is None:
        raise _fail("osnap_no_hit", "no snap candidate within tolerance",
                    point_nm=list(target), tolerance_nm=int(tolerance), kinds=list(wanted))
    uuid_ = _append(sk, kind="point",
                    geom={"x": _round_nm(best[3][0]), "y": _round_nm(best[3][1]),
                          "snap_kind": best[2]},
                    layer=layer_name, construction=construction_flag)
    return [uuid_]


# --------------------------------------------------------------------------------------
# 几何：正交与极轴追踪
# --------------------------------------------------------------------------------------


def ortho_polar_set(sk_or_doc: Any, *, mode: Any, enabled: Any = None, tolerance_deg: Any = None,
                    increments_deg: Any = None, sketch_index: int = 0) -> list[str]:
    """正交 / 极轴追踪**状态**：返回 `[该模式记录 uuid]`。

    `mode` ∈ `TRACK_MODES`；`enabled` / `tolerance_deg` / `increments_deg`（仅 polar）按需覆盖。
    正交与极轴是**互斥优先级**（`ortho_polar_apply` 里正交优先），但两个开关位各自独立保存。
    """
    name = _token(mode, "mode", TRACK_MODES)
    store = _state(sk_or_doc, sketch_index=sketch_index)
    assert store is not None
    record = _mode(store, name)
    if enabled is not None:
        record["enabled"] = _flag(enabled, what="enabled")
    if tolerance_deg is not None:
        record["config"]["tolerance_deg"] = _tolerance_deg(tolerance_deg)
    if increments_deg is not None:
        if name != "polar":
            raise _fail("bad_increments", "increments_deg only applies to polar", mode=name)
        record["config"]["increments_deg"] = _increments_deg(increments_deg)
    return [str(record["uuid"])]


def _ortho_lock(base: tuple[float, float], point: tuple[float, float],
                tolerance_deg: float) -> tuple[float, float] | None:
    """正交锁定：离轴角在容差内 ⇒ 锁到过 base 的水平/垂直线；否则 `None`（不锁）。"""
    dx, dy = point[0] - base[0], point[1] - base[1]
    if abs(dx) <= _EPS and abs(dy) <= _EPS:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 180.0
    for axis in (0.0, 90.0):
        delta = abs(angle - axis)
        delta = min(delta, 180.0 - delta)
        if delta <= tolerance_deg + _EPS:
            return (point[0], base[1]) if axis == 0.0 else (base[0], point[1])
    return None


def _polar_track(base: tuple[float, float], point: tuple[float, float], increments: Sequence[float],
                 tolerance_deg: float) -> tuple[float, float] | None:
    """极轴追踪：离最近增量射线的角差在容差内 ⇒ 投影到该射线；否则 `None`。"""
    dx, dy = point[0] - base[0], point[1] - base[1]
    radius = math.hypot(dx, dy)
    if radius <= _EPS:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 360.0
    best: tuple[float, float] | None = None
    for increment in increments:
        steps = round(angle / increment)
        for candidate in ((steps - 1) * increment, steps * increment, (steps + 1) * increment):
            delta = abs(angle - candidate % 360.0)
            delta = min(delta, 360.0 - delta)
            if delta <= tolerance_deg + _EPS and (best is None or delta < best[0]):
                best = (delta, candidate)
    if best is None:
        return None
    rad = math.radians(best[1])
    return (base[0] + radius * math.cos(rad), base[1] + radius * math.sin(rad))


def ortho_polar_apply(sk_or_doc: Any, *, base_nm: Any, point_nm: Any, mode: Any = None,
                      tolerance_deg: Any = None, layer: str = DEFAULT_LAYER,
                      construction: bool = False, sketch_index: int = 0) -> list[str]:
    """把一次输入点按**已启用的**追踪模式约束后**落成 `point` 实体**：返回 `[新实体 uuid]`。

    `mode=None` 时按优先级取：ortho 启用 ⇒ 用 ortho；否则 polar 启用 ⇒ 用 polar；都没启用 ⇒
    `no_tracking_mode`。追踪不上（容差外）⇒ `not_tracked`（**不落几何**）。
    落点实体 `geom = {x, y, track_mode}`；`tolerance_deg` 给了就覆盖本次容差（不写回状态表）。
    """
    base = _point(base_nm, "base_nm")
    point = _point(point_nm, "point_nm")
    store = _state(sk_or_doc, create=False, sketch_index=sketch_index)
    if store is None:
        raise _fail("no_tracking_mode", "no state: neither ortho nor polar is enabled")
    if mode is None:
        if _mode(store, "ortho")["enabled"]:
            name = "ortho"
        elif _mode(store, "polar")["enabled"]:
            name = "polar"
        else:
            raise _fail("no_tracking_mode", "neither ortho nor polar is enabled")
    else:
        name = _token(mode, "mode", TRACK_MODES)
        if not _mode(store, name)["enabled"]:
            raise _fail("tracking_disabled", "requested tracking mode is disabled", mode=name)
    record = _mode(store, name)
    tolerance = (_tolerance_deg(record["config"].get("tolerance_deg"))
                 if tolerance_deg is None else _tolerance_deg(tolerance_deg))
    if name == "ortho":
        locked = _ortho_lock((float(base[0]), float(base[1])), (float(point[0]), float(point[1])),
                             tolerance)
    else:
        increments = _increments_deg(record["config"].get("increments_deg"))
        locked = _polar_track((float(base[0]), float(base[1])), (float(point[0]), float(point[1])),
                              increments, tolerance)
    if locked is None:
        raise _fail("not_tracked", "point is outside the tracking tolerance",
                    mode=name, tolerance_deg=float(tolerance), point_nm=list(point))
    layer_name = _layer(layer)
    construction_flag = _flag(construction, what="construction")
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    uuid_ = _append(sk, kind="point",
                    geom={"x": _round_nm(locked[0]), "y": _round_nm(locked[1]), "track_mode": name},
                    layer=layer_name, construction=construction_flag)
    return [uuid_]


# --------------------------------------------------------------------------------------
# 几何：动态输入
# --------------------------------------------------------------------------------------


def dyninput_set(sk_or_doc: Any, *, enabled: Any = None, fields: Any = None,
                 sketch_index: int = 0) -> list[str]:
    """动态输入**状态**（开关 + 提示字段）：返回 `[dyninput 模式记录 uuid]`。

    `fields` 是 `DYN_FIELDS` 的子集（缺省 `["length", "angle"]`）；出现未知字段 ⇒
    `unknown_dyn_field`。动态输入是输入法，不改变落库几何 —— 几何后果由 `dyninput_point` 承担。
    """
    store = _state(sk_or_doc, sketch_index=sketch_index)
    assert store is not None
    record = _mode(store, "dyninput")
    if enabled is not None:
        record["enabled"] = _flag(enabled, what="enabled")
    if fields is not None:
        if isinstance(fields, (str, bytes)) or not isinstance(fields, Sequence):
            raise _fail("bad_dyn_fields", "fields must be a sequence of field names", value=repr(fields))
        wanted = [_token(item, "fields[]", DYN_FIELDS) for item in fields]
        if len(set(wanted)) != len(wanted):
            raise _fail("bad_dyn_fields", "fields must not repeat", value=wanted)
        record["config"]["fields"] = wanted
    return [str(record["uuid"])]


def dyninput_point(sk_or_doc: Any, *, start_nm: Any, length_nm: Any, angle_deg: Any,
                   layer: str = DEFAULT_LAYER, construction: bool = False,
                   sketch_index: int = 0) -> list[str]:
    """动态输入落点：从 `start_nm` 走 `length_nm`（整数纳米）在 `angle_deg` 方向 → `point` 实体。

    返回 `[新实体 uuid]`；`geom = {x, y, length_nm, angle_deg}`。换算口径与规格测试向量一致
    （10 mm + 90° ⇒ 位移 (0, 10000000) nm）。状态表按需建出（出厂 dyninput = 开）；
    `dyninput_set(enabled=False)` 之后 ⇒ `dyninput_disabled`（不落几何）。
    """
    start = _point(start_nm, "start_nm")
    length = _nm(length_nm, "length_nm")
    if length < 0:
        raise _fail("negative_length", "length_nm must be >= 0", value=length)
    angle = _deg(angle_deg)
    store = _state(sk_or_doc, create=True, sketch_index=sketch_index)
    assert store is not None
    if not _mode(store, "dyninput")["enabled"]:
        raise _fail("dyninput_disabled", "dynamic input is disabled")
    rad = math.radians(angle)
    x = _round_nm(start[0] + length * math.cos(rad))
    y = _round_nm(start[1] + length * math.sin(rad))
    layer_name = _layer(layer)
    construction_flag = _flag(construction, what="construction")
    sk = _sketch(sk_or_doc, sketch_index=sketch_index)
    uuid_ = _append(sk, kind="point",
                    geom={"x": x, "y": y, "length_nm": int(length), "angle_deg": float(angle)},
                    layer=layer_name, construction=construction_flag)
    return [uuid_]


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "revcloud": revcloud,
    "revcloud_rect": revcloud_rect,
    "osnap_toggle": osnap_toggle,
    "osnap_kinds_set": osnap_kinds_set,
    "osnap_probe": osnap_probe,
    "ortho_polar_set": ortho_polar_set,
    "ortho_polar_apply": ortho_polar_apply,
    "dyninput_set": dyninput_set,
    "dyninput_point": dyninput_point,
}


def module_facts() -> dict[str, Any]:
    """机读事实（门/测试打这个，别去猜模块内部）。"""
    return {
        "family": FAMILY,
        "operations": sorted(OPERATIONS),
        "op_count": len(OPERATIONS),
        "state_key": STATE_KEY,
        "state_path": ["sketch", "extra", STATE_KEY],
        "snap_kinds": list(SNAP_KINDS),
        "track_modes": list(TRACK_MODES),
        "dyn_fields": list(DYN_FIELDS),
        "state_ops": sorted(name for name in OPERATIONS if name.endswith("_set") or name.endswith("_toggle")),
        "geometry_ops": sorted(name for name in OPERATIONS if name not in
                               ("osnap_toggle", "osnap_kinds_set", "ortho_polar_set", "dyninput_set")),
        "unprobed_covered": ["revcloud", "osnap_switch", "ortho_polar", "dyninput"],
        "length_unit": "nm",
        "integer_nm": True,
    }


# --------------------------------------------------------------------------------------
# 自带自检（不依赖 pytest；`__main__` 打 EXTRA_OPS 判定行）
# --------------------------------------------------------------------------------------


def _selftest_sketch() -> dict[str, Any]:
    return {"uuid": _constraints.new_uuid(), "entities": []}


def selftest() -> int:
    """进程内自检：接口 + 四条能力各一条真调用 + 阴性对照。返回 0=PASS / 1=FAIL。"""
    checks: list[str] = []
    problems: list[str] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append(name)
        if not passed:
            problems.append("%s%s" % (name, (" (" + detail + ")") if detail else ""))

    facts = module_facts()
    check("interface", facts["family"] == FAMILY and facts["op_count"] == len(OPERATIONS) == 9
          and all(callable(value) for value in OPERATIONS.values())
          and sorted(OPERATIONS) == facts["operations"], "ops=%d" % len(OPERATIONS))

    # 1. 修订云线：矩形云线的弦长 ≤ arc_length_nm，且确实朝外鼓
    sk = _selftest_sketch()
    side, arc = 40 * NM_PER_MM, 10 * NM_PER_MM
    uuids = revcloud_rect(sk, center_nm=(0, 0), width_nm=side, height_nm=side, arc_length_nm=arc)
    entity = _entities(sk)[0] if _entities(sk) else {}
    geom = _geom_of(entity)
    pts = [(int(p[0]), int(p[1])) for p in geom.get("points", [])]
    chord_max = 0.0
    for index in range(len(pts) - 1):
        chord_max = max(chord_max, math.hypot(pts[index + 1][0] - pts[index][0],
                                              pts[index + 1][1] - pts[index][1]))
    outside = any(max(abs(x), abs(y)) > side // 2 for x, y in pts)
    check("revcloud", len(uuids) == 1 and str(entity.get("uuid")) == uuids[0]
          and str(entity.get("kind")) == "polyline" and geom.get("closed") is True
          and len(pts) == 128 and chord_max <= arc and outside,
          "vertices=%d chord_max=%.1f outside=%s" % (len(pts), chord_max, outside))

    # 2. 对象捕捉开关 + 真几何（含阴性对照：关掉后不许命中）
    sk = _selftest_sketch()
    _entities(sk).append({"uuid": _constraints.new_uuid(), "kind": "line", "layer": "0",
                          "construction": False, "geom": {"x1": 0, "y1": 0, "x2": 10 * NM_PER_MM,
                                                          "y2": 0}})
    try:
        osnap_probe(sk, point_nm=(1 * NM_PER_MM, 1 * NM_PER_MM))
        check("osnap_default_off", False, "expected osnap_disabled")
    except CadError as exc:
        check("osnap_default_off", exc.reason == "osnap_disabled", exc.reason)
    osnap_toggle(sk, enabled=True)
    hit = osnap_probe(sk, point_nm=(-100_000, 100_000), kinds=["endpoint"])
    hit_geom = _geom_of(_entities(sk)[-1])
    check("osnap_probe", len(hit) == 1 and hit_geom.get("snap_kind") == "endpoint"
          and (hit_geom.get("x"), hit_geom.get("y")) == (0, 0),
          "geom=%r" % (dict(hit_geom),))
    try:
        osnap_probe(sk, point_nm=(50 * NM_PER_MM, 50 * NM_PER_MM))
        check("osnap_no_hit", False, "expected osnap_no_hit")
    except CadError as exc:
        check("osnap_no_hit", exc.reason == "osnap_no_hit", exc.reason)
    osnap_toggle(sk, enabled=False)
    try:
        osnap_probe(sk, point_nm=(1 * NM_PER_MM, 1 * NM_PER_MM))
        check("osnap_off_again", False, "expected osnap_disabled")
    except CadError as exc:
        check("osnap_off_again", exc.reason == "osnap_disabled", exc.reason)

    # 3. 正交与极轴：正交优先，容差外不锁
    sk = _selftest_sketch()
    ortho_polar_set(sk, mode="ortho", enabled=True)
    ortho_polar_set(sk, mode="polar", enabled=True, increments_deg=(90.0,))
    locked = ortho_polar_apply(sk, base_nm=(0, 0), point_nm=(10 * NM_PER_MM, 100_000))
    locked_geom = _geom_of(_entities(sk)[-1])
    check("ortho_priority", len(locked) == 1 and locked_geom.get("track_mode") == "ortho"
          and (locked_geom.get("x"), locked_geom.get("y")) == (10 * NM_PER_MM, 0),
          "geom=%r" % (dict(locked_geom),))
    ortho_polar_set(sk, mode="ortho", enabled=False)
    polar = ortho_polar_apply(sk, base_nm=(0, 0), point_nm=(10 * NM_PER_MM, 100_000))
    polar_geom = _geom_of(_entities(sk)[-1])
    check("polar_track", len(polar) == 1 and polar_geom.get("track_mode") == "polar"
          and polar_geom.get("y") == 0 and polar_geom.get("x") == 10_000_500,
          "geom=%r" % (dict(polar_geom),))
    try:
        ortho_polar_apply(sk, base_nm=(0, 0), point_nm=(10 * NM_PER_MM, 10 * NM_PER_MM))
        check("not_tracked", False, "expected not_tracked")
    except CadError as exc:
        check("not_tracked", exc.reason == "not_tracked", exc.reason)

    # 4. 动态输入：10 mm + 90° ⇒ (0, 10000000) nm；关掉后拒绝
    sk = _selftest_sketch()
    dyninput_set(sk, enabled=True, fields=["length", "angle"])
    dyn = dyninput_point(sk, start_nm=(0, 0), length_nm=10 * NM_PER_MM, angle_deg=90.0)
    dyn_geom = _geom_of(_entities(sk)[-1])
    check("dyninput_point", len(dyn) == 1 and (dyn_geom.get("x"), dyn_geom.get("y")) == (0, 10 * NM_PER_MM)
          and dyn_geom.get("length_nm") == 10 * NM_PER_MM, "geom=%r" % (dict(dyn_geom),))
    dyninput_set(sk, enabled=False)
    try:
        dyninput_point(sk, start_nm=(0, 0), length_nm=10 * NM_PER_MM, angle_deg=90.0)
        check("dyninput_off", False, "expected dyninput_disabled")
    except CadError as exc:
        check("dyninput_off", exc.reason == "dyninput_disabled", exc.reason)

    # 5. 参数校验：bool 纳米 / 非整数值浮点一律拒绝
    sk = _selftest_sketch()
    try:
        revcloud(sk, points_nm=[(0, 0), (1, 1)], arc_length_nm=1.5)
        check("reject_non_int_nm", False, "expected not_integer_nm")
    except CadError as exc:
        check("reject_non_int_nm", exc.reason == "not_integer_nm", exc.reason)

    if problems:
        for item in problems:
            print("EXTRA_OPS_SELFTEST problem=%s" % item)
        return 1
    return 0


if __name__ == "__main__":
    _rc = selftest()
    print("EXTRA_OPS verdict=%s ops=%d" % ("PASS" if _rc == 0 else "FAIL", len(OPERATIONS)))
    raise SystemExit(_rc)
