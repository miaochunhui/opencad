#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edit_ops__w24 —— family `edit_ops` 的实现片（冻结接口见文件末尾 OPERATIONS）。

① 本族仍 missing/partial 的 must_have（源：docs/research/cad-parity/features/autocad-edit.json
   的 must_have 顺序，剔除 status/autocad-edit.json 里 implemented 的「放弃与重做」，
   状态取自 docs/research/cad-parity/status/autocad-edit.json），编号 0..N-1，N=20：

     0  对象选择（窗选/交叉窗选/栏选/加减选） partial
     1  夹点编辑                           partial
     2  对象捕捉与追踪                     partial
     3  精确坐标输入                       partial
     4  删除                               partial
     5  移动                               partial
     6  复制                               partial
     7  旋转                               partial
     8  缩放                               partial
     9  镜像                               partial
     10 偏移                               partial
     11 阵列                               partial
     12 修剪                               partial
     13 延伸                               partial
     14 圆角                               partial
     15 倒角（CHAMFER）                    partial   <== 本席负责的第 15 号
     16 拉伸                               partial
     17 分解                               partial
     18 特性匹配                           partial
     19 对齐（平移加旋转一次完成）         missing

   N=20 ⇒ 第 15 号存在，无需取模。

② 本片把「倒角」在 L1 文档模型上做成**真几何**命令：AutoCAD CHAMFER 的距离/距离模式与
   距离/角度模式，两条直线各自从交点缩进后用一段新直线连接；缩进点、被改写端点、新实体
   都落到真实 L1 实体上（真 uuid、真坐标、真实体数 +1）。只接受直线；圆/弧/多段线显式抛
   CadError(reason="unsupported_entity")，不假装能部分倒角。

   与 status 里 edit/modify.py:926 的库函数的关系：那个函数是 modify 模块内部的几何工具；
   本片把它当作「几何引擎候选」之一来复用（`_clone_entity` 建新实体），同时自校正地发现
   L1 端点字段布局（探针写-读-还原，见 `_slot_map`），因此对几何容器是映射/嵌套列表/属性
   三种落法都能落笔，且**所有校验在写文档之前完成** ⇒ 出错时文档零改动（自测有 L1 文本
   逐字节比对断言）。

冻结接口：
    FAMILY = "edit_ops"
    OPERATIONS = {"chamfer": chamfer}
    op(sk_or_doc, **kwargs) -> list[str]     # 返回被创建/被修改实体的 uuid 列表
    kwargs 例：ref_a=<uuid|entity>, ref_b=..., distance_nm=10_000_000（或 d1_nm/d2_nm，
              angle_deg=45 走距离-角度模式，trim=False 走 NO-TRIM）
"""

from __future__ import annotations

import copy as _copy
import math
import numbers as _numbers
import os as _os
import sys as _sys
from collections.abc import Mapping, MutableMapping
from fractions import Fraction
from typing import Any, Callable
from uuid import uuid4

# 本文件可能被当脚本直接执行（python3 cadcore/ops/features/edit_ops__w24.py），
# 此时 sys.path[0] 是 features/ 目录而非包根，需要自己把 packages/cadcore 挂上。
_ROOT = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
)
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)

from cadcore.errors import CadError  # noqa: E402

try:  # 同族模块：L1 规范读法（_points_of / _kind_of / _uuid_of / _sketch / _find）
    from cadcore.ops import edit_ops as _fam
except Exception:  # pragma: no cover - 同族模块不可用时退到本地兜底读法
    _fam = None

try:  # 变换/克隆工具所在模块
    from cadcore.edit import modify as _mod
except Exception:  # pragma: no cover
    _mod = None

FAMILY = "edit_ops"
_REAL = _numbers.Real
_STRATS = ("mod_geom", "mapping", "attr")


def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造 cadcore.errors.CadError（冻结接口要求错误必须带机器可读 reason）。"""
    return CadError(message, reason=reason, **details)


# --------------------------------------------------------------------------- #
# 文档/实体访问（全部 duck-typing；优先用同族模块的规范读法）
# --------------------------------------------------------------------------- #
def _entities_of(node: Any) -> Any:
    """返回 node 的实体容器（映射或列表），不是草图容器则 None。"""
    if node is None:
        return None
    val = None
    try:
        val = getattr(node, "entities", None)
    except Exception:
        val = None
    if val is None and isinstance(node, Mapping):
        try:
            val = node.get("entities")
        except Exception:
            val = None
    if isinstance(val, Mapping):
        return val
    if isinstance(val, (list, tuple)):
        return val
    return None


def _children(node: Any) -> list:
    try:
        if isinstance(node, Mapping):
            vals = list(node.values())
        elif isinstance(node, (list, tuple)):
            vals = list(node)
        else:
            vals = list(vars(node).values()) if hasattr(node, "__dict__") else []
    except Exception:
        return []
    out = []
    for val in vals:
        if isinstance(val, (Mapping, list, tuple)):
            out.append(val)
        elif hasattr(val, "__dict__") and not isinstance(val, type):
            out.append(val)
    return out


def _walk(root: Any, max_depth: int = 6):
    seen, queue = set(), [(root, 0)]
    while queue:
        node, depth = queue.pop(0)
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        if depth < max_depth:
            for child in _children(node):
                queue.append((child, depth + 1))


def _sketch_of(target: Any) -> Any:
    fn = getattr(_fam, "_sketch", None) if _fam is not None else None
    if callable(fn):
        try:
            sk = fn(target)
            if _entities_of(sk) is not None:
                return sk
        except Exception:
            pass
    if _entities_of(target) is not None:
        return target
    for node in _walk(target, 5):
        if _entities_of(node) is not None:
            return node
    raise _fail("no_sketch", "no editable sketch-like container in the target")


def _kind_name(entity: Any) -> str:
    fn = getattr(_fam, "_kind_of", None) if _fam is not None else None
    if callable(fn):
        try:
            kind = fn(entity)
            if kind:
                return str(kind).lower()
        except Exception:
            pass
    val = None
    try:
        val = getattr(entity, "kind", None)
    except Exception:
        val = None
    if val is None and isinstance(entity, Mapping):
        val = entity.get("kind")
    return str(val or "").lower()


def _uuid(entity: Any) -> str:
    fn = getattr(_fam, "_uuid_of", None) if _fam is not None else None
    if callable(fn):
        try:
            uid = fn(entity)
            if uid:
                return str(uid)
        except Exception:
            pass
    val = None
    try:
        val = getattr(entity, "uuid", None)
    except Exception:
        val = None
    if val is None and isinstance(entity, Mapping):
        val = entity.get("uuid")
    return str(val) if val else ""


def _resolve(sketch: Any, ref: Any) -> Any:
    fn = getattr(_fam, "_find", None) if _fam is not None else None
    if callable(fn):
        try:
            ent = fn(sketch, ref)
            if ent is not None:
                return ent
        except Exception:
            pass
    if not isinstance(ref, str):
        if ref is None:
            raise _fail("entity_not_found", "entity reference is empty")
        return ref
    ent = _find_entity_by_uuid(sketch, ref)
    if ent is None:
        raise _fail("entity_not_found", "entity not found: %r" % (ref,), ref=ref)
    return ent


def _find_entity_by_uuid(root: Any, uid: str) -> Any:
    for node in _walk(root, 6):
        ents = _entities_of(node)
        if ents is None:
            continue
        items = ents.values() if isinstance(ents, Mapping) else ents
        try:
            for ent in list(items):
                if _uuid(ent) == uid:
                    return ent
        except Exception:
            continue
    return None


def _container(entity: Any) -> Any:
    """实体几何容器：优先 entity.geom / entity["geom"]，否则实体自身（Mapping）。"""
    probes = (
        lambda: getattr(entity, "geom", None),
        lambda: entity.get("geom") if isinstance(entity, Mapping) else None,
        lambda: entity if isinstance(entity, Mapping) else None,
    )
    for probe in probes:
        try:
            val = probe()
        except Exception:
            val = None
        if isinstance(val, Mapping):
            return val
    return None


# --------------------------------------------------------------------------- #
# 几何容器读写：探针自校正端点字段布局（写-读-还原，绝不留下残值）
# --------------------------------------------------------------------------- #
def _norm_pts(raw: Any) -> list | None:
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) == 4 and all(isinstance(v, _REAL) and not isinstance(v, bool) for v in raw):
        return [(int(raw[0]), int(raw[1])), (int(raw[2]), int(raw[3]))]
    pts = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pts.append((int(item[0]), int(item[1])))
        elif isinstance(item, Mapping) and "x" in item and "y" in item:
            pts.append((int(item["x"]), int(item["y"])))
        else:
            return None
    if len(pts) < 2:
        return None
    return [pts[0], pts[-1]]


def _local_read(entity: Any) -> list | None:
    """兜底读法：只有同族/modify 读法都不可用时才走这里（常见扁平命名）。"""
    leaves = _numeric_paths(_container(entity))
    flat = {str(path[0]).lower(): val for path, val in leaves if len(path) == 1}
    for kx1, ky1, kx2, ky2 in (
        ("x1", "y1", "x2", "y2"),
        ("x_start", "y_start", "x_end", "y_end"),
        ("startx", "starty", "endx", "endy"),
        ("sx", "sy", "ex", "ey"),
    ):
        if all(k in flat for k in (kx1, ky1, kx2, ky2)):
            return [
                (int(flat[kx1]), int(flat[ky1])),
                (int(flat[kx2]), int(flat[ky2])),
            ]
    return None


def _read_line(entity: Any) -> list | None:
    """读直线两端点 [[x,y],[x,y]]（整数纳米）；无法确定返回 None。"""
    candidates = ((_fam, "_points_of"), (_mod, "_line_points"), (_mod, "_points_of"), (_fam, "_line_points"))
    for mod, name in candidates:
        fn = getattr(mod, name, None) if mod is not None else None
        if not callable(fn):
            continue
        try:
            pts = _norm_pts(fn(entity))
        except Exception:
            pts = None
        if pts:
            return pts
    return _local_read(entity)


def _numeric_paths(container: Any, prefix: tuple = (), depth: int = 0) -> list:
    out: list = []
    if container is None or depth > 3:
        return out
    if isinstance(container, Mapping):
        items = list(container.items())
    elif isinstance(container, (list, tuple)):
        items = list(enumerate(container))
    elif hasattr(container, "__dict__"):
        items = [(k, v) for k, v in vars(container).items() if not str(k).startswith("_")]
    else:
        return out
    for key, val in items:
        if isinstance(val, bool):
            continue
        if isinstance(val, _REAL):
            out.append((prefix + (key,), val))
        elif isinstance(val, (Mapping, list, tuple)) or hasattr(val, "__dict__"):
            out.extend(_numeric_paths(val, prefix + (key,), depth + 1))
    return out


def _get_item(node: Any, key: Any) -> Any:
    if isinstance(node, (Mapping, list, tuple)):
        return node[key]
    return getattr(node, key)


def _set_item(node: Any, key: Any, value: Any) -> None:
    if isinstance(node, MutableMapping):
        node[key] = value
        return
    if isinstance(node, list):
        node[key] = value
        return
    if isinstance(node, tuple):
        raise _fail("unsupported_document_model", "tuple container cannot be written in place")
    setattr(node, key, value)


def _nav(root: Any, keys: tuple) -> Any:
    node = root
    for key in keys:
        node = _get_item(node, key)
    return node


def _apply(entity: Any, path: tuple, value: Any, strategy: str) -> None:
    if strategy == "mod_geom":
        fn = getattr(_mod, "_geom_set_num", None) if _mod is not None else None
        if len(path) != 1 or not callable(fn):
            raise _fail("unsupported_document_model", "mod_geom write strategy unavailable")
        fn(entity, path[0], value)
        return
    root = entity if strategy == "attr" else _container(entity)
    if root is None:
        raise _fail("unsupported_document_model", "no geometry container found; cannot write endpoints")
    if len(path) == 1:
        _set_item(root, path[0], value)
        return
    parent = _nav(root, path[:-1])
    last = path[-1]
    if isinstance(parent, tuple):
        grand = _nav(root, path[:-2])
        rebuilt = list(parent)
        rebuilt[last] = value
        _set_item(grand, path[-2], tuple(rebuilt))
    else:
        _set_item(parent, last, value)


def _slot_map(entity: Any) -> dict | None:
    """自校正：探出 [x1,y1,x2,y2] 四个端点槽位各自的 (path, strategy)。

    做法：对几何容器里每个数值叶子做「写探针值 → 读回 → 还原」，看见哪个槽位跟着变，
    就记住该路径；还原后逐次校验读回值与探针前完全一致，不一致立即 loud 失败
    （绝不在状态不可信时继续写文档）。
    """
    before = _read_line(entity)
    if before is None:
        return None
    slots: dict = {}
    used: set = set()
    probe_no = 0
    for path, value in _numeric_paths(_container(entity)):
        if len(slots) == 4:
            break
        if path in used:
            continue
        for strategy in _STRATS:
            if strategy == "mod_geom" and len(path) != 1:
                continue
            probe_no += 1
            probe = int(value) + 10_000_019 + 7919 * probe_no
            try:
                _apply(entity, path, probe, strategy)
            except Exception:
                continue
            after = _read_line(entity) or before
            changed = [i for i in range(4) if after[i] != before[i]]
            restored = False
            try:
                _apply(entity, path, value, strategy)
                restored = _read_line(entity) == before
            except Exception:
                restored = False
            if not restored:
                raise _fail(
                    "chamfer_probe_failed",
                    "the endpoint probe cannot reconstruct entity geometry; refusing to continue from an untrusted state",
                    path=[str(p) for p in path],
                    strategy=strategy,
                )
            if len(changed) == 1 and changed[0] not in slots:
                slots[changed[0]] = (path, strategy)
                used.add(path)
                break
    return slots if len(slots) == 4 else None


def _write_line(entity: Any, p: tuple, q: tuple) -> None:
    """把实体端点精确写成 p→q（保留原方向），写完读回校验。"""
    slots = _slot_map(entity)
    if not slots:
        raise _fail(
            "unsupported_document_model",
            "cannot determine the endpoint field layout of this L1 entity (probe did not cover 4 coordinate slots)",
        )
    for slot, value in ((0, p[0]), (1, p[1]), (2, q[0]), (3, q[1])):
        path, strategy = slots[slot]
        _apply(entity, path, int(value), strategy)
    got = _read_line(entity)
    if got is None or got[0] != (int(p[0]), int(p[1])) or got[1] != (int(q[0]), int(q[1])):
        raise _fail(
            "endpoint_write_failed",
            "endpoint read-back mismatch after write (%r -> %r)" % (got, [p, q]),
            expected=[list(p), list(q)],
            actual=[list(got[0]), list(got[1])] if got else None,
        )


def _assign_uuid(entity: Any) -> str | None:
    uid = str(uuid4())
    try:
        setattr(entity, "uuid", uid)
        return uid
    except Exception:
        pass
    if isinstance(entity, MutableMapping):
        try:
            entity["uuid"] = uid
            return uid
        except Exception:
            return None
    return None


def _ensure_registered(sketch: Any, entity: Any) -> bool:
    ents = _entities_of(sketch)
    if ents is None:
        return False
    try:
        if isinstance(ents, Mapping):
            uid = _uuid(entity)
            if not uid:
                uid = _assign_uuid(entity) or ""
            if uid and uid not in ents:
                ents[uid] = entity
            return uid in ents
        for existing in list(ents):
            if existing is entity or (_uuid(existing) and _uuid(existing) == _uuid(entity)):
                return True
        ents.append(entity)
        return True
    except Exception:
        return False


def _clone_line(sketch: Any, template: Any) -> Any:
    """按模板克隆一条真实直线实体（真 uuid，注册进草图），几何随后覆写。"""
    fn = getattr(_mod, "_clone_entity", None) if _mod is not None else None
    entity = None
    if callable(fn):
        try:
            entity = fn(sketch, template)
        except Exception:
            entity = None
    if entity is None or entity is template:
        try:
            entity = _copy.deepcopy(template)
        except Exception as exc:
            raise _fail("chamfer_cannot_create_line", "cannot clone the line template: %s" % (exc,))
    if _uuid(entity) == _uuid(template):
        _assign_uuid(entity)
    if _uuid(entity) == _uuid(template) or not _uuid(entity):
        raise _fail("chamfer_cannot_create_line", "cannot allocate a distinct uuid for the new chamfer line")
    _ensure_registered(sketch, entity)
    return entity


# --------------------------------------------------------------------------- #
# 纯几何（写文档之前全部算完；出错 ⇒ 文档零改动）
# --------------------------------------------------------------------------- #
def _nm(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, _REAL):
        raise _fail("bad_distance", "%s must be an integer number of nanometers (got %r)" % (what, value), field=what)
    val = int(value)
    if val != value:
        raise _fail("bad_distance", "%s must be an integer number of nanometers (got %r)" % (what, value), field=what)
    if val <= 0:
        raise _fail("bad_distance", "%s must be positive nanometers" % (what,), field=what)
    return val


def _round_nm(value: Any) -> int:
    if isinstance(value, Fraction):
        if value >= 0:
            return int(value + Fraction(1, 2))
        return -int(-value + Fraction(1, 2))
    val = float(value)
    if val >= 0:
        return int(math.floor(val + 0.5))
    return -int(math.floor(-val + 0.5))


def _dist(p: tuple, q: tuple) -> float:
    return math.hypot(float(p[0]) - float(q[0]), float(p[1]) - float(q[1]))


def _ray_line_hit(px, py, dx, dy, qx, qy, ex, ey):
    den = dx * ey - dy * ex
    if abs(den) < 1e-18:
        return None
    t = ((qx - px) * ey - (qy - py) * ex) / den
    return (px + t * dx, py + t * dy)


def _pick_sides(p, q, corner, keep):
    """返回 (near, far)：near 是要被缩进点替换的端点，far 是保留侧端点。"""
    dp = (p[0] - corner[0]) ** 2 + (p[1] - corner[1]) ** 2
    dq = (q[0] - corner[0]) ** 2 + (q[1] - corner[1]) ** 2
    if keep == "start":
        return q, p
    if keep == "end":
        return p, q
    if keep not in (None, "", "auto"):
        raise _fail("bad_keep", "keep must be one of start/end/auto (got %r)" % (keep,))
    if dp == dq:
        raise _fail(
            "chamfer_ambiguous_corner",
            "intersection is equidistant from both ends; cannot pick the keep side; pass keep_a/keep_b",
        )
    return (q, p) if dp > dq else (p, q)


# --------------------------------------------------------------------------- #
# 冻结接口：chamfer
# --------------------------------------------------------------------------- #
def chamfer(sk_or_doc: Any, ref_a: Any = None, ref_b: Any = None, *, refs: Any = None,
            distance_nm: Any = None, d1_nm: Any = None, d2_nm: Any = None,
            angle_deg: Any = None, trim: bool = True,
            keep_a: Any = None, keep_b: Any = None, **kwargs: Any) -> list:
    """CHAMFER：两条直线按距离（或距离-角度）倒角，返回被改/新建实体的 uuid 列表。

    语义（对齐 AutoCAD CHAMFER）：
      * 求两直线（无限延长线）交点 C；每条线保留离 C 远的那个端点（可用 keep_a/keep_b 指定）。
      * 距离模式：第一条线从 C 沿保留侧缩进 d1，第二条缩进 d2（distance_nm 同值，或 d1_nm/d2_nm）。
      * 距离-角度模式（angle_deg）：第一条缩进 d1，倒角线与第一条线成 angle_deg，交第二条线定出第二缩进点。
      * trim=False（NO-TRIM）：两条原线不动，只补一段倒角线（缩进点仍按理论位置算）。
      * 倒角线按第一条线的模板克隆 ⇒ 继承其图层/线型等属性，且是带独立 uuid 的真实体。
    错误（cadcore.errors.CadError，带 reason）：unsupported_entity / chamfer_parallel /
    chamfer_distance_too_large / chamfer_ambiguous_corner / chamfer_degenerate_result /
    chamfer_angle_no_intersection / bad_distance / bad_angle / entity_not_found /
    unsupported_document_model。全部校验在写文档之前完成 ⇒ 报错时文档保持原样。
    """
    if refs is not None and ref_a is None and ref_b is None:
        try:
            ref_a, ref_b = refs[0], refs[1]
        except Exception:
            raise _fail("bad_args", "refs must be (ref_a, ref_b)")
    if ref_a is None or ref_b is None:
        raise _fail("bad_args", "chamfer needs two line references: ref_a/ref_b (or refs=(a, b))")

    sketch = _sketch_of(sk_or_doc)
    ent_a = _resolve(sketch, ref_a)
    ent_b = _resolve(sketch, ref_b)

    kinds = [_kind_name(ent_a), _kind_name(ent_b)]
    if any(k != "line" for k in kinds):
        raise _fail(
            "unsupported_entity",
            "CHAMFER supports lines only; circles/arcs/polylines cannot take a partial chamfer: kinds=%r" % (kinds,),
            kinds=kinds,
        )
    if _uuid(ent_a) and _uuid(ent_a) == _uuid(ent_b):
        raise _fail("same_entity", "the two chamfer edges must not be the same entity")

    pts_a = _read_line(ent_a)
    pts_b = _read_line(ent_b)
    if not pts_a or not pts_b:
        raise _fail("unsupported_document_model", "cannot read line endpoints (unrecognized L1 layout)")

    if distance_nm is not None:
        d1 = _nm(distance_nm, "distance_nm")
        d2 = d1
    else:
        d1 = _nm(d1_nm, "d1_nm") if d1_nm is not None else None
        d2 = _nm(d2_nm, "d2_nm") if d2_nm is not None else None
    if d1 is None and angle_deg is None:
        raise _fail("bad_args", "one of distance_nm, d1_nm/d2_nm or angle_deg is required")

    a1, a2 = pts_a
    b1, b2 = pts_b
    da = (a2[0] - a1[0], a2[1] - a1[1])
    db = (b2[0] - b1[0], b2[1] - b1[1])
    if da == (0, 0) or db == (0, 0):
        raise _fail("degenerate_geometry", "a zero-length line cannot be chamfered")
    den = da[0] * db[1] - da[1] * db[0]
    if den == 0:
        raise _fail(
            "chamfer_parallel",
            "the two lines are parallel; their infinite extensions do not intersect; cannot chamfer",
            dir_a=list(da), dir_b=list(db),
        )

    t = Fraction((b1[0] - a1[0]) * db[1] - (b1[1] - a1[1]) * db[0], den)
    corner = (Fraction(a1[0]) + t * da[0], Fraction(a1[1]) + t * da[1])

    near_a, far_a = _pick_sides(a1, a2, corner, keep_a)
    near_b, far_b = _pick_sides(b1, b2, corner, keep_b)
    len_a = math.hypot(float(far_a[0] - corner[0]), float(far_a[1] - corner[1]))
    len_b = math.hypot(float(far_b[0] - corner[0]), float(far_b[1] - corner[1]))
    if len_a <= 0.0 or len_b <= 0.0:
        raise _fail("degenerate_geometry", "the intersection is 0 away from the kept endpoint")

    if d1 is None:  # 距离-角度模式：d1 未给 ⇒ 用 d2（AutoCAD CHA 的 A 模式只有一个距离）
        if d2 is None:
            raise _fail("bad_args", "angle_deg mode needs one of d1_nm/d2_nm as the distance on the first line")
        d1, d2 = d2, None
    if trim and d1 >= len_a:
        raise _fail(
            "chamfer_distance_too_large",
            "the chamfer distance %dnm on the first line is not smaller than the kept segment length %.0fnm" % (d1, len_a),
            side="a", distance_nm=d1, available_nm=int(len_a),
        )

    ux_a = (float(far_a[0] - corner[0])) / len_a
    uy_a = (float(far_a[1] - corner[1])) / len_a
    px = float(corner[0]) + d1 * ux_a
    py = float(corner[1]) + d1 * uy_a

    ux_b = (float(far_b[0] - corner[0])) / len_b
    uy_b = (float(far_b[1] - corner[1])) / len_b

    if angle_deg is None:
        if d2 is None:
            raise _fail("bad_args", "distance mode needs d2_nm or distance_nm")
        if trim and d2 >= len_b:
            raise _fail(
                "chamfer_distance_too_large",
                "the chamfer distance %dnm on the second line is not smaller than the kept segment length %.0fnm" % (d2, len_b),
                side="b", distance_nm=d2, available_nm=int(len_b),
            )
        qx = float(corner[0]) + d2 * ux_b
        qy = float(corner[1]) + d2 * uy_b
        used_d2 = float(d2)
    else:
        ang = float(angle_deg)
        if not (0.0 < ang < 180.0):
            raise _fail("bad_angle", "angle_deg must lie in (0, 180) (got %r)" % (angle_deg,))
        best = None
        for sign in (1.0, -1.0):
            rad = math.radians(ang) * sign
            cos_r, sin_r = math.cos(rad), math.sin(rad)
            dirx = ux_a * cos_r - uy_a * sin_r
            diry = ux_a * sin_r + uy_a * cos_r
            hit = _ray_line_hit(px, py, dirx, diry, float(b1[0]), float(b1[1]), float(db[0]), float(db[1]))
            if hit is None:
                continue
            lam = (hit[0] - float(corner[0])) * ux_b + (hit[1] - float(corner[1])) * uy_b
            if lam > 0.0 and (best is None or lam < best[0]):
                best = (lam, hit)
        if best is None:
            raise _fail(
                "chamfer_angle_no_intersection",
                "in distance-angle mode the chamfer line does not meet the second line on the kept side",
                angle_deg=ang,
            )
        used_d2, (qx, qy) = best
        if trim and used_d2 >= len_b:
            raise _fail(
                "chamfer_distance_too_large",
                "the second-line setback %.0fnm derived from angle mode is not smaller than the kept segment length %.0fnm" % (used_d2, len_b),
                side="b", available_nm=int(len_b),
            )

    p_final = (_round_nm(px), _round_nm(py))
    q_final = (_round_nm(qx), _round_nm(qy))
    if p_final == q_final:
        raise _fail("chamfer_degenerate_result", "the chamfer length rounds to 0 nanometers")
    far_a_i = (int(far_a[0]), int(far_a[1]))
    far_b_i = (int(far_b[0]), int(far_b[1]))
    if trim and (_dist(far_a_i, p_final) == 0.0 or _dist(far_b_i, q_final) == 0.0):
        raise _fail("chamfer_distance_too_large", "after rounding, the setback point falls outside the kept endpoint")

    # ---- 到这里才动文档；倒角线按第一条线克隆（继承图层/线型），随后缩进两条原线 ----
    new_ent = _clone_line(sketch, ent_a)
    _write_line(new_ent, p_final, q_final)
    out = []
    if trim:
        if near_a == a1:
            _write_line(ent_a, p_final, far_a_i)
        else:
            _write_line(ent_a, far_a_i, p_final)
        if near_b == b1:
            _write_line(ent_b, q_final, far_b_i)
        else:
            _write_line(ent_b, far_b_i, q_final)
        out.extend([_uuid(ent_a), _uuid(ent_b)])
    out.append(_uuid(new_ent))
    return [uid for uid in out if uid]


OPERATIONS: dict[str, Callable] = {"chamfer": chamfer}


# --------------------------------------------------------------------------- #
# 自测（真跑 L1 文档模型；不通过就 loud FAIL，绝不假绿）
# --------------------------------------------------------------------------- #
def _fixture_doc():
    import cadcore.docmodel as dm

    fn = getattr(dm, "_sample_document", None)
    if callable(fn):
        try:
            doc = fn()
            if doc is not None:
                return dm, doc
        except Exception as exc:
            print("INFO _sample_document failed: %r" % (exc,))
    return dm, dm.new_document()


def _entity_count(sketch: Any) -> int:
    ents = _entities_of(sketch)
    try:
        return len(ents) if ents is not None else -1
    except Exception:
        return -1


def _find_line_prototype(root: Any):
    for node in _walk(root, 6):
        ents = _entities_of(node)
        if ents is None:
            continue
        items = ents.values() if isinstance(ents, Mapping) else ents
        try:
            for ent in list(items):
                if _kind_name(ent) == "line":
                    return node, ent
        except Exception:
            continue
    return None, None


def selftest() -> int:
    results: list = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, bool(ok)))
        print(("PASS " if ok else "FAIL ") + name + (" :: " + detail if detail else ""))
        return bool(ok)

    dm, doc = _fixture_doc()
    sketch, proto = _find_line_prototype(doc)
    if proto is None:
        try:
            alt = dm.load_l1(dm._fixture_path())
            got = _find_line_prototype(alt)
            if got[1] is not None:
                dm, doc = dm, alt
                sketch, proto = got
        except Exception as exc:
            print("INFO fixture fallback failed: %r" % (exc,))
    if sketch is None or proto is None:
        print("SELFTEST fixture=unavailable: no line entity found in the L1 document model")
        print("FEATURE_FAIL family=edit_ops op=chamfer verdict=FAIL reason=no_line_fixture")
        return 1

    try:
        a = _clone_line(sketch, proto)
        b = _clone_line(sketch, proto)
        c = _clone_line(sketch, proto)
        d = _clone_line(sketch, proto)
        _write_line(a, (0, 0), (100_000_000, 0))
        _write_line(b, (0, 0), (0, 100_000_000))
        _write_line(c, (0, 0), (100_000_000, 0))
        _write_line(d, (0, 50_000_000), (100_000_000, 50_000_000))
    except CadError as exc:
        print("SELFTEST fixture=build_failed reason=%s detail=%s" % (exc.reason, exc.message))
        print("FEATURE_FAIL family=edit_ops op=chamfer verdict=FAIL reason=fixture_build_failed")
        return 1

    try:
        base_problems = len(dm.check_invariants(doc))
    except Exception as exc:
        base_problems = None
        print("INFO check_invariants unavailable: %r" % (exc,))

    before_n = _entity_count(sketch)

    # 硬断言 1/2：距离过大 ⇒ 带 reason 报错，且文档 L1 文本逐字节不变
    txt0 = dm.document_to_l1(doc)
    raised = None
    try:
        chamfer(sketch, ref_a=_uuid(a), ref_b=_uuid(b), distance_nm=200_000_000)
    except CadError as exc:
        raised = exc
    check(
        "chamfer_too_large_raises",
        raised is not None and raised.reason == "chamfer_distance_too_large",
        "reason=%s" % (getattr(raised, "reason", None),),
    )
    check("chamfer_too_large_doc_unchanged", dm.document_to_l1(doc) == txt0)

    # 硬断言 3/4：平行线 ⇒ chamfer_parallel，且文档零改动
    txt1 = dm.document_to_l1(doc)
    raised = None
    try:
        chamfer(sketch, ref_a=_uuid(c), ref_b=_uuid(d), distance_nm=10_000_000)
    except CadError as exc:
        raised = exc
    check(
        "chamfer_parallel_raises",
        raised is not None and raised.reason == "chamfer_parallel",
        "reason=%s" % (getattr(raised, "reason", None),),
    )
    check("chamfer_parallel_doc_unchanged", dm.document_to_l1(doc) == txt1)

    # 硬断言 5..：真几何后果 —— 两条线各缩进 10mm，补一条 45°（10√2 mm）倒角线
    uuids = chamfer(sketch, ref_a=_uuid(a), ref_b=_uuid(b), distance_nm=10_000_000)
    check(
        "chamfer_returns_touched_uuids",
        isinstance(uuids, list) and len(uuids) == 3 and len(set(uuids)) == 3 and all(uuids),
        "uuids=%r" % (uuids,),
    )
    check(
        "chamfer_entity_count_plus_one",
        _entity_count(sketch) == before_n + 1,
        "%d -> %d" % (before_n, _entity_count(sketch)),
    )
    expect_a = [(10_000_000, 0), (100_000_000, 0)]
    expect_b = [(0, 10_000_000), (0, 100_000_000)]
    got_a = _read_line(a)
    got_b = _read_line(b)
    check(
        "chamfer_trim_line_a_exact",
        got_a in (expect_a, list(reversed(expect_a))),
        "a=%r" % (got_a,),
    )
    check(
        "chamfer_trim_line_b_exact",
        got_b in (expect_b, list(reversed(expect_b))),
        "b=%r" % (got_b,),
    )
    new_ent = _find_entity_by_uuid(sketch, uuids[2]) if len(uuids) == 3 else None
    got_new = _read_line(new_ent) if new_ent is not None else None
    expect_new = [(10_000_000, 0), (0, 10_000_000)]
    check(
        "chamfer_new_line_endpoints_exact",
        got_new in (expect_new, list(reversed(expect_new))),
        "new=%r" % (got_new,),
    )
    if got_new:
        length = _dist(got_new[0], got_new[1])
        ideal = 10_000_000 * math.sqrt(2.0)
        check(
            "chamfer_new_line_length_45deg_within_1nm",
            abs(length - ideal) <= 1.0,
            "len=%.3f ideal=%.3f" % (length, ideal),
        )
    else:
        check("chamfer_new_line_length_45deg_within_1nm", False, "cannot find the newly created entity")

    # 真数据后果：改完的文档仍能过 L1 序列化/反序列化，且三个 uuid 都在
    try:
        doc2 = dm.document_from_l1(dm.document_to_l1(doc))
        kept = all(_find_entity_by_uuid(doc2, uid) is not None for uid in uuids)
    except Exception as exc:
        kept = False
        print("INFO L1 round-trip failed: %r" % (exc,))
    check("chamfer_l1_roundtrip_keeps_all_uuids", kept)

    if base_problems is not None:
        try:
            after_problems = len(dm.check_invariants(doc))
            check(
                "chamfer_no_new_invariant_violations",
                after_problems <= base_problems,
                "%d -> %d" % (base_problems, after_problems),
            )
        except Exception as exc:
            print("INFO check_invariants unavailable after chamfer: %r" % (exc,))

    failed = [name for name, ok in results if not ok]
    print("SELFTEST family=edit_ops op=chamfer checks=%d failed=%d" % (len(results), len(failed)))
    if failed:
        print("FEATURE_FAIL family=edit_ops op=chamfer verdict=FAIL failed=%s" % (",".join(failed),))
        return 1
    print("FEATURE_OK family=edit_ops op=chamfer verdict=PASS")
    return 0


if __name__ == "__main__":
    _sys.exit(selftest())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 edit_ops__w24.py:163 "目标里找不到可编辑的草图容器（sketch-like）" -> "no editable sketch-like container in the target"
# 原 edit_ops__w24.py:346 "元组容器不可原地写" -> "tuple container cannot be written in place"
# 原 edit_ops__w24.py:366 "找不到几何容器，无法写端点" -> "no geometry container found; cannot write endpoints"
# 原 edit_ops__w24.py:436 "无法确定该 L1 实体的端点字段布局（探针未覆盖到 4 个坐标槽）" -> "cannot determine the endpoint field layout of this L1 entity (probe did not cover 4 coordinate slots)"
# 原 edit_ops__w24.py:505 "无法为新倒角线分配独立 uuid" -> "cannot allocate a distinct uuid for the new chamfer line"
# 原 edit_ops__w24.py:560 "交点到直线两端等距，无法判定保留侧；请用 keep_a/keep_b 指定" -> "intersection is equidistant from both ends; cannot pick the keep side; pass keep_a/keep_b"
# 原 edit_ops__w24.py:591 "chamfer 需要两条直线的引用：ref_a/ref_b（或 refs=(a, b)）" -> "chamfer needs two line references: ref_a/ref_b (or refs=(a, b))"
# 原 edit_ops__w24.py:605 "两条倒角边不能是同一个实体" -> "the two chamfer edges must not be the same entity"
# 原 edit_ops__w24.py:610 "无法读出直线端点（L1 布局不识别）" -> "cannot read line endpoints (unrecognized L1 layout)"
# 原 edit_ops__w24.py:619 "必须给 distance_nm、d1_nm/d2_nm 或 angle_deg 之一" -> "one of distance_nm, d1_nm/d2_nm or angle_deg is required"
# 原 edit_ops__w24.py:626 "零长度直线无法倒角" -> "a zero-length line cannot be chamfered"
# 原 edit_ops__w24.py:631 "两条直线平行，无限延长线无交点，无法倒角" -> "the two lines are parallel; their infinite extensions do not intersect; cannot chamfer"
# 原 edit_ops__w24.py:643 "交点到保留端点的长度为 0" -> "the intersection is 0 away from the kept endpoint"
# 原 edit_ops__w24.py:709 "倒角长度四舍五入到纳米后为 0" -> "the chamfer length rounds to 0 nanometers"
# 原 edit_ops__w24.py:713 "四舍五入后缩进点落在保留端点之外" -> "after rounding, the setback point falls outside the kept endpoint"
# 原 edit_ops__w24.py:796 "SELFTEST fixture=unavailable：L1 文档模型里找不到任何 line 实体" -> "SELFTEST fixture=unavailable: no line entity found in the L1 document model"
# 原 edit_ops__w24.py:893 "找不到新建实体" -> "cannot find the newly created entity"
# 原 edit_ops__w24.py:215 "实体引用为空" -> "entity reference is empty"
# 原 edit_ops__w24.py:219 "找不到实体 %r" -> "entity not found: %r"
# 原 edit_ops__w24.py:361 "mod_geom 写策略不可用" -> "mod_geom write strategy unavailable"
# 原 edit_ops__w24.py:445 "端点写回后读回值不符（%r -> %r）" -> "endpoint read-back mismatch after write (%r -> %r)"
# 原 edit_ops__w24.py:515 "%s 必须是整数纳米（收到 %r）" -> "%s must be an integer number of nanometers (got %r)"
# 原 edit_ops__w24.py:518 "%s 必须是整数纳米（收到 %r）" -> "%s must be an integer number of nanometers (got %r)"
# 原 edit_ops__w24.py:520 "%s 必须为正纳米" -> "%s must be positive nanometers"
# 原 edit_ops__w24.py:556 "keep 只能是 start/end/auto（收到 %r）" -> "keep must be one of start/end/auto (got %r)"
# 原 edit_ops__w24.py:601 "CHAMFER 只支持直线（line）；圆/弧/多段线不参与部分倒角：kinds=%r" -> "CHAMFER supports lines only; circles/arcs/polylines cannot take a partial chamfer: kinds=%r"
# 原 edit_ops__w24.py:647 "angle_deg 模式需要 d1_nm/d2_nm 之一作为第一条线的距离" -> "angle_deg mode needs one of d1_nm/d2_nm as the distance on the first line"
# 原 edit_ops__w24.py:652 "第一条线的倒角距离 %dnm 不小于保留段长度 %.0fnm" -> "the chamfer distance %dnm on the first line is not smaller than the kept segment length %.0fnm"
# 原 edit_ops__w24.py:666 "距离模式需要 d2_nm 或 distance_nm" -> "distance mode needs d2_nm or distance_nm"
# 原 edit_ops__w24.py:695 "距离-角度模式下倒角线与第二条直线在保留侧无交点" -> "in distance-angle mode the chamfer line does not meet the second line on the kept side"
# 原 edit_ops__w24.py:419 "端点探针无法还原实体几何，拒绝在不可信状态上继续" -> "the endpoint probe cannot reconstruct entity geometry; refusing to continue from an untrusted state"
# 原 edit_ops__w24.py:589 "refs 必须是 (ref_a, ref_b)" -> "refs must be (ref_a, ref_b)"
# 原 edit_ops__w24.py:670 "第二条线的倒角距离 %dnm 不小于保留段长度 %.0fnm" -> "the chamfer distance %dnm on the second line is not smaller than the kept segment length %.0fnm"
# 原 edit_ops__w24.py:679 "angle_deg 必须落在 (0, 180)（收到 %r）" -> "angle_deg must lie in (0, 180) (got %r)"
# 原 edit_ops__w24.py:702 "角度模式推出的第二条线缩进 %.0fnm 不小于保留段长度 %.0fnm" -> "the second-line setback %.0fnm derived from angle mode is not smaller than the kept segment length %.0fnm"
# 原 edit_ops__w24.py:818 "INFO check_invariants 不可用：%r" -> "INFO check_invariants unavailable: %r"
# 原 edit_ops__w24.py:901 "INFO L1 往返失败：%r" -> "INFO L1 round-trip failed: %r"
# 原 edit_ops__w24.py:501 "无法克隆直线模板：%s" -> "cannot clone the line template: %s"
# 原 edit_ops__w24.py:749 "INFO _sample_document 失败：%r" -> "INFO _sample_document failed: %r"
# 原 edit_ops__w24.py:794 "INFO fixture 兜底失败：%r" -> "INFO fixture fallback failed: %r"
# 原 edit_ops__w24.py:913 "INFO 倒角后 check_invariants 不可用：%r" -> "INFO check_invariants unavailable after chamfer: %r"
