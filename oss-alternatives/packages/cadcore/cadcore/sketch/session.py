#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""cadcore.sketch.session —— 草图会话状态机：进入 / 退出草图、选基准面、切换当前草图。

一句话用途
----------
把"**现在在编辑哪张草图、它建在哪个基准面上**"做成**文档级状态**（L1 往返存活），
而不是让整份文档永远只有一张隐式草图：`enter_sketch()` / `exit_sketch()` /
`set_sketch_plane()` / `active_sketch()` / `sketch_plane()` / `sketch_session()`。

落点与三态
----------
- 会话状态落 `doc.meta["sketch_session"]`（= `SESSION_KEY`），是**文档数据**，随 L1 往返存活；
  该键在 `docmodel.PRODUCT_EXTENSION_KEYS["meta"]` 里登记（有真生产者探针），
  不允许"写侧发得出去、读侧不认"。
- 平面枚举 `PLANES = ("XY", "XZ", "YZ")`；未知平面 ⇒ `CadError(reason="unknown_plane")`
  （**不静默回落成 XY** —— 建错基准面的草图和"没建"在下游是两回事）。
- 进入不存在的草图 ⇒ `CadError(reason="unknown_sketch")`；
- **退出只清"当前草图"标记**：草图对象、实体、约束一个字节都不动（退出 ≠ 删除）；
- 三态：没有活动草图时 `active_sketch()` 返回 `None`（不适用），`sketch_plane()` 在平面字段
  缺失时返回 `"NA"` —— 既不返回空串冒充 uuid，也不拿 `"XY"` 冒充"用户选过"。

自检（文件自带，不依赖 pytest）：

    python3 oss-alternatives/packages/cadcore/cadcore/sketch/session.py
"""

from __future__ import annotations

import sys
import uuid as _uuid
from collections.abc import Mapping, MutableMapping
from typing import Any

if __package__ in (None, ""):  # 直跑引导：python3 cadcore/sketch/session.py
    import os as _os

    _sys_path_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    if _sys_path_root not in sys.path:
        sys.path.insert(0, _sys_path_root)
    __package__ = "cadcore.sketch"

from ..errors import CadError  # noqa: E402

__all__ = ["PLANES", "SESSION_KEY", "active_sketch", "enter_sketch", "exit_sketch",
           "set_sketch_plane", "sketch_plane", "sketch_session", "selftest"]

#: 基准面枚举（冻结）：XY = 俯视、XZ = 前视、YZ = 右视。
PLANES: tuple[str, ...] = ("XY", "XZ", "YZ")

#: 会话状态在文档 `meta` 上的键（登记于 `docmodel.PRODUCT_EXTENSION_KEYS["meta"]`）。
SESSION_KEY = "sketch_session"

#: 不适用哨兵（与全仓三态口径一致：0 真为零 / -1 未采 / "NA" 不适用）。
NA = "NA"


def _fail(reason: str, message: str, **details: Any) -> CadError:
    return CadError(message, reason=reason, **details)


def _mapping(value: Any, *, what: str) -> MutableMapping[str, Any]:
    if not isinstance(value, MutableMapping):
        raise _fail("invalid_argument", "%s must be a mutable mapping" % what,
                    value_type=type(value).__name__)
    return value


def _doc(target: Any) -> MutableMapping[str, Any]:
    """文档容器：`Document`（有 `meta` 槽位）或 L1 映射（有 `sketches`/`meta`）。"""
    if isinstance(target, MutableMapping):
        meta = target.get("meta")
        if isinstance(meta, MutableMapping):
            return target
        raise _fail("bad_document", "document mapping has no meta mapping", reason_key="meta")
    meta = getattr(target, "meta", None)
    if isinstance(meta, MutableMapping):
        return target  # Document 对象：属性访问由 _meta()/_sketches() 分流
    raise _fail("bad_document", "target is neither a document mapping nor a Document",
                value_type=type(target).__name__)


def _meta(doc: Any) -> MutableMapping[str, Any]:
    meta = doc.get("meta") if isinstance(doc, Mapping) else getattr(doc, "meta", None)
    return _mapping(meta, what="document meta")


def _sketches(doc: Any) -> list[Any]:
    value = doc.get("sketches") if isinstance(doc, Mapping) else getattr(doc, "sketches", None)
    if value is None:
        raise _fail("bad_document", "document has no sketches list")
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise _fail("bad_document", "document sketches must be a list",
                    value_type=type(value).__name__)
    return value


def _uuid_of(node: Any) -> str:
    value = node.get("uuid") if isinstance(node, Mapping) else getattr(node, "uuid", None)
    return str(value) if value else ""


def _find_sketch(doc: Any, uuid_: Any) -> Any:
    wanted = str(uuid_)
    for sketch in _sketches(doc):
        if _uuid_of(sketch) == wanted:
            return sketch
    raise _fail("unknown_sketch", "no sketch with that uuid in this document", sketch=wanted)


def sketch_plane(sketch: Any) -> str:
    """草图所在基准面：合法枚举值原样返回；**字段缺失/非法 ⇒ "NA"**（不拿 XY 冒充"选过"）。"""
    if not isinstance(sketch, Mapping):
        raise _fail("invalid_argument", "sketch must be a mapping", value_type=type(sketch).__name__)
    value = sketch.get("plane")
    return str(value).upper() if isinstance(value, str) and str(value).upper() in PLANES else NA


def set_sketch_plane(sketch: Any, plane: Any) -> str:
    """给草图选定基准面（`XY`/`XZ`/`YZ`，大小写不敏感）；未知值 ⇒ `unknown_plane`。

    只改 `plane` 一个字段：实体/约束/尺寸**不动**（换基准面不是重建几何，几何是草图局部坐标）。
    """
    node = _mapping(sketch, what="sketch")
    token = str(plane).strip().upper() if plane is not None else ""
    if token not in PLANES:
        raise _fail("unknown_plane", "plane is not in this layer's frozen enum", plane=plane,
                    known=list(PLANES))
    node["plane"] = token
    return token


def sketch_session(doc: Any) -> dict[str, Any]:
    """会话状态（机读）：`{"active": <uuid|None>, "plane": <PLANE|"NA">, "entered": <n>}`。

    没有会话记录 ⇒ `active=None`（**不适用**，不是"失踪"）；`entered` 是从进程启动以来进入次数。
    """
    container = _doc(doc)
    meta = _meta(container)
    raw = meta.get(SESSION_KEY)
    if not isinstance(raw, Mapping):
        return {"active": None, "plane": NA, "entered": 0}
    active = raw.get("active")
    active_uuid = str(active) if isinstance(active, str) and active.strip() else None
    plane = raw.get("plane")
    return {"active": active_uuid,
            "plane": str(plane) if isinstance(plane, str) and str(plane).upper() in PLANES else NA,
            "entered": int(raw.get("entered") or 0)}


def active_sketch(doc: Any) -> str | None:
    """当前活动草图 uuid；没有 ⇒ `None`（三态：`None` = 不适用，撤出后也一样）。"""
    return sketch_session(doc)["active"]


def enter_sketch(doc: Any, *, sketch: Any = None, plane: Any = None, create: bool = True) -> str:
    """**进入草图**（并置为当前）：`sketch` 给 uuid / 草图节点就进已有的，不给就新建一张。

    - 新建：追加 `{"uuid", "plane", "entities": [], "constraints": [], "dimensions": []}`
      到 `doc.sketches`（与 `docmodel` 的草图契约同形），返回新 uuid；
    - `plane` 给了 ⇒ 同时选基准面（未知值 `unknown_plane`）；新建且没给 ⇒ `XY`；
    - `create=False` 且 `sketch=None` ⇒ `CadError(reason="unknown_sketch")`（不许静默建一张）；
    - 进入不改变任何几何。
    """
    container = _doc(doc)
    meta = _meta(container)
    if sketch is None:
        if not create:
            raise _fail("unknown_sketch", "enter_sketch needs a sketch uuid when create=False")
        chosen_plane = "XY" if plane is None else str(plane).strip().upper()
        set_sketch_plane({"plane": None}, chosen_plane)  # 校验枚举（用同一把尺子）
        node: MutableMapping[str, Any] = {"uuid": str(_uuid.uuid4()), "plane": chosen_plane,
                                          "entities": [], "constraints": [], "dimensions": []}
        _sketches(container).append(node)
    elif isinstance(sketch, Mapping):
        # 也接受**草图节点本身**（调用方手里常拿的就是它）：必须真在本文档里，不做"影子草图"。
        node = _find_sketch(container, _uuid_of(sketch))
        if plane is not None:
            set_sketch_plane(node, plane)
    else:
        node = _find_sketch(container, sketch)
        if plane is not None:
            set_sketch_plane(node, plane)
    state = meta.get(SESSION_KEY)
    entered = int(state.get("entered") or 0) if isinstance(state, Mapping) else 0
    meta[SESSION_KEY] = {"active": _uuid_of(node), "plane": sketch_plane(node),
                         "entered": entered + 1}
    return _uuid_of(node)


def exit_sketch(doc: Any) -> str | None:
    """**退出草图**：清掉"当前草图"标记，返回**退出前**的活动草图 uuid。

    没有活动草图 ⇒ 返回 `None`（不适用；不是"退出失败"，也不抛错）。几何一个字节不动。
    """
    container = _doc(doc)
    meta = _meta(container)
    state = sketch_session(container)
    previous = state["active"]
    meta[SESSION_KEY] = {"active": None, "plane": NA, "entered": int(state["entered"])}
    return previous


def selftest() -> int:
    """自检：进入/退出/选面/切换/未知平面拒收/退出不动几何（阴性对照：没选面就是 NA）。"""
    checks: list[str] = []
    doc: dict[str, Any] = {"sketches": [], "meta": {}}
    first = enter_sketch(doc, plane="XZ")
    assert active_sketch(doc) == first, active_sketch(doc)
    assert sketch_plane(doc["sketches"][0]) == "XZ", doc["sketches"][0]
    assert sketch_session(doc)["entered"] == 1, sketch_session(doc)
    second = enter_sketch(doc)
    assert active_sketch(doc) == second and second != first, "second sketch must become active"
    assert sketch_plane(doc["sketches"][1]) == "XY", doc["sketches"][1]
    doc["sketches"][0]["entities"].append({"uuid": str(_uuid.uuid4()), "kind": "line",
                                           "geom": {"x1": 0, "y1": 0, "x2": 10, "y2": 0}})
    before = [len(item["entities"]) for item in doc["sketches"]]
    set_sketch_plane(doc["sketches"][0], "yz")
    assert doc["sketches"][0]["plane"] == "YZ", doc["sketches"][0]["plane"]
    enter_sketch(doc, sketch=first)
    assert active_sketch(doc) == first, active_sketch(doc)
    left = exit_sketch(doc)
    assert left == first and active_sketch(doc) is None, (left, active_sketch(doc))
    assert [len(item["entities"]) for item in doc["sketches"]] == before, "exit must not touch geometry"
    assert sketch_plane({}) == NA, "missing plane must read NA, not XY"
    assert exit_sketch(doc) is None, "exiting with no active sketch is NA (None), not an error"
    checks.append("enter_exit_plane")
    for bad in ("ZX", "", 3, None):
        try:
            set_sketch_plane(doc["sketches"][0], bad)
        except CadError as exc:
            assert exc.reason == "unknown_plane", (bad, exc.reason)
        else:
            raise AssertionError("set_sketch_plane accepted %r" % (bad,))
    checks.append("unknown_plane_rejected")
    try:
        enter_sketch(doc, sketch="00000000-0000-0000-0000-000000000000")
    except CadError as exc:
        assert exc.reason == "unknown_sketch", exc.reason
    else:
        raise AssertionError("enter_sketch accepted an unknown sketch uuid")
    try:
        enter_sketch(doc, create=False)
    except CadError as exc:
        assert exc.reason == "unknown_sketch", exc.reason
    else:
        raise AssertionError("create=False must not silently create a sketch")
    checks.append("unknown_sketch_rejected")
    print("SW_SKETCH_SESSION verdict=PASS checks=%d plan=%s" % (len(checks), list(PLANES)))
    return 0


def main() -> int:
    try:
        return selftest()
    except Exception as exc:  # noqa: BLE001 - 自检必须 loud 失败
        print("SW_SKETCH_SESSION verdict=FAIL error=%s: %s" % (type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
