#!/usr/bin/env python3
"""cadcore.ops.features.edit_ops__w16 —— edit_ops 族分片：**旋转（ROTATE）**。

落在哪一条 must_have 上
-----------------------
真源：`docs/research/cad-parity/features/autocad-edit.json` 的 `must_have`（21 条）与
`docs/research/cad-parity/status/autocad-edit.json` 的逐条 `status`。仍 missing/partial 的条目按清单顺序编号
0..N-1 为：0 对象选择 / 1 夹点编辑 / 2 对象捕捉与追踪 / 3 精确坐标输入 / 4 删除 / 5 移动 / 6 复制 /
**7 旋转** / 8 缩放 / 9 镜像 / 10 偏移 / 11 阵列 / 12 修剪 / 13 延伸 / 14 圆角 / 15 倒角 / 16 拉伸 /
17 分解 / 18 特性匹配 / 19 对齐（第 18 条"放弃与重做"已 implemented，故不占号）。本分片负责 **第 7 号：
旋转（别名 ROTATE / RO，"含 Copy/Reference 选项"）**。

已落地的部分与真正的缺口
------------------------
`cadcore/edit/modify.py:602 rotate` 只覆盖**绝对角、原地改写**这一种用法（族模块 `cadcore.ops.edit_ops`
把它原样收编成 `OPERATIONS["rotate"]`）。must_have 里写明要的 **Copy 选项**（转副本、原件不动，含多重复制）
与 **Reference 选项**（按参照角/两点定参照，再给目标角/目标点，实际转动 = 目标角 − 参照角）此前**没有落点**
（`grep -n "ref_angle\\|rotate_copy" packages/cadcore` 命中 0）。本模块把这两条补成真几何，落在 L1 上。

实现口径（真几何，无桩）
------------------------
- 几何全部由 `cadcore.edit.modify` 的**公开**原语组合完成：先 `copy(dx=dy=0)` 造真副本（真新增实体、
  真 uuid），再 `rotate(...)` 把它们真的转到新角度；L1 实体字段被真的改写，不是返回个新对象就完事。
- **只写整数纳米**：坐标仍由 `modify` 的纳米取整路径落盘（旋转 90° 时逐字段精确成立，见自检）。
- **绝不半接受**：每个操作先快照实体列表，任何一步抛错就把实体列表整体还原成改前状态再抛出
  （`count>1` 时这一点才有意义：第 k 份失败不会留下第 1..k-1 份）。
- **失败一律抛 `cadcore.errors.CadError`**（本模块抛的是它的子类，带机器可读 `reason`）：
  `invalid_argument`（入参形态/互斥选项）、`empty_selection`（refs 为空，**不静默空操作**）、
  `degenerate_geometry`（参照两点重合 / 目标点与基点重合 ⇒ 方向未定义）、`no_target_sketch`
  （文档形态但 `sketches` 为空，**不静默造一张空草图**）；未知 uuid 由 `modify` 抛出，原样上抛。
- **本模块不实现撤销栈**：族撤销账（`cadcore.ops.edit_ops` 的 `HISTORY_KEY` 快照栈）是**唯一一份**实现，
  这里不抄第二份、也不假装记账；需要 undo 的下游请走族模块的 `undo`/`redo`（或先经族模块的收费入口）。

诚实的边界（不写 missing 谎报，也不写 implemented 谎报）
-------------------------------------------------------
本文件能写到的东西全部是真实现。must_have 第 7 条的**另一半**缺口 ——"没有 ROTATE 命令入口"
（`apps/opencad/server.py:475-542` 的 verb 白名单里没有旋转、`app.js` 无 `rotate` 工具）—— 在
**别人的文件**里（本席名下只准写 `cadcore/ops/features/edit_ops__w16.py`），故那半条仍是 missing，
本模块**不**声称它已解决。

冻结接口
--------
`FAMILY` / `OPERATIONS` / 每个操作 `op(sk_or_doc, **kwargs) -> list[str]`（返回受影响 uuid 列表）。

自检：``python3 cadcore/ops/features/edit_ops__w16.py``（或 ``python3 -m cadcore.ops.features.edit_ops__w16``）
真跑 7 组断言，末两行打印 ``FEATURE_OK family=edit_ops op=<名> verdict=PASS``。

中文只出现在 docstring 与注释里（`tools/cad/i18n_gate.py` 口径：非 docstring 的含汉字字符串常量算
`hardcoded_literals`）。
"""

from __future__ import annotations

import copy as _copy
import json as _json
import math as _math
import os as _os
import sys as _sys
from typing import Any, Callable, Mapping, Sequence

if __package__ not in (None, ""):  # 正常路径：作为包模块导入
    from cadcore.edit import modify as _modify
    from cadcore.errors import CadError as _BaseCadError
else:  # pragma: no cover - 以脚本方式直接运行（自带自检入口）时补包上下文
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.abspath(__file__))))))
    from cadcore.edit import modify as _modify
    from cadcore.errors import CadError as _BaseCadError

__all__ = [
    "FAMILY",
    "OPERATIONS",
    "CadError",
    "PARITY_ITEM_INDEX",
    "PARITY_ITEM_NAME",
    "SUPPORTED_KINDS",
    "rotate_copy",
    "rotate_reference",
    "selftest",
]

#: 族 id（冻结接口里的 `FAMILY`）。
FAMILY = "edit_ops"

#: 本分片在 `autocad-edit.json` must_have 的 missing/partial 子序列里的序号（0 起）。
PARITY_ITEM_INDEX = 7

#: 本分片对应的条目名（真源里的原文，机读用）。
PARITY_ITEM_NAME = "Rotate"

#: 本模块支持的实体种类（= L1 冻结集，与 `cadcore.edit.modify` 同源，不另立一份）。
SUPPORTED_KINDS: tuple[str, ...] = tuple(_modify.SUPPORTED_KINDS)


# --------------------------------------------------------------------------------------
# 错误族（收敛到 cadcore.errors.CadError；reason 是下游唯一的归因入口）
# --------------------------------------------------------------------------------------


class CadError(_BaseCadError):
    """旋转命令层的失败信号（`cadcore.errors.CadError` 的子类，带机器可读 `reason`）。"""

    default_reason = "cad_error"

    def __init__(self, message: str = "rotate command failed", *, reason: str = "", **details: Any) -> None:
        super().__init__(message, reason=reason, **details)


def _fail(reason: str, detail: str, **details: Any) -> "CadError":
    """构造失败信号（文案 ASCII；`reason` 是下游唯一的归因入口）。"""
    return CadError(detail, reason=reason, **details)


# --------------------------------------------------------------------------------------
# 目标解析（草图 / 文档两种形态；不猜第三种）
# --------------------------------------------------------------------------------------


def _entities_of(sketch: Any) -> list[Any]:
    """草图/文档的实体列表 —— **可原地增删的那个 list**（不是快照副本）。"""
    entities = sketch.get("entities") if isinstance(sketch, Mapping) else getattr(sketch, "entities", None)
    if not isinstance(entities, list):
        raise _fail("geometry_invalid", "sketch.entities must be a mutable list",
                    got=type(entities).__name__)
    return entities


def _sketch_of(sk_or_doc: Any) -> Any:
    """把 `sk_or_doc` 解析成**编辑面**：本身像草图就用它，带 `sketches` 就取第一张。

    `sketches` 为空 ⇒ `no_target_sketch`（**不静默造一张空草图**，否则旋转会"成功"地作用在空气上）。
    """
    container = sk_or_doc.get("sketches") if isinstance(sk_or_doc, Mapping) else getattr(sk_or_doc, "sketches", None)
    if container is None or isinstance(container, (str, bytes)) or not isinstance(container, Sequence):
        return sk_or_doc  # 草图形态（或形状不合规 ⇒ 交给 _entities_of 报 geometry_invalid）
    sketches = list(container)
    if not sketches:
        raise _fail("no_target_sketch", "document has no sketch to rotate")
    return sketches[0]


# --------------------------------------------------------------------------------------
# 入参把关（长度一律整数纳米；角度是浮点度）
# --------------------------------------------------------------------------------------


def _as_point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对（bool 不算数；非整数浮点/NaN 一律报错）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise _fail("invalid_argument", f"{what} must be a (x_nm, y_nm) pair", what=what, value=repr(value))
    out: list[int] = []
    for axis, item in zip(("x", "y"), value):
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not _math.isfinite(float(item)):
            raise _fail("invalid_argument", f"{what}.{axis} must be a finite nanometre value",
                        what=what, value=repr(item))
        if isinstance(item, float) and float(item) != float(int(item)):
            raise _fail("invalid_length", f"{what}.{axis} must be a whole number of nm",
                        what=what, value=repr(item))
        out.append(int(item))
    return (out[0], out[1])


def _as_angle(value: Any, *, what: str) -> float:
    """角度入参 → 有限浮点度（角度不是长度，允许任意实数，按 mod 360 生效）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not _math.isfinite(float(value)):
        raise _fail("invalid_argument", f"{what} must be a finite angle in degrees", what=what, value=repr(value))
    return float(value)


def _as_count(value: Any, *, what: str) -> int:
    """计数入参 → 正整数。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail("invalid_argument", f"{what} must be a positive integer", what=what, value=repr(value))
    return int(value)


def _check_refs(refs: Any) -> list[str]:
    """选择集入参 → uuid 字符串列表；空集 ⇒ `empty_selection`（**不静默空操作**）。"""
    if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
        raise _fail("invalid_argument", "refs must be a sequence of entity uuid strings",
                    got=type(refs).__name__)
    items = list(refs)
    if not items:
        raise _fail("empty_selection", "refs is empty: nothing to rotate")
    for item in items:
        if not isinstance(item, str) or not item:
            raise _fail("invalid_argument", "every ref must be a non-empty uuid string", value=repr(item))
    return items


def _direction_deg(from_nm: tuple[int, int], to_nm: tuple[int, int], *, what: str) -> float:
    """`from -> to` 的方向角（度，CCW，x 轴为 0）。两点重合 ⇒ `degenerate_geometry`。"""
    dx = to_nm[0] - from_nm[0]
    dy = to_nm[1] - from_nm[1]
    if dx == 0 and dy == 0:
        raise _fail("degenerate_geometry", f"{what} is undefined: the two points coincide", what=what)
    return _math.degrees(_math.atan2(dy, dx))


def _normalize_delta(deg: float) -> float:
    """实际转动角归一化到 `(-180, 180]`（几何等价；只为让返回值/日志里不出现 350 这种角）。"""
    value = _math.fmod(float(deg), 360.0)
    if value <= -180.0:
        value += 360.0
    elif value > 180.0:
        value -= 360.0
    return 0.0 if value == 0.0 else value


# --------------------------------------------------------------------------------------
# 失败即整体回滚（绝不留半成品）
# --------------------------------------------------------------------------------------


def _snapshot(sketch: Any) -> list[Any]:
    """改前实体列表的深拷贝（失败时用它整体还原）。"""
    return _copy.deepcopy(_entities_of(sketch))


def _restore(sketch: Any, snapshot: list[Any]) -> None:
    """把实体列表还原成 `snapshot`（原地切片赋值，保证引用同一 list 的下游也看得见）。"""
    _entities_of(sketch)[:] = snapshot


# --------------------------------------------------------------------------------------
# 操作一：ROTATE 的 Copy 选项（含多重复制）
# --------------------------------------------------------------------------------------


def rotate_copy(sk_or_doc: Any, *, refs: Any, center_nm: Any, angle_deg: Any, count: Any = 1) -> list[str]:
    """**旋转复制**（ROTATE 的 Copy 选项）：原件一个字节不动，返回**新实体 uuid** 列表。

    - 第 `k` 份（`k = 1..count`）是原件绕 `center_nm` 逆时针转 `angle_deg * k` 度的真副本 ⇒
      `count=3, angle_deg=90` 得到 90°/180°/270° 三份（AutoCAD 里就是连转三次各留一份的等价物）；
    - 返回顺序：按份递增、份内按 `refs` 的解析顺序（重复 uuid 只算一次，由 `modify.copy` 的口径决定）；
    - `count < 1` ⇒ `invalid_argument`；`refs` 空 ⇒ `empty_selection`；未知 uuid ⇒ 由 `modify` 抛错上抛；
    - **原子性**：任何一份失败 ⇒ 本次调用新增的副本全部撤销，实体列表回到改前状态再抛错。
    """
    sketch = _sketch_of(sk_or_doc)
    _entities_of(sketch)
    selected = _check_refs(refs)
    center = _as_point(center_nm, what="center_nm")
    angle = _as_angle(angle_deg, what="angle_deg")
    copies = _as_count(count, what="count")

    snapshot = _snapshot(sketch)
    created: list[str] = []
    try:
        for step in range(1, copies + 1):
            clone_ids = _modify.copy(sketch, selected, dx_nm=0, dy_nm=0)
            _modify.rotate(sketch, clone_ids, center_nm=center, angle_deg=angle * step)
            created.extend(clone_ids)
    except BaseException:
        _restore(sketch, snapshot)
        raise
    return created


# --------------------------------------------------------------------------------------
# 操作二：ROTATE 的 Reference 选项（两点/角度定参照 → 目标角/目标点）
# --------------------------------------------------------------------------------------


def rotate_reference(sk_or_doc: Any, *, refs: Any, center_nm: Any, ref_angle_deg: Any = None,
                     ref_from_nm: Any = None, ref_to_nm: Any = None, new_angle_deg: Any = None,
                     new_point_nm: Any = None, copy: Any = False) -> list[str]:
    """**按参照旋转**（ROTATE 的 Reference 选项）：实际转动 = 目标角 − 参照角。

    - 参照角二选一（**必须恰好给一种**，两种都给或都不给 ⇒ `invalid_argument`）：
      ① `ref_angle_deg=<度>`（键盘输入参照角）；② `ref_from_nm` + `ref_to_nm`（拾两点定方向，
      角由 `atan2` 得出，两点重合 ⇒ `degenerate_geometry`）。
    - 目标角二选一（同样**必须恰好给一种**）：① `new_angle_deg=<度>`；② `new_point_nm=<点>`
      （相对基点拾点定新方向，点与基点重合 ⇒ `degenerate_geometry`）。
    - `copy=False`（缺省）：**原地**旋转，返回**原 uuid**（与 `modify.rotate` 同序）；
      `copy=True`：转的是真副本，原件不动，返回**新 uuid** —— 这就是 ROTATE 里 Reference 与 Copy 同用的语义。
    - 差角按 `(-180, 180]` 归一化（几何等价，只为可读）；差角为 0 是**合法**空转（返回受影响 uuid，不报错）。
    """
    sketch = _sketch_of(sk_or_doc)
    _entities_of(sketch)
    selected = _check_refs(refs)
    center = _as_point(center_nm, what="center_nm")

    ref_by_points = ref_from_nm is not None or ref_to_nm is not None
    if ref_angle_deg is not None and ref_by_points:
        raise _fail("invalid_argument",
                    "reference angle is over-specified: give either ref_angle_deg or ref_from_nm/ref_to_nm",
                    got="ref_angle_deg+points")
    if ref_angle_deg is None and not (ref_from_nm is not None and ref_to_nm is not None):
        raise _fail("invalid_argument",
                    "reference angle is missing: give ref_angle_deg or both ref_from_nm and ref_to_nm")

    if new_angle_deg is not None and new_point_nm is not None:
        raise _fail("invalid_argument",
                    "target angle is over-specified: give either new_angle_deg or new_point_nm",
                    got="new_angle_deg+new_point_nm")
    if new_angle_deg is None and new_point_nm is None:
        raise _fail("invalid_argument",
                    "target angle is missing: give new_angle_deg or new_point_nm")

    if ref_by_points:
        reference = _direction_deg(_as_point(ref_from_nm, what="ref_from_nm"),
                                  _as_point(ref_to_nm, what="ref_to_nm"), what="ref_from_nm->ref_to_nm")
    else:
        reference = _as_angle(ref_angle_deg, what="ref_angle_deg")

    if new_point_nm is not None:
        target_point = _as_point(new_point_nm, what="new_point_nm")
        target = _direction_deg(center, target_point, what="center_nm->new_point_nm")
    else:
        target = _as_angle(new_angle_deg, what="new_angle_deg")

    delta = _normalize_delta(target - reference)
    make_copy = bool(copy)

    snapshot = _snapshot(sketch)
    try:
        if make_copy:
            created = _modify.copy(sketch, selected, dx_nm=0, dy_nm=0)
            _modify.rotate(sketch, created, center_nm=center, angle_deg=delta)
        else:
            created = _modify.rotate(sketch, selected, center_nm=center, angle_deg=delta)
    except BaseException:
        _restore(sketch, snapshot)
        raise
    return list(created)


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "rotate_copy": rotate_copy,
    "rotate_reference": rotate_reference,
}


# --------------------------------------------------------------------------------------
# 自检（7 组；判定行 FEATURE_OK family=edit_ops op=<名> verdict=PASS）
# --------------------------------------------------------------------------------------

#: 取几何数字时要跳过的非几何键（长度类特性不是坐标，会把断言污染成假绿）。
_NON_GEOMETRY_KEYS = frozenset({"lineweight_nm", "uuid", "layer", "linetype", "color"})


def _primitives() -> Any:
    """草图原语模块（构造自检夹具用；两种运行方式都要能导入）。"""
    if __package__ not in (None, ""):
        from cadcore.sketch import primitives as module
    else:  # pragma: no cover - 脚本方式运行
        from cadcore.sketch import primitives as module
    return module


def _numbers(value: Any, skip: frozenset[str]) -> list[float]:
    """递归收集容器里的数字（bool 不算；键名在 `skip` 里的整枝跳过）。"""
    out: list[float] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key in skip:
                continue
            out.extend(_numbers(item, skip))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            out.extend(_numbers(item, skip))
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        out.append(float(value))
    return out


def _geometry_numbers(entity: Any) -> list[float]:
    """实体的几何数字（有序）—— 有嵌套 `geom` 就只取它，否则取整实体并跳过非几何键。

    这样断言**不依赖字段命名**（`x1/y1/x2/y2` 还是 `p1/p2` 都行），测的是"坐标真的变了没有"。
    """
    geom = entity.get("geom") if isinstance(entity, Mapping) else getattr(entity, "geom", None)
    if isinstance(geom, Mapping):
        return sorted(_numbers(geom, frozenset()))
    return sorted(_numbers(entity, _NON_GEOMETRY_KEYS))


def _entity_by_uuid(sketch: Any, uuid_: str) -> Any:
    """按 uuid 找实体（找不到就是夹具/实现坏了，直接断言失败）。"""
    for entity in _entities_of(sketch):
        value = entity.get("uuid") if isinstance(entity, Mapping) else getattr(entity, "uuid", None)
        if value == uuid_:
            return entity
    raise AssertionError(f"entity {uuid_!r} not found in sketch")


def _canon(value: Any) -> str:
    """规范化文本（逐字段比较用；`default=str` 让 dataclass 也能落成稳定文本）。"""
    return _json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _rot_int(point: tuple[int, int], center: tuple[int, int], deg: float) -> tuple[int, int]:
    """90° 整数倍的**独立整数 oracle**（不经过 cadcore 的浮点矩阵），做精确相等断言用。"""
    quarter = int(round(float(deg) / 90.0)) % 4
    dx, dy = point[0] - center[0], point[1] - center[1]
    if quarter == 0:
        rx, ry = dx, dy
    elif quarter == 1:
        rx, ry = -dy, dx
    elif quarter == 2:
        rx, ry = -dx, -dy
    else:
        rx, ry = dy, -dx
    return (center[0] + rx, center[1] + ry)


def _rot_float(point: tuple[int, int], center: tuple[int, int], deg: float) -> tuple[float, float]:
    """任意角度的独立 oracle（浮点；配 ±1 nm 容差 —— L1 落盘必须是整数纳米，误差上界 0.5 nm）。"""
    angle = _math.radians(float(deg))
    dx, dy = point[0] - center[0], point[1] - center[1]
    return (center[0] + dx * _math.cos(angle) - dy * _math.sin(angle),
            center[1] + dx * _math.sin(angle) + dy * _math.cos(angle))


def _expect(points: Sequence[tuple[int, int]], center: tuple[int, int], deg: float) -> list[float]:
    """整数倍 90° 的期望几何数字（有序）。"""
    return sorted(float(v) for point in points for v in _rot_int(point, center, deg))


def _new_sketch() -> dict[str, Any]:
    """空草图的 L1 形态（与 `cadcore.ops.edit_ops` 自检同形）。"""
    return {"entities": [], "constraints": [], "dimensions": []}


def selftest() -> int:
    """7 组真断言：接口契约 / Copy 精确几何 / 多重复制 / 两点参照 / 角度参照 / 错误原子性 / 文档形态。"""
    primitives = _primitives()
    checks: list[str] = []

    # 1) 冻结接口
    assert FAMILY == "edit_ops"
    assert set(OPERATIONS) == {"rotate_copy", "rotate_reference"}, sorted(OPERATIONS)
    for name, fn in OPERATIONS.items():
        assert callable(fn), name
    assert SUPPORTED_KINDS == ("line", "arc", "circle", "polyline"), SUPPORTED_KINDS
    checks.append("contract")

    # 2) ROTATE + Copy：原件逐字段不动，副本 90° 精确（整数 oracle 精确相等）
    sk = _new_sketch()
    p1, p2 = (1234567, 7654321), (2345678, 8765432)
    source = primitives.add_line(sk, p1=p1, p2=p2)
    before = _canon(sk["entities"][0])
    center = (1111111, 2222222)
    made = rotate_copy(sk, refs=[source], center_nm=center, angle_deg=90)
    assert len(made) == 1 and made[0] != source, made
    assert len(sk["entities"]) == 2, len(sk["entities"])
    assert _canon(_entity_by_uuid(sk, source)) == before, "the original must stay byte-identical"
    got = _geometry_numbers(_entity_by_uuid(sk, made[0]))
    assert got == _expect((p1, p2), center, 90), (got, _expect((p1, p2), center, 90))
    checks.append("rotate_copy_exact_90")

    # 3) 多重复制：count=3 ⇒ 三份新 uuid，第 k 份转 90k°（360° 那句就是原件本身）
    sk = _new_sketch()
    q1, q2 = (1000000, 500000), (2000000, 500000)
    source = primitives.add_line(sk, p1=q1, p2=q2)
    made = rotate_copy(sk, refs=[source], center_nm=(0, 0), angle_deg=90, count=3)
    assert len(made) == 3 and len(set(made)) == 3 and source not in made, made
    assert len(sk["entities"]) == 4, len(sk["entities"])
    for step, uuid_ in enumerate(made, start=1):
        got = _geometry_numbers(_entity_by_uuid(sk, uuid_))
        assert got == _expect((q1, q2), (0, 0), 90 * step), (step, got, _expect((q1, q2), (0, 0), 90 * step))
    assert _geometry_numbers(_entity_by_uuid(sk, made[2])) != _geometry_numbers(_entity_by_uuid(sk, source)), \
        "the 270-degree copy must not coincide with the original"
    for bad in (0, -1, 1.5, True):
        try:
            rotate_copy(sk, refs=[source], center_nm=(0, 0), angle_deg=90, count=bad)
        except CadError as exc:
            assert exc.reason == "invalid_argument", (bad, exc.reason)
        else:
            raise AssertionError(f"count={bad!r} must be rejected")
    checks.append("rotate_copy_multi")

    # 4) Reference 选项：拾两点定参照（0°）+ 拾点定目标（90°）⇒ 真转 90°，且 copy=True 原件不动
    sk = _new_sketch()
    r1, r2 = (500000, 500000), (1500000, 500000)
    source = primitives.add_line(sk, p1=r1, p2=r2)
    before = _canon(sk["entities"][0])
    center = (500000, 500000)
    made = rotate_reference(sk, refs=[source], center_nm=center,
                            ref_from_nm=(500000, 500000), ref_to_nm=(600000, 500000),
                            new_point_nm=(500000, 1500000), copy=True)
    assert len(made) == 1 and made[0] != source, made
    assert _canon(_entity_by_uuid(sk, source)) == before, "copy=True must leave the original untouched"
    got = _geometry_numbers(_entity_by_uuid(sk, made[0]))
    assert got == _expect((r1, r2), center, 90), (got, _expect((r1, r2), center, 90))
    checks.append("rotate_reference_two_point")

    # 5) Reference 选项：键盘角 30° -> 90° ⇒ 原地真转 +60°（任意角用 ±1 nm 容差）
    sk = _new_sketch()
    s1, s2 = (1000000, 0), (2000000, 0)
    source = primitives.add_line(sk, p1=s1, p2=s2)
    ids = rotate_reference(sk, refs=[source], center_nm=(0, 0), ref_angle_deg=30, new_angle_deg=90)
    assert ids == [source], ids
    got = _geometry_numbers(_entity_by_uuid(sk, source))
    want = sorted(float(v) for point in (s1, s2) for v in _rot_float(point, (0, 0), 60.0))
    assert len(got) == len(want) and all(abs(a - b) <= 1.0 for a, b in zip(got, want)), (got, want)
    assert got != sorted(float(v) for point in (s1, s2) for v in point), "the geometry must have moved"
    # 差角为 0 是合法空转：几何不动，但仍返回受影响 uuid
    ids = rotate_reference(sk, refs=[source], center_nm=(0, 0), ref_angle_deg=45, new_angle_deg=45)
    assert ids == [source]
    got_after = _geometry_numbers(_entity_by_uuid(sk, source))
    assert all(abs(a - b) <= 1.0 for a, b in zip(got_after, got)), (got, got_after)
    checks.append("rotate_reference_angles")

    # 6) 错误路径 + 原子性：坏入参/坏 ref 一律 CadError，且文档一个字节都不动
    sk = _new_sketch()
    source = primitives.add_line(sk, p1=(1000000, 0), p2=(2000000, 0))
    frozen = _canon(sk["entities"])
    for kwargs, reason in (
        (dict(ref_angle_deg=0.0), "invalid_argument"),                                   # 缺目标角
        (dict(new_angle_deg=0.0), "invalid_argument"),                                   # 缺参照角
        (dict(ref_angle_deg=0.0, new_angle_deg=0.0, new_point_nm=(1, 1)), "invalid_argument"),
        (dict(ref_angle_deg=0.0, ref_from_nm=(0, 0), ref_to_nm=(1, 0), new_angle_deg=0.0),
         "invalid_argument"),                                                            # 参照给重了
        (dict(ref_from_nm=(0, 0), ref_to_nm=(0, 0), new_angle_deg=45.0), "degenerate_geometry"),
        (dict(ref_angle_deg=0.0, new_point_nm=(0, 0)), "degenerate_geometry"),           # 目标点=基点
    ):
        try:
            rotate_reference(sk, refs=[source], center_nm=(0, 0), **kwargs)
        except _BaseCadError as exc:
            assert exc.reason == reason, (kwargs, exc.reason, reason)
        else:
            raise AssertionError(f"expected CadError reason={reason} for {kwargs!r}")
    for bad_call in (
        lambda: rotate_copy(sk, refs=[], center_nm=(0, 0), angle_deg=90),
        lambda: rotate_copy(sk, refs=["no-such-uuid"], center_nm=(0, 0), angle_deg=90, count=2),
        lambda: rotate_reference(sk, refs=[source], center_nm=(0, 0), ref_angle_deg=0.0,
                                 new_angle_deg=float("nan")),
    ):
        try:
            bad_call()
        except _BaseCadError:
            pass
        else:
            raise AssertionError("expected CadError for the bad call")
    assert _canon(sk["entities"]) == frozen, "a failed call must not leave a half-built result"
    checks.append("errors_atomic")

    # 7) 文档形态（sketches[0]）与空文档报错
    class _Doc:
        """最小文档形态（只要求有 `sketches`）。"""

        def __init__(self, sketches: list[Any]) -> None:
            self.sketches = sketches

    sk = _new_sketch()
    d1, d2 = (1000000, 0), (2000000, 0)
    source = primitives.add_line(sk, p1=d1, p2=d2)
    doc = _Doc([sk])
    made = rotate_copy(doc, refs=[source], center_nm=(0, 0), angle_deg=180)
    assert len(made) == 1 and len(sk["entities"]) == 2, (made, len(sk["entities"]))
    assert _geometry_numbers(_entity_by_uuid(sk, made[0])) == _expect((d1, d2), (0, 0), 180)
    try:
        rotate_copy(_Doc([]), refs=[source], center_nm=(0, 0), angle_deg=90)
    except CadError as exc:
        assert exc.reason == "no_target_sketch", exc.reason
    else:
        raise AssertionError("an empty document must raise no_target_sketch")
    checks.append("document_form")

    print(f"EDIT_OPS_W16_SELFTEST verdict=PASS checks={len(checks)} list={','.join(checks)}")
    for name in ("rotate_copy", "rotate_reference"):
        print(f"FEATURE_OK family={FAMILY} op={name} verdict=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 edit_ops__w16.py:89 "旋转" -> "Rotate"
