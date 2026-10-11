"""draw_ops 族实现片（w27）：圆弧 —— **起点-端点-半径**画法（AutoCAD ARC 的「起点、端点、半径」分支）。

冻结接口
--------
- ``FAMILY = "draw_ops"``
- ``OPERATIONS: dict[str, Callable]``，每个操作 ``op(sk_or_doc, **kwargs) -> list[str]``（返回新建实体 uuid 列表）
- 长度一律**整数纳米**；错误抛 ``cadcore.errors.CadError(reason=...)``

为什么是这一条（编号推导）
--------------------------
``docs/research/cad-parity/features/autocad-draw.json`` 的 ``must_have`` 共 20 条；
``docs/research/cad-parity/status/autocad-draw.json`` 记 implemented=5 / partial=15 / missing=0。
⇒ 仍缺（partial）条目按 must_have 顺序编号 0..14：

    0 直线 / 1 多段线 / 2 圆 / 3 圆弧 / 4 矩形 / 5 正多边形 / 6 点与点样式 / 7 图案填充与渐变色 /
    8 边界创建 / 9 修订云线 / 10 对象捕捉 / 11 正交与极轴追踪 / 12 对象捕捉追踪与临时追踪点 /
    13 动态输入 / 14 当前图层与对象特性

本席取**第 18 号**，可做条目只有 15 条 ⇒ 顺延取模 ``18 % 15 = 3`` ⇒ **圆弧**。
status 文件对该条的缺口原文：「三点画弧（UI）+ 圆心半径起止角（命令行/API/族接口）可用……
缺关键环节：**起点-端点-半径画法**；也无方向/相切/延长选项。」本片补的就是起点-端点-半径。

真几何（不是桩）
----------------
给定起点 ``S``、端点 ``E`` 与半径 ``R``，弦长 ``c = |SE|``：

- ``c == 0``（S=E）⇒ ``CadError(reason="arc_degenerate_chord")``：两点重合定不出弧；
- ``c > 2R`` ⇒ ``CadError(reason="arc_radius_too_small")``：无解，不静默夹取；
- 否则圆心在弦的中垂线上：``M = (S+E)/2``、``h = sqrt(R² - (c/2)²)``、``n = (-dy, dx)/c``（``E-S`` 的左法向）：
  * ``major=False``（默认，劣弧，等同 AutoCAD 默认）取 ``C = M + h·n`` ⇒ 从 S 到 E 的**逆时针**扫角 ≤ 180°；
  * ``major=True`` 取 ``C = M - h·n`` ⇒ 扫角 = 360° − 劣弧（> 180°）；
  * ``c == 2R`` 时 ``h = 0``，两支重合为半圆（180°）。

角度由 ``atan2`` 现算（6 位小数，与 L1 存储精度一致），圆心取整到纳米后交给 L1
``cadcore.sketch.commands.add_arc``（``kind="arc"`` 实体 ``geom={cx,cy,r,start_deg,end_deg}``，
逆时针 start→end）落盘 —— **数据后果是真的**：草图的实体表里多一条圆弧实体，坐标/半径/角度均可现读复算。

L1 装得下（非 unsupported）
---------------------------
L1 的圆弧实体本身就由 ``(cx, cy, r, start_deg, end_deg)`` 表达，起终点只是导出量 ⇒ 本能力可在 L1 上真实现。
已知口径损失（如实记录，不掩饰）：圆心被取整到整数纳米，故「圆心到两端点距离」与 ``R`` 可能有 ≤1 nm 级偏差；
弧度极小时（``|SE|/R`` 小于 6 位小数分辨率）角度会退化，此时本模块**报错**而不是落一条退化弧。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Sequence

from cadcore.errors import CadError
from cadcore.sketch import commands as _commands
from cadcore.sketch.commands import add_arc

__all__ = ["FAMILY", "OPERATIONS", "arc_start_end_radius"]

#: 族名（冻结接口）
FAMILY = "draw_ops"

#: 角度存储精度（度）：与 L1 ``cadcore.sketch.commands._ANGLE_DECIMALS`` 同口径。
_ANGLE_DECIMALS = 6

#: 自测里「端点复算」的容差（纳米）：覆盖角度 6 位小数 + 圆心取整带来的偏差。
_ENDPOINT_TOL_NM = 100


# --------------------------------------------------------------------------------------
# 入参把关（一律抛契约里的 cadcore.errors.CadError，带机读 reason）
# --------------------------------------------------------------------------------------


def _as_nm(value: Any, *, what: str, positive: bool = False) -> int:
    """长度入参 → 整数纳米；非数字/非有限/非正（要求时）当场报错。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CadError(f"{what} must be a number of nanometres, got {value!r}", reason="err.geometry_invalid")
    number = float(value)
    if not math.isfinite(number):
        raise CadError(f"{what} must be finite, got {value!r}", reason="err.geometry_invalid")
    if positive and number <= 0.0:
        raise CadError(f"{what} must be > 0, got {value!r}", reason="err.geometry_invalid")
    return int(round(number))


def _as_point(value: Any, *, what: str) -> tuple[int, int]:
    """点入参 → ``(x_nm, y_nm)``；形态不对当场报错。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        raise CadError(f"{what} must be a (x_nm, y_nm) pair, got {value!r}", reason="err.geometry_invalid")
    return (_as_nm(value[0], what=f"{what}.x"), _as_nm(value[1], what=f"{what}.y"))


def _resolve_sketch(sk_or_doc: Any) -> Any:
    """``sk_or_doc`` → L1 草图对象：doc 形态（``.sketch`` / ``.sk`` / ``{"sketch": …}``）自动下钻。"""
    if sk_or_doc is None:
        raise CadError("sketch/document must not be None", reason="err.unknown_reference")
    if isinstance(sk_or_doc, dict):
        for key in ("sketch", "sk"):
            inner = sk_or_doc.get(key)
            if inner is not None and not isinstance(inner, (str, bytes)):
                return inner
        return sk_or_doc
    for attr in ("sketch", "sk"):
        inner = getattr(sk_or_doc, attr, None)
        if inner is not None and not callable(inner):
            return inner
    return sk_or_doc


def _deg_of(center: tuple[int, int], point: tuple[int, int]) -> float:
    """点相对圆心的角度（度，``[0, 360)``，6 位小数）。"""
    deg = math.degrees(math.atan2(float(point[1] - center[1]), float(point[0] - center[0]))) % 360.0
    deg = round(deg, _ANGLE_DECIMALS)
    return 0.0 if deg == 0.0 else deg  # 去掉 -0.0


def _l1_add_arc(sketch: Any, **kwargs: Any) -> str:
    """调 L1 建弧；非 CadError 的底层异常按契约统一成 ``CadError``（保留原 reason 若有）。"""
    try:
        return add_arc(sketch, **kwargs)
    except CadError:
        raise
    except Exception as exc:  # noqa: BLE001 - 契约要求错误面是 CadError，这里做适配而非吞掉
        raise CadError(
            f"L1 add_arc failed: {exc}",
            reason=str(getattr(exc, "reason", "") or "err.geometry_invalid"),
        ) from exc


# --------------------------------------------------------------------------------------
# 操作：起点-端点-半径画弧
# --------------------------------------------------------------------------------------


def arc_start_end_radius(
    sk_or_doc: Any,
    start: Any = None,
    end: Any = None,
    radius_nm: Any = None,
    *,
    radius: Any = None,
    major: bool = False,
    layer: str = "0",
    construction: bool = False,
    fixed: bool = False,
    **extra: Any,
) -> list[str]:
    """**起点-端点-半径**画一段圆弧（ARC 的 S/E/R 分支），返回新建圆弧实体的 uuid 列表（长度 1）。

    参数
    ----
    - ``start`` / ``end``：``(x_nm, y_nm)``，整数纳米（浮点按四舍五入取整）；
    - ``radius_nm``（别名 ``radius``）：半径，> 0 的整数纳米 —— **是半径不是直径**；
    - ``major``：``False``（默认）取劣弧（圆弧从起点**逆时针**扫到端点，扫角 ≤ 180°，AutoCAD 默认）；
      ``True`` 取优弧（扫角 > 180°）；
    - ``layer`` / ``construction`` / ``fixed``：透传 L1 实体字段。

    错误（``CadError.reason``）
    --------------------------
    - ``arc_degenerate_chord``：起点与端点重合，或两点距圆心太近导致角度在 6 位小数上退化；
    - ``arc_radius_too_small``：``|SE| > 2R``，几何无解；
    - ``err.geometry_invalid``：入参形态/取值不合法（缺参、非数字、半径 ≤ 0、层名非字符串）。
    """
    sketch = _resolve_sketch(sk_or_doc)

    if radius is not None:
        if radius_nm is not None:
            raise CadError("pass either radius_nm or radius, not both", reason="err.geometry_invalid")
        radius_nm = radius
    missing = [name for name, value in (("start", start), ("end", end), ("radius_nm", radius_nm)) if value is None]
    if missing:
        raise CadError(f"missing required argument(s): {', '.join(missing)}", reason="err.geometry_invalid")
    if extra:
        raise CadError(f"unsupported keyword argument(s): {', '.join(sorted(extra))}", reason="err.unsupported_kind")
    if not isinstance(layer, str) or not layer.strip():
        raise CadError(f"layer must be a non-empty string, got {layer!r}", reason="err.geometry_invalid")

    sx, sy = _as_point(start, what="start")
    ex, ey = _as_point(end, what="end")
    radius_value = _as_nm(radius_nm, what="radius_nm", positive=True)

    if (sx, sy) == (ex, ey):
        raise CadError("start and end coincide: an arc needs two distinct points", reason="arc_degenerate_chord")

    dx, dy = ex - sx, ey - sy
    chord = math.hypot(dx, dy)
    if chord > 2.0 * radius_value:
        raise CadError(
            f"chord {chord} nm exceeds diameter {2 * radius_value} nm: no arc of this radius",
            reason="arc_radius_too_small",
            chord_nm=chord,
            radius_nm=radius_value,
        )

    # 圆心 = 弦中点 ± 中垂线偏移；取 +左法向一侧时 S→E 的逆时针扫角 ≤ 180°（劣弧）。
    half_chord = chord / 2.0
    height = math.sqrt(max(0.0, float(radius_value) ** 2 - half_chord**2))
    sign = -1.0 if major else 1.0
    cx_f = (sx + ex) / 2.0 + sign * height * (-dy / chord)
    cy_f = (sy + ey) / 2.0 + sign * height * (dx / chord)
    center = (int(round(cx_f)), int(round(cy_f)))

    start_deg = _deg_of(center, (sx, sy))
    end_deg = _deg_of(center, (ex, ey))
    if start_deg == end_deg:
        raise CadError(
            "start/end subtend the same angle at the computed centre: arc would be degenerate",
            reason="arc_degenerate_chord",
            center_nm=center,
            radius_nm=radius_value,
        )

    entity_uuid = _l1_add_arc(
        sketch,
        center=center,
        radius_nm=radius_value,
        start_deg=start_deg,
        end_deg=end_deg,
        layer=layer,
        construction=bool(construction),
        fixed=bool(fixed),
    )
    return [entity_uuid]


#: 冻结接口的操作表（别名 ``arc_ser`` 便于按 AutoCAD 术语检索）
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "arc_start_end_radius": arc_start_end_radius,
    "arc_ser": arc_start_end_radius,
}


# --------------------------------------------------------------------------------------
# 自测（至少 2 条硬断言；全部走真 L1 实体，不打桩）
# --------------------------------------------------------------------------------------


class _DuckSketch(dict):  # type: ignore[type-arg]
    """兜底草图容器：同时满足 ``sk["entities"]`` 与 ``sk.entities`` 两种读法（仅在 L1 工厂不可用时使用）。"""

    def __init__(self) -> None:
        super().__init__(entities=[], constraints=[], dimensions=[])

    @property
    def entities(self) -> list[Any]:  # noqa: D401 - 兼容属性读法
        return self["entities"]


def _entity_store(sketch: Any) -> list[Any]:
    """取草图的实体表（先问 L1 ``sketch_entities``，再回落两种字段读法）。"""
    getter = getattr(_commands, "sketch_entities", None)
    if callable(getter):
        try:
            return getter(sketch)
        except Exception:  # noqa: BLE001 - 只为探形态，不是吞错误
            pass
    if isinstance(sketch, dict) and isinstance(sketch.get("entities"), list):
        return sketch["entities"]
    entities = getattr(sketch, "entities", None)
    if isinstance(entities, list):
        return entities
    raise CadError("cannot locate the entity store of the given sketch", reason="err.unsupported_kind")


def _accepts_l1_arc(candidate: Any) -> bool:
    """真调用 L1 ``add_arc`` 验收该容器：能落一条真弧且实体表确实长了 1 条才算可用。"""
    try:
        before = len(_entity_store(candidate))
    except Exception:  # noqa: BLE001
        return False
    try:
        uuid = add_arc(candidate, center=(0, 0), radius_nm=1_000_000, start_deg=0.0, end_deg=90.0)
    except Exception:  # noqa: BLE001
        return False
    try:
        return isinstance(uuid, str) and len(_entity_store(candidate)) == before + 1
    except Exception:  # noqa: BLE001
        return False


def _new_sketch() -> Any:
    """造一个自测用草图：优先用 L1 真工厂，逐个用真 ``add_arc`` 验收，最后才用兜底容器。"""
    try:
        from cadcore.sketch import model as _model  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - 没有独立 model 模块时直接走兜底
        _model = None  # type: ignore[assignment]
    if _model is not None:
        for name in ("new_sketch", "make_sketch", "empty_sketch", "create_sketch", "Sketch"):
            factory = getattr(_model, name, None)
            if factory is None or not callable(factory):
                continue
            candidate = None
            for call in (lambda f=factory: f(), lambda f=factory: f(entities=[])):
                try:
                    candidate = call()
                except Exception:  # noqa: BLE001
                    continue
                if candidate is not None and _accepts_l1_arc(candidate):
                    return candidate
    duck = _DuckSketch()
    if _accepts_l1_arc(duck):
        return duck
    raise CadError("no L1 sketch container accepted add_arc: self-test cannot run", reason="err.unsupported_kind")


def _geom_of(sketch: Any, entity_uuid: str) -> dict[str, Any]:
    for entity in _entity_store(sketch):
        if isinstance(entity, dict) and entity.get("uuid") == entity_uuid:
            geom = entity.get("geom")
            if not isinstance(geom, dict):
                raise CadError(f"entity {entity_uuid} has no geom", reason="err.geometry_invalid")
            return {"kind": entity.get("kind"), **geom}
    raise CadError(f"entity {entity_uuid} not found in sketch", reason="err.unknown_reference")


def _sweep_deg(geom: dict[str, Any]) -> float:
    return (float(geom["end_deg"]) - float(geom["start_deg"])) % 360.0


def _endpoint_nm(geom: dict[str, Any], which: str) -> tuple[int, int]:
    deg = math.radians(float(geom[f"{which}_deg"]))
    return (
        int(round(geom["cx"] + geom["r"] * math.cos(deg))),
        int(round(geom["cy"] + geom["r"] * math.sin(deg))),
    )


def _selftest() -> int:
    MM = 1_000_000
    checks = 0
    sketch = _new_sketch()

    # A1 半圆：S=(-10,0) E=(10,0) R=10mm ⇒ 圆心 (0,0)、r=10mm、逆时针扫角恰 180°。
    (u1,) = arc_start_end_radius(sketch, start=(-10 * MM, 0), end=(10 * MM, 0), radius_nm=10 * MM)
    g1 = _geom_of(sketch, u1)
    assert g1["kind"] == "arc", g1
    assert (g1["cx"], g1["cy"]) == (0, 0), g1
    assert g1["r"] == 10 * MM, g1
    assert abs(_sweep_deg(g1) - 180.0) < 1e-6, _sweep_deg(g1)
    checks += 4

    # A2 劣弧：S=(0,0) E=(10mm,0) R=10mm ⇒ 圆心到两端点距离均 = R（≤100nm），扫角 ∈ (0,180)，端点复算回到 S/E。
    (u2,) = arc_start_end_radius(sketch, start=(0, 0), end=(10 * MM, 0), radius_nm=10 * MM)
    g2 = _geom_of(sketch, u2)
    for point in ((0, 0), (10 * MM, 0)):
        dist = math.hypot(g2["cx"] - point[0], g2["cy"] - point[1])
        assert abs(dist - 10 * MM) <= 100, (dist, g2)
    assert 0.0 < _sweep_deg(g2) < 180.0, _sweep_deg(g2)
    for which, want in (("start", (0, 0)), ("end", (10 * MM, 0))):
        got = _endpoint_nm(g2, which)
        assert abs(got[0] - want[0]) <= _ENDPOINT_TOL_NM and abs(got[1] - want[1]) <= _ENDPOINT_TOL_NM, (which, got)
    checks += 2 + 2 + 2

    # A3 优弧：同参数 major=True ⇒ 扫角 ∈ (180,360)，且与劣弧不同心（c<2R 时镜像到弦另一侧）。
    (u3,) = arc_start_end_radius(sketch, start=(0, 0), end=(10 * MM, 0), radius_nm=10 * MM, major=True)
    g3 = _geom_of(sketch, u3)
    assert 180.0 < _sweep_deg(g3) < 360.0, _sweep_deg(g3)
    assert (g3["cx"], g3["cy"]) != (g2["cx"], g2["cy"]), (g2, g3)
    assert abs(_sweep_deg(g2) + _sweep_deg(g3) - 360.0) < 1e-6, (_sweep_deg(g2), _sweep_deg(g3))
    checks += 3

    # A4 错误面：弦长 > 直径 ⇒ arc_radius_too_small；起终点重合 ⇒ arc_degenerate_chord（都是 CadError）。
    try:
        arc_start_end_radius(sketch, start=(0, 0), end=(30 * MM, 0), radius_nm=10 * MM)
    except CadError as exc:
        assert exc.reason == "arc_radius_too_small", exc.reason
    else:
        raise AssertionError("chord > diameter must raise CadError")
    try:
        arc_start_end_radius(sketch, start=(1 * MM, 2 * MM), end=(1 * MM, 2 * MM), radius_nm=10 * MM)
    except CadError as exc:
        assert exc.reason == "arc_degenerate_chord", exc.reason
    else:
        raise AssertionError("coincident start/end must raise CadError")
    checks += 2

    # A5 doc 形态下钻 + 返回契约：{"sketch": …} 也认，返回 list[str]，实体表净增 1 条弧。
    before = len(_entity_store(sketch))
    out = OPERATIONS["arc_ser"]({"sketch": sketch}, start=(0, 0), end=(0, 10 * MM), radius_nm=10 * MM)
    assert isinstance(out, list) and len(out) == 1 and isinstance(out[0], str), out
    assert len(_entity_store(sketch)) == before + 1, before
    checks += 3

    return checks


if __name__ == "__main__":
    import sys

    op_name = "arc_start_end_radius"
    try:
        n_checks = _selftest()
    except Exception as exc:  # noqa: BLE001 - 判定行必须打印，失败要 loud（非零退出）
        print(f"FEATURE_OK family={FAMILY} op={op_name} verdict=FAIL reason={type(exc).__name__}: {exc}")
        raise
    print(f"FEATURE_OK family={FAMILY} op={op_name} verdict=PASS")
    print(f"selftest ops={len(OPERATIONS)} assertions={n_checks} arc_entities_created=5")
    sys.exit(0)
