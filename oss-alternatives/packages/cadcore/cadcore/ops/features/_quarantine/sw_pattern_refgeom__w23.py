#!/usr/bin/env python3
"""sw_pattern_refgeom 族「missing/partial 清单」第 14 号（取模）条目：阵列/镜像的参数化重建。

族清单（`docs/research/cad-parity/status/sw-pattern-refgeom.json`）里仍 missing/partial 的 must_have 共 8 条，
按清单顺序编号 0..7：0 线性阵列第二方向 / 1 阵列对象类型 / 2 跳过与恢复实例 / 3 圆周阵列角度范围 /
4 镜像实体（多实体） / 5 装配体镜像零部件 / 6 **阵列/镜像的参数化重建** / 7 质量属性。
第 14 号落到 14 mod 8 = 6 ⇒ 本片做「阵列/镜像的参数化重建」。

口径：长度一律整数纳米（int nm），角度用浮点度；真源是 `cadcore.docmodel` 的 L1 规范 JSON 文本。
本模块只写 L1（不改 docmodel、不写 __init__.py、不碰内核），因此实例几何是**草图级刚体变换 + 既有
extrude/revolve/boolean 重建**，不需要给闭集内核新增 op。

实现的能力（真数据后果，非桩）：

1. `pattern_define` 往 L1 `features` 写**驱动特征**（kind = pattern_linear / pattern_circular / mirror），
   父子关系用 `inputs`（uuid 列表，符合 docmodel“引用一律 uuid”口径）；驱动参数放 `x_pat`
   （docmodel 把 `x_` 前缀视为厂商扩展，未知字段原样保留且不报诊断）。
   驱动 uuid 由 (doc_uuid, 种子 uuid, kind, 规范化参数) 决定 ⇒ 同参重复 define 幂等，不会堆重复驱动。
2. `pattern_rebuild` **实例化**：按「间距+个数 / 角度范围+个数 / 镜像面」算出每个实例的刚体变换，
   复制种子特征引用的草图并**逐实体变换几何**（平移 / 绕心旋转 / 镜像），坐标取整回整数纳米，
   生成确定 uuid（uuid5）的派生草图 + 派生特征节点。派生特征的 kind/params 与种子一致 ⇒ 交给既有
   `docmodel.recompute` 就能真重建实例几何。
3. **参数化跟随**（本条的“参数化重建”字面核心）：派生特征 params 是种子**当前** params 的深拷贝，
   种子（父）改尺寸后重跑 rebuild 即自动跟随；实例 uuid 由 (doc_uuid, 驱动 uuid, 实例号) 决定
   ⇒ 改参不换身份，父子的 uuid 引用不失效。
4. **跳过/恢复**与**压缩/解除压缩**：`skip` 实例 = 派生节点删除、恢复 = 同一 uuid 复现（可逆）；
   驱动 suppressed / 单实例 suppressed = 节点保留但 `suppressed=True`（数据不丢，恢复即原样）。
5. **幂等**：同输入连续 rebuild 两次，L1 文本逐字节相同（无重复节点、无顺序漂移、无随机 uuid）。

边界（诚实声明，不许静默当已实现）：

* 实体级任意轴旋转/布尔的对形状变换（kernel transform op）不在本模块内，也在本席写面之外：本模块做的是
  L1 层的参数化重建与草图级变换；对 extrude/revolve 的实例，其“形状”由派生草图的坐标承载，若种子特征
  的 `op` 是布尔（cut/fuse），实例会复现同一次布尔语义——这一点与 SolidWorks「按特征阵列」一致。
* 派生草图丢弃父草图的 `solved.cache`（那份缓存按父几何算的，照抄即假绿），保留 dof/converged。
* 驱动特征 kind 不在 docmodel 三类闭集内，`recompute` 会按既有约定记 `feature_kind_unknown` 诊断并跳过
  驱动节点本身——这是设计内行为（驱动只承载参数，几何由派生特征承担），不是本模块的失败。
* 派生草图只搬已知字段（uuid/plane/entities/constraints/dimensions/solved），草图级未知字段不搬：未知字段
  的语义可能依赖原几何，照搬比不搬更危险；此取舍写在这里，不藏。

自检：`python3 cadcore/ops/features/sw_pattern_refgeom__w23.py`（硬断言计数见判定行）。
"""

from __future__ import annotations

import copy
import json
import math
import uuid as _uuid
from typing import Any, Callable, Iterable, MutableMapping, Sequence

if __package__ not in (None, ""):  # 正常路径：作为包模块导入
    from cadcore.errors import CadError
else:  # pragma: no cover - 以脚本方式直接运行时的包上下文补全
    import os as _os
    import sys as _sys

    _ROOT = _os.path.dirname(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    )
    if _ROOT not in _sys.path:
        _sys.path.insert(0, _ROOT)
    from cadcore.errors import CadError

__all__ = ["FAMILY", "PATTERN_KINDS", "OPERATIONS", "pattern_define", "pattern_rebuild",
           "pattern_skip", "pattern_suppress", "selftest"]

FAMILY = "sw_pattern_refgeom"

#: 驱动特征 kind（阵列/镜像的参数化重建的三种驱动）
PATTERN_KINDS = ("pattern_linear", "pattern_circular", "mirror")

#: 派生节点的稳定命名空间（固定值 ⇒ 跨进程 uuid 可复现）
_NS = _uuid.UUID("8f0f3f4c-9a2e-5b71-9c3d-0a1b2c3d4e5f")

_DRIVER_ROLE = "driver"
_INSTANCE_ROLE = "instance"
_INSTANCE_SKETCH_ROLE = "instance_sketch"


# --------------------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------------------
def _cad(reason: str) -> CadError:
    """构造 ``CadError(reason=...)``：对签名差异做一次兜底，绝不吞掉错误。

    ``CadError.__init__(self, message, *, reason="", **details)`` 的 ``message`` 是**必填位置参数**，
    只给 ``reason=`` 会抛 ``TypeError`` 被兜底支吞掉、把 reason 当 message ⇒ ``.reason`` 回落成
    ``default_reason``（"cad_error"）、**真原因丢失**。故 reason 一律同时作 message 传。
    """
    try:
        return CadError(str(reason), reason=reason)
    except TypeError:  # pragma: no cover - CadError 签名变体兜底（只有 message 位置参数）
        return CadError(str(reason))  # type: ignore[call-arg]


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _iround(v: float) -> int:
    """四舍五入到整数纳米（半数远离零，确定性；不用 banker's rounding）。"""
    if v >= 0:
        return int(math.floor(v + 0.5))
    return -int(math.floor(-v + 0.5))


def _wrap360(a: float) -> float:
    return float(a) % 360.0


def _unit(vec: Sequence[Any]) -> tuple[float, float]:
    if not isinstance(vec, (list, tuple)) or len(vec) != 2:
        raise _cad("pattern_direction_invalid")
    try:
        x = float(vec[0])
        y = float(vec[1])
    except (TypeError, ValueError):
        raise _cad("pattern_direction_invalid")
    n = math.hypot(x, y)
    if not (n > 0.0):
        raise _cad("pattern_direction_invalid")
    return x / n, y / n


def _det_uuid(doc_uuid: str, *parts: Any) -> str:
    name = "|".join([str(doc_uuid or "")] + [str(p) for p in parts])
    return str(_uuid.uuid5(_NS, name))


def _doc_uuid(l1: MutableMapping[str, Any]) -> str:
    meta = l1.get("meta")
    if isinstance(meta, MutableMapping):
        v = meta.get("doc_uuid")
        if isinstance(v, str):
            return v
    return ""


def _doc_l1(sk_or_doc: Any) -> MutableMapping[str, Any]:
    """取 L1 映射（唯一权威文本）。非映射输入尝试 docmodel 转换，失败即 CadError。"""
    if isinstance(sk_or_doc, MutableMapping):
        if "features" not in sk_or_doc:
            raise _cad("doc_l1_mapping_required")
        return sk_or_doc
    for name in ("to_l1", "as_l1"):
        fn = getattr(sk_or_doc, name, None)
        if callable(fn):
            try:
                out = fn()
            except Exception:  # noqa: BLE001 - 转换失败继续尝试下一条路径
                out = None
            if isinstance(out, MutableMapping) and "features" in out:
                return out
    try:
        from cadcore.docmodel import document_to_l1

        out = document_to_l1(sk_or_doc)
    except Exception as exc:  # noqa: BLE001
        raise _cad("doc_l1_mapping_required") from exc
    if isinstance(out, str):
        # ``document_to_l1`` 的返回口径是 **JSON 文本**（唯一权威文本），不解析就等于拒绝真 Document。
        import json

        try:
            out = json.loads(out)
        except ValueError as exc:
            raise _cad("doc_l1_mapping_required") from exc
    if isinstance(out, MutableMapping) and "features" in out:
        return out
    raise _cad("doc_l1_mapping_required")


def _nodes(l1: MutableMapping[str, Any], key: str) -> list:
    v = l1.get(key)
    return v if isinstance(v, list) else []


def _find(nodes: Iterable[Any], node_uuid: Any) -> Any:
    if not isinstance(node_uuid, str):
        return None
    for n in nodes:
        if isinstance(n, MutableMapping) and n.get("uuid") == node_uuid:
            return n
    return None


def _x_pat(node: Any) -> dict:
    x = node.get("x_pat") if isinstance(node, MutableMapping) else None
    return x if isinstance(x, dict) else {}


def _is_driver(node: Any) -> bool:
    if not isinstance(node, MutableMapping):
        return False
    x = _x_pat(node)
    return (node.get("kind") in PATTERN_KINDS and x.get("family") == FAMILY
            and x.get("role") == _DRIVER_ROLE)


def _is_derived_of(node: Any, driver_uuid: str) -> bool:
    return _x_pat(node).get("driver") == driver_uuid


# --------------------------------------------------------------------------------------
# 参数规范化（长度整数纳米；角度浮点度）
# --------------------------------------------------------------------------------------
def _norm_skip(skip: Any, count: int) -> list:
    if skip is None:
        return []
    if isinstance(skip, (str, bytes)) or not isinstance(skip, Iterable):
        raise _cad("pattern_skip_invalid")
    out = set()
    for v in skip:
        if not _is_int(v) or not (0 <= int(v) < count):
            raise _cad("pattern_skip_index_invalid")
        out.add(int(v))
    return sorted(out)


def _norm_plane(plane: Any) -> dict:
    if not isinstance(plane, MutableMapping):
        raise _cad("pattern_plane_invalid")
    origin = plane.get("origin_nm")
    if not isinstance(origin, (list, tuple)) or len(origin) != 2 or not all(_is_int(v) for v in origin):
        raise _cad("pattern_plane_invalid")
    nx, ny = _unit(plane.get("normal"))
    return {"origin_nm": [int(origin[0]), int(origin[1])], "normal": [nx, ny]}


def _norm_spec(kind: str, count: Any, spacing_nm: Any, direction: Any, center_nm: Any,
               total_angle_deg: Any, plane: Any, skip: Any, symmetric: Any) -> dict:
    spec: dict = {"family": FAMILY, "role": _DRIVER_ROLE, "version": 1, "kind": kind}
    if kind == "mirror":
        cnt = 2
    else:
        if not _is_int(count) or int(count) < 2:
            raise _cad("pattern_count_invalid")
        cnt = int(count)
    spec["count"] = cnt
    spec["skip"] = [] if kind == "mirror" else _norm_skip(skip, cnt)

    if kind == "pattern_linear":
        if not _is_int(spacing_nm) or int(spacing_nm) <= 0:
            raise _cad("pattern_spacing_invalid")
        dx, dy = _unit(direction if direction is not None else [1, 0])
        spec["spacing_nm"] = int(spacing_nm)
        spec["direction"] = [dx, dy]
        spec["symmetric"] = bool(symmetric)
    elif kind == "pattern_circular":
        if (not isinstance(center_nm, (list, tuple)) or len(center_nm) != 2
                or not all(_is_int(v) for v in center_nm)):
            raise _cad("pattern_center_invalid")
        ang = 360.0 if total_angle_deg is None else float(total_angle_deg)
        if not (0.0 < ang <= 360.0):
            raise _cad("pattern_angle_invalid")
        spec["center_nm"] = [int(center_nm[0]), int(center_nm[1])]
        spec["total_angle_deg"] = ang
    else:  # mirror
        spec["plane"] = _norm_plane(plane)
    return spec


def _driver_params(kind: str, spec: dict) -> dict:
    """驱动特征的 params：只用 docmodel 的 params 白名单键与合法类型（长度 int nm / 角度 float）。"""
    if kind == "pattern_linear":
        return {"distance_nm": int(spec["spacing_nm"])}
    if kind == "pattern_circular":
        return {"angle_deg": float(spec["total_angle_deg"])}
    return {}


def _transforms(spec: dict) -> dict:
    """实例号 -> 刚体变换（整数纳米 / 浮点度）。号 0 是种子本身，不在这里。"""
    kind = spec["kind"]
    cnt = int(spec["count"])
    out: dict = {}
    if kind == "pattern_linear":
        dx, dy = spec["direction"]
        sp = int(spec["spacing_nm"])
        for i in range(1, cnt):
            k = (2 * i - (cnt - 1)) if spec.get("symmetric") else (2 * i)
            ox = _iround(sp * k * dx / 2.0 if spec.get("symmetric") else sp * i * dx)
            oy = _iround(sp * k * dy / 2.0 if spec.get("symmetric") else sp * i * dy)
            out[i] = {"kind": "translate", "delta_nm": [ox, oy], "spacing_nm": sp, "index": i}
    elif kind == "pattern_circular":
        cx, cy = spec["center_nm"]
        total = float(spec["total_angle_deg"])
        step = 360.0 / cnt if abs(total - 360.0) < 1e-9 else total / (cnt - 1)
        for i in range(1, cnt):
            out[i] = {"kind": "rotate", "center_nm": [cx, cy], "angle_deg": step * i,
                      "step_deg": step, "total_angle_deg": total, "index": i}
    else:  # mirror
        pl = spec["plane"]
        out[1] = {"kind": "mirror", "origin_nm": list(pl["origin_nm"]),
                  "normal": list(pl["normal"]), "index": 1}
    return out


# --------------------------------------------------------------------------------------
# 刚体变换（作用在草图几何上）
# --------------------------------------------------------------------------------------
def _flips(xf: dict) -> bool:
    return xf.get("kind") == "mirror"


def _xf_xy(x: Any, y: Any, xf: dict) -> tuple[int, int]:
    xi, yi = int(x), int(y)
    kind = xf.get("kind")
    if kind == "translate":
        dx, dy = xf["delta_nm"]
        return xi + int(dx), yi + int(dy)
    if kind == "rotate":
        cx, cy = xf["center_nm"]
        th = math.radians(float(xf["angle_deg"]))
        c, s = math.cos(th), math.sin(th)
        vx, vy = xi - cx, yi - cy
        return _iround(cx + vx * c - vy * s), _iround(cy + vx * s + vy * c)
    if kind == "mirror":
        ox, oy = xf["origin_nm"]
        nx, ny = xf["normal"]
        vx, vy = xi - ox, yi - oy
        d = vx * nx + vy * ny
        return _iround(xi - 2.0 * d * nx), _iround(yi - 2.0 * d * ny)
    raise _cad("pattern_transform_unknown")


def _pt_on_circle(cx: Any, cy: Any, r: Any, deg: Any) -> tuple[int, int]:
    a = math.radians(float(deg))
    return _iround(float(cx) + float(r) * math.cos(a)), _iround(float(cy) + float(r) * math.sin(a))


def _angle_of(px: Any, py: Any, cx: Any, cy: Any) -> float:
    return _wrap360(math.degrees(math.atan2(float(py) - float(cy), float(px) - float(cx))))


def _transform_geom(geom: MutableMapping[str, Any], xf: dict) -> dict:
    """刚体变换一条实体几何；未知字段原样保留；长度仍为整数纳米。"""
    out = {k: copy.deepcopy(v) for k, v in geom.items()}
    if "points_nm" in geom:
        pts = [[_xf_xy(p[0], p[1], xf)[0], _xf_xy(p[0], p[1], xf)[1]] for p in geom["points_nm"]]
        if _flips(xf) and geom.get("closed"):
            pts.reverse()
        out["points_nm"] = pts
        return out
    if "x1" in geom and "x2" in geom:
        nx1, ny1 = _xf_xy(geom["x1"], geom.get("y1", 0), xf)
        nx2, ny2 = _xf_xy(geom["x2"], geom.get("y2", 0), xf)
        out["x1"], out["y1"], out["x2"], out["y2"] = nx1, ny1, nx2, ny2
        return out
    if "r" in geom and "cx" in geom and "cy" in geom:
        ncx, ncy = _xf_xy(geom["cx"], geom["cy"], xf)
        out["cx"], out["cy"] = ncx, ncy
        if "start_deg" in geom and "end_deg" in geom:
            r = geom["r"]
            sx, sy = _pt_on_circle(geom["cx"], geom["cy"], r, geom.get("start_deg", 0.0))
            ex, ey = _pt_on_circle(geom["cx"], geom["cy"], r, geom.get("end_deg", 0.0))
            tsx, tsy = _xf_xy(sx, sy, xf)
            tex, tey = _xf_xy(ex, ey, xf)
            a0 = _angle_of(tsx, tsy, ncx, ncy)
            a1 = _angle_of(tex, tey, ncx, ncy)
            if _flips(xf):
                a0, a1 = a1, a0
            out["start_deg"], out["end_deg"] = a0, a1
        return out
    return out


def _remap_refs(refs: Any, emap: dict) -> list:
    out = []
    for r in refs if isinstance(refs, (list, tuple)) else []:
        if isinstance(r, (list, tuple)) and r:
            head = emap.get(r[0], r[0])
            out.append([head] + [copy.deepcopy(x) for x in list(r[1:])])
        elif isinstance(r, str):
            out.append(emap.get(r, r))
        else:
            out.append(copy.deepcopy(r))
    return out


# --------------------------------------------------------------------------------------
# 派生草图 / 派生特征
# --------------------------------------------------------------------------------------
def _derive_sketch(l1: MutableMapping[str, Any], base_sketch: MutableMapping[str, Any],
                   driver_uuid: str, index: int, xf: dict, doc_uuid: str) -> dict:
    su = _det_uuid(doc_uuid, "sketch", driver_uuid, index)
    emap: dict = {}
    ents = []
    for ent in _nodes(base_sketch, "entities"):
        old = ent.get("uuid")
        new = _det_uuid(doc_uuid, "entity", driver_uuid, index, old)
        emap[old] = new
        ne = {k: copy.deepcopy(v) for k, v in ent.items() if k not in ("uuid", "geom")}
        ne["uuid"] = new
        ne["geom"] = _transform_geom(ent.get("geom") if isinstance(ent.get("geom"), dict) else {}, xf)
        ents.append(ne)

    cons = []
    for c in _nodes(base_sketch, "constraints"):
        nc = {k: copy.deepcopy(v) for k, v in c.items() if k not in ("uuid", "refs")}
        nc["uuid"] = _det_uuid(doc_uuid, "constraint", driver_uuid, index, c.get("uuid"))
        if "refs" in c:
            nc["refs"] = _remap_refs(c.get("refs"), emap)
        cons.append(nc)

    dims = []
    for d in _nodes(base_sketch, "dimensions"):
        nd = {k: copy.deepcopy(v) for k, v in d.items() if k not in ("uuid", "refs")}
        nd["uuid"] = _det_uuid(doc_uuid, "dimension", driver_uuid, index, d.get("uuid"))
        if "refs" in d:
            nd["refs"] = _remap_refs(d.get("refs"), emap)
        dims.append(nd)

    solved = base_sketch.get("solved")
    sk: dict = {
        "uuid": su,
        "plane": base_sketch.get("plane"),
        "entities": ents,
        "constraints": cons,
        "dimensions": dims,
        "solved": {"dof": (solved or {}).get("dof", 0) if isinstance(solved, dict) else 0,
                   "converged": bool((solved or {}).get("converged", True))
                   if isinstance(solved, dict) else True},
        "x_pat": {"family": FAMILY, "role": _INSTANCE_SKETCH_ROLE, "version": 1,
                  "driver": driver_uuid, "seed": base_sketch.get("uuid"), "index": index},
    }
    return sk


def _derive_feature(base: MutableMapping[str, Any], driver: MutableMapping[str, Any], index: int,
                    xf: dict, base_sketch_uuid: str, derived_sketch_uuid: str, doc_uuid: str,
                    suppressed: bool) -> dict:
    drv = driver["uuid"]
    inputs = []
    for u in (base.get("inputs") or []):
        inputs.append(derived_sketch_uuid if u == base_sketch_uuid else u)
    return {
        "uuid": _det_uuid(doc_uuid, "feature", drv, index),
        "kind": base.get("kind"),
        "inputs": inputs,
        "params": copy.deepcopy(base.get("params") or {}),
        "suppressed": bool(suppressed),
        "x_pat": {"family": FAMILY, "role": _INSTANCE_ROLE, "version": 1, "driver": drv,
                  "seed": base.get("uuid"), "index": index,
                  "count": int(_x_pat(driver).get("count", 0)),
                  "transform": copy.deepcopy(xf)},
    }


def _splice(l1: MutableMapping[str, Any], key: str, driver_uuid: str, anchor_uuid: str,
            new_nodes: list) -> None:
    """先摘掉该驱动的旧派生节点，再把新节点插到锚点之后 ⇒ 重建收敛、顺序稳定。"""
    kept = [n for n in _nodes(l1, key) if not _is_derived_of(n, driver_uuid)]
    out: list = []
    hit = False
    for n in kept:
        out.append(n)
        if isinstance(n, MutableMapping) and n.get("uuid") == anchor_uuid:
            out.extend(new_nodes)
            hit = True
    if not hit:
        out.extend(new_nodes)
    l1[key] = out


def _rebuild_one(l1: MutableMapping[str, Any], driver: MutableMapping[str, Any]) -> list:
    spec = _x_pat(driver)
    kind = spec.get("kind")
    if kind not in PATTERN_KINDS:
        raise _cad("pattern_kind_unknown")
    cnt = spec.get("count")
    if not _is_int(cnt) or int(cnt) < 2:
        raise _cad("pattern_count_invalid")
    cnt = int(cnt)
    inputs = driver.get("inputs") or []
    seed = inputs[0] if inputs else None
    feats = _nodes(l1, "features")
    base = _find(feats, seed)
    if base is None:
        raise _cad("pattern_seed_missing")

    base_sketch_uuid = None
    for u in (base.get("inputs") or []):
        if _find(_nodes(l1, "sketches"), u) is not None:
            base_sketch_uuid = u
            break
    if base_sketch_uuid is None:
        raise _cad("pattern_sketch_missing")
    base_sketch = _find(_nodes(l1, "sketches"), base_sketch_uuid)

    doc_uuid = _doc_uuid(l1)
    drv = driver["uuid"]
    skip = set(_norm_skip(spec.get("skip"), cnt))
    suppress_idx = set(_norm_skip(spec.get("suppress_indices"), cnt))
    drv_suppressed = bool(driver.get("suppressed"))
    xfs = _transforms(spec)

    new_sketches: list = []
    new_features: list = []
    derived_uuid: dict = {}
    for i in range(1, cnt):
        if i in skip:
            continue
        xf = xfs[i]
        sk = _derive_sketch(l1, base_sketch, drv, i, xf, doc_uuid)
        ft = _derive_feature(base, driver, i, xf, base_sketch_uuid, sk["uuid"], doc_uuid,
                             suppressed=drv_suppressed or (i in suppress_idx))
        new_sketches.append(sk)
        new_features.append(ft)
        derived_uuid[i] = ft["uuid"]

    _splice(l1, "sketches", drv, base_sketch_uuid, new_sketches)
    _splice(l1, "features", drv, drv, new_features)
    return [seed] + [derived_uuid[i] for i in range(1, cnt) if i in derived_uuid]


def _driver(l1: MutableMapping[str, Any], pattern_uuid: Any) -> MutableMapping[str, Any]:
    drivers = [f for f in _nodes(l1, "features") if _is_driver(f)]
    if pattern_uuid is None:
        if len(drivers) == 1:
            return drivers[0]
        if not drivers:
            raise _cad("pattern_not_found")
        raise _cad("pattern_uuid_required")
    for d in drivers:
        if d.get("uuid") == pattern_uuid:
            return d
    raise _cad("pattern_not_found")


# --------------------------------------------------------------------------------------
# 对外操作：op(sk_or_doc, **kwargs) -> list[str]
# --------------------------------------------------------------------------------------
def pattern_define(sk_or_doc: Any, kind: str = "pattern_linear", seed_uuid: Any = None,
                   base_uuid: Any = None, target_uuid: Any = None, count: Any = 2,
                   spacing_nm: Any = None, direction: Any = None, total_angle_deg: Any = None,
                   center_nm: Any = None, plane: Any = None, skip: Any = None,
                   symmetric: Any = False, **_ignored: Any) -> list:
    """定义阵列/镜像**驱动特征**（只写驱动，不实例化）。返回 [驱动 uuid]。

    重定义同参 = 幂等（同 uuid 覆盖，含重置 skip）；异参 = 新驱动。
    """
    l1 = _doc_l1(sk_or_doc)
    seed = seed_uuid or base_uuid or target_uuid
    feats = _nodes(l1, "features")
    if not isinstance(seed, str) or _find(feats, seed) is None:
        raise _cad("pattern_seed_missing")
    if kind not in PATTERN_KINDS:
        raise _cad("pattern_kind_unknown")
    spec = _norm_spec(kind, count, spacing_nm, direction, center_nm, total_angle_deg, plane,
                      skip, symmetric)
    drv_uuid = _det_uuid(_doc_uuid(l1), "driver", seed, kind,
                         json.dumps(spec, sort_keys=True, ensure_ascii=True))
    node = _find(feats, drv_uuid)
    if node is None:
        node = {"uuid": drv_uuid, "kind": kind, "inputs": [seed],
                "params": _driver_params(kind, spec), "suppressed": False}
        feats.append(node)
    node["kind"] = kind
    node["inputs"] = [seed]
    node["params"] = _driver_params(kind, spec)
    node["x_pat"] = copy.deepcopy(spec)
    if isinstance(l1, MutableMapping) and "features" not in l1:
        l1["features"] = feats
    return [drv_uuid]


def pattern_rebuild(sk_or_doc: Any, pattern_uuid: Any = None, **_ignored: Any) -> list:
    """参数化重建：按驱动参数实例化/刷新派生草图与派生特征。返回实例 uuid 列表（[0] = 种子）。"""
    l1 = _doc_l1(sk_or_doc)
    drivers = [f for f in _nodes(l1, "features") if _is_driver(f)]
    if pattern_uuid is not None:
        sel = [d for d in drivers if d.get("uuid") == pattern_uuid]
        if not sel:
            raise _cad("pattern_not_found")
        drivers = sel
    uuids: list = []
    for d in drivers:
        uuids.extend(_rebuild_one(l1, d))
    return uuids


def pattern_skip(sk_or_doc: Any, pattern_uuid: Any = None, indices: Any = None, index: Any = None,
                 skipped: bool = True, **_ignored: Any) -> list:
    """跳过/恢复实例（可逆：恢复用同一 uuid 复现）。返回重建后的实例 uuid 列表。"""
    l1 = _doc_l1(sk_or_doc)
    d = _driver(l1, pattern_uuid)
    cnt = int(_x_pat(d).get("count", 0))
    sel = indices if indices is not None else ([index] if index is not None else None)
    if sel is None:
        raise _cad("pattern_skip_index_invalid")
    idx = set(_norm_skip(sel, cnt))
    cur = set(_norm_skip(_x_pat(d).get("skip"), cnt))
    cur = (cur | idx) if skipped else (cur - idx)
    d["x_pat"]["skip"] = sorted(cur)
    return _rebuild_one(l1, d)


def pattern_suppress(sk_or_doc: Any, pattern_uuid: Any = None, suppressed: bool = True,
                     index: Any = None, **_ignored: Any) -> list:
    """压缩/解除压缩：驱动级（index=None）或单实例级。数据保留，恢复即原样。"""
    l1 = _doc_l1(sk_or_doc)
    d = _driver(l1, pattern_uuid)
    cnt = int(_x_pat(d).get("count", 0))
    if index is None:
        d["suppressed"] = bool(suppressed)
        return _rebuild_one(l1, d)
    if not _is_int(index) or int(index) == 0:
        raise _cad("pattern_seed_suppress_unsupported")
    _norm_skip([index], cnt)
    sup = set(_norm_skip(_x_pat(d).get("suppress_indices"), cnt))
    sup = (sup | {int(index)}) if suppressed else (sup - {int(index)})
    d["x_pat"]["suppress_indices"] = sorted(sup)
    return _rebuild_one(l1, d)


OPERATIONS: dict = {
    "pattern_define": pattern_define,
    "pattern_rebuild": pattern_rebuild,
    "pattern_skip": pattern_skip,
    "pattern_suppress": pattern_suppress,
}

#: 可调用性自证（冻结接口要求 OPERATIONS: dict[str, Callable]）
assert all(callable(v) for v in OPERATIONS.values()) and FAMILY == "sw_pattern_refgeom"


# --------------------------------------------------------------------------------------
# 自检（硬断言；夹具 = 手写 L1 规范文档，另附 docmodel 不变量对照读数）
# --------------------------------------------------------------------------------------
_DOC_UUID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
SK1 = "11111111-1111-1111-1111-111111111111"
E1 = "e1e1e1e1-0000-0000-0000-000000000001"
E2 = "e2e2e2e2-0000-0000-0000-000000000002"
E3 = "e3e3e3e3-0000-0000-0000-000000000003"
SEED = "22222222-2222-2222-2222-222222222222"
MM = 1_000_000

_CHECKS = {"n": 0}


def _check(cond: Any, label: str) -> None:
    _CHECKS["n"] += 1
    if not cond:
        raise AssertionError("ASSERT_FAIL " + label)


def _expect_cad(fn: Callable[[], Any], label: str, want: str) -> None:
    _CHECKS["n"] += 1
    try:
        fn()
    except CadError as exc:
        got = str(getattr(exc, "reason", None) or exc)
        if want not in got:
            raise AssertionError("ASSERT_FAIL %s: reason=%r want~%r" % (label, got, want))
        return
    raise AssertionError("ASSERT_FAIL " + label + ": no CadError raised")


def _fixture() -> dict:
    """L1 规范文档夹具：1 草图（线/圆/弧）+ 1 拉伸特征。"""
    return {
        "schema_version": 1,
        "meta": {"doc_uuid": _DOC_UUID, "created_at": "2026-01-01T00:00:00+00:00",
                 "app_version": "0.1.0", "title_key": "doc.untitled"},
        "unit": "mm",
        "standard_profile": "iso",
        "projection_angle": "first",
        "sketches": [{
            "uuid": SK1,
            "plane": "XY",
            "entities": [
                {"uuid": E1, "kind": "line", "layer": "0", "construction": False,
                 "geom": {"x1": 0, "y1": 0, "x2": 10 * MM, "y2": 0}},
                {"uuid": E2, "kind": "circle", "layer": "0", "construction": False,
                 "geom": {"cx": 5 * MM, "cy": 0, "r": MM}},
                {"uuid": E3, "kind": "arc", "layer": "0", "construction": False,
                 "geom": {"cx": 2 * MM, "cy": 0, "r": MM, "start_deg": 0.0, "end_deg": 90.0}},
            ],
            "constraints": [{"uuid": "c1c1c1c1-0000-0000-0000-000000000001", "kind": "coincident",
                             "refs": [[E1, "start"], [E1, "end"]]}],
            "dimensions": [{"uuid": "d1d1d1d1-0000-0000-0000-000000000001", "kind": "distance",
                            "refs": [[E1, "start"], [E1, "end"]], "value_nm": 10 * MM,
                            "text_key": "dim.linear"}],
            "solved": {"dof": 0, "converged": True, "cache": "c0ffee"},
        }],
        "features": [{"uuid": SEED, "kind": "extrude", "inputs": [SK1],
                      "params": {"distance_nm": 10 * MM, "symmetric": False}, "suppressed": False}],
        "annotations": [],
        "sheets": [],
    }


def _dump(l1: Any) -> str:
    return json.dumps(l1, sort_keys=True, ensure_ascii=False)


def _invariant_problems(l1: Any) -> Any:
    """docmodel 不变量读数（int = 问题数；str = 取不到的原因，绝不静默当 0）。"""
    try:
        from cadcore import docmodel
    except Exception as exc:  # noqa: BLE001
        return "unavailable:" + type(exc).__name__
    fn = getattr(docmodel, "check_invariants", None)
    if not callable(fn):
        return "unavailable:no_check_invariants"
    try:
        out = fn(copy.deepcopy(l1))
    except Exception as exc:  # noqa: BLE001
        return "unavailable:" + type(exc).__name__
    if out is None:
        return 0
    if isinstance(out, bool):
        return 0 if out else 1
    if isinstance(out, int):
        return int(out)
    if isinstance(out, (list, tuple, set)):
        return len(out)
    if isinstance(out, dict):
        return sum(len(v) if isinstance(v, (list, tuple, set)) else 1 for v in out.values())
    return "unavailable:" + type(out).__name__


def _feature(l1: dict, u: str) -> Any:
    return _find(_nodes(l1, "features"), u)


def _entity_geom(l1: dict, feature_uuid: str, ent_index: int) -> dict:
    f = _feature(l1, feature_uuid)
    sk = None
    for u in (f.get("inputs") or []):
        sk = _find(_nodes(l1, "sketches"), u)
        if sk is not None:
            break
    return sk["entities"][ent_index]["geom"]


def _instance_sketch(l1: dict, feature_uuid: str) -> dict:
    f = _feature(l1, feature_uuid)
    for u in (f.get("inputs") or []):
        sk = _find(_nodes(l1, "sketches"), u)
        if sk is not None:
            return sk
    raise AssertionError("ASSERT_FAIL instance_sketch_missing")


def _driver_count(l1: dict) -> int:
    return len([f for f in _nodes(l1, "features") if _is_driver(f)])


def selftest() -> int:
    doc = _fixture()
    inv_before = _invariant_problems(doc)

    # --- ① 线性阵列：真几何（草图级平移，整数纳米） ---
    drv = pattern_define(doc, kind="pattern_linear", seed_uuid=SEED, count=4,
                         spacing_nm=25 * MM, direction=[1, 0])
    uuids = pattern_rebuild(doc)
    _check(len(uuids) == 4, "linear.instance_count")
    _check(uuids[0] == SEED, "linear.seed_is_instance_0")
    _check(len(set(uuids)) == 4, "linear.uuid_unique")
    g1 = _entity_geom(doc, uuids[1], 0)
    _check(g1["x1"] == 25 * MM and g1["x2"] == 35 * MM and g1["y1"] == 0, "linear.instance1_translated")
    g3 = _entity_geom(doc, uuids[3], 0)
    _check(g3["x1"] == 75 * MM and g3["x2"] == 85 * MM, "linear.instance3_translated")
    sk1 = _instance_sketch(doc, uuids[1])
    _check(sk1["uuid"] != SK1, "linear.derived_sketch_new_uuid")
    _check(sk1["entities"][0]["uuid"] != E1, "linear.derived_entity_new_uuid")
    _check(sk1["constraints"][0]["refs"][0][0] == sk1["entities"][0]["uuid"], "linear.refs_remapped")
    _check("cache" not in (sk1.get("solved") or {}), "linear.stale_solved_cache_dropped")
    _check(_driver_count(doc) == 1, "linear.single_driver")

    # --- ② 幂等：同输入 rebuild 两次，L1 文本逐字节相同 ---
    snap = _dump(doc)
    again = pattern_rebuild(doc)
    _check(again == uuids, "rebuild.idempotent_uuid_list")
    _check(_dump(doc) == snap, "rebuild.idempotent_bytes")

    # --- ③ 参数化跟随：父特征改尺寸 ⇒ 实例跟随，且身份（uuid）不变 ---
    _feature(doc, SEED)["params"]["distance_nm"] = 30 * MM
    after = pattern_rebuild(doc)
    _check(after == uuids, "propagate.instance_identity_stable")
    _check(all(_feature(doc, u)["params"]["distance_nm"] == 30 * MM for u in uuids[1:]),
           "propagate.parent_param_follows")
    _check(_feature(doc, SEED)["params"]["distance_nm"] == 30 * MM, "propagate.seed_untouched")

    # --- ④ 跳过 / 恢复（可逆） ---
    live = pattern_skip(doc, pattern_uuid=drv[0], index=2)
    _check(len(live) == 3 and uuids[2] not in live, "skip.instance_removed_from_result")
    _check(_feature(doc, uuids[2]) is None, "skip.node_removed_from_l1")
    back = pattern_skip(doc, pattern_uuid=drv[0], index=2, skipped=False)
    _check(back == uuids, "skip.restore_same_uuid_and_order")

    # --- ⑤ 压缩 / 解除压缩（驱动级 + 单实例级） ---
    pattern_suppress(doc, pattern_uuid=drv[0], suppressed=True)
    _check(all(_feature(doc, u)["suppressed"] for u in uuids[1:]), "suppress.driver_cascades")
    _check(not _feature(doc, SEED)["suppressed"], "suppress.seed_kept")
    pattern_suppress(doc, pattern_uuid=drv[0], suppressed=False)
    _check(not any(_feature(doc, u)["suppressed"] for u in uuids[1:]), "suppress.restore")
    pattern_suppress(doc, pattern_uuid=drv[0], index=1, suppressed=True)
    _check(_feature(doc, uuids[1])["suppressed"] and not _feature(doc, uuids[2])["suppressed"],
           "suppress.single_instance")

    # --- ⑥ 圆周阵列：绕心旋转 90°（线/圆/弧真坐标 + 弧角随动） ---
    cdoc = _fixture()
    pattern_define(cdoc, kind="pattern_circular", seed_uuid=SEED, count=4,
                   center_nm=[0, 0], total_angle_deg=360.0)
    cu = pattern_rebuild(cdoc)
    _check(len(cu) == 4, "circular.count")
    cg = _entity_geom(cdoc, cu[1], 0)
    _check(cg["x2"] == 0 and cg["y2"] == 10 * MM, "circular.line_rotated_90")
    cc = _entity_geom(cdoc, cu[1], 1)
    _check(abs(cc["cx"]) <= 1 and abs(cc["cy"] - 5 * MM) <= 1, "circular.circle_center_rotated_90")
    ca = _entity_geom(cdoc, cu[1], 2)
    _check(abs(ca["cx"]) <= 1 and abs(ca["cy"] - 2 * MM) <= 1, "circular.arc_center_rotated_90")
    _check(abs(_wrap360(ca["start_deg"]) - 90.0) < 0.05
           and abs(_wrap360(ca["end_deg"]) - 180.0) < 0.05, "circular.arc_angles_rotated_90")
    _check(len(pattern_rebuild(cdoc)) == 4, "circular.rebuild_stable")

    # --- ⑦ 镜像：反射坐标 + 弧走向翻转（det<0） ---
    mdoc = _fixture()
    pattern_define(mdoc, kind="mirror", seed_uuid=SEED,
                   plane={"origin_nm": [0, 0], "normal": [1, 0]})
    mu = pattern_rebuild(mdoc)
    _check(len(mu) == 2 and mu[0] == SEED, "mirror.single_instance")
    mg = _entity_geom(mdoc, mu[1], 0)
    _check(mg["x1"] == 0 and mg["x2"] == -10 * MM, "mirror.line_reflected")
    mc = _entity_geom(mdoc, mu[1], 1)
    _check(mc["cx"] == -5 * MM, "mirror.circle_reflected")
    ma = _entity_geom(mdoc, mu[1], 2)
    _check(ma["cx"] == -2 * MM and ma["cy"] == 0, "mirror.arc_center_reflected")
    _check(abs(_wrap360(ma["start_deg"]) - 90.0) < 0.05
           and abs(_wrap360(ma["end_deg"]) - 180.0) < 0.05, "mirror.arc_orientation_flipped")

    # --- ⑧ 错误面（禁静默：全走 CadError(reason=...)） ---
    _expect_cad(lambda: pattern_define(_fixture(), seed_uuid=SEED, spacing_nm=25.5),
                "err.spacing_float_nm", "pattern_spacing_invalid")
    _expect_cad(lambda: pattern_define(_fixture(), seed_uuid=SEED, count=1),
                "err.count_below_two", "pattern_count_invalid")
    _expect_cad(lambda: pattern_define(_fixture(), seed_uuid="deadbeef-0000-0000-0000-000000000000"),
                "err.seed_missing", "pattern_seed_missing")
    _expect_cad(lambda: pattern_rebuild(_fixture(), pattern_uuid="deadbeef-0000-0000-0000-000000000000"),
                "err.pattern_not_found", "pattern_not_found")
    _expect_cad(lambda: pattern_define(_fixture(), kind="pattern_circular", seed_uuid=SEED, count=4),
                "err.center_missing", "pattern_center_invalid")
    _expect_cad(lambda: pattern_define(_fixture(), seed_uuid=SEED, skip=[9]),
                "err.skip_out_of_range", "pattern_skip_index_invalid")
    _expect_cad(lambda: pattern_define(_fixture(), kind="nope", seed_uuid=SEED),
                "err.kind_unknown", "pattern_kind_unknown")
    _expect_cad(lambda: pattern_rebuild({"sketches": [], "features": []}),
                "err.not_a_document", "doc_l1_mapping_required")

    inv_after = _invariant_problems(doc)
    detail = "n/a"
    if isinstance(inv_before, int) and isinstance(inv_after, int):
        _check(inv_after <= inv_before, "docmodel.no_new_invariant_problems")
        detail = "%d->%d" % (inv_before, inv_after)
    else:
        detail = "%s->%s" % (inv_before, inv_after)
    print("SELFTEST family=%s op=pattern_rebuild assertions=%d docmodel_invariants=%s "
          "kernel_transform=external_to_module" % (FAMILY, _CHECKS["n"], detail))
    print("FEATURE_OK family=%s op=pattern_rebuild verdict=PASS" % FAMILY)
    return 0


if __name__ == "__main__":
    try:
        selftest()
    except Exception as exc:  # noqa: BLE001 - 失败必须 loud，禁假绿
        print("FEATURE_FAIL family=%s op=pattern_rebuild verdict=FAIL error=%s: %s"
              % (FAMILY, type(exc).__name__, exc))
        raise SystemExit(1)
