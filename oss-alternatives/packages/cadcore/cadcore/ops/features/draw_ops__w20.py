#!/usr/bin/env python3
"""draw_ops 族实现片 w20：**正交与极轴追踪**（AutoCAD F8 / F10）。

覆盖的 must_have 条目
---------------------
`docs/research/cad-parity/features/autocad-draw.json` 的 must_have 按清单顺序编号 0..N-1，
扣除 `status/autocad-draw.json` 里已 implemented 的条目后，仍 missing/partial 的有 15 条：
0 直线 / 1 多段线 / 2 圆 / 3 圆弧 / 4 矩形 / 5 正多边形 / 6 点与点样式 / 7 图案填充与渐变色 /
8 边界创建 / 9 修订云线 / 10 对象捕捉 / **11 正交与极轴追踪** / 12 对象捕捉追踪与临时追踪点 /
13 动态输入 / 14 当前图层与对象特性。本片负责 **第 11 号：正交与极轴追踪**。

冻结接口
--------
`FAMILY = "draw_ops"` + `OPERATIONS: dict[str, Callable]`；每个 `op(sk_or_doc, **kwargs) -> list[str]`
返回 uuid 列表；长度一律整数纳米；失败抛 `cadcore.errors.CadError(reason=...)`。
本片只写自己名下这一支文件，**不创建 `__init__.py`**。

能力口径（真几何，不是桩）
--------------------------
1. **正交（ortho）**：把光标点投到相对基点的主轴方向 —— `|dx| >= |dy|` 取水平（45° 平局也判水平，
   见 `ortho_lock_point` 的不等号），否则取垂直；另一轴分量归零。
2. **极轴（polar）**：以基点为极点，把光标点吸附到**最近的增量角整数倍**射线；仅当角偏差
   `|θ - k·inc| <= tol` 时锁住（AutoCAD 的对齐容差语义），超容差**原样返回**（不锁）。
   `preserve="distance"` 保持光标到基点的径向距离；`preserve="projection"` 保持光标在射线上的投影长度。
3. **优先级**：正交优先 —— 两者同时开时只做正交（与 `precision_ops.precision_apply` 的"未锁住才试追踪"一致）。
4. **几何后果落到真实体**：`ortho_polar_apply(..., create=True)` 用族内 `draw_ops.line_create` 在草图上
   真建一条 `line` 实体（`geom = {x1,y1,x2,y2}`，整数纳米），返回 `[uuid]`；被锁定的端点就是实体端点，
   所以"锁没锁住"在数据上可见（锁住 ⇒ 端点 ≠ 原始光标点）。
5. **设置落点**：L1 没有"绘图辅助"一等节点，`ortho_polar_set` 把配置写进草图节点的 `ortho_polar`
   扩展字段（docmodel 的未知字段原样保留 + 记 diagnostics），返回 `[]`（就地操作，与族内
   `polyline_edit` / `layer_apply` 的返回口径一致）。设置的真后果：后续 `ortho_polar_apply` 不带显式
   开关时读它，几何随之改变。

自检（文件自带，可直接跑）：

    cd packages/cadcore && python3 cadcore/ops/features/draw_ops__w20.py

中文只出现在 docstring/注释里：`tools/cad/i18n_gate.py` 把"非 docstring 的含汉字字符串常量"计为
`hardcoded_literals`，运行期文案一律英文。
"""

from __future__ import annotations

import math
import os
import sys
import uuid as _uuid
from typing import Any, Callable, Mapping

#: 以脚本方式直接运行时补包上下文（本文件在 cadcore/ops/features/ 下 ⇒ 上溯 4 层到 packages/cadcore）。
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from cadcore.errors import CadError  # noqa: E402  （路径引导之后再导入，见上）

__all__ = [
    "FAMILY", "OPERATIONS", "ortho_polar_set", "ortho_polar_apply",
    "ortho_lock_point", "polar_lock_point", "resolve_settings", "selftest",
]

#: 冻结的族名（与 `cadcore/ops/draw_ops.py:106` 同值）。
FAMILY = "draw_ops"

#: 设置写在草图节点上的扩展键名（L1 未知字段会被原样保留）。
_SETTINGS_KEY = "ortho_polar"

#: 极轴增量角缺省 15°（AutoCAD 出厂值），对齐容差缺省 2°。
_DEFAULT_INCREMENT_DEG = 15.0
_DEFAULT_TOLERANCE_DEG = 2.0
_DEFAULT_PRESERVE = "distance"
_PRESERVES = ("distance", "projection")

#: 1 mm 的纳米数（本文件自用，不引跨模块常量）。
_MM_NM = 1_000_000

#: 族的兄弟模块句柄（懒加载：缺席时抛 `dependency_missing`，绝不静默降级成空结果）。
_FAMILY_MODULE: Any = None


# --------------------------------------------------------------------------------------
# 基础设施：族模块句柄 / 错误 / 数值口径
# --------------------------------------------------------------------------------------
def _fail(reason: str, message: str, **details: Any) -> CadError:
    """构造 `CadError`（reason 机读，details 供判定行归因）。"""
    return CadError(message, reason=reason, **details)


def _family() -> Any:
    """族模块 `cadcore.ops.draw_ops`：`_sketch_of` / `_point` / `_flag` / `_geom_of` / `line_create`。

    这些是**同一族**的解析与建实体口径（草图文档解析、`geom={x1,y1,x2,y2}` 实体形状），
    复用它们而不是在本片复制一份，避免"两处同名实现"的静默分叉。
    """
    global _FAMILY_MODULE
    if _FAMILY_MODULE is None:
        try:
            from cadcore.ops import draw_ops as module
        except Exception as exc:  # 显式降级信号，不吞
            raise _fail("dependency_missing", "family module cadcore.ops.draw_ops is unavailable",
                        module="cadcore.ops.draw_ops", error=type(exc).__name__) from exc
        for name in ("_sketch_of", "_point", "_flag", "_geom_of", "line_create"):
            if not hasattr(module, name):
                raise _fail("dependency_missing", "family module lacks required helper",
                            module="cadcore.ops.draw_ops", helper=name)
        _FAMILY_MODULE = module
    return _FAMILY_MODULE


def _round_nm(value: float) -> int:
    """浮点纳米 → int，**半进位（away from zero）**，与族接口的取整口径一致。"""
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


def _deg(value: Any, *, what: str) -> float:
    """角度参数：拒绝 bool/None，要求有限浮点。"""
    if isinstance(value, bool) or value is None:
        raise _fail("bad_argument", f"{what} must be a finite number of degrees", what=what)
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise _fail("bad_argument", f"{what} must be a finite number of degrees",
                    what=what, value=repr(value)) from exc
    if not math.isfinite(num):
        raise _fail("bad_argument", f"{what} must be finite", what=what, value=repr(value))
    return num


def _norm_deg(value: float) -> float:
    """角度归一到 [0, 360)。"""
    return float(value % 360.0)


def _inc_tol(increment_deg: Any, tolerance_deg: Any) -> tuple[float, float]:
    """校验增量角/容差：`0 < inc <= 180`、`0 <= tol <= inc/2`。"""
    inc = _deg(increment_deg, what="increment_deg")
    tol = _deg(tolerance_deg, what="tolerance_deg")
    if not (0.0 < inc <= 180.0):
        raise _fail("bad_argument", "increment_deg must be in (0, 180]", increment_deg=inc)
    if not (0.0 <= tol <= inc / 2.0):
        raise _fail("bad_argument", "tolerance_deg must be in [0, increment_deg/2]",
                    tolerance_deg=tol, increment_deg=inc)
    return inc, tol


def _preserve(value: Any) -> str:
    """保留量口径：`distance`（径向距离）或 `projection`（沿射线投影长度）。"""
    token = str(value)
    if token not in _PRESERVES:
        raise _fail("bad_argument", "preserve must be one of distance|projection",
                    preserve=token, allowed=list(_PRESERVES))
    return token


def _fill(explicit: Any, stored: Any, default: Any) -> Any:
    """显式参数 > 草图上的设置 > 缺省（显式 `None` 表示"沿用设置"）。"""
    if explicit is not None:
        return explicit
    if stored is not None:
        return stored
    return default


# --------------------------------------------------------------------------------------
# 纯几何：正交 / 极轴
# --------------------------------------------------------------------------------------
def ortho_lock_point(base: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    """正交锁定：`|dx| >= |dy|` ⇒ 水平（另一轴归零），否则垂直；45° 平局判水平。"""
    dx = int(target[0]) - int(base[0])
    dy = int(target[1]) - int(base[1])
    if abs(dx) >= abs(dy):
        return (int(base[0]) + dx, int(base[1]))
    return (int(base[0]), int(base[1]) + dy)


def polar_lock_point(base: tuple[int, int], target: tuple[int, int], *, increment_deg: Any = _DEFAULT_INCREMENT_DEG,
                     tolerance_deg: Any = _DEFAULT_TOLERANCE_DEG,
                     preserve: Any = _DEFAULT_PRESERVE) -> tuple[tuple[int, int], bool, float]:
    """极轴锁定 → `(点, 是否锁住, 角偏差°)`；超容差**原样返回**且 `locked=False`。

    锁住时角度取最近的 `k·inc`（归一到 [0,360)），长度按 `preserve` 取径向距离或投影长度，
    坐标 = 基点 + 取整后的 `(r·cosθ, r·sinθ)`。
    """
    inc, tol = _inc_tol(increment_deg, tolerance_deg)
    mode = _preserve(preserve)
    bx, by = int(base[0]), int(base[1])
    dx = int(target[0]) - bx
    dy = int(target[1]) - by
    if dx == 0 and dy == 0:
        return ((bx, by), False, 0.0)
    angle = _norm_deg(math.degrees(math.atan2(dy, dx)))
    locked_angle = _norm_deg(round(angle / inc) * inc)
    deviation = abs((angle - locked_angle + 180.0) % 360.0 - 180.0)
    if deviation > tol:
        return ((int(target[0]), int(target[1])), False, float(deviation))
    rad = math.radians(locked_angle)
    length = math.hypot(dx, dy)
    if mode == "projection":
        length = dx * math.cos(rad) + dy * math.sin(rad)
    point = (bx + _round_nm(length * math.cos(rad)), by + _round_nm(length * math.sin(rad)))
    return (point, True, float(deviation))


# --------------------------------------------------------------------------------------
# 草图上的设置读写
# --------------------------------------------------------------------------------------
def _read_raw(sketch: Any) -> Any:
    """从草图（映射或对象）读扩展字段，两者都不支持时返回 None。"""
    if isinstance(sketch, Mapping):
        return sketch.get(_SETTINGS_KEY)
    return getattr(sketch, _SETTINGS_KEY, None)


def _write_raw(sketch: Any, payload: Mapping[str, Any]) -> None:
    """写扩展字段：映射走 `__setitem__`，对象走 `setattr`；都不行抛 `unsupported_scope`。"""
    if isinstance(sketch, Mapping):
        try:
            sketch[_SETTINGS_KEY] = dict(payload)
            return
        except TypeError as exc:
            raise _fail("unsupported_scope", "sketch mapping is read-only",
                        key=_SETTINGS_KEY) from exc
    try:
        setattr(sketch, _SETTINGS_KEY, dict(payload))
    except (AttributeError, TypeError) as exc:
        raise _fail("unsupported_scope", "sketch object rejects settings attribute",
                    key=_SETTINGS_KEY, error=type(exc).__name__) from exc


def resolve_settings(sketch: Any) -> dict[str, Any]:
    """读生效设置：草图上的 `ortho_polar` 覆盖缺省；缺字段按缺省补（不猜别的）。"""
    config: dict[str, Any] = {"ortho": False, "polar": False, "increment_deg": _DEFAULT_INCREMENT_DEG,
                              "tolerance_deg": _DEFAULT_TOLERANCE_DEG, "preserve": _DEFAULT_PRESERVE}
    stored = _read_raw(sketch)
    if isinstance(stored, Mapping):
        for key in config:
            if stored.get(key) is not None:
                config[key] = stored[key]
    return config


def ortho_polar_set(sk_or_doc: Any, *, ortho: Any = None, polar: Any = None, increment_deg: Any = None,
                    tolerance_deg: Any = None, preserve: Any = None) -> list[str]:
    """就地写追踪设置（F8/F10 开关 + 增量角 + 对齐容差），返回 `[]`。

    只校验**显式给出的**参数（`None` 表示"不动这一项"）；两项都给的开关经族内 `_flag` 归一成 bool。
    L1 无绘图辅助一等节点 ⇒ 落在草图节点的 `ortho_polar` 扩展字段上（未知字段原样保留）。
    """
    family = _family()
    sketch = family._sketch_of(sk_or_doc)
    config = resolve_settings(sketch)
    if ortho is not None:
        config["ortho"] = bool(family._flag(ortho, what="ortho"))
    if polar is not None:
        config["polar"] = bool(family._flag(polar, what="polar"))
    if increment_deg is not None or tolerance_deg is not None:
        inc, tol = _inc_tol(_fill(increment_deg, config["increment_deg"], _DEFAULT_INCREMENT_DEG),
                            _fill(tolerance_deg, config["tolerance_deg"], _DEFAULT_TOLERANCE_DEG))
        config["increment_deg"] = inc
        config["tolerance_deg"] = tol
    if preserve is not None:
        config["preserve"] = _preserve(preserve)
    _write_raw(sketch, {"ortho": bool(config["ortho"]), "polar": bool(config["polar"]),
                        "increment_deg": float(config["increment_deg"]),
                        "tolerance_deg": float(config["tolerance_deg"]),
                        "preserve": str(config["preserve"])})
    return []


def ortho_polar_apply(sk_or_doc: Any, *, base_nm: Any, target_nm: Any, ortho: Any = None, polar: Any = None,
                      increment_deg: Any = None, tolerance_deg: Any = None, preserve: Any = None,
                      create: bool = True, layer: str = "0") -> list[str]:
    """按正交/极轴把光标点锁到约束几何，并在 `create=True` 时**真建一条 line 实体**返回 `[uuid]`。

    优先级：正交优先（`|dx| >= |dy|` 判水平），只有正交未开时才试极轴；两者都没开时端点等于原始
    光标点（"没锁"与"锁到原处"在几何上都表现为端点 == 光标点，**不伪造**锁住标志）。
    零长线段（锁定后与基点重合）由 `line_create` 抛 `CadError(reason="geometry_invalid")`。
    `create=False` 是纯几何查询：不建实体，返回 `[]`。
    """
    family = _family()
    sketch = family._sketch_of(sk_or_doc)
    config = resolve_settings(sketch)
    base = family._point(base_nm, what="base_nm")
    target = family._point(target_nm, what="target_nm")
    use_ortho = bool(family._flag(_fill(ortho, config["ortho"], False), what="ortho"))
    use_polar = bool(family._flag(_fill(polar, config["polar"], False), what="polar"))
    inc, tol = _inc_tol(_fill(increment_deg, config["increment_deg"], _DEFAULT_INCREMENT_DEG),
                        _fill(tolerance_deg, config["tolerance_deg"], _DEFAULT_TOLERANCE_DEG))
    mode = _preserve(_fill(preserve, config["preserve"], _DEFAULT_PRESERVE))
    if use_ortho:
        locked = ortho_lock_point(base, target)
    elif use_polar:
        locked = polar_lock_point(base, target, increment_deg=inc, tolerance_deg=tol, preserve=mode)[0]
    else:
        locked = target
    if not create:
        return []
    return [str(item) for item in family.line_create(sketch, p1_nm=base, p2_nm=locked, layer=layer)]


# --------------------------------------------------------------------------------------
# 自检：≥2 条硬断言（几何真值 + 真实体后果 + 设置后果 + 错误路径 + L1 往返）
# --------------------------------------------------------------------------------------
def _fixture_sketch() -> dict[str, Any]:
    """L1 形状的草图节点（映射形态，被族内 `_sketch_of` 明确支持）。"""
    return {"uuid": str(_uuid.uuid4()), "name": "S1", "kind": "sketch",
            "entities": [], "constraints": []}


def _entity_endpoint(sketch: Any) -> tuple[int, int] | None:
    """取草图最后一个实体的端点（支持 `geom={x1,y1,x2,y2}` 与 `geom={points:[...]}`）。"""
    entities = sketch.get("entities") if isinstance(sketch, Mapping) else getattr(sketch, "entities", None)
    if not isinstance(entities, list) or not entities:
        return None
    geom = _family()._geom_of(entities[-1])
    if not isinstance(geom, Mapping):
        return None
    if all(key in geom for key in ("x2", "y2")):
        return (int(geom["x2"]), int(geom["y2"]))
    points = geom.get("points")
    if isinstance(points, list) and points:
        last = points[-1]
        return (int(last[0]), int(last[1]))
    return None


def _expect_reason(callable_: Callable[..., Any], expected: str, *args: Any, **kwargs: Any) -> tuple[bool, str]:
    """错误路径断言：必须是 `CadError` 且 reason 命中；别的异常也算失败（不静默）。"""
    try:
        callable_(*args, **kwargs)
    except CadError as exc:
        return (exc.reason == expected, f"reason={exc.reason}")
    except Exception as exc:  # noqa: BLE001 - 非 CadError 同样是失败
        return (False, f"unexpected {type(exc).__name__}: {exc}")
    return (False, "no error raised")


def selftest() -> tuple[int, int, list[str]]:
    """返回 `(通过数, 总数, 失败明细)`。每条断言都打真值，失败即红。"""
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), str(detail)))

    mm = _MM_NM
    # A1 正交：水平主导 ⇒ y 归零（真值逐纳米）
    got = ortho_lock_point((10 * mm, 5 * mm), (40 * mm, 7 * mm))
    check("ortho_horizontal", got == (40 * mm, 5 * mm), f"got={got}")
    # A2 正交：垂直主导 ⇒ x 归零
    got = ortho_lock_point((0, 0), (3 * mm, 9 * mm))
    check("ortho_vertical", got == (0, 9 * mm), f"got={got}")
    # A3 正交：45° 平局判水平（口径写死，不许两边都算对）
    got = ortho_lock_point((0, 0), (4 * mm, 4 * mm))
    check("ortho_tie_is_horizontal", got == (4 * mm, 0), f"got={got}")
    # A4 极轴：7° 光标 + 增量 15° + 容差 3° ⇒ 锁到 0° 且保持径向距离
    base = (0, 0)
    raw = (_round_nm(10 * mm * math.cos(math.radians(7.0))), _round_nm(10 * mm * math.sin(math.radians(7.0))))
    locked, hit, deviation = polar_lock_point(base, raw, increment_deg=15.0, tolerance_deg=3.0)
    want_radius = _round_nm(math.hypot(raw[0] - base[0], raw[1] - base[1]))
    check("polar_lock_to_zero_deg", hit and locked == (want_radius, 0) and deviation <= 3.0,
          f"raw={raw} locked={locked} hit={hit} dev={deviation:.4f}")
    # A5 极轴：超出对齐容差 ⇒ 原样返回（不锁、不改点）
    outside = (_round_nm(10 * mm * math.cos(math.radians(7.5))), _round_nm(10 * mm * math.sin(math.radians(7.5))))
    locked2, hit2, deviation2 = polar_lock_point(base, outside, increment_deg=15.0, tolerance_deg=3.0)
    check("polar_no_lock_outside_tolerance", (not hit2) and locked2 == outside and deviation2 > 3.0,
          f"locked={locked2} raw={outside} hit={hit2} dev={deviation2:.4f}")
    # B1 真实体后果：apply 建出 line 实体，uuid 一致、端点是锁定点（不是原始光标点）
    sketch = _fixture_sketch()
    uuids = ortho_polar_apply(sketch, base_nm=(10 * mm, 5 * mm), target_nm=(40 * mm, 7 * mm), ortho=True)
    entity = sketch["entities"][-1]
    check("apply_creates_locked_line",
          len(sketch["entities"]) == 1 and uuids == [str(entity.get("uuid"))]
          and _entity_endpoint(sketch) == (40 * mm, 5 * mm),
          f"uuids={uuids} endpoint={_entity_endpoint(sketch)} n_entities={len(sketch['entities'])}")
    # B2 设置后果：set(ortho=True) 后不带显式开关的 apply 也走正交（设置真影响几何）
    sketch2 = _fixture_sketch()
    ortho_polar_set(sketch2, ortho=True)
    ortho_polar_apply(sketch2, base_nm=(0, 0), target_nm=(3 * mm, 9 * mm))
    stored = resolve_settings(sketch2)
    check("settings_change_geometry",
          stored["ortho"] is True and _entity_endpoint(sketch2) == (0, 9 * mm),
          f"stored={stored} endpoint={_entity_endpoint(sketch2)}")
    # B3 未开任何辅助 ⇒ 端点等于光标点（"没锁"不许伪装成"锁住"）
    sketch3 = _fixture_sketch()
    ortho_polar_apply(sketch3, base_nm=(0, 0), target_nm=(3 * mm, 9 * mm))
    check("no_aid_keeps_raw_point", _entity_endpoint(sketch3) == (3 * mm, 9 * mm),
          f"endpoint={_entity_endpoint(sketch3)}")
    # C1 错误路径：目标不含 entities/sketches ⇒ no_sketch
    ok, detail = _expect_reason(ortho_polar_apply, "no_sketch", {}, base_nm=(0, 0), target_nm=(1 * mm, 0))
    check("error_no_sketch", ok, detail)
    # C2 错误路径：锁定后与基点重合（零长线）⇒ geometry_invalid
    ok, detail = _expect_reason(ortho_polar_apply, "geometry_invalid", _fixture_sketch(),
                                base_nm=(5 * mm, 5 * mm), target_nm=(5 * mm, 5 * mm), ortho=True)
    check("error_zero_length", ok, detail)
    # C3 错误路径：非法增量角 ⇒ bad_argument
    ok, detail = _expect_reason(ortho_polar_set, "bad_argument", _fixture_sketch(), increment_deg=0.0)
    check("error_bad_increment", ok, detail)
    # D1 L1 往返：文档里的实体端点在 document_to_l1 → document_from_l1 之后逐纳米不变
    try:
        from cadcore.docmodel import document_from_l1, document_to_l1, new_document
        doc = new_document()
        node = _fixture_sketch()
        doc.sketches.append(node)
        ortho_polar_apply(doc, base_nm=(0, 0), target_nm=(3 * mm, 9 * mm), polar=True,
                          increment_deg=90.0, tolerance_deg=45.0)
        reloaded = document_from_l1(document_to_l1(doc))
        endpoint = _entity_endpoint(reloaded.sketches[0])
        check("l1_roundtrip_keeps_locked_endpoint", endpoint == (0, 9 * mm),
              f"endpoint={endpoint} locked_expected={(0, 9 * mm)}")
    except Exception as exc:  # noqa: BLE001 - L1 后果拿不到就是红，不许静默
        check("l1_roundtrip_keeps_locked_endpoint", False, f"{type(exc).__name__}: {exc}")

    failed = [f"{name}({detail})" for name, ok, detail in checks if not ok]
    return (len(checks) - len(failed), len(checks), failed)


#: 族接口操作表（名字对齐能力条目，便于族门按 op 名绑定）。
OPERATIONS: dict[str, Callable[..., Any]] = {
    "ortho_polar_set": ortho_polar_set,
    "ortho_polar_apply": ortho_polar_apply,
}


def main() -> int:
    passed, total, failed = selftest()
    if failed:
        print(f"SELFTEST verdict=FAIL assertions={total} passed={passed} failed={len(failed)} "
              f"first_failure={failed[0]}")
        print(f"FEATURE_FAIL family={FAMILY} op=ortho_polar_apply verdict=FAIL missing="
              f"{','.join(failed)}")
        return 1
    print(f"SELFTEST verdict=PASS assertions={total} passed={passed} failed=0 "
          f"ops={','.join(sorted(OPERATIONS))}")
    print(f"FEATURE_OK family={FAMILY} op=ortho_polar_apply verdict=PASS assertions={total} "
          f"ortho=PASS polar=PASS l1_roundtrip=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
