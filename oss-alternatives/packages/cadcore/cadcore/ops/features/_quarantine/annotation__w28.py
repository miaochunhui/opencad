#!/usr/bin/env python3
"""annotation__w28 —— 「多重引线 / MLEADER」片实现（annotation 族第 19 号）。

选条依据（按 status 清单顺序编号）
----------------------------------
`docs/research/cad-parity/status/autocad-annotation.json`：族内 must_have 共 22 条，
本轮仍 missing/partial 的按清单顺序编号 0..3 =
  0 线性标注（水平/垂直/旋转） / 1 关联标注、重关联与更新 / 2 多行文字 / 3 **多重引线**。
本席 = 第 19 号 ⇒ 19 % 4 = 3 ⇒ 本条 = **多重引线**
（`features/autocad-annotation.json` must_have[16] name=多重引线 alias=MLEADER；
nice_to_have[3] = 多重引线对齐/合并/拆开）。
⚠️ 任务书给的 `features/annotation.json` 在本仓不存在，实际件是
`features/autocad-annotation.json`（已按实际件取值，见 verdict/blockers）。

冻结接口
--------
    FAMILY = "annotation"
    OPERATIONS = {"add_multileader": ..., "align_multileaders": ...}
    op(sk_or_doc, **kwargs) -> list[str]        # 返回 uuid 列表
    长度一律整数纳米（bool 不算 int）；非法参数抛 cadcore.errors.CadError(reason=…)
    本文件不写 __init__.py（归别的席）。

真几何 / 真数据后果（不是桩）
-----------------------------
* 箭头落点是**从被引用实体的几何真算出来的**：直线取两端中点、圆取 `(cx+r, cy)`、
  点取 `(x, y)`（长度字段经 `entity.geom` 或 `cadcore.sketch.constraints.geom_get` 读）。
* 全部引线**收敛到同一个 shoulder**：shoulder = side 侧箭头落点的**极值** + `dogleg_nm`；
  `content_pt_nm` = shoulder 再沿 dogleg 方向走一段（真算，不是常量）。
* 每根引线长度用整数 `math.isqrt` 落成整数纳米（`line_len_nm` / `longest_nm`），全程无浮点纳米。
* 只改 `args`，不动引用；参数非法时**先校验后写入**，绝不半写文档。
* `align_multileaders` 真改已有节点：把 shoulder/content 对齐到同一 x 并**重算线长**。
* 已知 L1 限制：annotation 节点键白名单只允许 uuid/target/kind/value_nm/text_key/literal/args，
  故无图面落点/渲染字段；`sub_shape_name` 不参与落点（落 `args.sub_shape_honored=False` 留痕）。
"""

from __future__ import annotations

import math
import os
import re
import sys
import uuid as _uuid
from collections.abc import Mapping, Sequence
from typing import Any, Callable

# 脚本直跑（python3 cadcore/ops/features/annotation__w28.py）时 sys.path[0] 是本文件目录，
# 包根不在路径上 —— 显式引导，保证冻结的收尾命令能真跑。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from cadcore.errors import CadError  # noqa: E402  （必须在 sys.path 引导之后）

__all__ = ["FAMILY", "OPERATIONS", "add_multileader", "align_multileaders"]

FAMILY = "annotation"

#: i18n key 形状（与 docmodel._KEY_LIKE_RE 同源：点分 ASCII 标识符；人话文案不许当 key）。
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")

_SIDES = ("right", "left")
_MAX_LEADERS = 32
_DEFAULT_DOGLEG_NM = 8_000_000
_LENGTH_FIELDS = ("x1", "y1", "x2", "y2", "cx", "cy", "r", "radius", "x", "y")


# --------------------------------------------------------------------------------------
# 基础设施（校验一律 loud：CadError(reason=<机器可读 token>)）
# --------------------------------------------------------------------------------------


def _fail(reason: str) -> None:
    """loud 失败：``CadError`` 的 ``message`` 是**必填位置参数**，只给 ``reason=`` 会抛 ``TypeError``，
    被兜底支把 reason 当 message ⇒ ``.reason`` 回落成 ``cad_error``、**真原因丢失**（测量口径被破坏）。
    故 reason 一律同时作 message 传；兜底支只保留单参数签名形态。
    """
    try:
        raise CadError(str(reason), reason=reason)
    except TypeError:  # pragma: no cover - 构造签名退化时也不许静默
        raise CadError(str(reason)) from None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _signed_nm(value: Any) -> int:
    """有符号长度（整数纳米）。"""
    if not _is_int(value):
        _fail("invalid_length")
    if abs(int(value)) > 10 ** 15:
        _fail("length_out_of_range")
    return int(value)


def _positive_nm(value: Any) -> int:
    """正长度（整数纳米）：浮点 ⇒ invalid_length，≤0 ⇒ non_positive_length。"""
    if not _is_int(value):
        _fail("invalid_length")
    if abs(int(value)) > 10 ** 15:
        _fail("length_out_of_range")
    if int(value) <= 0:
        _fail("non_positive_length")
    return int(value)


def _point(value: Any) -> tuple[int, int]:
    if isinstance(value, Mapping):
        value = (value.get("x_nm"), value.get("y_nm"))
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        _fail("invalid_point")
    return (_signed_nm(value[0]), _signed_nm(value[1]))


def _ref(value: Any) -> tuple[str, str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 2:
        _fail("invalid_reference")
    uuid_, sub = value[0], value[1]
    if not isinstance(uuid_, str) or not uuid_:
        _fail("invalid_reference")
    if not isinstance(sub, str):
        _fail("invalid_reference")
    return (uuid_, sub)


def _is_ref(value: Any) -> bool:
    return (isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 2
            and isinstance(value[0], str) and isinstance(value[1], str))


def _ref_list(targets: Any) -> list[tuple[str, str]]:
    if _is_ref(targets):
        targets = [targets]
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence) or len(targets) == 0:
        _fail("invalid_targets")
    if len(targets) > _MAX_LEADERS:
        _fail("too_many_leaders")
    return [_ref(t) for t in targets]


def _key(value: Any) -> str:
    if not isinstance(value, str) or not _KEY_RE.match(value):
        _fail("invalid_key")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("invalid_text")
    return value


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _sibling_annotation() -> Any:
    try:
        from cadcore.ops import annotation as mod
        return mod
    except Exception:
        return None


def _new_uuid() -> str:
    mod = _sibling_annotation()
    fn = getattr(mod, "_new_uuid", None)
    if callable(fn):
        try:
            return str(fn())
        except Exception:
            pass
    return _uuid.uuid4().hex


def _append(sk_or_doc: Any, node: dict) -> list[str]:
    """落盘：文档（真实例或字典型）直接进 `annotations[]`；草图入参交同族模块的落盘口径。"""
    ann = _get(sk_or_doc, "annotations")
    if isinstance(ann, list):
        ann.append(node)
        return [node["uuid"]]
    mod = _sibling_annotation()
    fn = getattr(mod, "_append", None)
    if callable(fn):
        return list(fn(sk_or_doc, node))
    _fail("unsupported_annotation_container")
    return []


# --------------------------------------------------------------------------------------
# 被引用实体的真几何取值
# --------------------------------------------------------------------------------------


def _sketch_candidates(sk_or_doc: Any) -> list:
    sks = _get(sk_or_doc, "sketches")
    if isinstance(sks, Sequence) and not isinstance(sks, (str, bytes)):
        return list(sks)
    return [sk_or_doc]


def _entities_of(sk: Any) -> list:
    ents = _get(sk, "entities")
    if isinstance(ents, Sequence) and not isinstance(ents, (str, bytes)):
        return list(ents)
    return []


def _find_entity(sk_or_doc: Any, uuid_: str) -> Any:
    for sk in _sketch_candidates(sk_or_doc):
        for ent in _entities_of(sk):
            if _get(ent, "uuid") == uuid_:
                return ent
        try:
            from cadcore.sketch.constraints import find_entity as _fe
        except Exception:
            continue
        try:
            return _fe(sk, uuid_)
        except Exception:
            continue
    return None


def _entity_lengths(entity: Any) -> dict:
    geom = _get(entity, "geom")
    out: dict = {}
    if isinstance(geom, Mapping):
        for key in _LENGTH_FIELDS:
            v = geom.get(key)
            if _is_int(v):
                out[key] = int(v)
    if out:
        return out
    try:
        from cadcore.sketch.constraints import geom_get
    except Exception:
        return out
    for key in _LENGTH_FIELDS:
        try:
            v = geom_get(entity, key)
        except Exception:
            continue
        if _is_int(v):
            out[key] = int(v)
    return out


def _arrow_point(sk_or_doc: Any, ref: tuple[str, str]) -> tuple[int, int]:
    """箭头落点 = 被引用实体的真几何落点：直线中点 / 圆 (cx+r, cy) / 点 (x, y)。"""
    entity = _find_entity(sk_or_doc, ref[0])
    if entity is None:
        _fail("unknown_reference")
    f = _entity_lengths(entity)
    if all(k in f for k in ("x1", "y1", "x2", "y2")):
        return ((f["x1"] + f["x2"]) // 2, (f["y1"] + f["y2"]) // 2)
    if "cx" in f and "cy" in f:
        r = f.get("r", f.get("radius"))
        if _is_int(r):
            return (f["cx"] + int(r), f["cy"])
        return (f["cx"], f["cy"])
    if "x" in f and "y" in f:
        return (f["x"], f["y"])
    _fail("unsupported_multileader_geometry")
    return (0, 0)


# --------------------------------------------------------------------------------------
# 操作
# --------------------------------------------------------------------------------------


def add_multileader(sk_or_doc: Any, *, targets: Any, at_nm: Any = None, text_key: Any = None,
                    literal_text: Any = None, dogleg_nm: Any = _DEFAULT_DOGLEG_NM,
                    side: str = "right") -> list[str]:
    """**多重引线（MLEADER）**：N 条引线 → 一个 shoulder → dogleg → 内容附着点。

    `targets`：引用序列 `[(uuid, sub_shape_name), ...]`（也接受单个引用）。
    `at_nm`：可选，显式箭头落点序列（与 `targets` 等长，整数纳米）；不给则**从实体几何真算**。
    `text_key` 与 `literal_text` 二选一（同时给 ⇒ ambiguous_content，都不给 ⇒ missing_content）。
    `dogleg_nm`：正长度，shoulder 相对极值侧的偏移，也是 shoulder → content 的水平段长。
    `side`：`"right"`（缺省，取最大 x 侧）/ `"left"`（取最小 x 侧）。

    返回 `[uuid]`；落 `annotations[]`，`target` 存引用列表，全部几何落 `args`。
    """
    refs = _ref_list(targets)
    dogleg = _positive_nm(dogleg_nm)
    if side not in _SIDES:
        _fail("invalid_side")
    if text_key is not None and literal_text is not None:
        _fail("ambiguous_content")
    if text_key is None and literal_text is None:
        _fail("missing_content")

    for r in refs:
        if _find_entity(sk_or_doc, r[0]) is None:
            _fail("unknown_reference")

    if at_nm is None:
        arrows = [_arrow_point(sk_or_doc, r) for r in refs]
    else:
        if isinstance(at_nm, (str, bytes)) or not isinstance(at_nm, Sequence) or len(at_nm) != len(refs):
            _fail("invalid_point_count")
        arrows = [_point(p) for p in at_nm]

    sgn = 1 if side == "right" else -1
    if sgn > 0:
        idx = max(range(len(arrows)), key=lambda i: arrows[i][0])
    else:
        idx = min(range(len(arrows)), key=lambda i: arrows[i][0])
    shoulder = (arrows[idx][0] + sgn * dogleg, arrows[idx][1])
    content_pt = (shoulder[0] + sgn * dogleg, shoulder[1])
    lens = [int(math.isqrt((shoulder[0] - a[0]) ** 2 + (shoulder[1] - a[1]) ** 2)) for a in arrows]
    for ln in lens:
        if ln <= 0:
            _fail("degenerate_leader")

    args: dict = {
        "arrow_nm": [[a[0], a[1]] for a in arrows],
        "shoulder_nm": [shoulder[0], shoulder[1]],
        "content_pt_nm": [content_pt[0], content_pt[1]],
        "dogleg_nm": dogleg,
        "side": side,
        "line_count": len(refs),
        "line_len_nm": lens,
        "longest_nm": max(lens),
        "content_kind": "text",
        "sub_shape_honored": False,
    }
    node: dict = {"uuid": _new_uuid(), "target": [[r[0], r[1]] for r in refs],
                  "kind": "multileader", "args": args}
    if text_key is not None:
        node["text_key"] = _key(text_key)
    else:
        args["literal"] = True
        args["text"] = _text(literal_text)
    return _append(sk_or_doc, node)


def align_multileaders(sk_or_doc: Any, *, at_x_nm: Any = None, uuids: Any = None) -> list[str]:
    """**多重引线对齐**：把页内（或点名）多重引线的 shoulder/content 对齐到同一 x。

    真改数据：`shoulder_nm` → `(x, 原 y)`，`content_pt_nm` → `(x ± dogleg, 原 y)`，
    并按新 shoulder **重算 `line_len_nm` / `longest_nm`**（箭头落点不动 —— 箭头必须留在被引用实体上）。
    `at_x_nm` 不给时取各节点 content_pt 的最大 x。返回被改节点的 uuid 列表。
    """
    want = None
    if uuids is not None:
        if isinstance(uuids, (str, bytes)) or not isinstance(uuids, Sequence) or len(uuids) == 0:
            _fail("invalid_targets")
        want = {str(u) for u in uuids}

    picked: list = []
    for node in _annotation_list(sk_or_doc):
        if _get(node, "kind") != "multileader":
            continue
        if want is not None and _get(node, "uuid") not in want:
            continue
        picked.append(node)
    if not picked:
        _fail("no_multileader")

    if at_x_nm is None:
        xs = []
        for node in picked:
            cp = _get(_get(node, "args"), "content_pt_nm")
            if isinstance(cp, Sequence) and len(cp) == 2 and _is_int(cp[0]):
                xs.append(int(cp[0]))
        if not xs:
            _fail("invalid_point")
        target_x = max(xs)
    else:
        target_x = _signed_nm(at_x_nm)

    out: list[str] = []
    for node in picked:
        args = _get(node, "args")
        if not isinstance(args, dict):
            _fail("immutable_annotation")
        dogleg = _positive_nm(args.get("dogleg_nm"))
        side = args.get("side", "right")
        if side not in _SIDES:
            _fail("invalid_side")
        sgn = 1 if side == "right" else -1
        shoulder = args.get("shoulder_nm")
        if not (isinstance(shoulder, Sequence) and len(shoulder) == 2 and _is_int(shoulder[1])):
            _fail("invalid_point")
        sy = int(shoulder[1])
        args["shoulder_nm"] = [target_x, sy]
        args["content_pt_nm"] = [target_x + sgn * dogleg, sy]
        lens = []
        for a in (args.get("arrow_nm") or []):
            if isinstance(a, Sequence) and len(a) == 2 and _is_int(a[0]) and _is_int(a[1]):
                lens.append(int(math.isqrt((target_x - int(a[0])) ** 2 + (sy - int(a[1])) ** 2)))
        args["line_len_nm"] = lens
        args["longest_nm"] = max(lens) if lens else 0
        out.append(str(_get(node, "uuid", "")))
    return out


def _annotation_list(sk_or_doc: Any) -> list:
    ann = _get(sk_or_doc, "annotations")
    return ann if isinstance(ann, list) else []


#: 操作名 -> 可调用（冻结接口：第一个位置参数是 `sk_or_doc`，其余走关键字）。
OPERATIONS: dict[str, Callable[..., list[str]]] = {
    "add_multileader": add_multileader,
    "align_multileaders": align_multileaders,
}


# --------------------------------------------------------------------------------------
# 自带 selftest（硬断言：几何数值逐个精确比对 + 非法入参 loud + 不半写）
# --------------------------------------------------------------------------------------


def _fixture() -> dict:
    """自证夹具：一页合法 L1（直线 + 圆 + 点，长度全整数纳米，annotations 为空）。"""
    return {"annotations": [], "sketches": [{"uuid": "sk-1", "entities": [
        {"uuid": "ln-1", "kind": "line", "geom": {"x1": 0, "y1": 0, "x2": 20_000_000, "y2": 0}},
        {"uuid": "ci-1", "kind": "circle", "geom": {"cx": 40_000_000, "cy": 10_000_000, "r": 5_000_000}},
        {"uuid": "pt-1", "kind": "point", "geom": {"x": 0, "y": 30_000_000}},
    ]}]}


def _expect_error(fn: Any, reason: str, checks: list) -> None:
    try:
        fn()
    except CadError as exc:
        got = getattr(exc, "reason", None)
        if got is None and exc.args:
            got = exc.args[0]
        ok = (got == reason) or (isinstance(got, str) and got.endswith(reason))
        checks.append(("error:%s" % reason, ok))
        if not ok:
            raise AssertionError("expected reason=%s got=%r" % (reason, got))
        return
    raise AssertionError("expected CadError(reason=%s) but call succeeded" % reason)


def _selftest() -> int:
    checks: list = []

    def check(name: str, cond: Any) -> None:
        checks.append((name, bool(cond)))
        if not cond:
            raise AssertionError("selftest FAILED: %s" % name)

    # 1) 两条引线收敛到同一 shoulder（真几何：直线中点 + 圆 (cx+r, cy) 的整数算术）
    doc = _fixture()
    ids = add_multileader(doc, targets=[("ln-1", ""), ("ci-1", "")],
                          text_key="anno.mleader.note", dogleg_nm=8_000_000)
    node = doc["annotations"][0]
    args = node["args"]
    check("one_uuid", isinstance(ids, list) and len(ids) == 1 and ids[0] == node["uuid"])
    check("kind", node["kind"] == "multileader")
    check("two_refs", node["target"] == [["ln-1", ""], ["ci-1", ""]])
    check("arrow_geom", args["arrow_nm"] == [[10_000_000, 0], [45_000_000, 10_000_000]])
    check("shoulder", args["shoulder_nm"] == [53_000_000, 10_000_000])
    check("content_pt", args["content_pt_nm"] == [61_000_000, 10_000_000])
    check("line_len_int_nm", args["line_len_nm"] == [44_000_000, 8_000_000])
    check("longest", args["longest_nm"] == 44_000_000)
    check("text_key_only", node["text_key"] == "anno.mleader.note" and "literal" not in args)

    # 2) 负坐标 + side=left（纳米坐标必须是有符号整数）
    doc2 = _fixture()
    add_multileader(doc2, targets=[("pt-1", "")], text_key="anno.mleader.note",
                    dogleg_nm=5_000_000, side="left")
    a2 = doc2["annotations"][0]["args"]
    check("left_shoulder", a2["shoulder_nm"] == [-5_000_000, 30_000_000])
    check("left_content", a2["content_pt_nm"] == [-10_000_000, 30_000_000])
    check("left_len", a2["line_len_nm"] == [5_000_000])

    # 3) 非法入参一律 loud，且不半写文档
    doc3 = _fixture()
    _expect_error(lambda: add_multileader(doc3, targets=[("ln-1", "")], text_key="a.b",
                                         dogleg_nm=1.5), "invalid_length", checks)
    _expect_error(lambda: add_multileader(doc3, targets=[("ln-1", "")], text_key="a.b",
                                         dogleg_nm=0), "non_positive_length", checks)
    _expect_error(lambda: add_multileader(doc3, targets=[("nope", "")], text_key="a.b"),
                  "unknown_reference", checks)
    _expect_error(lambda: add_multileader(doc3, targets=[("ln-1", "")]), "missing_content", checks)
    _expect_error(lambda: add_multileader(doc3, targets=[], text_key="a.b"), "invalid_targets", checks)
    _expect_error(lambda: add_multileader(doc3, targets=[("ln-1", "")], text_key="Hole A"),
                  "invalid_key", checks)
    check("no_partial_write", doc3["annotations"] == [])

    # 4) literal 内容 + 对齐（真改 args 并重算线长）
    doc4 = _fixture()
    add_multileader(doc4, targets=[("ln-1", "")], literal_text="Hole A", dogleg_nm=4_000_000)
    add_multileader(doc4, targets=[("ci-1", "")], literal_text="Hole B", dogleg_nm=4_000_000)
    check("literal_flag", doc4["annotations"][0]["args"]["literal"] is True
          and doc4["annotations"][0]["args"]["text"] == "Hole A")
    moved = align_multileaders(doc4, at_x_nm=60_000_000)
    check("align_count", len(moved) == 2)
    check("align_shoulder_x", all(n["args"]["shoulder_nm"][0] == 60_000_000 for n in doc4["annotations"]))
    check("align_content_x", all(n["args"]["content_pt_nm"][0] == 64_000_000 for n in doc4["annotations"]))
    check("align_len_recomputed", all(len(n["args"]["line_len_nm"]) == 1
                                      and _is_int(n["args"]["line_len_nm"][0])
                                      and n["args"]["line_len_nm"][0] > 0 for n in doc4["annotations"]))
    _expect_error(lambda: align_multileaders(_fixture(), at_x_nm=0), "no_multileader", checks)

    if not all(ok for _name, ok in checks):
        raise AssertionError("selftest FAILED: %r" % [n for n, ok in checks if not ok])
    return len(checks)


if __name__ == "__main__":
    _N = _selftest()
    print("SELFTEST family=annotation file=annotation__w28.py ops=%d checks=%d verdict=PASS"
          % (len(OPERATIONS), _N))
    print("FEATURE_OK family=annotation op=add_multileader verdict=PASS")


# ── i18n 中文原文（本文件运行时字符串常量已改英文；原文逐条搬到这里，勿删）──
# 原 annotation__w28.py:489 "孔 A" -> "Hole A"
# 原 annotation__w28.py:490 "孔 B" -> "Hole B"
# 原 annotation__w28.py:492 "孔 A" -> "Hole A"
# 原 annotation__w28.py:483 "孔 A" -> "Hole A"
