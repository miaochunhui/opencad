#!/usr/bin/env python3
"""cadcore.ops.features.sw_assembly__w12 —— 装配族 w12 片：**配合对齐方向控制**（aligned / anti-aligned）。

一句话用途
----------
给装配配合补上"**方向对齐 / 反转对齐**"这一格：``mate_align()`` 把已记录的配合在
``aligned``（同向）与 ``anti_aligned``（反向）之间切换，并**真的把第二个零部件刚性搬到另一侧**
（参考点过第一参考的镜像面 + 绕该点 180° 反向），随后当场重算残差/状态。这是
`cadcore.ops.features.<family>__w<k>` 家族的一个**片**（shard）。

本片负责的缺条目（编号依据）
----------------------------
依据 ``docs/research/cad-parity/status/sw-assembly.json`` 的 ``items`` 顺序，按**逐条 status**
筛出仍 ``missing`` / ``partial`` 的 must_have（0-based）：

====  ======================  =========  ====================================
编号  条目                     状态       说明
====  ======================  =========  ====================================
0     **配合对齐方向控制**     missing    ← 本片负责（第 3 号：3 mod 3 = 0）
1     过定义与配合冲突诊断     partial    记录级残差诊断已有；过定义自动解无
2     干涉检查                 partial    世界 AABB 粗检查已有；精确布尔求交无
====  ======================  =========  ====================================

N = 3 ⇒ 第 3 号顺延取模得 0 号 = **配合对齐方向控制**（原证据：严格词边界 grep
``aligned|anti_aligned|antialigned`` 在 cadcore+opencad+dxf 命中 0；族模块只把 ``flip`` 当
建配合时的入参，没有任何"事后切换对齐"的入口）。

⚠️ 口径声明（不一致不藏）：同一份 status JSON 的汇总计数写 ``implemented=0 / partial=0 /
missing=21 / coverage_pct=0``，与它自己 19 条逐条 ``status="implemented"`` + file:line 证据
（并注明"未现跑族门"）**互相矛盾**。本片按**逐条 status**取值（汇总计数像是"只有族门验过的才算"
的另一套规则留下的旧值），理由：若按汇总计数把 21 条全算 missing，则本片会去重做已由
``cadcore/ops/sw_assembly.py`` 真实现过的条目，与"补缺口"的片分工相悖。此行如实登记，便于复核。

冻结接口（本文件对外只有这三样是契约）
--------------------------------------
- ``FAMILY = "sw_assembly"``；
- ``OPERATIONS: dict[str, Callable]``：操作名 → 函数（本片只登记 ``mate_align``）；
- 每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``：返回本次受影响的**配合 uuid**；
  长度一律整数纳米；错误一律抛 ``cadcore.errors.CadError(reason=...)``。

``mate_align(sk_or_doc, *, mates, alignment="anti_aligned", apply_pose=True)``
------------------------------------------------------------------------------
- ``mates``：配合 uuid（单个字符串或字符串序列；重复项自动去重，顺序保留）；
- ``alignment``：``"aligned"`` / ``"anti_aligned"``（别名 ``anti-aligned`` / ``antialigned`` /
  ``flip`` 一并接受；未知取值 ⇒ ``CadError(reason="unknown_alignment")``，**不静默忽略**）；
- ``apply_pose=True``（缺省）：把第 2 个零部件**真搬**到另一侧（见下"真几何口径"）；
- ``apply_pose=False``：只改配合记录（aligned/anti_aligned 是**数据事实**），不动位姿 ——
  此时若几何与记录不符，``mate_check()`` 会如实报 ``violated``（记录与几何不一致必须可见，
  绝不替调用方"顺手摆正"）。

真几何口径（不是翻标志位）
--------------------------
翻转 = 一个**确定性的刚体运动**（对第 2 个零部件），两步复合：

1. **镜像位移**：第 2 参考点过"第一参考点 + 第一参考方向"定义的平面镜像
   （``p2' = p2 - 2·((p2-p1)·n)·n``，``n`` = 第一参考方向；第一参考无方向时退化为第二参考方向）；
   距离配合的"换边"就是这一步 —— 有符号投影由 ``+value`` 变成 ``-value``；
2. **绕新点反向 180°**：绕"过 ``p2'`` 且垂直于第二参考方向"的轴旋转 180°，姿态由
   ``R' = Rot(axis,180°) @ R``，平移由 ``at' = p2' - R' @ p_local`` 反解 ⇒ **``p2'`` 保持不动**。
   该旋转是**正常旋转**（``det(R') = +1``，不是镜像体），方向恰好反向：``dir' = -dir``。

两条合起来把"同向对齐"变成"反向对齐"且仍然自洽：重合/平行/同轴配合在 ``flip=True`` 下重新
``satisfied``；距离配合跑到另一侧（``+5 mm`` ⇒ ``-5 mm``）后重新 ``satisfied``。操作是**对合**：
再切回去位姿逐值回到原样（片内自检断言了这条往返）。位姿落盘仍为整数纳米（四舍五入 ≤ 0.5 nm）。

有意限定的边界（写下来，防下游靠猜）
------------------------------------
1. **本层不是约束求解器**（与族模块一致）：翻转只作用于**被点名的那条配合**的第 2 个零部件，
   不做多配合联立求解；其它配合若因此变得不满足，``mate_check()`` 如实报 ``violated``。
2. **角度配合没有"对齐"语义**：族模块的 ``angle`` 只比无符号夹角（0..180），翻转只改方向，
   残差可能由满足变违反 —— 如实记录，不假装满足。
3. **``mate_check()`` 会回写 ``status/measured_nm/measured_deg``**：本操作在改完后对受影响的
   配合重算一次，因此"改记录"有**可观测的数据后果**（不是只动一个字段）。
4. 三态口径沿用族模块：``0`` = 真为零、``-1`` = 未采、``None``/``"NA"`` = 不适用。
"""

import os
import sys
from typing import Any, Callable

if __package__ in (None, ""):  # 允许 `python3 cadcore/ops/features/sw_assembly__w12.py` 直接跑
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, os.pardir)))

from cadcore.errors import CadError
from cadcore.ops import sw_assembly as _family
from cadcore.ops.sw_assembly import (_component, _component_refs, _fail, _flag, _mat_apply,
                                     _mat_identity, _mat_mul, _ref_geometry, _rot_axis, _store, _table,
                                     _text, _world, _world_mate_ref, component_pose,
                                     insert_component, mate_check, mate_distance, mate_parallel)

__all__ = ["FAMILY", "OPERATIONS", "ALIGNMENTS", "mate_align", "selftest"]

#: 族 id（冻结接口）。
#: 别名解析层：别名表住在语言包（cadcore/i18n/aliases.py，复用 cadcore/i18n/catalog.py），
#: 运行时按当前语言取。
from cadcore.i18n.aliases import alias_map as _alias_map

FAMILY = "sw_assembly"

#: 对齐方向的冻结枚举（本层只认这两个；别用 bool 冒充）。
ALIGNMENTS: tuple[str, ...] = ("aligned", "anti_aligned")

_ALIGNED = "aligned"
_ANTI = "anti_aligned"

#: 入参别名 → 冻结枚举（只做名称归一，不改语义）。
#: 对齐方向入参别名 → 冻结枚举：**别名表住在语言包**（`alias.alignment.<name>`），运行时按当前语言取。
_ALIGNMENT_ALIASES = _alias_map("alignment", ALIGNMENTS)

#: 浮点比较地板（几何量算出来是精确值，这里只吸浮点尾）。
_EPS = 1e-9


# ======================================================================================
# 小工具（只服务本片，不碰族模块的私有表）
# ======================================================================================


def _dot3(a: Any, b: Any) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _sub3(a: Any, b: Any) -> list[float]:
    return [float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2])]


def _cross3(a: Any, b: Any) -> list[float]:
    return [float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
            float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
            float(a[0]) * float(b[1]) - float(a[1]) * float(b[0])]


def _norm3(a: Any) -> float:
    return float(_dot3(a, a)) ** 0.5


def _det3(matrix: Any) -> float:
    """3×3 行列式：``+1`` 证明是正常旋转（刚体），``-1`` 就是镜像体（本层不许产生）。"""
    m = [[float(matrix[row][col]) for col in range(3)] for row in range(3)]
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def _normalize_alignment(value: Any) -> str:
    """对齐方向入参 → 冻结枚举；未知取值早失败（``unknown_alignment``）。"""
    if isinstance(value, bool):
        raise _fail("unknown_alignment", "alignment must be a name, not a bool", value=value,
                    known=list(ALIGNMENTS))
    text = _text(value, what="alignment").strip().lower().replace(" ", "_")
    key = _ALIGNMENT_ALIASES.get(text)
    if key is None:
        raise _fail("unknown_alignment", "alignment is not in this layer's frozen enum",
                    value=value, known=list(ALIGNMENTS))
    return key


def _alignment_of(row: dict[str, Any]) -> str:
    """读一条配合当前的对齐方向；老记录只有 ``flip`` 时从它推（``flip`` = 反对齐）。"""
    value = row.get("alignment")
    if isinstance(value, str):
        text = value.strip().lower().replace("-", "_")
        if text in ALIGNMENTS:
            return text
    return _ANTI if bool(row.get("flip")) else _ALIGNED


def _mate_row(store: dict[str, Any], ref: Any) -> dict[str, Any]:
    """按 uuid 找配合行（找不到报 ``unknown_mate``，不做下标引用）。"""
    key = _text(ref, what="mate")
    rows = _table(store, "mates")
    for row in rows:
        if str(row.get("uuid")) == key:
            return row
    raise _fail("unknown_mate", "no mate with that uuid", mate=key,
                known=[str(row.get("uuid")) for row in rows])


def _flip_axis(direction: Any) -> list[float]:
    """构造与 ``direction``（单位向量）**垂直**的单位轴：取与该方向最不平行的世界轴做叉积。"""
    best: list[float] | None = None
    best_abs: float | None = None
    for index in range(3):
        candidate = [0.0, 0.0, 0.0]
        candidate[index] = 1.0
        aligned = abs(_dot3(direction, candidate))
        if best_abs is None or aligned < best_abs:
            best, best_abs = candidate, aligned
    axis = _cross3(direction, best)
    length = _norm3(axis)
    if length <= _EPS:  # 方向退化（全零/未归一）⇒ 不猜一个假轴，早失败
        raise _fail("degenerate_mate", "cannot build a flip axis for this reference direction")
    return [item / length for item in axis]


def _flipped_pose(component: dict[str, Any], geometry: dict[str, Any],
                  first_world: dict[str, Any]) -> tuple[list[list[float]], list[int]]:
    """算"翻到另一侧"的**刚性**新位姿（返回 ``(rotation, at_nm)``；只算不写，便于先全量校验）。"""
    world = _world(component, geometry)
    direction = world.get("dir")
    if direction is None:
        raise _fail("unflippable_mate", "alignment is undefined for a reference without a direction",
                    component=str(component.get("uuid")))
    rotation = component.get("rotation") or _mat_identity()
    local = [float(item) for item in geometry.get("point_nm", [0, 0, 0])]
    point = world["point"]
    normal = first_world.get("dir") or direction  # 第一参考无方向 ⇒ 退化用第二参考方向当镜像面法线
    along = _dot3(_sub3(point, first_world["point"]), normal)
    target = [point[axis] - 2.0 * along * float(normal[axis]) for axis in range(3)]
    flipped = _mat_mul(_rot_axis(_flip_axis(direction), 180.0), rotation)
    moved = _mat_apply(flipped, local)
    at_nm = [int(round(target[axis] - float(moved[axis]))) for axis in range(3)]
    return [[float(item) for item in row] for row in flipped], at_nm


# ======================================================================================
# 操作：配合对齐方向控制
# ======================================================================================


def mate_align(sk_or_doc: Any, *, mates: Any, alignment: Any = _ANTI,
               apply_pose: Any = True) -> list[str]:
    """**配合对齐方向控制**：把点名配合切到 ``aligned`` / ``anti_aligned``，并（缺省）真搬零件。

    两阶段：**先全量校验**（uuid 存在、参考对完整、第二参考有方向）再**统一落盘**，
    因此不存在"改了一半才报错"的半记录。

    * ``alignment`` 与当前一致 ⇒ 幂等：仍然返回该 uuid，但不动位姿；
    * ``apply_pose=False`` ⇒ 只把对齐方向写成数据事实（``alignment`` + 同步 ``flip``），
      位姿一个纳米都不动；几何与记录不符时 ``mate_check()`` 会如实报 ``violated``。

    落盘字段：``mates[]`` 行的 ``alignment``（冻结枚举）与 ``flip``（与之一致，兼容族模块
    既有的 ``flip`` 口径）；``apply_pose=True`` 时另改第 2 个组件的 ``rotation`` / ``at_nm``
    （整数纳米）。最后对受影响的配合跑一次 ``mate_check()``，回写 ``status/measured_*``。

    返回：受影响的配合 uuid 列表（按入参顺序，重复项去重）。
    """
    store = _store(sk_or_doc)
    target = _normalize_alignment(alignment)
    pose_flag = bool(_flag(apply_pose, what="apply_pose"))

    wanted: list[str] = []
    for item in _component_refs(mates):
        key = str(item)
        if key not in wanted:
            wanted.append(key)

    rows = [_mate_row(store, key) for key in wanted]  # 早失败：uuid 不存在当场报
    plans: list[tuple[dict[str, Any], list[list[float]], list[int]] | None] = []
    for row in rows:
        if _alignment_of(row) == target or not pose_flag:
            plans.append(None)
            continue
        pairs = row.get("refs")
        if not isinstance(pairs, (list, tuple)) or len(pairs) != 2:
            raise _fail("corrupt_store", "a mate row must hold exactly two references",
                        mate=str(row.get("uuid")), refs=pairs)
        first_world = _world_mate_ref(store, (str(pairs[0][0]), str(pairs[0][1])))
        second = _component(store, pairs[1][0])
        geometry = _ref_geometry(second, str(pairs[1][1]))
        rotation, at_nm = _flipped_pose(second, geometry, first_world)
        plans.append((second, rotation, at_nm))

    for row, plan in zip(rows, plans):
        if plan is not None:
            component, rotation, at_nm = plan
            component["rotation"] = rotation
            component["at_nm"] = at_nm
        row["alignment"] = target
        row["flip"] = (target == _ANTI)

    if rows:
        mate_check(sk_or_doc, mates=list(wanted))  # 真数据后果：残差/status 当场按新对齐重算
    return list(wanted)


OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "mate_align": mate_align,
}


# ======================================================================================
# 自检
# ======================================================================================


def _expect_error(reason: str, call: Callable[[], Any], label: str) -> None:
    """阴性对照：必须抛出**指定 reason** 的 ``CadError``，否则自检当场炸（尺子看得见红）。"""
    try:
        call()
    except CadError as exc:
        assert exc.reason == reason, "%s: reason=%r expected %r" % (label, exc.reason, reason)
        return
    raise AssertionError("%s: expected CadError(reason=%r)" % (label, reason))


def selftest() -> int:
    """自检：族注册 / 距离配合换边（真几何 + det=+1）/ 往返逐值回滚 / apply_pose=False 尺子红 / 阴性对照。

    全部通过 ⇒ 打印 ``W12_ALIGN ... verdict=PASS`` 与
    ``FEATURE_OK family=sw_assembly op=mate_align verdict=PASS``，返回 0；否则抛异常（返回 1）。
    """
    checks: list[str] = []
    mm = 1_000_000

    # ---- 1. 族注册与"这一格真是族里缺的"（夹具自证：测的是真件，不是自造的同名物）----
    assert FAMILY == _family.FAMILY == "sw_assembly", FAMILY
    assert "mate_align" not in _family.OPERATIONS, "this slice must fill the one cell that the family really lacks"
    assert isinstance(OPERATIONS.get("mate_align"), type(mate_align)), OPERATIONS
    assert callable(OPERATIONS["mate_align"]), "every operation must be callable"
    checks.append("family_and_ops")

    # ---- 2. 夹具：一块板 + 一根销，距离配合 5 mm 真满足 ----------------------------------
    doc: dict[str, Any] = {"sketches": [], "features": [], "extra": {}, "meta": {"doc_uuid": "w12"}}
    plate = insert_component(doc, part_key="plate", at_nm=(0, 0, 0),
                             bbox_nm=[[0, 0, 0], [100 * mm, 100 * mm, 10 * mm]],
                             refs={"face": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]}})[0]
    pin = insert_component(doc, part_key="pin", at_nm=(0, 0, 5 * mm),
                           bbox_nm=[[-5 * mm, -5 * mm, 0], [5 * mm, 5 * mm, 10 * mm]],
                           refs={"face": {"point_nm": [0, 0, 0], "dir": [0, 0, 1]}})[0]
    assert len(plate) == 36 and len(pin) == 36, "uuids are uuid4 strings"
    gap = mate_distance(doc, refs=[[plate, "face"], [pin, "face"]], value_nm=5 * mm)[0]
    assert mate_check(doc) == [], "the 5 mm distance mate is satisfied before the flip"
    checks.append("fixture_baseline")

    # ---- 3. 反对齐：销必须真搬到 -z 侧，且姿态是正常旋转（det=+1），配合重新满足 ----------
    assert mate_align(doc, mates=gap, alignment="anti-aligned") == [gap], "alias normalization + return uuid"
    row = _mate_row(_store(doc), gap)
    assert row["alignment"] == "anti_aligned" and row["flip"] is True, row["alignment"]
    assert component_pose(doc, component=pin)["at_nm"][2] == -5 * mm, "side swap: +5mm -> -5mm"
    rotation = _component(_store(doc), pin)["rotation"]
    assert abs(_det3(rotation) - 1.0) < 1e-9, "flip must stay a rigid rotation (det=+1), not a mirror"
    world_dir = _world(_component(_store(doc), pin), _ref_geometry(_component(_store(doc), pin), "face"))["dir"]
    assert abs(_dot3(world_dir, [0.0, 0.0, 1.0]) + 1.0) < 1e-9, ("world dir must reverse", world_dir)
    assert row["status"] == "satisfied", row["status"]
    assert mate_check(doc) == [], "after the flip the distance mate is satisfied on the other side"
    checks.append("flip_distance_mate")

    # ---- 4. 对合：切回 aligned ⇒ 位姿逐值回到原样，配合仍满足 ------------------------------
    assert mate_align(doc, mates=gap, alignment="aligned") == [gap]
    row = _mate_row(_store(doc), gap)
    assert row["flip"] is False and row["alignment"] == "aligned", row["alignment"]
    assert component_pose(doc, component=pin)["at_nm"] == [0, 0, 5 * mm], "round trip must be exact"
    assert mate_check(doc) == [], "back to the aligned side, still satisfied"
    checks.append("roundtrip_exact")

    # ---- 5. apply_pose=False：记录改了、几何没动 ⇒ 尺子必须看见"违反" ----------------------
    parallel = mate_parallel(doc, refs=[[plate, "face"], [pin, "face"]])[0]
    assert mate_check(doc) == [], "parallel mate is satisfied while both faces point +z"
    assert mate_align(doc, mates=parallel, apply_pose=False) == [parallel]
    assert _mate_row(_store(doc), parallel)["flip"] is True, "the record is the data fact"
    assert component_pose(doc, component=pin)["at_nm"] == [0, 0, 5 * mm], "pose must not move"
    assert mate_check(doc) == [parallel], "record and geometry disagree => violated, never silent"
    checks.append("record_without_pose")

    # ---- 6. 阴性对照：未知配合 uuid / 未知对齐方向 ---------------------------------------
    _expect_error("unknown_mate",
                  lambda: mate_align(doc, mates="00000000-0000-0000-0000-000000000000"),
                  "unknown mate uuid")
    _expect_error("unknown_alignment",
                  lambda: mate_align(doc, mates=parallel, alignment="sideways"),
                  "unknown alignment")
    checks.append("negative_controls")

    print("W12_ALIGN checks=%d names=%s op=%s verdict=PASS"
          % (len(checks), ",".join(checks), "mate_align"))
    print("FEATURE_OK family=sw_assembly op=mate_align verdict=PASS")
    return 0


def main() -> int:
    try:
        return selftest()
    except AssertionError as exc:
        print("W12_ALIGN checks=%s verdict=FAIL detail=%s" % ("selftest", exc))
        print("FEATURE_FAIL family=sw_assembly op=mate_align verdict=FAIL")
        return 1
    except CadError as exc:
        print("W12_ALIGN checks=%s verdict=FAIL reason=%s detail=%s" % ("selftest", exc.reason, exc))
        print("FEATURE_FAIL family=sw_assembly op=mate_align verdict=FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 sw_assembly__w12.py:308 "本片补的必须是族里真缺的那一格" -> "this slice must fill the one cell that the family really lacks"
# 原 sw_assembly__w12.py:327 "别名归一 + 返回 uuid" -> "alias normalization + return uuid"
# 原 sw_assembly__w12.py:330 "换边：+5mm -> -5mm" -> "side swap: +5mm -> -5mm"
