"""cadcore.sketch.commands —— A9 二维编辑命令：画线 / 画圆 / 画弧 / 标尺寸（全部返回实体 uuid）。

一句话用途
----------
草图编辑的**唯一入口**：把用户的一次画图/标注动作变成 L1 文档里的实体或尺寸条目，
返回值一律是 uuid（禁下标引用），坐标一律整数纳米。

为什么这样设计
--------------
1. **命令只落库，不顺手求解**：`add_*` 只做"校验 + 追加 + 返回 uuid"，求解是显式的 `solve()`
   （A10）。把两者绑在一起会让"画一笔就动一次几何"，用户无法预测；也让失败定位变成一团。
2. **坏输入当场报错，绝不半接受**：坐标不是整数纳米、半径非正、零长线、退化的弧（首尾同角）、
   引用不存在的实体 —— 全部抛 `SketchError`（带 i18n key）。宁可拒绝，也不写进文档留个暗雷。
3. **尺寸分驱动与参考**：契约签名里 `value_nm: int | None`，`None` 即**参考尺寸**（只测量不驱动，
   解算后由 A10 回填 `measured_nm`）；非 `None` 即**驱动尺寸**（参与方程）。角度类尺寸的
   `value_nm` 口径 = **微度**（`1° = 1e6`），理由见 `constraints` 模块 docstring。
4. **与 A5 `docmodel` 结构解耦**：草图按结构接受（映射或对象），`docmodel` 落盘后本文件零改动。
5. **文案走 key**：本文件不产出任何用户可见文案，错误只带 `err.*` key + ASCII 细节。

本文件与 `constraints.py` 的分工
--------------------------------
- 本文件（A9）：`add_line` / `add_circle` / `add_arc` / `add_dimension`（几何与标注的写入命令）；
- `constraints.py`（A10）：`add_constraint` / `solve` / `closed_profiles`（约束与解算）。
  三个 A10 入口**在本文件再导出一次**，两条 import 路径都可用（下游照契约取名即可）。
"""

from __future__ import annotations

import copy

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from .constraints import (
    CONSTRAINT_KINDS as CONSTRAINT_KINDS,
    DIMENSION_KINDS as DIMENSION_KINDS,
    UDEG_PER_DEG as UDEG_PER_DEG,
    GeometryError as GeometryError,
    InvalidLength as InvalidLength,
    OutOfRange as OutOfRange,
    Sketch as Sketch,
    SketchError as SketchError,
    SolveResult as SolveResult,
    UnknownReference as UnknownReference,
    UnsupportedKind as UnsupportedKind,
    _as_nm,
    _get,
    _set,
    add_constraint as add_constraint,
    closed_profiles as closed_profiles,
    new_uuid,
    sketch_constraints,
    sketch_dimensions,
    sketch_entities,
    solve as solve,
    validate_dimension,
)
from ..units import finite_number
from ..i18n.keys import is_text_key

__all__ = [
    "CONSTRAINT_KINDS",
    "DIMENSION_KINDS",
    "GeometryError",
    "HISTORY_KEY",
    "InvalidLength",
    "MAX_HISTORY",
    "OutOfRange",
    "SketchError",
    "SolveResult",
    "UDEG_PER_DEG",
    "UnknownReference",
    "UnsupportedKind",
    "add_arc",
    "add_circle",
    "add_constraint",
    "add_dimension",
    "add_line",
    "closed_profiles",
    "delete_entity",
    "redo",
    "solve",
    "undo",
    "update_dimension",
]

#: 圆弧角度的存储精度（度）：1e-6 度在 1 m 半径上约 17 nm，与整数纳米分辨率同量级。
_ANGLE_DECIMALS: int = 6


# --------------------------------------------------------------------------------------
# 入参把关（数字/坐标/层名）
# --------------------------------------------------------------------------------------


def _as_point(point: Any, *, what: str) -> tuple[int, int]:
    """点入参 → `(x_nm, y_nm)` 整数对；形态/类型不对当场报错。"""
    if isinstance(point, (str, bytes)) or not isinstance(point, Sequence) or len(point) != 2:
        raise GeometryError("err.geometry_invalid", detail=f"{what} must be a (x_nm, y_nm) pair, got {point!r}")
    return (_as_nm(point[0], positive=False, what=f"{what}.x"), _as_nm(point[1], positive=False, what=f"{what}.y"))


def _as_layer(layer: Any) -> str:
    """图层名：非空字符串（空串会让实体在图层管理里"消失"）。"""
    if not isinstance(layer, str) or not layer.strip():
        raise GeometryError("err.geometry_invalid", detail=f"layer must be a non-empty string, got {layer!r}")
    return layer


def _as_deg(value: Any, *, what: str) -> float:
    """角度入参 → 归一化到 `[0, 360)` 的浮点度（只规范存储，不改变几何）。

    可表示性判断走 `cadcore.units.finite_number`（唯一真源）：309 位整数在 `float()` 上会抛
    `OverflowError`，"先转再判"等于让参数校验自己泄漏裸内建异常（B6 缺陷 RC5，`add_arc.start_deg`）。
    """
    number = finite_number(value)
    if number is None:
        raise GeometryError("err.geometry_invalid",
                            detail=f"{what} must be a finite angle in degrees, got {value!r}")
    normalized = round(number % 360.0, _ANGLE_DECIMALS)
    return 0.0 if normalized == 0.0 else normalized  # 去掉 -0.0，保证 L1 规范化逐字节稳定


def _append_entity(
    sk: Sketch, *, kind: str, layer: str, geom: dict[str, Any], construction: bool, fixed: bool
) -> str:
    """把实体追加进草图并返回 uuid（实体字段照 `MVP-INTERFACES` §C：uuid/kind/layer/construction/geom）。"""
    entity_uuid = new_uuid()
    entity: dict[str, Any] = {
        "uuid": entity_uuid,
        "kind": kind,
        "layer": layer,
        "construction": bool(construction),
        "geom": geom,
    }
    if fixed:
        # 契约外**增补**的可选字段：只在本席的求解器里被读取（持久锚定），缺席即未锚定。
        entity["fixed"] = True
    sketch_entities(sk).append(entity)
    return entity_uuid


# --------------------------------------------------------------------------------------
# 几何命令（A9）
# --------------------------------------------------------------------------------------


def add_line(
    sk: Sketch,
    *,
    p1: tuple[int, int],
    p2: tuple[int, int],
    layer: str = "0",
    construction: bool = False,
    fixed: bool = False,
) -> str:
    """加一条线段，返回实体 uuid；`p1 == p2`（零长线）当场报错。

    `construction` / `fixed` 是契约外**增补**的关键字（默认 False）：前者标记参考几何，
    后者把实体坐标钉住（求解器的持久锚定，DoF 记账需要）。
    """
    start = _as_point(p1, what="p1")
    end = _as_point(p2, what="p2")
    layer_name = _as_layer(layer)
    if start == end:
        raise GeometryError("err.geometry_invalid", detail=f"zero-length line at {start!r}")
    return _append_entity(
        sk,
        kind="line",
        layer=layer_name,
        geom={"x1": start[0], "y1": start[1], "x2": end[0], "y2": end[1]},
        construction=construction,
        fixed=fixed,
    )


def add_circle(
    sk: Sketch,
    *,
    center: tuple[int, int],
    radius_nm: int,
    layer: str = "0",
    construction: bool = False,
    fixed: bool = False,
) -> str:
    """加一个整圆，返回实体 uuid；半径必须 > 0 且为整数纳米（`radius_nm` 就是半径，不是直径）。"""
    center_point = _as_point(center, what="center")
    radius = _as_nm(radius_nm, positive=True, what="radius_nm")
    layer_name = _as_layer(layer)
    return _append_entity(
        sk,
        kind="circle",
        layer=layer_name,
        geom={"cx": center_point[0], "cy": center_point[1], "r": radius},
        construction=construction,
        fixed=fixed,
    )


def add_arc(
    sk: Sketch,
    *,
    center: tuple[int, int],
    radius_nm: int,
    start_deg: float,
    end_deg: float,
    layer: str = "0",
    construction: bool = False,
    fixed: bool = False,
) -> str:
    """加一段圆弧，返回实体 uuid；角度入参按 `mod 360` 归一到 `[0, 360)` 后存储。

    口径：圆弧**逆时针**从 `start_deg` 扫到 `end_deg`（与 DXF/数学惯例一致）；
    首尾同角（含 `0 → 360`）是退化弧 ⇒ 报错，整圆请用 `add_circle`。
    圆弧的 `start`/`end` 端点由 `(cx, cy, r)` + 这两个角度导出，角度本身不参与求解（见 A10 docstring）。
    """
    center_point = _as_point(center, what="center")
    radius = _as_nm(radius_nm, positive=True, what="radius_nm")
    layer_name = _as_layer(layer)
    start = _as_deg(start_deg, what="start_deg")
    end = _as_deg(end_deg, what="end_deg")
    if start == end:
        raise GeometryError(
            "err.geometry_invalid",
            detail=f"degenerate arc: start_deg and end_deg are the same after normalisation ({start})",
        )
    return _append_entity(
        sk,
        kind="arc",
        layer=layer_name,
        geom={"cx": center_point[0], "cy": center_point[1], "r": radius,
              "start_deg": start, "end_deg": end},
        construction=construction,
        fixed=fixed,
    )


def add_dimension(
    sk: Sketch,
    *,
    kind: str,
    refs: Sequence[tuple[str, str]],
    value_nm: int | None = None,
    text_key: str | None = None,
) -> str:
    """加一条尺寸（标注），返回尺寸 uuid；`value_nm is None` = **参考尺寸**。

    - `kind`：`linear` / `radial` / `diameter` / `angular`（契约冻结集）；
    - `refs`：`(entity_uuid, sub_shape_name)` 列表，禁下标；引用形态按等价几何约束校验
      （`linear` ↔ 距离、`radial`/`diameter` ↔ 半径、`angular` ↔ 两整线的夹角）；
    - `value_nm`：驱动值（纳米）。**`angular` 的口径是微度**（`1° = 1e6`）；
    - `text_key`：文案 key（语言包里的键），本模块**不翻译**它 —— 渲染时由 A16 语言层取词；
      缺省 `None`，跨尺寸类型复用时才需要显式给（例如统一写 `dim.length.overall`）。
    """
    normalized = validate_dimension(sk, kind=kind, refs=refs, value_nm=value_nm)
    if text_key is not None and not is_text_key(text_key):
        # `text_key` 是 i18n key，不是人话文案：只查"非空字符串"会让产品自己造出违反
        # `docmodel.check_invariants`（`text_not_key`）的文档并存盘/重开（B6 缺陷 RC6）。
        # 判据与读侧同源（`cadcore.i18n.keys.is_text_key`），免得写侧读侧各判各的。
        # `text_key=None` 仍是合法的"不指定文案"（参考尺寸等），不在闸内。
        raise GeometryError(
            "err.geometry_invalid",
            detail=f"text_key must be an i18n key (dotted ascii identifier) or None, got {text_key!r}",
        )
    value = None if value_nm is None else _as_nm(value_nm, positive=False, what="dimension.value_nm")
    item: dict[str, Any] = {
        "uuid": new_uuid(),
        "kind": kind,
        "refs": [[entity_ref, sub] for entity_ref, sub in normalized],
        "value_nm": value,
        "text_key": text_key,
    }
    sketch_dimensions(sk).append(item)
    return str(item["uuid"])


def update_dimension(sk: Sketch, dimension_uuid: str, *, value_nm: int) -> str:
    """改一条**驱动尺寸**的值（改数即改形），返回尺寸 uuid；只写文档，不顺手求解。

    - 找不到 uuid ⇒ `UnknownReference`；`value_nm=None` ⇒ `GeometryError`（参考尺寸不是驱动尺寸；
      "改值"与"改驱动/参考属性"是两件事，后者请删了重标）；
    - 引用形态与取值重新走 `validate_dimension`（手改过的旧文档也接得住，不信任落库时的旧校验）；
    - 角度类尺寸的口径仍是**微度**，区间 `(0, 360e6)`。
    """
    if not isinstance(dimension_uuid, str) or not dimension_uuid:
        raise GeometryError(
            "err.geometry_invalid", detail=f"dimension_uuid must be a non-empty string, got {dimension_uuid!r}"
        )
    if value_nm is None:
        raise GeometryError("err.geometry_invalid",
                            detail="update_dimension needs a driving value_nm; a reference dimension has none")
    value = _as_nm(value_nm, positive=False, what="dimension.value_nm")
    for item in sketch_dimensions(sk):
        if _get(item, "uuid", None) != dimension_uuid:
            continue
        if _get(item, "value_nm", None) is None:
            raise GeometryError(
                "err.geometry_invalid",
                detail=f"dimension {dimension_uuid} is a reference (driven) dimension; "
                       "converting it into a driving one is a different operation",
            )
        validate_dimension(sk, kind=_get(item, "kind", None), refs=_get(item, "refs", None), value_nm=value)
        _set(item, "value_nm", value)
        return str(dimension_uuid)
    raise UnknownReference("err.unknown_reference", reference=str(dimension_uuid))


# --------------------------------------------------------------------------------------
# 删除实体 + 撤销/重做（A9-E5 新增：删除必须带安全网，且不污染 L1 契约）
# --------------------------------------------------------------------------------------

#: 草图级撤销栈的落点：`sk["extra"][HISTORY_KEY]` —— `docmodel.PRODUCT_EXTENSION_KEYS["sketch"]`
#: 已登记 `extra`（**不新增草图顶层键**，否则自家 save→open 会多出 `unknown_field_preserved`）。
HISTORY_KEY: str = "edit_history"
#: 撤销栈容量（先进先出）。栈只装"被删掉的东西"，不是整图快照，故可以给得比较宽。
MAX_HISTORY: int = 64


def _extra_container(sk: Sketch, *, create: bool) -> Any:
    """草图的扩展容器 `sk["extra"]`（映射/对象双兼容；不创建时返回 `None`）。"""
    extra = _get(sk, "extra", None)
    if extra is None:
        if not create:
            return None
        extra = {}
        _set(sk, "extra", extra)
    if not isinstance(extra, MutableMapping):
        raise GeometryError("err.geometry_invalid",
                            detail=f"sketch 'extra' must be a mutable mapping, got {type(extra).__name__}")
    return extra


def _history_store(sk: Sketch, *, create: bool) -> dict[str, Any] | None:
    """撤销栈容器 `{"undo": [...], "redo": [...]}`；结构损坏当场报错（不静默重建丢历史）。"""
    extra = _extra_container(sk, create=create)
    if extra is None:
        return None
    store = extra.get(HISTORY_KEY)
    if store is None:
        if not create:
            return None
        store = {"undo": [], "redo": []}
        extra[HISTORY_KEY] = store
    if (not isinstance(store, MutableMapping) or not isinstance(store.get("undo"), list)
            or not isinstance(store.get("redo"), list)):
        raise GeometryError("err.geometry_invalid", detail="damaged sketch history store: expected {undo: [], redo: []}")
    return store


def _item_refs_uuid(item: Any) -> set[str]:
    """条目引用的实体 uuid 集合（形态坏了就当没引用，交给读侧校验报错）。"""
    refs = _get(item, "refs", None) or []
    found: set[str] = set()
    if isinstance(refs, (str, bytes, Mapping)):
        return found
    for ref in refs:
        if isinstance(ref, (str, bytes)) or not isinstance(ref, Sequence) or len(ref) != 2:
            continue
        found.add(str(ref[0]))
    return found


def _remove_entity(sk: Sketch, entity_uuid: str) -> dict[str, Any]:
    """删实体 + 连带摘掉引用它的约束/尺寸，返回"删除包"（不动撤销栈；`redo` 复用它）。"""
    entities = sketch_entities(sk)
    entity_index = next((index for index, item in enumerate(entities)
                         if _get(item, "uuid", None) == entity_uuid), None)
    if entity_index is None:
        raise UnknownReference("err.unknown_reference", reference=str(entity_uuid))
    pack: dict[str, Any] = {"entity": copy.deepcopy(entities[entity_index]), "entity_index": int(entity_index),
                            "constraints": [], "dimensions": []}
    for container, bucket in ((sketch_constraints(sk), pack["constraints"]),
                              (sketch_dimensions(sk), pack["dimensions"])):
        positions = [index for index, item in enumerate(container) if entity_uuid in _item_refs_uuid(item)]
        for index in reversed(positions):
            bucket.append([int(index), copy.deepcopy(container[index])])
            del container[index]
        bucket.reverse()
    del entities[entity_index]
    return pack


def _restore_pack(sk: Sketch, pack: Mapping[str, Any]) -> None:
    """把删除包整包放回原位（实体回原下标，约束/尺寸按原下标升序插回）。"""
    entities = sketch_entities(sk)
    index = min(int(pack.get("entity_index", 0)), len(entities))
    entities.insert(index, copy.deepcopy(pack["entity"]))
    for container, key in ((sketch_constraints(sk), "constraints"), (sketch_dimensions(sk), "dimensions")):
        for position, item in sorted(pack.get(key) or [], key=lambda pair: int(pair[0])):
            container.insert(min(int(position), len(container)), copy.deepcopy(item))


def delete_entity(sk: Sketch, entity_uuid: str) -> dict[str, Any]:
    """删实体 + 连带清掉引用它的约束/尺寸；删下来的整包进撤销栈（`undo` 可原位放回）。

    - 找不到实体 ⇒ `UnknownReference`（不静默成功）；
    - 引用被删实体的约束与尺寸**一并删掉**：留着就是悬空引用，save→open 会被不变量检查判非法；
    - 返回 `{"uuid", "constraints_removed": [uuid...], "dimensions_removed": [uuid...], "history_depth"}`，
      三个计数都为 `0` 时是**真为零**（这条实体没人引用）。
    """
    pack = _remove_entity(sk, entity_uuid)
    store = _history_store(sk, create=True)
    assert store is not None  # create=True 时不会返回 None
    store["undo"].append(pack)
    del store["undo"][:-MAX_HISTORY]
    store["redo"].clear()  # 新编辑作废重做分支（与常见编辑器语义一致）
    return {
        "uuid": str(entity_uuid),
        "constraints_removed": [str(item["uuid"]) for _position, item in pack["constraints"]],
        "dimensions_removed": [str(item["uuid"]) for _position, item in pack["dimensions"]],
        "history_depth": len(store["undo"]),
    }


def undo(sk: Sketch) -> bool:
    """撤销最近一次删除（实体与连带删掉的约束/尺寸整包放回）；没有可撤销的 ⇒ `False`。"""
    store = _history_store(sk, create=False)
    if store is None or not store["undo"]:
        return False
    pack = store["undo"].pop()
    _restore_pack(sk, pack)
    store["redo"].append(pack)
    return True


def redo(sk: Sketch) -> bool:
    """重做最近一次被撤销的删除；没有可重做的 ⇒ `False`。"""
    store = _history_store(sk, create=False)
    if store is None or not store["redo"]:
        return False
    pack = store["redo"].pop()
    fresh = _remove_entity(sk, str(pack["entity"]["uuid"]))
    store["undo"].append(fresh)
    del store["undo"][:-MAX_HISTORY]
    return True
